"""Orquestador principal del pipeline de scraping.

Flujo:
1. Obtiene la clasificación de las ligas configuradas.
2. Para cada equipo de interés, obtiene roster y calendario (solo si es la
   primera vez o se pide explícitamente con --refresh-teams).
3. Filtra los box scores a capturar:
   - Enfrentamientos directos entre los equipos de interés.
   - Últimos N partidos de cada equipo.
4. Guarda todo en la base de datos de forma idempotente.

Uso:
    python main.py
    python main.py --refresh-teams   # fuerza releer roster/calendario de los equipos
    python main.py --fix-league      # corrige la competición real de los partidos ya guardados
"""
import logging
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import date, datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from packages.baskonia_core import config
from packages.baskonia_core.db import models
from packages.baskonia_core.db.storage import (
    upsert_boxscore,
    upsert_game,
    upsert_player,
    upsert_player_game_log,
    upsert_season_team_stats,
    upsert_team,
    upsert_team_game_stats,
)
from apps.ingest.scraper import baskonia_official
from apps.ingest.scraper import realgm
from apps.ingest.scraper.bbr import fetch_boxscore, fetch_standings, fetch_team
from apps.ingest.scraper.client import BBRClient
from apps.ingest.scraper.fusion import merge_sources
from apps.ingest.scraper.parser import parse_schedule_games
from packages.baskonia_core.stats import effective_fg_pct, team_game_ratings, true_shooting_pct

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _to_int(value: object) -> Optional[int]:
    """Convierte un valor a entero de forma segura."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _normalize_team_name(name: str) -> str:
    """Normaliza un nombre de equipo para comparaciones.

    Convierte a minúsculas, elimina espacios/guiones y quita acentos/diacríticos
    (p.ej. "Río Breogán" y "Rio Breogan" deben normalizar igual: BBR y la web
    oficial del Baskonia no siempre coinciden en si llevan tilde).

    Args:
        name: Nombre del equipo.

    Returns:
        Nombre normalizado.
    """
    without_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch)
    )
    return re.sub(r"[\s-]+", "", without_accents.lower())


def _select_boxscores(
    team_games: Dict[str, List[Dict[str, object]]],
    teams: List[str],
    last_n: int = config.LAST_N_GAMES,
) -> List[Dict[str, object]]:
    """Selecciona los box scores a capturar.

    Incluye:
    - Enfrentamientos directos entre los equipos de interés.
    - Los últimos `last_n` partidos de cada equipo.

    Args:
        team_games: Mapa de slug de equipo a lista de partidos.
        teams: Lista de slugs de equipos de interés.
        last_n: Número de últimos partidos por equipo.

    Returns:
        Lista de partidos seleccionados (sin duplicados).
    """
    selected: List[Dict[str, object]] = []
    seen: Set[str] = set()

    for team_slug, games in team_games.items():
        # Últimos N partidos jugados (con box score disponible)
        played = [g for g in games if g.get("boxscore_url")]
        for game in played[-last_n:]:
            key = game["boxscore_url"]
            if key and key not in seen:
                seen.add(key)
                selected.append(game)

        # Enfrentamientos directos contra otros equipos de interés
        other_teams = {_normalize_team_name(t) for t in teams if t != team_slug}
        for game in games:
            opp = _normalize_team_name(str(game.get("opponent", "")))
            # Coincidencia por subcadena: BBR usa nombres de display con
            # prefijos/sufijos (p.ej. "Surne Bilbao Basket" para el slug "bilbao").
            if opp and any(t in opp for t in other_teams):
                key = game["boxscore_url"]
                if key and key not in seen:
                    seen.add(key)
                    selected.append(game)

    return selected


def _team_games_from_db(session, team, team_slug: str) -> List[Dict[str, object]]:
    """Reconstruye la lista de partidos de un equipo desde la BD, sin red.

    Se usa cuando el equipo ya existe y no se pide refrescar roster/calendario:
    permite que `_select_boxscores` siga funcionando con lo que ya se sabe.
    """
    rows = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    games: List[Dict[str, object]] = []
    for row in rows:
        is_home = row.home_team_id == team.id
        opponent_team = row.away_team if is_home else row.home_team
        points = row.home_score if is_home else row.away_score
        opp_points = row.away_score if is_home else row.home_score
        games.append(
            {
                "date": row.date,
                "opponent": opponent_team.name,
                "opponent_slug": opponent_team.slug,
                "boxscore_url": row.boxscore_url,
                "is_home": is_home,
                "points": points,
                "opp_points": opp_points,
                "notes": row.notes,
                # Propaga la liga ya persistida: sin esto, una ejecución sin
                # --refresh-teams volvería a caer en la liga fija del equipo de
                # origen y regresaría a 'acb' partidos ya corregidos.
                "league": row.league,
                "season": row.season,
                "team_slug": team_slug,
            }
        )
    return games


def resolve_opponent_team(
    session, opponent_name: str, opponent_slug: Optional[str], league: str
) -> models.Team:
    """Resuelve (o crea) el equipo rival de un partido del calendario.

    Prioriza el slug real de BBR extraído del enlace del calendario
    (`opponent_slug`, ver `parse_schedule_games`); si no hay slug (fila sin
    enlace, muy raro), cae en el nombre normalizado como identificador.

    Si ya existe un equipo creado por una ejecución anterior a este fix con
    el nombre normalizado como slug "falso" (p.ej. "clubjoventutbadalona"),
    migra ese equipo al slug real en vez de crear uno duplicado: conserva su
    historial de partidos/box scores ya capturado.

    La búsqueda del equipo existente es por subcadena (no igualdad exacta):
    ni BBR ni otras fuentes (p.ej. la web oficial del Baskonia, ver
    `scraper/baskonia_official.py`) muestran siempre el mismo nombre
    completo del rival (p.ej. "Joventut" vs "Club Joventut Badalona"), así
    que el nombre visto aquí puede no coincidir literalmente con el que se
    usó para crear el equipo la primera vez.

    Cuando no hay `opponent_slug` (fuentes sin slug de BBR) y se encuentra
    un equipo existente por subcadena, se reutiliza tal cual sin tocar su
    slug real; solo se migra el slug cuando `opponent_slug` sí lo aporta.
    """
    slug = opponent_slug or _normalize_team_name(opponent_name)
    team = session.query(models.Team).filter_by(slug=slug).first()
    if team is not None:
        return team

    norm_name = _normalize_team_name(opponent_name)
    match = None
    if len(norm_name) >= 4:
        match = next(
            (
                t
                for t in session.query(models.Team).all()
                if norm_name in _normalize_team_name(t.name) or _normalize_team_name(t.name) in norm_name
            ),
            None,
        )
    if match is not None:
        if opponent_slug:
            logger.info("  Migrando equipo '%s': slug '%s' -> '%s'", match.name, match.slug, slug)
            match.slug = slug
            session.flush()
        return match

    return upsert_team(session, slug, opponent_name, league)


def persist_schedule(session, team: models.Team, games: List[Dict[str, object]]) -> List[models.Game]:
    """Guarda todo el calendario de un equipo en la base de datos.

    A diferencia de `_select_boxscores` (que filtra qué box scores se
    descargan para no saturar el rate-limit), esto guarda **todos** los
    partidos del calendario, incluidos los que aún no se han jugado (sin
    resultado ni box score). Es lo que permite listar los "próximos
    enfrentamientos" sin tener que descargar nada de ellos todavía.

    Propaga la temporada (`season`) a cada partido si el dict la aporta
    (clave `season`), de modo que el backfill de temporada histórica y la
    descarga partido a partido de la temporada actual etiquetan cada partido
    con su temporada.
    """
    saved: List[models.Game] = []
    for game in games:
        opponent_name = str(game.get("opponent", "")).strip()
        if not opponent_name:
            continue
        opponent_team = resolve_opponent_team(session, opponent_name, game.get("opponent_slug"), team.league)

        is_home = bool(game.get("is_home"))
        home_team, away_team = (team, opponent_team) if is_home else (opponent_team, team)
        team_score, opp_score = _to_int(game.get("points")), _to_int(game.get("opp_points"))
        home_score, away_score = (team_score, opp_score) if is_home else (opp_score, team_score)

        game_obj = upsert_game(
            session,
            date=str(game.get("date", "")),
            league=game.get("league") or team.league,
            home_team=home_team,
            away_team=away_team,
            home_score=home_score,
            away_score=away_score,
            boxscore_url=game.get("boxscore_url") or None,
            notes=game.get("notes") or None,
            season=_to_int(game.get("season")),
        )
        saved.append(game_obj)
    return saved


def _merge_duplicate_teams(session, canonical_team, team_slug: str) -> None:
    """Fusiona equipos duplicados creados por el antiguo bug de emparejamiento.

    Antes del fix de sustring matching, un rival cuyo slug (p.ej. "bilbao")
    aparecía dentro de su nombre de display (p.ej. "Surne Bilbao Basket") se
    guardaba como un `Team` nuevo en vez de reutilizar el ya existente. Esto
    reasigna partidos/box scores/jugadores/stats de esos duplicados al equipo
    canónico y borra el duplicado.
    """
    norm_slug = _normalize_team_name(team_slug)
    duplicates = [
        t
        for t in session.query(models.Team).all()
        if t.id != canonical_team.id and norm_slug in _normalize_team_name(t.name)
    ]
    for dup in duplicates:
        session.query(models.Game).filter_by(home_team_id=dup.id).update({"home_team_id": canonical_team.id})
        session.query(models.Game).filter_by(away_team_id=dup.id).update({"away_team_id": canonical_team.id})
        session.query(models.BoxScore).filter_by(team_id=dup.id).update({"team_id": canonical_team.id})
        session.query(models.TeamGameStats).filter_by(team_id=dup.id).update({"team_id": canonical_team.id})
        session.query(models.Player).filter_by(team_id=dup.id).update({"team_id": canonical_team.id})
        session.delete(dup)
        logger.info("  Fusionado equipo duplicado '%s' -> '%s'", dup.name, canonical_team.name)
    if duplicates:
        session.flush()


def _team_boxscore_totals(session, game_id: int, team_id: int) -> Dict[str, float]:
    """Agrega los totales de un equipo en un partido a partir de sus box scores.

    Devuelve tanto los totales necesarios para las estadísticas avanzadas
    (fga, fta, orb, tov) como los totales de equipo que se persisten en
    `team_game_stats` (team_points, team_rebounds, team_assists,
    team_fg_attempted, team_ft_attempted).
    """
    rows = session.query(models.BoxScore).filter_by(game_id=game_id, team_id=team_id).all()
    return {
        "fga": sum(r.fg_attempted or 0 for r in rows),
        "fta": sum(r.ft_attempted or 0 for r in rows),
        "orb": sum(r.offensive_rebounds or 0 for r in rows),
        "tov": sum(r.turnovers or 0 for r in rows),
        "team_points": sum(r.points or 0 for r in rows),
        "team_rebounds": sum(r.rebounds or 0 for r in rows),
        "team_assists": sum(r.assists or 0 for r in rows),
        "team_fg_attempted": sum(r.fg_attempted or 0 for r in rows),
        "team_ft_attempted": sum(r.ft_attempted or 0 for r in rows),
    }


def _backfill_player_advanced(session, game_id: int) -> None:
    """Calcula eFG%/TS% para box scores ya guardados que aun no los tengan.

    Solo usa datos ya persistidos (sin red), para cubrir filas guardadas antes
    de que existiera este cálculo.
    """
    rows = (
        session.query(models.BoxScore)
        .filter_by(game_id=game_id)
        .filter(models.BoxScore.efg_pct.is_(None))
        .all()
    )
    for row in rows:
        row.efg_pct = effective_fg_pct(row.fg_made, row.fg_attempted, row.fg3_made)
        row.ts_pct = true_shooting_pct(row.points, row.fg_attempted, row.ft_attempted)
    if rows:
        session.flush()


def _ensure_advanced_stats(
    session,
    game_obj: "models.Game",
    home_team: "models.Team",
    away_team: "models.Team",
) -> None:
    """Calcula y guarda pace/ORtg/DRtg/Net Rating del partido si aun no existen.

    No recalcula si ya hay estadisticas guardadas para ambos equipos, y no hace
    nada si todavia no hay box scores de los que partir.
    """
    existing_team_ids = {
        row.team_id
        for row in session.query(models.TeamGameStats).filter_by(game_id=game_obj.id).all()
    }
    if home_team.id in existing_team_ids and away_team.id in existing_team_ids:
        return

    home_totals = _team_boxscore_totals(session, game_obj.id, home_team.id)
    away_totals = _team_boxscore_totals(session, game_obj.id, away_team.id)
    if not home_totals["fga"] and not away_totals["fga"]:
        return  # sin box score todavia, nada que calcular

    home_ratings = team_game_ratings(home_totals, away_totals, game_obj.home_score, game_obj.away_score)
    away_ratings = team_game_ratings(away_totals, home_totals, game_obj.away_score, game_obj.home_score)
    upsert_team_game_stats(session, game_obj, home_team, home_ratings)
    upsert_team_game_stats(session, game_obj, away_team, away_ratings)
    pace = home_ratings.get("pace")
    logger.info("    Estadisticas avanzadas calculadas (pace=%s)", round(pace, 1) if pace else None)


def _capture_and_store_boxscore(session, client: BBRClient, game_obj: "models.Game") -> None:
    """Descarga (si falta) el box score de un partido ya guardado y calcula sus stats avanzadas.

    No hace nada si el partido no tiene `boxscore_url` todavía (no se ha
    jugado) o si su box score ya está en la base de datos. Reutilizado tanto
    por el pipeline batch (`run`) como por la descarga puntual de un rival
    desde la GUI (`fetch_opponent_scouting`).
    """
    already_saved = session.query(models.BoxScore).filter_by(game_id=game_obj.id).first() is not None
    if already_saved:
        logger.info("    Box score ya en base de datos, se omite descarga: %s", game_obj.boxscore_url)
    elif not game_obj.boxscore_url:
        return  # partido aun no jugado, nada que descargar
    else:
        try:
            box_data = fetch_boxscore(client, game_obj.boxscore_url)
            logger.info("    Box score capturado: %s (%d equipos)", game_obj.boxscore_url, len(box_data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("    Error capturando box score %s: %s", game_obj.boxscore_url, exc)
            return

        for box_team, rows in (
            (game_obj.home_team, box_data.get("home", [])),
            (game_obj.away_team, box_data.get("away", [])),
        ):
            for player_row in rows:
                player_name = player_row.get("Player") or player_row.get("player")
                if not player_name:
                    continue
                upsert_boxscore(session, game_obj, box_team, player_name, player_row)

    # Estadísticas avanzadas del partido (eFG%/TS% ya se calculan por jugador
    # dentro de upsert_boxscore; aquí calculamos pace/ORtg/DRtg por equipo).
    _backfill_player_advanced(session, game_obj.id)
    _ensure_advanced_stats(session, game_obj, game_obj.home_team, game_obj.away_team)


def fetch_opponent_scouting(session, client: BBRClient, opponent_team: "models.Team", last_n: int) -> None:
    """Descarga bajo demanda los datos de un rival para poder scoutearlo.

    Pensado para el flujo de la GUI: el usuario elige un próximo
    enfrentamiento y solo entonces se piden (y guardan) los datos del rival
    que aun no estén en la base de datos: roster/calendario si es la
    primera vez que se ve a este equipo, y el box score de sus últimos
    `last_n` partidos jugados para poder calcular su forma reciente.

    No hace nada de red para lo que ya esté guardado (idempotente, igual que
    el resto del pipeline).

    Raises:
        RuntimeError: si el equipo no se encuentra en BBR con el slug
            guardado. Pasa con rivales resueltos desde una fuente sin slug
            real de BBR (p.ej. el calendario oficial de baskonia.com, ver
            `resolve_opponent_team`) cuando su nombre no coincidía con
            ningún equipo ya conocido: el slug guardado es una suposición
            (nombre normalizado) que puede no existir en BBR.
    """
    has_roster = session.query(models.Player).filter_by(team_id=opponent_team.id).first() is not None
    if not has_roster:
        logger.info("Descargando roster/calendario de %s (primera vez que se scoutea)", opponent_team.name)
        try:
            team_data = fetch_team(client, opponent_team.slug, config.SEASON)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "No se pudo obtener a '%s' de BBR con el slug '%s': %s",
                opponent_team.name,
                opponent_team.slug,
                exc,
            )
            raise RuntimeError(
                f"No se encontró a '{opponent_team.name}' en Basketball-Reference "
                f"(slug probado: '{opponent_team.slug}'). Es un rival resuelto por nombre desde "
                "otra fuente (p.ej. el calendario oficial del Baskonia) y su nombre no coincide "
                "con ningún equipo ya conocido de BBR, así que no se puede adivinar su slug real."
            ) from exc
        for player_row in team_data.get("roster", []):
            player_name = player_row.get("Player") or player_row.get("player")
            if player_name:
                upsert_player(
                    session,
                    player_name,
                    opponent_team,
                    position=player_row.get("Pos"),
                    number=player_row.get("No"),
                )
        schedule_games = parse_schedule_games(team_data.get("html", ""))
        persist_schedule(session, opponent_team, schedule_games)
        session.commit()

    games = _team_games_from_db(session, opponent_team, opponent_team.slug)
    played = [g for g in games if g.get("boxscore_url")]
    for game in played[-last_n:]:
        game_obj = session.query(models.Game).filter_by(boxscore_url=game["boxscore_url"]).first()
        if game_obj is not None:
            _capture_and_store_boxscore(session, client, game_obj)
    session.commit()


def run(refresh_teams: bool = False) -> None:
    """Ejecuta el pipeline completo de captura.

    Args:
        refresh_teams: Si es True, vuelve a descargar roster y calendario de
            los equipos aunque ya existan en la base de datos. Por defecto
            (False), un equipo ya conocido no se vuelve a consultar en BBR.
    """
    logger.info("Iniciando pipeline de scraping de Basketball-Reference")
    logger.info("Temporada: %s | Equipos: %s | Ligas: %s", config.SEASON, config.TEAMS, config.LEAGUES)

    client = BBRClient()
    Session = models.init_db()
    session = Session()

    try:
        # 1. Clasificaciones de las ligas
        for league in config.LEAGUES:
            logger.info("=== Clasificación %s ===", league)
            standings = fetch_standings(client, league, config.SEASON)
            for row in standings:
                team_name = row.get("team") or next(iter(row.values()), "")
                logger.info("  %s", team_name)

        # 2. Datos de los equipos de interés
        teams_by_slug: Dict[str, models.Team] = {}
        team_games: Dict[str, List[Dict[str, object]]] = {}
        for team_slug in config.TEAMS:
            logger.info("=== Equipo %s ===", team_slug)
            existing_team = session.query(models.Team).filter_by(slug=team_slug).first()

            if existing_team is not None and not refresh_teams:
                logger.info(
                    "  Equipo ya conocido, se omite roster/calendario (usa --refresh-teams para forzar)"
                )
                team = existing_team
                games = _team_games_from_db(session, team, team_slug)
            else:
                team_data = fetch_team(client, team_slug, config.SEASON)

                # Guardar equipo (asumimos liga ACB para el caso de uso)
                team_name = config.TEAM_DISPLAY_NAMES.get(team_slug, team_slug.title())
                team = upsert_team(session, team_slug, team_name, "acb")

                # Roster
                for player_row in team_data.get("roster", []):
                    player_name = player_row.get("Player") or player_row.get("player")
                    if player_name:
                        upsert_player(
                            session,
                            player_name,
                            team,
                            position=player_row.get("Pos"),
                            number=player_row.get("No"),
                        )
                logger.info("  Roster: %d jugadores", len(team_data.get("roster", [])))

                # Calendario estructurado: se guarda completo (jugados y por
                # jugar) para poder listar "próximos enfrentamientos" sin
                # tener que descargar nada de ellos todavía.
                games = parse_schedule_games(team_data.get("html", ""))
                persist_schedule(session, team, games)
                for game in games:
                    game["team_slug"] = team_slug  # equipo dueño del calendario

            teams_by_slug[team_slug] = team
            team_games[team_slug] = games
            logger.info("  Calendario: %d partidos", len(games))

            # Reparar duplicados creados por el bug de emparejamiento exacto (ya corregido)
            _merge_duplicate_teams(session, team, team_slug)

        # 2.5 Calendario oficial de baskonia.com: completa "próximos
        # enfrentamientos" con partidos que BBR aún no ha publicado (nueva
        # temporada, Supercopa, Euskal Kopa...). Solo aplica al Baskonia
        # (primer equipo de TEAMS): es la única web que tenemos de esta
        # fuente. No descarga box scores; se ejecuta siempre (barato, un par
        # de peticiones) y nunca debe tumbar el resto del pipeline si falla.
        focus_team = teams_by_slug.get(config.TEAMS[0])
        if focus_team is not None:
            try:
                official_games = baskonia_official.fetch_upcoming_games()
                persist_schedule(session, focus_team, official_games)
                logger.info(
                    "=== Calendario oficial baskonia.com: %d partidos próximos ===", len(official_games)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo obtener el calendario oficial de baskonia.com: %s", exc)

            # 2.6 Plantilla actual de baskonia.com: nombre, posición, dorsal
            # y foto de cada jugador (BBR no tiene fotos y solo se actualiza
            # con --refresh-teams). Mismo motivo para el try/except: es un
            # complemento, no debe tumbar el pipeline si la fuente falla.
            try:
                roster = baskonia_official.fetch_current_roster()
                for player_row in roster:
                    upsert_player(
                        session,
                        player_row["name"],
                        focus_team,
                        position=player_row.get("position"),
                        number=player_row.get("number"),
                        photo_url=player_row.get("photo_url"),
                    )
                session.commit()
                logger.info("=== Plantilla oficial baskonia.com: %d jugadores ===", len(roster))
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo obtener la plantilla oficial de baskonia.com: %s", exc)

        # 3. Seleccionar box scores a capturar
        selected = _select_boxscores(team_games, config.TEAMS)
        logger.info("=== Box scores seleccionados: %d ===", len(selected))
        for game in selected:
            logger.info("  %s vs %s (%s)", game.get("date"), game.get("opponent"), game.get("boxscore_url"))

        # 4. Capturar box scores seleccionados y persistir partidos + stats
        for game in selected:
            source_team = teams_by_slug[game["team_slug"]]
            opponent_team = resolve_opponent_team(
                session, str(game.get("opponent", "")), game.get("opponent_slug"), source_team.league
            )

            is_home = bool(game.get("is_home"))
            home_team, away_team = (source_team, opponent_team) if is_home else (opponent_team, source_team)
            team_score, opp_score = _to_int(game.get("points")), _to_int(game.get("opp_points"))
            home_score, away_score = (team_score, opp_score) if is_home else (opp_score, team_score)

            game_obj = upsert_game(
                session,
                date=str(game.get("date", "")),
                league=game.get("league") or source_team.league,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                boxscore_url=game.get("boxscore_url"),
                notes=game.get("notes") or None,
                season=_to_int(game.get("season")),
            )
            _capture_and_store_boxscore(session, client, game_obj)

        session.commit()
        logger.info("Pipeline completado. Datos guardados en %s", config.DATABASE_URL)

    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Error en el pipeline: %s", exc)
        sys.exit(1)
    finally:
        session.close()


def backfill_league() -> None:
    """Corrige `Game.league` de partidos ya persistidos con la competición real de BBR.

    Reutiliza el calendario de los equipos que ya tienen roster propio guardado
    (hoy: vitoria, bilbao, gran-canaria — los únicos con `fetch_team()` ya
    ejecutado alguna vez): son los únicos cuya página de calendario de BBR se
    puede re-visitar para leer la competición real de cada fila (ver
    `scraper.parser._table_competition`). No re-descarga equipos sin roster
    propio (su calendario solo se conoce por reconstrucción desde la BD u otra
    fuente, sin id de tabla de BBR que consultar).

    Hace copia de seguridad de la base de datos (`_backup_database()`) antes de
    escribir nada, y es idempotente: `upsert_game`/`persist_schedule` ya son
    upserts por `(date, home_team_id, away_team_id)`, así que ejecutarlo varias
    veces no duplica filas; solo puede volver a fijar `.league` al mismo valor
    correcto.
    """
    logger.info("=== Backfill de Game.league ===")
    _log_backup(_backup_database())

    client = BBRClient()
    Session = models.init_db()
    session = Session()
    try:
        logger.info("Distribución ANTES (partidos jugados): %s", _league_counts(session))

        teams_with_roster = session.query(models.Team).join(models.Team.players).distinct().all()
        logger.info("Equipos con roster propio a re-consultar: %s", [t.slug for t in teams_with_roster])

        for team in teams_with_roster:
            logger.info("--- Re-descargando calendario de %s ---", team.slug)
            try:
                team_data = fetch_team(client, team.slug, config.SEASON)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo re-descargar el calendario de %s: %s", team.slug, exc)
                continue
            games = parse_schedule_games(team_data.get("html", ""))
            persist_schedule(session, team, games)
            session.commit()

        logger.info("Distribución DESPUÉS (partidos jugados): %s", _league_counts(session))
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Error en el backfill de liga: %s", exc)
        sys.exit(1)
    finally:
        session.close()


def _league_counts(session) -> Dict[str, int]:
    """Cuenta partidos jugados por liga (para el log antes/después del backfill)."""
    rows = session.query(models.Game.league).filter(models.Game.home_score.isnot(None)).all()
    counts: Dict[str, int] = {}
    for (league,) in rows:
        counts[league] = counts.get(league, 0) + 1
    return counts


def _season_date_range(season: int) -> Tuple[date, date]:
    """Devuelve el rango de fechas de una temporada (octubre a junio).

    Args:
        season: Año de inicio de la temporada (p.ej. 2025 para 2025-26).

    Returns:
        Tupla (fecha_inicio, fecha_fin) del rango de la temporada.
    """
    return date(season, 10, 1), date(season + 1, 6, 30)


def _is_realgm_url(url: str) -> bool:
    """Indica si una URL de box score apunta a RealGM.

    Args:
        url: URL guardada en `Game.boxscore_url`.

    Returns:
        `True` si el host es el de RealGM.
    """
    return urlparse(str(url)).netloc.endswith("realgm.com")


def _capture_realgm_boxscore(session, game_obj: "models.Game") -> None:
    """Descarga (si falta) el box score de un partido desde RealGM y rellena
    los game logs de jugador.

    RealGM es la fuente principal de box scores. Para cada partido jugado con
    `boxscore_url` de RealGM, descarga las tablas de jugadores de ambos
    equipos, persiste los box scores (`upsert_boxscore`) y rellena
    `player_game_logs` (`upsert_player_game_log`). No hace nada si el partido
    ya tiene box score guardado (idempotente).

    El `boxscore_url` guardado no siempre es de RealGM: si RealGM falló para esa
    competición, la fusión de fuentes puede haber dejado ganar a BBR y la URL
    apunta a basketball-reference.com. Pasarla al parser de RealGM no daría un
    box score, así que se omite explícitamente con un aviso en vez de intentar
    la descarga y fallar en el parseo.

    Args:
        session: Sesión de base de datos.
        game_obj: Partido ya persistido.
    """
    already_saved = session.query(models.BoxScore).filter_by(game_id=game_obj.id).first() is not None
    if already_saved:
        return
    if not game_obj.boxscore_url:
        return  # partido aun no jugado o sin enlace
    if not _is_realgm_url(game_obj.boxscore_url):
        logger.warning(
            "    Box score de otra fuente (no RealGM), se omite: %s", game_obj.boxscore_url
        )
        return

    try:
        box_data = realgm.fetch_game_boxscore(game_obj.boxscore_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("    Error capturando box score RealGM %s: %s", game_obj.boxscore_url, exc)
        return

    for box_team, rows in (
        (game_obj.home_team, box_data.get("home", [])),
        (game_obj.away_team, box_data.get("away", [])),
    ):
        for player_row in rows:
            player_name = player_row.get("player_name")
            if not player_name:
                continue
            upsert_boxscore(session, game_obj, box_team, player_name, player_row)
            # Rellenar el game log del jugador (nivel jugador/temporada).
            player = upsert_player(session, player_name, box_team)
            upsert_player_game_log(
                session, player, game_obj, player_row, season=game_obj.season
            )

    # Estadísticas avanzadas del partido (eFG%/TS% ya se calculan por jugador
    # dentro de upsert_boxscore; aquí pace/ORtg/DRtg por equipo).
    _backfill_player_advanced(session, game_obj.id)
    _ensure_advanced_stats(session, game_obj, game_obj.home_team, game_obj.away_team)


def _capture_boxscores_and_logs(session, team: "models.Team", season: int) -> None:
    """Captura los box scores de los partidos jugados de un equipo en una
    temporada y rellena `player_game_logs`.

    Recorre los partidos del equipo en la temporada que ya tienen
    `boxscore_url` (jugados) y los captura desde RealGM de forma idempotente.
    Es el paso que conecta la captura completa a nivel de partido/jugador en
    `backfill_season`/`scout_team`.

    Args:
        session: Sesión de base de datos.
        team: Equipo objetivo.
        season: Año de inicio de la temporada.
    """
    games = (
        session.query(models.Game)
        .filter(
            (models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id),
            models.Game.season == season,
            models.Game.boxscore_url.isnot(None),
        )
        .all()
    )
    for game_obj in games:
        _capture_realgm_boxscore(session, game_obj)
    session.commit()
    logger.info("  Box scores capturados para %d partidos de %s (%s)", len(games), team.name, season)


def _populate_season_team_stats(session, team: "models.Team", season: int) -> None:
    """Calcula y persiste los agregados de temporada de un equipo.

    Agrega desde los partidos jugados y los box scores ya guardados del
    equipo en la temporada: partidos jugados, victorias/derrotas, medias de
    puntos/rebotes/asistencias y las medias de pace/ORtg/DRtg/Net Rating de
    `team_game_stats`. Idempotente (`upsert_season_team_stats`).

    Args:
        session: Sesión de base de datos.
        team: Equipo objetivo.
        season: Año de inicio de la temporada.
    """
    games = (
        session.query(models.Game)
        .filter(
            (models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id),
            models.Game.season == season,
            models.Game.home_score.isnot(None),
        )
        .all()
    )
    if not games:
        return

    wins = losses = 0
    total_points = total_rebounds = total_assists = 0
    pace_vals = off_vals = def_vals = net_vals = []
    for game_obj in games:
        is_home = game_obj.home_team_id == team.id
        team_score = game_obj.home_score if is_home else game_obj.away_score
        opp_score = game_obj.away_score if is_home else game_obj.home_score
        if team_score is not None and opp_score is not None:
            if team_score > opp_score:
                wins += 1
            elif team_score < opp_score:
                losses += 1
        total_points += team_score or 0

        # Agregados de box score del equipo en el partido.
        totals = _team_boxscore_totals(session, game_obj.id, team.id)
        total_rebounds += totals["team_rebounds"]
        total_assists += totals["team_assists"]

        # Medias de pace/ORtg/DRtg/Net Rating desde team_game_stats.
        tgs = (
            session.query(models.TeamGameStats)
            .filter_by(game_id=game_obj.id, team_id=team.id)
            .first()
        )
        if tgs is not None:
            if tgs.pace is not None:
                pace_vals.append(tgs.pace)
            if tgs.off_rating is not None:
                off_vals.append(tgs.off_rating)
            if tgs.def_rating is not None:
                def_vals.append(tgs.def_rating)
            if tgs.net_rating is not None:
                net_vals.append(tgs.net_rating)

    n = len(games)
    stats = {
        "games_played": n,
        "wins": wins,
        "losses": losses,
        "points_per_game": round(total_points / n, 1) if n else None,
        "rebounds_per_game": round(total_rebounds / n, 1) if n else None,
        "assists_per_game": round(total_assists / n, 1) if n else None,
        "pace": round(sum(pace_vals) / len(pace_vals), 1) if pace_vals else None,
        "off_rating": round(sum(off_vals) / len(off_vals), 1) if off_vals else None,
        "def_rating": round(sum(def_vals) / len(def_vals), 1) if def_vals else None,
        "net_rating": round(sum(net_vals) / len(net_vals), 1) if net_vals else None,
    }
    upsert_season_team_stats(session, team, season, stats)
    session.commit()
    logger.info("  Agregados de temporada %s de %s: %d PJ, %d V, %d D",
                season, team.name, n, wins, losses)


def backfill_season(season: int) -> None:
    """Completa los partidos de una temporada histórica del Baskonia en las 4 competiciones.

    Usa RealGM como fuente principal (Euroliga y ACB) y BBR, CMS baskonia.com
    y la API ACB como backup, fusionando todas las fuentes con
    `scraper.fusion.merge_sources` (prioridad RealGM > BBR > CMS > ACB) y
    persistiendo con upserts idempotentes. Cubre Euroliga, ACB, Copa del Rey
    y Supercopa (estas dos últimas solo las aportan CMS/ACB).

    Hace copia de seguridad de la base de datos antes de escribir nada.

    Args:
        season: Año de inicio de la temporada a completar (p.ej. 2025 para
            2025-26).
    """
    logger.info("=== Backfill de temporada %s ===", season)
    _log_backup(_backup_database())

    Session = models.init_db()
    session = Session()
    try:
        # Equipo del Baskonia (slug 'vitoria' en BBR, nombre 'Baskonia' en
        # RealGM/CMS/ACB). Se crea si no existe.
        team = session.query(models.Team).filter_by(slug="vitoria").first()
        if team is None:
            team = upsert_team(session, "vitoria", "Baskonia", "acb")

        from_date, to_date = _season_date_range(season)

        for league in config.BACKFILL_COMPETITIONS:
            logger.info("--- Competición %s ---", league)
            sources: List[Tuple[str, List[Dict[str, object]]]] = []

            # RealGM (principal): Euroliga y ACB.
            if league in ("euroleague", "acb"):
                try:
                    rgm_games = realgm.fetch_team_schedule(
                        config.REALGM_TEAM_NAME, season, league
                    )
                    sources.append(("realgm", rgm_games))
                    logger.info("  RealGM: %d partidos", len(rgm_games))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("  RealGM falló para %s: %s", league, exc)

            # BBR (backup): Euroliga y ACB.
            if league in ("euroleague", "acb"):
                try:
                    client = BBRClient()
                    team_data = fetch_team(client, "vitoria", season)
                    bbr_games = parse_schedule_games(team_data.get("html", ""))
                    # Filtrar por la competición objetivo (BBR mezcla ligas en
                    # la misma página de calendario).
                    bbr_games = [g for g in bbr_games if g.get("league") == league]
                    sources.append(("bbr", bbr_games))
                    logger.info("  BBR: %d partidos", len(bbr_games))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("  BBR falló para %s: %s", league, exc)

            # CMS baskonia.com (backup): cubre las 4 competiciones.
            try:
                cms_games = baskonia_official.fetch_games(from_date=from_date, to_date=to_date)
                cms_games = [g for g in cms_games if g.get("league") == league]
                sources.append(("cms", cms_games))
                logger.info("  CMS: %d partidos", len(cms_games))
            except Exception as exc:  # noqa: BLE001
                logger.warning("  CMS falló para %s: %s", league, exc)

            # API ACB (backup): ACB y Copa del Rey.
            if league in ("acb", "copa-del-rey"):
                try:
                    from apps.ingest.scraper import acb_api

                    acb_games = acb_api.fetch_team_games(config.ACB_TEAM_NAME, season)
                    acb_games = [g for g in acb_games if g.get("league") == league]
                    sources.append(("acb", acb_games))
                    logger.info("  ACB API: %d partidos", len(acb_games))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("  ACB API falló para %s: %s", league, exc)

            merged = merge_sources(sources)
            for game in merged:
                game["season"] = season
            persist_schedule(session, team, merged)
            session.commit()
            logger.info("  %d partidos únicos persistidos", len(merged))

        # Captura completa: box scores de los partidos jugados + game logs de
        # jugador + agregados de temporada (criterio 5 del diseño).
        _capture_boxscores_and_logs(session, team, season)
        _populate_season_team_stats(session, team, season)

        logger.info("Backfill de temporada %s completado", season)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Error en el backfill de temporada: %s", exc)
        sys.exit(1)
    finally:
        session.close()


def scout_team(team_ref: str, season: int) -> None:
    """Descarga puntualmente y cachea los partidos de un equipo rival (no solo Baskonia).

    Permite evaluar el estado de forma de un rival: descarga su calendario y
    box scores de la temporada indicada y los persiste en la misma BD con
    upserts idempotentes, de modo que no haya que volver a descargarlos.

    Args:
        team_ref: Nombre o slug del equipo a scoutear (p.ej. "Real Madrid" o
            "real-madrid").
        season: Año de inicio de la temporada a scoutear.
    """
    logger.info("=== Scout de equipo %s (temporada %s) ===", team_ref, season)
    _log_backup(_backup_database())

    Session = models.init_db()
    session = Session()
    try:
        # Resolver (o crear) el equipo objetivo por nombre/slug.
        team = session.query(models.Team).filter_by(slug=team_ref).first()
        if team is None:
            team = session.query(models.Team).filter_by(name=team_ref).first()
        if team is None:
            team = upsert_team(session, _normalize_team_name(team_ref), team_ref, "acb")

        # RealGM como fuente principal (Euroliga y ACB).
        sources: List[Tuple[str, List[Dict[str, object]]]] = []
        for league in ("euroleague", "acb"):
            try:
                rgm_games = realgm.fetch_team_schedule(team_ref, season, league)
                sources.append(("realgm", rgm_games))
                logger.info("  RealGM %s: %d partidos", league, len(rgm_games))
            except Exception as exc:  # noqa: BLE001
                logger.warning("  RealGM falló para %s: %s", league, exc)

        merged = merge_sources(sources)
        for game in merged:
            game["season"] = season
        persist_schedule(session, team, merged)
        session.commit()

        # Captura completa del rival: box scores + game logs + agregados.
        _capture_boxscores_and_logs(session, team, season)
        _populate_season_team_stats(session, team, season)

        logger.info("Scout de %s completado: %d partidos", team_ref, len(merged))
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Error en el scout de equipo: %s", exc)
        sys.exit(1)
    finally:
        session.close()


def _log_backup(backup_path: Optional[str]) -> None:
    """Registra el resultado de `_backup_database()`.

    Args:
        backup_path: Ruta devuelta por `_backup_database()`, o `None` si no
            había base de datos previa que copiar.
    """
    if backup_path is None:
        logger.info("Sin base de datos previa: no se ha creado copia de seguridad")
    else:
        logger.info("Copia de seguridad creada en %s", backup_path)


def _backup_database() -> Optional[str]:
    """Copia el fichero sqlite de `config.DATABASE_URL` con un sufijo de timestamp.

    Solo soporta `DATABASE_URL` de tipo `sqlite:///<ruta>` (único backend usado
    en este PoC). Nunca sobrescribe una copia anterior: cada llamada genera un
    fichero nuevo.

    La base de datos se abre en modo WAL, así que las escrituras recientes
    pueden vivir en el fichero `-wal` y no en el `.db`. Antes de copiar se hace
    `wal_checkpoint(TRUNCATE)` para volcarlas: sin eso la copia perdería en
    silencio todo lo que aún no se hubiera consolidado.

    Returns:
        Ruta del fichero de copia de seguridad creado, o `None` si todavía no
        existe base de datos (primera ejecución: no hay nada que salvar).

    Raises:
        RuntimeError: si `DATABASE_URL` no es sqlite (no hay fichero que copiar).
    """
    prefix = "sqlite:///"
    if not config.DATABASE_URL.startswith(prefix):
        raise RuntimeError(f"Backup no soportado para DATABASE_URL='{config.DATABASE_URL}' (solo sqlite:///)")
    db_path = config.DATABASE_URL[len(prefix):]

    if not os.path.exists(db_path):
        # Primera ejecución: `models.init_db()` creará la BD después. No hay
        # datos previos que proteger, así que no es un error.
        logger.info("No existe %s todavía: se omite la copia de seguridad", db_path)
        return None

    # Consolidar el WAL en el .db antes de copiar (ver docstring).
    try:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    except sqlite3.Error as exc:  # noqa: BLE001
        logger.warning("No se pudo consolidar el WAL antes del backup: %s", exc)

    backup_path = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    return backup_path

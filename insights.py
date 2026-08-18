"""Forma reciente por jugador (medias últimos N partidos), stats por-36 minutos,
capa de temporada/competición, análisis de scouting (rachas, dificultad de
calendario, proyección de partido, narrativa, carga de minutos) y validaciones
básicas de calidad de los datos guardados.

No hace peticiones de red: solo agrega lo que ya está en `boxscores`/`games`.
"""
from datetime import datetime
from typing import Dict, List, Optional

from db import models
from stats import project_matchup

# Umbrales de z-score para etiquetar una racha (idea 1 de 7.3): ±1 desviación
# típica respecto a la media del propio jugador en esa temporada.
ZSCORE_HOT_THRESHOLD = 1.0
ZSCORE_COLD_THRESHOLD = -1.0

# Umbrales heurísticos de la narrativa automática (idea 5 de 7.3). No se
# calculan contra una media de liga real (la BD no tiene muestra suficiente para
# eso): son valores de diseño, ajustables sin tocar ninguna firma pública.
_NARRATIVE_PACE_FAST = 75.0
_NARRATIVE_PACE_SLOW = 68.0
_NARRATIVE_FG3A_RATE_HIGH = 0.40
_NARRATIVE_FG3A_RATE_LOW = 0.25

# Etiquetas de competición para la UI (`Game.league` guarda el código interno).
_LEAGUE_LABELS = {"acb": "ACB", "euroleague": "Euroliga", "supercopa": "Supercopa"}


def season_start_year(date_str: Optional[str]) -> Optional[int]:
    """Deriva el año de inicio de la temporada europea a partir de la fecha
    de un partido en formato BBR ('%a, %b %d, %Y').

    Una temporada europea cruza el año natural (p.ej. 2025-26 va de
    septiembre de 2025 a junio de 2026); el año de la fecha del partido NO
    equivale a "temporada" si el partido es de enero-junio. Regla de corte:
    mes >= 7 -> la temporada empieza ese año natural; mes < 7 -> empezó el
    año natural anterior. Verificado sin ambigüedad contra
    `data/baskonia.db` real: nunca hay partidos en julio/agosto (descanso
    real entre temporadas), así que ningún partido depende de dónde se
    ponga el corte dentro de ese hueco.

    Duplica deliberadamente el parseo de `app.parse_bbr_date` (mismo
    formato): `insights.py` no puede importar de `app.py` (regla de capas)
    y esta derivación es estructural para el filtrado por temporada de las
    funciones de agregación, no una cuestión de presentación.

    Args:
        date_str: Fecha del partido tal cual se guarda en `Game.date`.

    Returns:
        Año de inicio de temporada (p.ej. 2025 para "2025-26"), o `None`
        si `date_str` no se puede parsear.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%a, %b %d, %Y")
    except (ValueError, TypeError):
        return None
    return dt.year if dt.month >= 7 else dt.year - 1


def season_label(season: Optional[int]) -> str:
    """Formatea un año de inicio de temporada como 'AAAA-AA' (2025 -> '2025-26'), o '-' si `None`."""
    if season is None:
        return "-"
    return f"{season}-{str(season + 1)[-2:]}"


def list_seasons(session, team: "models.Team") -> List[int]:
    """Temporadas con al menos un partido guardado (jugado o pendiente) de `team`.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyo calendario se recorre.

    Returns:
        Años de inicio de temporada, ordenados descendente (más reciente/
        futura primero). Vacía si el equipo no tiene ningún partido.
    """
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    seasons = {season_start_year(g.date) for g in games}
    seasons.discard(None)
    return sorted(seasons, reverse=True)


def current_season(
    session, team: "models.Team", reference_date: Optional[datetime] = None
) -> Optional[int]:
    """Temporada a preseleccionar por defecto en la UI.

    Prioriza la temporada de `reference_date` (por defecto ahora) SI ya
    tiene algún partido jugado (algún `TeamGameStats` de `team` dentro de
    ese bucket); si no (descanso de temporada real), cae a la temporada más
    reciente con al menos un partido jugado, para no abrir la app con
    paneles vacíos por defecto. El usuario puede elegir explícitamente la
    temporada vacía desde el selector si quiere verla.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo de referencia.
        reference_date: fecha desde la que se decide cuál es "la temporada
            actual" (por defecto, ahora).

    Returns:
        Año de inicio de temporada, o `None` si el equipo no tiene ningún
        partido guardado en absoluto (BD recién inicializada).
    """
    seasons = list_seasons(session, team)
    if not seasons:
        return None
    reference_date = reference_date or datetime.now()
    ref_season = season_start_year(reference_date.strftime("%a, %b %d, %Y"))
    stats_rows = (
        session.query(models.TeamGameStats)
        .join(models.Game, models.TeamGameStats.game_id == models.Game.id)
        .filter(models.TeamGameStats.team_id == team.id)
        .all()
    )
    played_seasons = {season_start_year(r.game.date) for r in stats_rows}
    played_seasons.discard(None)
    if ref_season in played_seasons:
        return ref_season
    if played_seasons:
        return max(played_seasons)
    return ref_season if ref_season in seasons else seasons[0]


def list_leagues(session, team: "models.Team") -> List[str]:
    """Competiciones con al menos un partido guardado (jugado o pendiente) de `team`.

    A diferencia de `list_seasons`, no depende de `config.LEAGUES` (concepto de la
    capa de scraping, usado solo para decidir qué páginas de BBR visitar): se
    deriva de `Game.league` ya persistido, que refleja la competición real de cada
    partido, no la liga fija del equipo de origen.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyo calendario se recorre.

    Returns:
        Valores distintos de `Game.league` (no vacíos), ordenados
        alfabéticamente. Vacía si el equipo no tiene ningún partido guardado.
    """
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    leagues = {g.league for g in games if g.league}
    return sorted(leagues)


def league_label(league: Optional[str]) -> str:
    """Formatea un código de competición para la UI ('acb' -> 'ACB'), o 'Todas' si
    `league` es `None` (sin filtro, comportamiento por defecto).

    Un código no listado en `_LEAGUE_LABELS` (ninguno visto hoy en los datos
    reales, pero robustez ante uno nuevo) se muestra con `.capitalize()` en vez
    de fallar.
    """
    if league is None:
        return "Todas"
    return _LEAGUE_LABELS.get(league, league.capitalize())


def parse_minutes(value: Optional[str]) -> Optional[float]:
    """Convierte 'MM:SS' (formato de BBR) a minutos decimales."""
    if not value:
        return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60
        return float(value)
    except (ValueError, TypeError):
        return None


def per_36(value: Optional[float], minutes: Optional[float]) -> Optional[float]:
    """Escala una estadística de conteo (PTS, REB...) a un ritmo de 36 minutos."""
    if value is None or not minutes:
        return None
    return value * 36 / minutes


def _mean(values: List[float]) -> Optional[float]:
    """Media aritmética de una lista de valores, o `None` si está vacía."""
    return sum(values) / len(values) if values else None


def _pstdev(values: List[float], mean_value: Optional[float]) -> Optional[float]:
    """Desviación típica poblacional de una lista, o `None` si está vacía.

    Se calcula a mano (sin `statistics`) por consistencia con el resto del
    fichero, que agrega siempre con `sum()/len()`.
    """
    if not values or mean_value is None:
        return None
    return (sum((v - mean_value) ** 2 for v in values) / len(values)) ** 0.5


def player_recent_form(
    session,
    team: "models.Team",
    last_n: int = 5,
    season: Optional[int] = None,
    league: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Medias recientes por jugador de un equipo (últimos `last_n` partidos jugados).

    Los partidos sin minutos registrados (jugador no jugó) no cuentan para la media.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyos jugadores se analizan.
        last_n: nº de partidos recientes por jugador (ya dentro del filtro de
            temporada/competición, si se pasa alguno).
        season: año de inicio de temporada (ver `season_start_year`); `None`
            agrega todo el histórico guardado (comportamiento por defecto).
        league: código de competición (`Game.league`); `None` no filtra por
            competición. Se combina con `season` por intersección (AND).

    Returns:
        Lista de dicts con player_name, games, avg_minutes, avg_pts,
        avg_pts_per36, avg_efg_pct, avg_ts_pct, avg_plus_minus, avg_turnovers,
        fg3a_rate (proporción de intentos que son de 3) y ft_rate (FTA/FGA, no
        % de acierto) — ordenada por avg_pts descendente.
    """
    query = (
        session.query(models.BoxScore)
        .join(models.Game, models.BoxScore.game_id == models.Game.id)
        .filter(models.BoxScore.team_id == team.id)
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    rows = query.order_by(models.Game.id.desc()).all()
    if season is not None:
        rows = [r for r in rows if season_start_year(r.game.date) == season]

    by_player: Dict[str, List[models.BoxScore]] = {}
    for row in rows:
        by_player.setdefault(row.player_name, []).append(row)

    results: List[Dict[str, object]] = []
    for player_name, player_rows in by_player.items():
        # player_rows ya viene ordenado de más reciente a más antiguo (por Game.id desc)
        candidates = player_rows[:last_n]
        played = [(row, parse_minutes(row.minutes)) for row in candidates]
        played = [(row, minutes) for row, minutes in played if minutes]
        if not played:
            continue

        avg_minutes = sum(minutes for _, minutes in played) / len(played)
        avg_pts = sum(row.points or 0 for row, _ in played) / len(played)
        per36_values = [per_36(row.points, minutes) for row, minutes in played]
        per36_values = [v for v in per36_values if v is not None]
        efg_values = [row.efg_pct for row, _ in played if row.efg_pct is not None]
        ts_values = [row.ts_pct for row, _ in played if row.ts_pct is not None]
        # plus_minus casi nunca tiene dato: los box scores internacionales de
        # BBR no incluyen columna "+/-" (verificado contra una página real;
        # solo NBA la tiene). Se deja calculado por si alguna fuente futura
        # la aporta, pero no depender de ella para nada visible en la GUI.
        plus_minus_values = [row.plus_minus for row, _ in played if row.plus_minus is not None]
        turnover_values = [row.turnovers for row, _ in played if row.turnovers is not None]

        # Perfil de tiro sobre la SUMA de intentos (criterio estándar de "shot
        # profile"): evita dividir por cero en un partido suelto sin tiros de campo.
        fg3a_total = sum(row.fg3_attempted or 0 for row, _ in played)
        fga_total = sum(row.fg_attempted or 0 for row, _ in played)
        fta_total = sum(row.ft_attempted or 0 for row, _ in played)

        results.append(
            {
                "player_name": player_name,
                "games": len(played),
                "avg_minutes": avg_minutes,
                "avg_pts": avg_pts,
                "avg_pts_per36": sum(per36_values) / len(per36_values) if per36_values else None,
                "avg_efg_pct": sum(efg_values) / len(efg_values) if efg_values else None,
                "avg_ts_pct": sum(ts_values) / len(ts_values) if ts_values else None,
                "avg_plus_minus": sum(plus_minus_values) / len(plus_minus_values) if plus_minus_values else None,
                "avg_turnovers": sum(turnover_values) / len(turnover_values) if turnover_values else None,
                "fg3a_rate": fg3a_total / fga_total if fga_total else None,
                "ft_rate": fta_total / fga_total if fga_total else None,
            }
        )

    results.sort(key=lambda r: r["avg_pts"], reverse=True)
    return results


def team_advanced_summary(
    session, team: "models.Team", season: Optional[int] = None, league: Optional[str] = None
) -> Dict[str, Optional[float]]:
    """Medias de las estadísticas avanzadas de un equipo sobre sus partidos guardados.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo a resumir.
        season: año de inicio de temporada (ver `season_start_year`); `None`
            agrega todo el histórico guardado (comportamiento por defecto).
        league: código de competición (`Game.league`); `None` no filtra.

    Returns:
        Dict con avg_pace, avg_off_rating, avg_def_rating, avg_net_rating
        (de `team_game_stats`) y avg_efg_pct, avg_ts_pct (de `boxscores`).
        Cada clave es `None` si no hay ninguna fila con ese dato dentro del
        filtro pedido.
    """
    stats_query = (
        session.query(models.TeamGameStats)
        .join(models.Game, models.TeamGameStats.game_id == models.Game.id)
        .filter(models.TeamGameStats.team_id == team.id)
    )
    box_query = (
        session.query(models.BoxScore)
        .join(models.Game, models.BoxScore.game_id == models.Game.id)
        .filter(models.BoxScore.team_id == team.id)
    )
    if league is not None:
        stats_query = stats_query.filter(models.Game.league == league)
        box_query = box_query.filter(models.Game.league == league)
    stats_rows = stats_query.all()
    box_rows = box_query.all()
    if season is not None:
        stats_rows = [r for r in stats_rows if season_start_year(r.game.date) == season]
        box_rows = [r for r in box_rows if season_start_year(r.game.date) == season]

    def _avg(values) -> Optional[float]:
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    return {
        "avg_pace": _avg(r.pace for r in stats_rows),
        "avg_off_rating": _avg(r.off_rating for r in stats_rows),
        "avg_def_rating": _avg(r.def_rating for r in stats_rows),
        "avg_net_rating": _avg(r.net_rating for r in stats_rows),
        "avg_efg_pct": _avg(r.efg_pct for r in box_rows),
        "avg_ts_pct": _avg(r.ts_pct for r in box_rows),
    }


def player_form_zscore(
    session,
    team: "models.Team",
    season: int,
    recent_n: int = 5,
    min_season_games: int = 6,
    league: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Detecta rachas dentro de una temporada: z-score de los últimos
    `recent_n` partidos jugados de cada jugador frente a la media/
    desviación de todos sus partidos con minutos registrados **de esa
    temporada** (no de todo el histórico) — dos métricas independientes:
    volumen anotador (PTS) y eficiencia de tiro (TS%).

    Sigue el mismo criterio de recencia que `player_recent_form` (orden por
    `Game.id` descendente entre los partidos de la temporada indicada).

    Un jugador se omite del todo si tiene menos de `min_season_games`
    partidos con minutos registrados en la temporada. El z-score de TS%
    (`z_score_ts`) se calcula de forma independiente al de PTS
    (`z_score_pts`) y puede ser `None` para un jugador aunque `z_score_pts`
    no lo sea (si tiene menos de `min_season_games` partidos con `ts_pct`
    no nulo, o su desviación de TS% en temporada es 0).

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyos jugadores se analizan.
        season: año de inicio de temporada (ver `season_start_year`); los
            partidos fuera de esta temporada no se consideran ni para la
            racha ni para la media/desviación base.
        recent_n: nº de partidos recientes de la "racha" (3-5 recomendado).
        min_season_games: mínimo de partidos para una desviación de
            temporada mínimamente estable (aplicado por separado a PTS y a
            TS%, ya que la cobertura de `ts_pct` puede ser algo menor que
            la de partidos con minutos).
        league: código de competición (`Game.league`); `None` no filtra por
            competición. `games_season` refleja el recuento dentro de
            temporada+competición.

    Returns:
        Lista de dicts con player_name, games_season, recent_avg_pts,
        season_avg_pts, season_std_pts, z_score_pts, recent_avg_ts_pct,
        season_avg_ts_pct, season_std_ts_pct, z_score_ts — ordenada por
        z_score_pts descendente (métrica principal de "racha"). Vacía si
        nadie cumple el mínimo de partidos en esa temporada (incluida una
        temporada sin ningún partido jugado todavía).
    """
    query = (
        session.query(models.BoxScore)
        .join(models.Game, models.BoxScore.game_id == models.Game.id)
        .filter(models.BoxScore.team_id == team.id)
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    rows = query.order_by(models.Game.id.desc()).all()
    rows = [r for r in rows if season_start_year(r.game.date) == season]

    by_player: Dict[str, List[models.BoxScore]] = {}
    for row in rows:
        by_player.setdefault(row.player_name, []).append(row)

    results: List[Dict[str, object]] = []
    for player_name, player_rows in by_player.items():
        # player_rows ya viene de más reciente a más antiguo (por Game.id desc)
        played = [row for row in player_rows if parse_minutes(row.minutes)]
        if len(played) < min_season_games:
            continue

        pts_values = [row.points or 0 for row in played]
        season_avg_pts = _mean(pts_values)
        season_std_pts = _pstdev(pts_values, season_avg_pts)
        recent_avg_pts = _mean(pts_values[:recent_n])
        z_score_pts = (recent_avg_pts - season_avg_pts) / season_std_pts if season_std_pts else None

        ts_values = [row.ts_pct for row in played if row.ts_pct is not None]
        if len(ts_values) >= min_season_games:
            season_avg_ts = _mean(ts_values)
            season_std_ts = _pstdev(ts_values, season_avg_ts)
            recent_avg_ts = _mean(ts_values[:recent_n])
            z_score_ts = (recent_avg_ts - season_avg_ts) / season_std_ts if season_std_ts else None
        else:
            season_avg_ts = season_std_ts = recent_avg_ts = z_score_ts = None

        results.append(
            {
                "player_name": player_name,
                "games_season": len(played),
                "recent_avg_pts": recent_avg_pts,
                "season_avg_pts": season_avg_pts,
                "season_std_pts": season_std_pts,
                "z_score_pts": z_score_pts,
                "recent_avg_ts_pct": recent_avg_ts,
                "season_avg_ts_pct": season_avg_ts,
                "season_std_ts_pct": season_std_ts,
                "z_score_ts": z_score_ts,
            }
        )

    # z_score_pts puede ser None (desviación 0 en la temporada): esos jugadores
    # van al final en vez de romper la ordenación.
    results.sort(
        key=lambda r: r["z_score_pts"] if r["z_score_pts"] is not None else float("-inf"),
        reverse=True,
    )
    return results


def schedule_difficulty(
    session,
    team: "models.Team",
    upcoming_games: List["models.Game"],
    season: int,
    next_n: int = 5,
    league: Optional[str] = None,
) -> Dict[str, object]:
    """Dificultad del próximo tramo de calendario: media del Net Rating de
    los próximos `next_n` rivales, calculado sobre la temporada `season` de
    cada rival (ver `team_advanced_summary`).

    Recibe `upcoming_games` ya resuelto por el llamador (ver
    `app.upcoming_games`, que NO se acota por temporada ni competición: es
    calendario pendiente) en vez de reimplementar el parseo/ordenación de
    fechas de BBR aquí (regla de capas: `insights.py` no depende de `app.py`).

    Si `league` no es `None`, se descartan del calendario pendiente los
    partidos de otras competiciones **antes** de tomar los próximos
    `next_n` — es decir, "los próximos 5 partidos de Euroliga", no "de los
    próximos 5 partidos (cualquier competición), cuántos son de Euroliga":
    es la lectura útil para preparar el tramo de una competición concreta,
    mientras que la alternativa mezclaría rivales de competiciones distintas
    en la misma cuenta de "próximos N".

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo de referencia (para resolver quién es "el rival" en
            cada partido de `upcoming_games`).
        upcoming_games: partidos pendientes de `team`, mismo orden que
            devuelve `app.upcoming_games`.
        season: temporada cuyo Net Rating de cada rival se usa como proxy
            de forma actual (normalmente la resuelta por `current_season`).
        next_n: nº de próximos rivales a considerar.
        league: competición a la que acotar el tramo; `None` = todas.

    Returns:
        Dict (nunca `None`) con games_considered, opponents_scouted,
        avg_opponent_net_rating (`None` si ningún rival tiene Net Rating en
        `season`), league (el recibido, para que el llamador pueda etiquetar
        la sección) y opponents (lista de {opponent_name, date, net_rating}).
        `games_considered` puede ser menor que `next_n` si `league` filtra
        tantos partidos que no quedan suficientes candidatos.
    """
    candidates = [g for g in upcoming_games if league is None or g.league == league]
    next_games = candidates[:next_n]

    opponents: List[Dict[str, object]] = []
    net_ratings: List[float] = []
    for game in next_games:
        opponent = game.away_team if game.home_team_id == team.id else game.home_team
        summary = team_advanced_summary(session, opponent, season=season, league=league)
        net_rating = summary["avg_net_rating"]
        if net_rating is not None:
            net_ratings.append(net_rating)
        opponents.append(
            {"opponent_name": opponent.name, "date": game.date, "net_rating": net_rating}
        )

    return {
        "games_considered": len(next_games),
        "opponents_scouted": len(net_ratings),
        "avg_opponent_net_rating": _mean(net_ratings),
        "league": league,
        "opponents": opponents,
    }


def project_next_matchup(
    session,
    team: "models.Team",
    opponent: "models.Team",
    season: int,
    league: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Proyecta el marcador esperado entre `team` y `opponent` combinando sus
    medias de la temporada `season` vía `stats.project_matchup`.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo de referencia.
        opponent: rival a proyectar.
        season: año de inicio de temporada (ver `season_start_year`).
        league: código de competición (`Game.league`); `None` no filtra.

    Returns:
        Igual que `stats.project_matchup`, o `None` si a alguno de los dos
        equipos le falta pace/ORtg/DRtg en esa temporada (incluye el caso
        de una temporada sin ningún partido jugado todavía).
    """
    team_summary = team_advanced_summary(session, team, season=season, league=league)
    opp_summary = team_advanced_summary(session, opponent, season=season, league=league)
    return project_matchup(
        team_summary["avg_pace"],
        team_summary["avg_off_rating"],
        team_summary["avg_def_rating"],
        opp_summary["avg_pace"],
        opp_summary["avg_off_rating"],
        opp_summary["avg_def_rating"],
    )


def scouting_narrative(
    session, team: "models.Team", season: int, recent_n: int = 5, league: Optional[str] = None
) -> Optional[str]:
    """Genera un resumen narrativo en español sobre el estilo de un equipo en
    una temporada concreta, combinando pace/ratings, perfil de tiro, forma de
    temporada (`player_recent_form`) y rachas (`player_form_zscore`).

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo a describir.
        season: año de inicio de temporada (ver `season_start_year`).
        recent_n: ventana de partidos usada para la frase de racha.
        league: código de competición (`Game.league`); `None` no filtra.

    Returns:
        Párrafo de 3-5 frases, o `None` si el equipo no tiene ningún
        partido con estadísticas avanzadas guardado **en esa temporada**
        (nada que resumir — incluye el caso de temporada sin jugar todavía).
    """
    summary = team_advanced_summary(session, team, season=season, league=league)
    pace = summary["avg_pace"]
    if pace is None:
        return None

    sentences: List[str] = []
    if pace >= _NARRATIVE_PACE_FAST:
        sentences.append(f"Juega a ritmo alto ({pace:.1f} posesiones estimadas por partido).")
    elif pace <= _NARRATIVE_PACE_SLOW:
        sentences.append(f"Juega a ritmo bajo ({pace:.1f} posesiones estimadas por partido).")
    else:
        sentences.append(
            f"Juega a ritmo intermedio ({pace:.1f} posesiones estimadas por partido)."
        )

    net_rating = summary["avg_net_rating"]
    if net_rating is not None:
        balance = "positivo" if net_rating > 0 else "negativo"
        sentences.append(f"Balance {balance}: {net_rating:+.1f} de Net Rating medio.")

    form = player_recent_form(session, team, last_n=1000, season=season, league=league)
    avg_fg3a_rate = _mean([r["fg3a_rate"] for r in form if r["fg3a_rate"] is not None])
    if avg_fg3a_rate is not None:
        pct = avg_fg3a_rate * 100
        if avg_fg3a_rate >= _NARRATIVE_FG3A_RATE_HIGH:
            sentences.append(f"Ataque muy volcado al triple: {pct:.0f}% de los intentos son de 3.")
        elif avg_fg3a_rate <= _NARRATIVE_FG3A_RATE_LOW:
            sentences.append(
                f"Ataque poco dependiente del triple: solo {pct:.0f}% de los intentos son de 3."
            )
        else:
            sentences.append(f"Reparto de tiro equilibrado: {pct:.0f}% de los intentos son de 3.")

    if form:
        top_scorer = form[0]  # `player_recent_form` ya ordena por avg_pts descendente
        sentences.append(
            f"Máximo anotador: {top_scorer['player_name']} "
            f"({top_scorer['avg_pts']:.1f} puntos por partido)."
        )

    # Se usa z_score_pts (volumen) como señal principal de racha: la tabla
    # detallada ya muestra ambas métricas, la narrativa resume con la más
    # directa de interpretar.
    streaks = player_form_zscore(session, team, season=season, recent_n=recent_n, league=league)
    hot = [
        s["player_name"]
        for s in streaks
        if s["z_score_pts"] is not None and s["z_score_pts"] >= ZSCORE_HOT_THRESHOLD
    ]
    cold = [
        s["player_name"]
        for s in streaks
        if s["z_score_pts"] is not None and s["z_score_pts"] <= ZSCORE_COLD_THRESHOLD
    ]
    if hot:
        sentences.append(f"En racha anotadora (últimos {recent_n} partidos): {', '.join(hot)}.")
    if cold:
        sentences.append(f"Bajo forma anotadora (últimos {recent_n} partidos): {', '.join(cold)}.")

    return " ".join(sentences)


def player_load(
    session, team: "models.Team", games: List["models.Game"]
) -> List[Dict[str, object]]:
    """Minutos acumulados por jugador en una lista concreta de partidos.

    Agregación pura sobre los partidos que le pasa el llamador (ver
    `app.games_in_window`, que resuelve la ventana de días): esta función no
    parsea fechas ni conoce el concepto de temporada/competición — la carga
    física de un jugador es transversal a ambos.

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyos jugadores se agregan.
        games: partidos ya jugados a considerar.

    Returns:
        Lista de dicts con player_name, games, total_minutes, avg_minutes —
        ordenada por total_minutes descendente. Vacía si no hay partidos o
        si ninguno tiene box score con minutos registrados.
    """
    if not games:
        return []

    rows = (
        session.query(models.BoxScore)
        .filter(models.BoxScore.team_id == team.id)
        .filter(models.BoxScore.game_id.in_([g.id for g in games]))
        .all()
    )

    by_player: Dict[str, List[float]] = {}
    for row in rows:
        minutes = parse_minutes(row.minutes)
        if not minutes:
            continue  # no jugó ese partido: no suma carga
        by_player.setdefault(row.player_name, []).append(minutes)

    results: List[Dict[str, object]] = [
        {
            "player_name": player_name,
            "games": len(minutes_list),
            "total_minutes": sum(minutes_list),
            "avg_minutes": sum(minutes_list) / len(minutes_list),
        }
        for player_name, minutes_list in by_player.items()
    ]
    results.sort(key=lambda r: r["total_minutes"], reverse=True)
    return results


def validate_data(session) -> List[str]:
    """Revisa incoherencias básicas en partidos/box scores ya guardados.

    Comprueba que la suma de puntos del box score cuadra (con margen) con el
    resultado guardado del partido, y avisa de jugadores sin minutos registrados.

    Returns:
        Lista de mensajes de aviso (vacía si no se detecta nada raro).
    """
    warnings: List[str] = []
    for game in session.query(models.Game).all():
        for side, team_id, score in (
            ("local", game.home_team_id, game.home_score),
            ("visitante", game.away_team_id, game.away_score),
        ):
            rows = session.query(models.BoxScore).filter_by(game_id=game.id, team_id=team_id).all()
            if not rows:
                continue  # sin box score todavía, nada que validar

            summed = sum(row.points or 0 for row in rows)
            if score is not None and abs(summed - score) > 2:  # margen por datos ausentes/redondeo
                warnings.append(
                    f"Partido {game.id} ({game.date}): puntos del box score {side} ({summed}) "
                    f"no cuadran con el resultado guardado ({score})"
                )

            missing_minutes = [row.player_name for row in rows if not row.minutes]
            if missing_minutes:
                warnings.append(
                    f"Partido {game.id} ({game.date}): jugadores sin minutos registrados en el "
                    f"box score {side}: {', '.join(missing_minutes)}"
                )

    return warnings

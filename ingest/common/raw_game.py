"""Transforma un "raw game dict" (contrato común, ver más abajo) al esquema.

Contrato interno que debe producir el adapter de cada fuente (`ingest.acb`,
`ingest.euroleague`) antes de llamar a `parse_and_resolve`; es el mismo para
las dos, así la resolución de identidad/carga se escribe una sola vez:

```
{
  "game_id": "...", "date": "YYYY-MM-DD", "season": 2025, "competition": "ACB",
  "home_team": {"id": "...", "name": "..."}, "away_team": {"id": "...", "name": "..."},
                 # + "acb_club_id" opcional en cada equipo (solo ACB: `clubId`
                 # estable del club, ver `resolve_or_create_team`)
  "home_score": 88, "away_score": 82, "pace": 71.2, "narrative": None,
  "team_stats": [{"team_id":.., "efg_pct":.., "ts_pct":.., "tov_pct":.., "orb_pct":..,
                  "ortg":.., "drtg":.., "ast_pct":.., "stl_pct":.., "blk_pct":..,
                  "ft_rate":.., "ast_to_ratio":.., "ftm":.., "fta":..,
                  "stl":.., "tov":.., "blk":.., "blk_against":.., "pf":..,
                  "pf_drawn":.., "oreb":.., "dreb":.., "plus_minus":.., "pir":..}, ...],
                 # todo lo que va detrás de orb_pct es opcional: los 5 primeros
                 # fieles a 04_team_stats.R de OpenACB (S_assist/S_steal/
                 # S_blocks/FT_rate/ast_to_ratio), "ftm"/"fta" el recuento
                 # bruto de tiros libres del equipo, y el resto (Fase 1) el
                 # boxscore ampliado de equipo — todos opcionales, "no dado"
                 # queda en NULL.
  "players": [{"player_id":.., "team_id":.., "name":.., "number":.., "position":..,
               "minutes":.., "pts":.., "reb":.., "ast":.., "efg_pct":..,
               "ftm":.., "fta":.., "photo_url":..,
               "stl":.., "tov":.., "blk":.., "blk_against":.., "pf":..,
               "pf_drawn":.., "oreb":.., "dreb":.., "plus_minus":.., "pir":.., "dunks":..}, ...],
           # "ftm"/"fta" opcionales (tiros libres convertidos/intentados): una
           # fuente que no los dé deja las columnas en NULL, no en 0. El
           # bloque de Fase 1 (stl.."dunks") es igual de opcional.
           # "photo_url" opcional (hotlink, nunca se descarga a disco desde
           # aquí): RELLENA `players.photo_url` solo si está vacío, nunca lo
           # pisa — la plantilla propia la fija `ingest/baskonia_web`, que es
           # la fuente autorizada y siempre gana; esto es solo para que un
           # RIVAL (sin scraper propio) deje de depender del badge de
           # iniciales. Ver `ingest/acb/adapter.py` (`headshotImageUrl` del
           # boxscore oficial) para la fuente real que lo puebla hoy.
  "lineups": [{"team_id":.., "player_ids":[...], "minutes":.., "plus_minus":..}, ...],
           # opcional: si no se da (lista vacía) pero sí hay "play_by_play" +
           # "starters", los quintetos se reconstruyen jugada a jugada (ver
           # `ingest.common.lineups.reconstruct_lineups`) y, con ellos, los
           # TRAMOS con reloj y marcador (`lineup_stints`). Una fuente que
           # entrega los quintetos ya agregados aquí no produce tramos: el
           # detalle por tiempo solo existe si hay play-by-play.
  "shots": [{"player_id":.., "team_id":.., "x":.., "y":.., "made": bool}, ...],
           # x/y ya en la escala 0-500 de court_zones; la conversión desde el
           # sistema de coordenadas nativo de cada fuente es responsabilidad
           # del adapter de esa fuente (ver `ingest/acb/parser.py`).
  "events": [{"team_id":.., "quarter":.., "clock":.., "label":..}, ...],
  "score_progression": [{"step":.., "home":.., "away":..}, ...],
  "quarter_stats": [{"team_id":.., "quarter": 1..4, "points_for":.., "points_against":..}, ...],
           # opcional, fiel a la parte "quarters" de 09_team_pace.R.
           # "fouls_for"/"fouls_against" NO se dan aquí: los deriva el loader
           # de "play_events" (Fase 2).
  "starters": {"home": [player_id, ...5], "away": [player_id, ...5]},  # opcional
  "play_by_play": [  # opcional, ids externos (se resuelven con player_lookup)
      {"team_id":.., "type": "sub_in"|"sub_out"|"score", "quarter":.., "clock":..,
       "player_id":.. (sub_in/sub_out), "points":.. (score)},
      ...
  ],
  "play_events": [  # opcional (Fase 2): eventos tipados con reloj exacto
      {"team_id":.., "player_id":.. (ids externos, o None), "quarter":.., "clock":..,
       "event_type": "steal"|"turnover"|"block"|"oreb"|"dreb"|
       "assist"|"foul_drawn"|"foul_personal", "event_detail":.. (opcional),
       "home_score":.., "away_score":..},
      ...
  ],  # "seconds" NO se da aquí: lo calcula parse_and_resolve con
      # game_clock_to_seconds(quarter, clock), igual que ya hace para
      # reconstruir quintetos — un solo sitio con esa conversión.
  "arena": .., "attendance": .., "referees": .., "home_coach": .., "away_coach": ..,
           # opcionales (Fase 3): metadata de partido, ya viaja en las
           # respuestas que se descargan hoy en las dos fuentes.
  "quarter_boxscore": [  # opcional (Fase 3, ACB-only): boxscore de jugador por cuarto
      {"player_id":.., "quarter": 1..4, "minutes":.., "pts":.., "reb":.., "ast":..,
       "stl":.., "tov":.., "blk":.., "pf":.., "oreb":.., "dreb":.., "ftm":.., "fta":..,
       "plus_minus":.., "pir":..},
      ...
  ],
  "player_advanced": [  # opcional (Fase 4, ACB-only): avanzadas oficiales por jugador
      {"player_id":.., "ast_ratio":.., "ast_pct":.., "stl_ratio":.., "stl_pct":..,
       "blk_pct":.., "tov_pct":.., "orb_pct":.., "drb_pct":.., "trb_pct":..,
       "ts_pct":.., "three_par":.., "ppt":.., "pp2ps":.., "pp3ps":.., "ppft":..,
       "possessions":.., "pace":..},
      ...
  ],
}
```
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ingest.common.game_clock import game_clock_to_seconds
from ingest.common.identity import (
    get_competition_id,
    get_or_create_season,
    resolve_or_create_player,
    resolve_or_create_team,
)
from ingest.common.lineups import PlayByPlayEvent, reconstruct_lineups
from ingest.common.schema_types import (
    GameAdvancedStat,
    KeyEvent,
    LineupRecord,
    NormalizedGame,
    PlayerAdvancedStat,
    PlayerGameStat,
    PlayerQuarterStat,
    PlayEvent,
    QuarterStat,
    ScoreStep,
    ShotRecord,
    StintRecord,
)
from ingest.common.zones import MATE_ZONE_ID, classify_zone

logger = logging.getLogger(__name__)


def parse_and_resolve(conn: Connection, raw: Dict[str, Any], source: str) -> NormalizedGame:
    """Resuelve identidades (equipos/jugadores/temporada) y arma el `NormalizedGame`."""
    season_id = get_or_create_season(conn, raw["season"])
    competition_id = get_competition_id(conn, raw["competition"])

    # `acb_club_id`: identificador estable del club que solo trae ACB (ver
    # `resolve_or_create_team`); las demás fuentes no lo ponen y queda `None`.
    home_team_id = resolve_or_create_team(
        conn, source, raw["home_team"]["id"], raw["home_team"]["name"],
        acb_club_id=raw["home_team"].get("acb_club_id"),
    )
    away_team_id = resolve_or_create_team(
        conn, source, raw["away_team"]["id"], raw["away_team"]["name"],
        acb_club_id=raw["away_team"].get("acb_club_id"),
    )

    team_lookup: Dict[str, str] = {
        raw["home_team"]["id"]: home_team_id,
        raw["away_team"]["id"]: away_team_id,
    }

    player_lookup: Dict[str, str] = {}
    # Detecta colisiones de identidad DENTRO de este partido: dos ids externos
    # distintos resolviendo al mismo `player_id` interno (visto en vivo,
    # 2026-08-27, reingesta de producción — `resolve_or_create_player` los
    # fusiona vía su fallback "mismo team_id + mismo dorsal", que no comprueba
    # que sea la misma persona; ver su docstring). No se corrige aquí — sería
    # inventar una heurística de desambiguación sin decisión de producto —
    # pero SIN esto la colisión era invisible: `player_game_stats` la absorbía
    # en silencio vía `ON CONFLICT DO UPDATE` (las estadísticas de un jugador
    # pisaban las del otro) y las tablas nuevas de Fase 3/4 (`PRIMARY KEY`
    # estricta, sin upsert) directamente reventaban la carga del partido
    # entero. Se registra en el log — no se calla — y las listas de Fase 3/4
    # se deduplican más abajo para no tumbar el resto del partido por esto.
    _claimed_by: Dict[str, str] = {}
    boxscore = []
    for player in raw.get("players", []):
        team_id = team_lookup[player["team_id"]]
        player_id = resolve_or_create_player(
            conn, source, player["player_id"], player["name"], team_id,
            number=player.get("number"), position=player.get("position"),
        )
        if player_id in _claimed_by and _claimed_by[player_id] != player["player_id"]:
            logger.warning(
                "%s: colisión de identidad en el partido %s — los ids externos %r y %r "
                "resuelven ambos a player_id=%r (nombre actual en boxscore: %r); sus "
                "estadísticas se están mezclando bajo un mismo jugador.",
                source, raw.get("game_id"), _claimed_by[player_id], player["player_id"],
                player_id, player["name"],
            )
        _claimed_by[player_id] = player["player_id"]
        player_lookup[player["player_id"]] = player_id
        if player.get("photo_url"):
            # Solo RELLENA el hueco (`COALESCE(photo_url, ...)`, no al revés):
            # un boxscore de partido no es la fuente autorizada de la
            # plantilla propia, así que nunca pisa lo que ya haya puesto
            # `ingest/baskonia_web` — ver nota del contrato arriba.
            conn.execute(
                text("UPDATE players SET photo_url = COALESCE(photo_url, :photo_url) WHERE id = :id"),
                {"photo_url": player["photo_url"], "id": player_id},
            )
        boxscore.append(
            PlayerGameStat(
                player_id=player_id, minutes=player["minutes"], pts=player["pts"],
                reb=player["reb"], ast=player["ast"], efg_pct=player["efg_pct"],
                ftm=player.get("ftm"), fta=player.get("fta"),
                stl=player.get("stl"), tov=player.get("tov"), blk=player.get("blk"),
                blk_against=player.get("blk_against"), pf=player.get("pf"),
                pf_drawn=player.get("pf_drawn"), oreb=player.get("oreb"), dreb=player.get("dreb"),
                plus_minus=player.get("plus_minus"), pir=player.get("pir"), dunks=player.get("dunks"),
            )
        )

    advanced = [
        GameAdvancedStat(
            team_id=team_lookup[row["team_id"]], efg_pct=row["efg_pct"], ts_pct=row["ts_pct"],
            tov_pct=row["tov_pct"], orb_pct=row["orb_pct"], ortg=row.get("ortg"), drtg=row.get("drtg"),
            net_rating=(row["ortg"] - row["drtg"]) if row.get("ortg") is not None and row.get("drtg") is not None else None,
            ast_pct=row.get("ast_pct"), stl_pct=row.get("stl_pct"), blk_pct=row.get("blk_pct"),
            ft_rate=row.get("ft_rate"), ast_to_ratio=row.get("ast_to_ratio"),
            ftm=row.get("ftm"), fta=row.get("fta"),
            stl=row.get("stl"), tov=row.get("tov"), blk=row.get("blk"), blk_against=row.get("blk_against"),
            pf=row.get("pf"), pf_drawn=row.get("pf_drawn"), oreb=row.get("oreb"), dreb=row.get("dreb"),
            plus_minus=row.get("plus_minus"), pir=row.get("pir"),
        )
        for row in raw.get("team_stats", [])
    ]

    quarter_stats = [
        QuarterStat(
            team_id=team_lookup[row["team_id"]], quarter=row["quarter"],
            points_for=row["points_for"], points_against=row["points_against"],
        )
        for row in raw.get("quarter_stats", [])
    ]

    lineups = [
        LineupRecord(
            player_ids=[player_lookup[pid] for pid in row["player_ids"]],
            minutes=row["minutes"], plus_minus=row["plus_minus"],
            # Una fuente que entrega quintetos ya agregados suele decir de qué
            # equipo son; si no lo dice, queda en None y se infiere aguas
            # abajo (ver `LineupRecord.team_id`).
            team_id=team_lookup.get(row.get("team_id")),
        )
        for row in raw.get("lineups", [])
    ]
    stints: List[StintRecord] = []
    if not lineups and raw.get("play_by_play"):
        starters = raw.get("starters") or _derive_starters_from_players(raw)
        if starters is not None:
            lineups, stints = _reconstruct_lineups_from_pbp(
                raw, starters, team_lookup, player_lookup, home_team_id, away_team_id
            )

    # Los tiros de un jugador ausente del boxscore (inconsistencia real entre
    # endpoints de una misma fuente) se descartan en vez de fallar toda la carga.
    #
    # `located=False` (hoy solo mates de ACB, sin coordenadas medidas — ver
    # `shots.located` en `schema.sql`) se asigna a `MATE_ZONE_ID` DIRECTAMENTE,
    # sin pasar por `classify_zone`: su centinela reescalado cae exactamente
    # dentro de 'Pintura', y sin `ORDER BY` en `classify_zone` qué zona "gana"
    # el solape no está garantizado — ver el comentario de `court_zones` en
    # `schema.sql`.
    shots = [
        ShotRecord(
            player_id=player_lookup[row["player_id"]], team_id=team_lookup[row["team_id"]],
            pos_x=row["x"], pos_y=row["y"], made=row["made"],
            zone_id=MATE_ZONE_ID if not row.get("located", True) else classify_zone(conn, row["x"], row["y"]),
            located=row.get("located", True),
        )
        for row in raw.get("shots", [])
        if row["player_id"] in player_lookup
    ]

    key_events = [
        KeyEvent(team_id=team_lookup[row["team_id"]], quarter=row["quarter"], game_clock=row["clock"], label=row["label"])
        for row in raw.get("events", [])
    ]

    score_progression = [
        ScoreStep(step_index=row["step"], home_score=row["home"], away_score=row["away"])
        for row in raw.get("score_progression", [])
    ]

    # Play-by-play tipado (Fase 2): eventos sin jugador resuelto (p.ej. una
    # falta de equipo) se cargan igual con `player_id=None` — la columna es
    # nullable a propósito (ver `play_events.player_id` en `schema.sql`);
    # solo se descarta un evento si el JUGADOR que sí trae no está en el
    # boxscore (misma inconsistencia real entre endpoints que ya tolera
    # `shots` más arriba).
    play_events = []
    for row in raw.get("play_events", []):
        external_player = row.get("player_id")
        if external_player is not None and external_player not in player_lookup:
            continue
        play_events.append(
            PlayEvent(
                team_id=team_lookup[row["team_id"]],
                player_id=player_lookup.get(external_player) if external_player is not None else None,
                quarter=row["quarter"], game_clock=row["clock"],
                seconds=game_clock_to_seconds(row["quarter"], row["clock"]),
                event_type=row["event_type"], event_detail=row.get("event_detail"),
                home_score=row["home_score"], away_score=row["away_score"],
            )
        )

    quarter_boxscore = _dedupe_after_identity_collision(
        [
            PlayerQuarterStat(
                player_id=player_lookup[row["player_id"]], quarter=row["quarter"],
                minutes=row.get("minutes"), pts=row.get("pts"), reb=row.get("reb"), ast=row.get("ast"),
                stl=row.get("stl"), tov=row.get("tov"), blk=row.get("blk"), pf=row.get("pf"),
                oreb=row.get("oreb"), dreb=row.get("dreb"), ftm=row.get("ftm"), fta=row.get("fta"),
                plus_minus=row.get("plus_minus"), pir=row.get("pir"),
            )
            for row in raw.get("quarter_boxscore", [])
            if row["player_id"] in player_lookup
        ],
        key=lambda r: (r.player_id, r.quarter),
        table="player_game_quarter_stats", game_id=raw.get("game_id"),
    )

    player_advanced = _dedupe_after_identity_collision(
        [
            PlayerAdvancedStat(
                player_id=player_lookup[row["player_id"]],
                ast_ratio=row.get("ast_ratio"), ast_pct=row.get("ast_pct"),
                stl_ratio=row.get("stl_ratio"), stl_pct=row.get("stl_pct"), blk_pct=row.get("blk_pct"),
                tov_pct=row.get("tov_pct"), orb_pct=row.get("orb_pct"), drb_pct=row.get("drb_pct"),
                trb_pct=row.get("trb_pct"), ts_pct=row.get("ts_pct"), three_par=row.get("three_par"),
                ppt=row.get("ppt"), pp2ps=row.get("pp2ps"), pp3ps=row.get("pp3ps"), ppft=row.get("ppft"),
                possessions=row.get("possessions"), pace=row.get("pace"),
            )
            for row in raw.get("player_advanced", [])
            if row["player_id"] in player_lookup
        ],
        key=lambda r: r.player_id,
        table="player_advanced_stats", game_id=raw.get("game_id"),
    )

    return NormalizedGame(
        id=f"{source}-{raw['game_id']}",
        season_id=season_id,
        competition_id=competition_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        game_date=raw["date"],
        home_score=raw["home_score"],
        away_score=raw["away_score"],
        pace=raw.get("pace", 0.0),
        narrative=raw.get("narrative"),
        advanced=advanced,
        boxscore=boxscore,
        lineups=lineups,
        stints=stints,
        shots=shots,
        key_events=key_events,
        score_progression=score_progression,
        quarter_stats=quarter_stats,
        arena=raw.get("arena"), attendance=raw.get("attendance"), referees=raw.get("referees"),
        home_coach=raw.get("home_coach"), away_coach=raw.get("away_coach"),
        play_events=play_events, quarter_boxscore=quarter_boxscore, player_advanced=player_advanced,
    )


def _reconstruct_lineups_from_pbp(raw, starters, team_lookup, player_lookup, home_team_id, away_team_id):
    """Convierte `raw["play_by_play"]`/`starters` (ids externos) y reconstruye quintetos.

    Jugadas de sustitución que referencian a un jugador ausente del boxscore
    (inconsistencias reales entre endpoints de una misma fuente) se
    descartan en vez de fallar: es preferible un quinteto con algún hueco a
    tumbar la carga de todo el partido.

    Returns:
        `(lineups, stints)` — los quintetos agregados de los DOS equipos y sus
        tramos con reloj y marcador (ver `ingest/common/lineups.py`).
    """
    events = []
    for row in raw["play_by_play"]:
        player_id = player_lookup.get(row["player_id"]) if row.get("player_id") else None
        if row["type"] in ("sub_in", "sub_out") and player_id is None:
            continue
        events.append(
            PlayByPlayEvent(
                team_id=team_lookup[row["team_id"]],
                type=row["type"],
                seconds=game_clock_to_seconds(row["quarter"], row["clock"]),
                player_id=player_id,
                points=row.get("points"),
            )
        )
    home_starters = [player_lookup[pid] for pid in starters["home"] if pid in player_lookup]
    away_starters = [player_lookup[pid] for pid in starters["away"] if pid in player_lookup]

    reconstruction = reconstruct_lineups(home_team_id, away_team_id, home_starters, away_starters, events)
    by_team = reconstruction.by_team
    return by_team[home_team_id] + by_team[away_team_id], reconstruction.stints


def _dedupe_after_identity_collision(rows: List, *, key, table: str, game_id) -> List:
    """Quita filas con clave duplicada tras resolver identidad, quedándose con la primera.

    Existe por la colisión de identidad documentada donde se construye
    `player_lookup` (dos ids externos que resuelven al mismo `player_id`):
    tablas con clave natural real (`player_game_stats`) absorben esa colisión
    en silencio vía `ON CONFLICT DO UPDATE`, pero las de Fase 3/4
    (`player_game_quarter_stats`, `player_advanced_stats`) tienen `PRIMARY
    KEY` estricta sin upsert — sin este filtro, una colisión ajena a ellas
    tumbaría la carga del partido ENTERO (boxscore, tiros, quintetos...) por
    una tabla que ni siquiera es la fuente de verdad de esas estadísticas.
    Se registra en el log para que la colisión siga siendo visible.
    """
    seen = set()
    deduped = []
    for row in rows:
        row_key = key(row)
        if row_key in seen:
            logger.warning(
                "%s: fila descartada por clave duplicada tras colisión de identidad "
                "(partido %s, clave %r) — %r",
                table, game_id, row_key, row,
            )
            continue
        seen.add(row_key)
        deduped.append(row)
    return deduped


def _derive_starters_from_players(raw) -> Optional[Dict[str, list]]:
    """Deriva el quinteto titular del flag `"starter"` del boxscore, si viene.

    Fallback para cuando la fuente no da un bloque `"starters"` explícito pero
    sí marca qué jugadores empezaron el partido (habitual en boxscores: `GS`).
    """
    home_id, away_id = raw["home_team"]["id"], raw["away_team"]["id"]
    starters: Dict[str, list] = {"home": [], "away": []}
    for player in raw.get("players", []):
        if not player.get("starter"):
            continue
        side = "home" if player["team_id"] == home_id else "away"
        starters[side].append(player["player_id"])
    if len(starters["home"]) != 5 or len(starters["away"]) != 5:
        return None
    return starters

"""Transforma un "raw game dict" (contrato común, ver más abajo) al esquema.

Contrato interno que debe producir el adapter de cada fuente (`ingest.acb`,
`ingest.euroleague`) antes de llamar a `parse_and_resolve`; es el mismo para
las dos, así la resolución de identidad/carga se escribe una sola vez:

```
{
  "game_id": "...", "date": "YYYY-MM-DD", "season": 2025, "competition": "ACB",
  "home_team": {"id": "...", "name": "..."}, "away_team": {"id": "...", "name": "..."},
  "home_score": 88, "away_score": 82, "pace": 71.2, "narrative": None,
  "team_stats": [{"team_id":.., "efg_pct":.., "ts_pct":.., "tov_pct":.., "orb_pct":..,
                  "ortg":.., "drtg":.., "ast_pct":.., "stl_pct":.., "blk_pct":..,
                  "ft_rate":.., "ast_to_ratio":.., "ftm":.., "fta":..}, ...],
                 # todo lo que va detrás de orb_pct es opcional: los 5 primeros
                 # fieles a 04_team_stats.R de OpenACB (S_assist/S_steal/
                 # S_blocks/FT_rate/ast_to_ratio) y "ftm"/"fta" el recuento
                 # bruto de tiros libres del equipo (los del rival son la
                 # entrada del OTRO equipo de este mismo partido).
  "players": [{"player_id":.., "team_id":.., "name":.., "number":.., "position":..,
               "minutes":.., "pts":.., "reb":.., "ast":.., "efg_pct":..,
               "ftm":.., "fta":..}, ...],
           # "ftm"/"fta" opcionales (tiros libres convertidos/intentados): una
           # fuente que no los dé deja las columnas en NULL, no en 0.
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
  "starters": {"home": [player_id, ...5], "away": [player_id, ...5]},  # opcional
  "play_by_play": [  # opcional, ids externos (se resuelven con player_lookup)
      {"team_id":.., "type": "sub_in"|"sub_out"|"score", "quarter":.., "clock":..,
       "player_id":.. (sub_in/sub_out), "points":.. (score)},
      ...
  ],
}
```
"""
from typing import Any, Dict, List, Optional

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
    PlayerGameStat,
    QuarterStat,
    ScoreStep,
    ShotRecord,
    StintRecord,
)
from ingest.common.zones import classify_zone


def parse_and_resolve(conn: Connection, raw: Dict[str, Any], source: str) -> NormalizedGame:
    """Resuelve identidades (equipos/jugadores/temporada) y arma el `NormalizedGame`."""
    season_id = get_or_create_season(conn, raw["season"])
    competition_id = get_competition_id(conn, raw["competition"])

    home_team_id = resolve_or_create_team(conn, source, raw["home_team"]["id"], raw["home_team"]["name"])
    away_team_id = resolve_or_create_team(conn, source, raw["away_team"]["id"], raw["away_team"]["name"])

    team_lookup: Dict[str, str] = {
        raw["home_team"]["id"]: home_team_id,
        raw["away_team"]["id"]: away_team_id,
    }

    player_lookup: Dict[str, str] = {}
    boxscore = []
    for player in raw.get("players", []):
        team_id = team_lookup[player["team_id"]]
        player_id = resolve_or_create_player(
            conn, source, player["player_id"], player["name"], team_id,
            number=player.get("number"), position=player.get("position"),
        )
        player_lookup[player["player_id"]] = player_id
        boxscore.append(
            PlayerGameStat(
                player_id=player_id, minutes=player["minutes"], pts=player["pts"],
                reb=player["reb"], ast=player["ast"], efg_pct=player["efg_pct"],
                ftm=player.get("ftm"), fta=player.get("fta"),
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
    shots = [
        ShotRecord(
            player_id=player_lookup[row["player_id"]], team_id=team_lookup[row["team_id"]],
            pos_x=row["x"], pos_y=row["y"], made=row["made"],
            zone_id=classify_zone(conn, row["x"], row["y"]),
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

"""Adapta los DataFrames de `euroleague_api` al contrato común (`ingest.common.raw_game`).

Columnas **verificadas en vivo** (2026-08-20, `euroleague-api` instalado,
temporada 2025 / gamecode 7 - Virtus Bologna vs Real Madrid):

- metadata (`get_game_metadata`): `Season`, `Gamecode`, `Date` (`"DD/MM/YYYY"`),
  `TeamA`/`TeamB` (nombre completo, MAYÚSCULAS), `CodeTeamA`/`CodeTeamB`
  (código corto, p.ej. `"MAD"` - es el id estable que usan boxscore/shots/pbp
  para referirse a un equipo, no el nombre), `ScoreA`/`ScoreB`, `Competition`
  (p.ej. `"EUROLEAGUE 2025-26"`). A = local, B = visitante.
- boxscore (`get_players_boxscore_stats`): `Player_ID`, `Player`
  (`"APELLIDO, Nombre"`), `Team` (código corto, como `CodeTeamA/B`), `Dorsal`,
  `IsStarter` (`0.0`/`1.0`), `Minutes` (`"MM:SS"`, no decimal), `Points`,
  `FieldGoalsMade2/Attempted2/Made3/Attempted3`, `FreeThrowsMade/Attempted`,
  `OffensiveRebounds`, `DefensiveRebounds`, `TotalRebounds`, `Assistances`,
  `Turnovers`.
- shot data (`get_game_shot_data`): `ID_PLAYER` (¡no `PLAYER_ID`!), `TEAM`
  (código corto), `COORD_X`/`COORD_Y` (rango observado real: X en
  [-696, 677], Y en [-94, 777]; `(-1, -1)` es el centinela de "sin
  ubicación", usado en tiros libres - se descartan), `ID_ACTION` (`"2FGM"`,
  `"3FGM"`, `"FTM"` anotados; `"2FGA"`, `"3FGA"` fallados).
- play-by-play (`get_game_play_by_play_data`): `CODETEAM` (código corto -
  usarlo, no `TEAM` que trae el nombre completo), `PLAYER_ID`, `PLAYTYPE`
  (`"IN"`/`"OUT"` sustituciones; `"2FGM"`/`"3FGM"`/`"FTM"` canastas
  anotadas), `PERIOD` (1-4 cuartos, 5+ prórroga), `MARKERTIME`
  (`"MM:SS"` restantes en el periodo).

`game_advanced_stats` se deriva agregando el boxscore por equipo (eFG%/TS%/
TOV%/ORB% con las fórmulas estándar); `ortg`/`drtg` quedan `None` (no
derivables sin posesiones estimadas). `lineups` se reconstruye a partir del
play-by-play (`ingest.common.lineups.reconstruct_lineups`, invocado desde
`ingest.common.raw_game.parse_and_resolve`) usando `IsStarter` como quinteto
titular; si no se pasa play-by-play, o ningún jugador viene marcado
`IsStarter`, queda vacío (datos insuficientes) en vez de inventarse algo.

Si la versión instalada de `euroleague_api` difiere, este es el único
fichero que hay que tocar — el contrato de salida (`ingest.common.raw_game`)
no cambia.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

COMPETITION_NAME = "Euroliga"

# Cancha: rango observado real de COORD_X/COORD_Y (ver docstring del módulo),
# reescalado al rango 0-500 que usa `court_zones`.
_COORD_X_RANGE = (-750.0, 750.0)
_COORD_Y_RANGE = (-100.0, 800.0)
_NO_LOCATION_SENTINEL = (-1, -1)


def _rescale_shot_coords(coord_x: float, coord_y: float) -> tuple:
    x_min, x_max = _COORD_X_RANGE
    y_min, y_max = _COORD_Y_RANGE
    x = (coord_x - x_min) / (x_max - x_min) * 500
    y = (coord_y - y_min) / (y_max - y_min) * 500
    return x, y


def _clean_id(value: Any) -> str:
    """`str(value).strip()` - el boxscore real trae `Player_ID`/`Team` con espacios de relleno."""
    return str(value).strip()


def _parse_minutes(value: Any) -> float:
    """Convierte `"MM:SS"` a minutos decimales; `0` si viene vacío/DNP."""
    text = str(value or "").strip()
    if not text or ":" not in text:
        return 0.0
    minutes, _, seconds = text.partition(":")
    try:
        return int(minutes) + int(seconds) / 60.0
    except ValueError:
        return 0.0


def _parse_date(value: Any) -> str:
    """Convierte `"DD/MM/YYYY"` a ISO `"YYYY-MM-DD"`."""
    return datetime.strptime(str(value), "%d/%m/%Y").strftime("%Y-%m-%d")


def _team_totals(boxscore_records: list, team_code: str) -> Dict[str, float]:
    rows = [
        r for r in boxscore_records
        if _clean_id(r["Team"]) == team_code and _clean_id(r["Player_ID"]) not in _AGGREGATE_ROW_IDS
    ]
    return {
        "pts": sum(r.get("Points", 0) or 0 for r in rows),
        "fgm": sum((r.get("FieldGoalsMade2", 0) or 0) + (r.get("FieldGoalsMade3", 0) or 0) for r in rows),
        "fgm3": sum(r.get("FieldGoalsMade3", 0) or 0 for r in rows),
        "fga": sum((r.get("FieldGoalsAttempted2", 0) or 0) + (r.get("FieldGoalsAttempted3", 0) or 0) for r in rows),
        "ftm": sum(r.get("FreeThrowsMade", 0) or 0 for r in rows),
        "fta": sum(r.get("FreeThrowsAttempted", 0) or 0 for r in rows),
        "tov": sum(r.get("Turnovers", 0) or 0 for r in rows),
        "orb": sum(r.get("OffensiveRebounds", 0) or 0 for r in rows),
        "drb": sum(r.get("DefensiveRebounds", 0) or 0 for r in rows),
    }


def _advanced_stats_for_team(team_id: str, own: Dict[str, float], opponent: Dict[str, float]) -> Dict[str, Any]:
    possessions_denom = own["fga"] + 0.44 * own["fta"] + own["tov"]
    efg_pct = round(100 * (own["fgm"] + 0.5 * own["fgm3"]) / own["fga"], 1) if own["fga"] else 0.0
    ts_denom = 2 * (own["fga"] + 0.44 * own["fta"])
    ts_pct = round(100 * own["pts"] / ts_denom, 1) if ts_denom else 0.0
    tov_pct = round(100 * own["tov"] / possessions_denom, 1) if possessions_denom else 0.0
    orb_denom = own["orb"] + opponent["drb"]
    orb_pct = round(100 * own["orb"] / orb_denom, 1) if orb_denom else 0.0
    return {
        "team_id": team_id, "efg_pct": efg_pct, "ts_pct": ts_pct, "tov_pct": tov_pct, "orb_pct": orb_pct,
        "ortg": None, "drtg": None,  # requieren posesiones estimadas por play-by-play, fuera de alcance
    }


_MADE_SHOT_PLAYTYPES = {"2FGM", "3FGM", "FTM"}
_POINTS_BY_PLAYTYPE = {"2FGM": 2, "3FGM": 3, "FTM": 1}

# El boxscore real de euroleague_api añade una fila "Team" y una "Total" por
# equipo (agregados, no jugadores) - se descartan.
_AGGREGATE_ROW_IDS = {"Team", "Total"}


def _period_to_quarter(period: int) -> str:
    return f"Q{period}" if period <= 4 else f"OT{period - 4}"


def _convert_play_by_play(records: List[dict], team_ids_by_code: Dict[str, str]) -> List[dict]:
    """Convierte filas de `get_game_play_by_play_data` al contrato común de eventos.

    Solo se quedan las jugadas relevantes para reconstruir lineups
    (sustituciones y canastas anotadas); el resto (pérdidas, faltas,
    rebotes...) no afecta a qué 5 jugadores están en pista ni al marcador.
    """
    events = []
    for row in records:
        playtype = str(row.get("PLAYTYPE", "")).strip()
        code = row.get("CODETEAM")
        code = _clean_id(code) if code else None
        if not code or code not in team_ids_by_code:
            continue  # jugadas sin equipo (p.ej. "Begin Period") o de un equipo desconocido
        team_id = team_ids_by_code[code]
        quarter = _period_to_quarter(int(row["PERIOD"]))
        clock = str(row["MARKERTIME"])

        if playtype in ("IN", "OUT"):
            events.append(
                {
                    "team_id": team_id, "type": "sub_in" if playtype == "IN" else "sub_out",
                    "quarter": quarter, "clock": clock, "player_id": _clean_id(row["PLAYER_ID"]),
                }
            )
        elif playtype in _MADE_SHOT_PLAYTYPES:
            events.append(
                {
                    "team_id": team_id, "type": "score", "quarter": quarter, "clock": clock,
                    "points": _POINTS_BY_PLAYTYPE[playtype],
                }
            )
    return events


def build_raw_game(
    metadata: Dict[str, Any], boxscore_records: list, shot_records: list, play_by_play_records: Optional[list] = None,
) -> Dict[str, Any]:
    """Ensambla el contrato común a partir de metadata + boxscore + tiros ya en formato `records`.

    Args:
        metadata: una fila de `fetch_game_metadata` (ya convertida a dict).
        boxscore_records: `fetch_game_boxscore(...).to_dict("records")`.
        shot_records: `fetch_game_shot_data(...).to_dict("records")`.
        play_by_play_records: `fetch_game_play_by_play(...).to_dict("records")`, opcional -
            si se da, permite reconstruir `lineups` (ver `_convert_play_by_play`).
    """
    home_code, away_code = _clean_id(metadata["CodeTeamA"]), _clean_id(metadata["CodeTeamB"])
    home_team = {"id": home_code, "name": str(metadata["TeamA"]).title()}
    away_team = {"id": away_code, "name": str(metadata["TeamB"]).title()}
    team_ids_by_code = {home_code: home_team["id"], away_code: away_team["id"]}

    players = []
    for row in boxscore_records:
        player_id = _clean_id(row["Player_ID"])
        if player_id in _AGGREGATE_ROW_IDS:
            continue
        team_code = _clean_id(row["Team"])
        fgm = (row.get("FieldGoalsMade2", 0) or 0) + (row.get("FieldGoalsMade3", 0) or 0)
        fga = (row.get("FieldGoalsAttempted2", 0) or 0) + (row.get("FieldGoalsAttempted3", 0) or 0)
        efg_pct = round(100 * (fgm + 0.5 * (row.get("FieldGoalsMade3", 0) or 0)) / fga, 1) if fga else 0.0
        players.append(
            {
                "player_id": player_id,
                "team_id": team_ids_by_code.get(team_code, team_code),
                "name": row["Player"],
                "number": row.get("Dorsal"),
                "position": None,  # no viene en el boxscore de euroleague_api
                "minutes": _parse_minutes(row.get("Minutes")),
                "pts": int(row.get("Points", 0) or 0),
                "reb": int(row.get("TotalRebounds", 0) or 0),
                "ast": int(row.get("Assistances", 0) or 0),
                "efg_pct": efg_pct,
                "starter": bool(row.get("IsStarter", 0)),
            }
        )

    home_totals = _team_totals(boxscore_records, home_code)
    away_totals = _team_totals(boxscore_records, away_code)
    team_stats = [
        _advanced_stats_for_team(home_team["id"], home_totals, away_totals),
        _advanced_stats_for_team(away_team["id"], away_totals, home_totals),
    ]

    shots = []
    for row in shot_records:
        coord_x, coord_y = row.get("COORD_X"), row.get("COORD_Y")
        if (coord_x, coord_y) == _NO_LOCATION_SENTINEL:
            continue  # tiro libre u otra jugada sin ubicación real (centinela -1,-1)
        action = str(row.get("ID_ACTION", ""))
        made = action.endswith("M")  # p.ej. "2FGM" (anotado) vs "2FGA" (fallado)
        x, y = _rescale_shot_coords(float(coord_x), float(coord_y))
        shots.append(
            {
                "player_id": _clean_id(row["ID_PLAYER"]),
                "team_id": team_ids_by_code.get(_clean_id(row.get("TEAM", "")), row.get("TEAM")),
                "x": x,
                "y": y,
                "made": made,
            }
        )

    return {
        "game_id": str(metadata["Gamecode"]),
        "date": _parse_date(metadata["Date"]),
        "season": int(metadata["Season"]),
        "competition": COMPETITION_NAME,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": int(metadata["ScoreA"]),
        "away_score": int(metadata["ScoreB"]),
        "pace": 0.0,  # no viene en la metadata; requeriría estimarlo por posesiones
        "narrative": None,
        "team_stats": team_stats,
        "players": players,
        "lineups": [],  # se reconstruyen desde play_by_play si se ha pasado (ver parse_and_resolve)
        "shots": shots,
        "events": [],
        "score_progression": [],
        "play_by_play": _convert_play_by_play(play_by_play_records or [], team_ids_by_code),
    }

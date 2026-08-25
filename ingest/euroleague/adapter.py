"""Adapta los DataFrames de `euroleague_api` al contrato común (`ingest.common.raw_game`).

Columnas **verificadas en vivo** (2026-08-20, `euroleague-api` instalado,
temporada 2025 / gamecode 7 - Virtus Bologna vs Real Madrid):

- metadata (`get_game_metadata`): `Season`, `Gamecode`, `Date` (`"DD/MM/YYYY"`),
  `TeamA`/`TeamB` (nombre completo, MAYÚSCULAS), `CodeTeamA`/`CodeTeamB`
  (código corto, p.ej. `"MAD"` - es el id estable que usan boxscore/shots/pbp
  para referirse a un equipo, no el nombre), `ScoreA`/`ScoreB`, `Competition`
  (p.ej. `"EUROLEAGUE 2025-26"`). A = local, B = visitante.
- boxscore (`get_players_boxscore_stats`): `Player_ID`, `Player`
  (`"APELLIDO, Nombre"`, verificado en vivo 2026-08-24: AMBAS partes en
  MAYÚSCULAS de verdad - p.ej. `"VILDOZA, LUCA"`, `"ALSTON JR., DERRICK"` -
  no solo el apellido como sugería el ejemplo original de este docstring;
  ver `_format_player_name`), `Team` (código corto, como `CodeTeamA/B`),
  `Dorsal`, `IsStarter` (`0.0`/`1.0`), `Minutes` (`"MM:SS"`, no decimal),
  `Points`, `FieldGoalsMade2/Attempted2/Made3/Attempted3`,
  `FreeThrowsMade/Attempted`, `OffensiveRebounds`, `DefensiveRebounds`,
  `TotalRebounds`, `Assistances`, `Turnovers`.
- shot data (`get_game_shot_data`): `ID_PLAYER` (¡no `PLAYER_ID`!), `TEAM`
  (código corto), `COORD_X`/`COORD_Y` (cm RELATIVOS AL ARO: X lateral
  [-750, 750], Y distancia al aro [0, ~1300] con 0 = canasta; `(-1, -1)` es
  el centinela de "sin ubicación", usado en tiros libres - se descartan; ver
  `_rescale_shot_coords`), `ZONE` (letra de zona oficial: "A" bajo el aro,
  "H"/"I" triples izquierda/derecha... - no se importa, pero sirve para
  verificar la convención de coordenadas), `ID_ACTION` (`"2FGM"`, `"3FGM"`,
  `"FTM"` anotados; `"2FGA"`, `"3FGA"` fallados).
- play-by-play (`get_game_play_by_play_data`): `CODETEAM` (código corto -
  usarlo, no `TEAM` que trae el nombre completo), `PLAYER_ID`, `PLAYTYPE`
  (`"IN"`/`"OUT"` sustituciones; `"2FGM"`/`"3FGM"`/`"FTM"` canastas
  anotadas), `PERIOD` (1-4 cuartos, 5+ prórroga), `MARKERTIME`
  (`"MM:SS"` restantes en el periodo).

`game_advanced_stats` se deriva agregando el boxscore por equipo (eFG%/TS%/
TOV%/ORB% con las fórmulas estándar; `ortg`/`drtg`/`ast_pct`/`ft_rate`/
`ast_to_ratio` con posesiones estimadas vía la fórmula Dean Oliver - fiel a
`oer`/`der`/`S_assist`/`FT_rate`/`ast_to_ratio` de `04_team_stats.R`, ver
`_estimate_possessions`). `stl_pct`/`blk_pct` (`S_steal`/`S_blocks`) usan
columnas `Steals`/`BlocksFavour` ASUMIDAS - a diferencia del resto de este
módulo, no se han verificado en vivo; si no existen, degradan a 0
silenciosamente en `_team_totals` en vez de romper la carga. `quarter_stats`
(fiel a la parte "quarters" de `09_team_pace.R`) solo se rellena si se pasa
`play_by_play_records` (igual que `lineups`) - sin PBP no se sabe en qué
cuarto se anotó cada punto. `lineups` se reconstruye a partir del
play-by-play (`ingest.common.lineups.reconstruct_lineups`, invocado desde
`ingest.common.raw_game.parse_and_resolve`) usando `IsStarter` como quinteto
titular; si no se pasa play-by-play, o ningún jugador viene marcado
`IsStarter`, queda vacío (datos insuficientes) en vez de inventarse algo.

Si la versión instalada de `euroleague_api` difiere, este es el único
fichero que hay que tocar — el contrato de salida (`ingest.common.raw_game`)
no cambia.

CALENDARIO FUTURO (`build_scheduled_matchup`, 2026-08-24): `Schedule.get_schedule`
(la misma llamada que ya usa `fetch_season_game_codes`) trae el calendario
COMPLETO de la temporada -jugado y no jugado- en una sola respuesta (a
diferencia de ACB, no hace falta recorrer semanas): columnas verificadas en
vivo (temporada 2026, 380 filas) `date` (`"Sep 24, 2026"`), `hometeam`/
`awayteam` (nombre completo MAYÚSCULAS), `homecode`/`awaycode` (código
corto, mismo espacio que `CodeTeamA/B` del boxscore y `code` de
`fetch_clubs`), `played` (`"true"`/`"false"`, string). Fiel al patrón de
`ingest/acb/adapter.py::build_scheduled_matchup`.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from ingest.common.zones import to_court_coords

COMPETITION_NAME = "Euroliga"

# Cancha: COORD_X/COORD_Y vienen en CENTÍMETROS RELATIVOS AL ARO, no en un
# rango arbitrario que haya que normalizar de extremo a extremo (verificado en
# vivo 2026-08-24 contra la columna `ZONE` del propio feed: la zona "A" -bajo
# el aro- tiene COORD_Y en [0, 25], y el pico de la distribución de COORD_Y
# está en 0-100 -bandejas- y en 650-750 -la línea de 6.75 m-):
#   COORD_Y = distancia al aro hacia el centro del campo (0 = canasta),
#   COORD_X = desplazamiento lateral (negativo = izquierda del atacante).
# Se reescala anclando en el aro, no normalizando rangos (ver
# `ingest.common.zones.to_court_coords`, compartido con ACB): `court_zones`
# usa la convención "y ALTO = cerca del aro", así que la profundidad va
# RESTANDO. La versión anterior la sumaba, lo que dejaba el mapa de tiros de
# Euroliga invertido en vertical: las bandejas caían en "Triple exterior"
# (con un 62% de acierto, imposible para triples) y los triples junto al aro.
_NO_LOCATION_SENTINEL = (-1, -1)


def _rescale_shot_coords(coord_x: float, coord_y: float) -> tuple:
    """`COORD_X` (lateral) / `COORD_Y` (distancia al aro) en cm -> escala de `court_zones`."""
    return to_court_coords(lateral_cm=coord_x, depth_cm=coord_y)


def _clean_id(value: Any) -> str:
    """`str(value).strip()` - el boxscore real trae `Player_ID`/`Team` con espacios de relleno."""
    return str(value).strip()


def _format_player_name(raw: Any) -> str:
    """`"APELLIDO, Nombre"` (formato real del boxscore, ver docstring del módulo) ->
    `"Nombre Apellido"` con capitalización normal.

    Sin este formateo, `players.name` se guardaba tal cual venía de la fuente
    (p.ej. `"VILDOZA, LUCA"`) y se propagaba a toda la interfaz (roster,
    quintetos, boxscore...) - se detectó al ver nombres con la coma en medio
    y el apellido primero en "Quintetos más utilizados" de Próximo rival.
    Se parte por la PRIMERA coma (no por espacios): un apellido compuesto
    como `"ALSTON JR."` no se rompe. Sin coma en el valor, se deja tal cual
    (defensivo, no debería pasar con el boxscore real pero no debe romper la
    carga de un partido si pasa).
    """
    text = str(raw).strip()
    if "," not in text:
        return text
    last, _, first = text.partition(",")
    return f"{first.strip().title()} {last.strip().title()}"


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
        "ast": sum(r.get("Assistances", 0) or 0 for r in rows),
        # Steals/BlocksFavour: nombres de columna ASUMIDOS (no verificados en
        # vivo, a diferencia del resto de esta función) - si difieren, esto
        # degrada en silencio a 0 en vez de romper la carga del partido.
        "stl": sum(r.get("Steals", 0) or 0 for r in rows),
        "blk": sum(r.get("BlocksFavour", 0) or 0 for r in rows),
    }


def _estimate_possessions(totals: Dict[str, float]) -> float:
    """Posesiones estimadas (fórmula estándar Dean Oliver: FGA - ORB + TOV + 0.44*FTA).

    Igual que en `ingest/acb/adapter.py`: `04_team_stats.R` usa un contador
    exacto de viajes a la línea (`FT_trip`, del play-by-play) que no
    tenemos, así que se aproxima con `0.44*FTA` (estándar de boxscore).
    `ortg`/`drtg` siguen la misma fórmula que `oer`/`der` en
    `04_team_stats.R`: `puntos / posesiones * 100`.
    """
    return totals["fga"] - totals["orb"] + totals["tov"] + 0.44 * totals["fta"]


def _advanced_stats_for_team(team_id: str, own: Dict[str, float], opponent: Dict[str, float]) -> Dict[str, Any]:
    efg_pct = round(100 * (own["fgm"] + 0.5 * own["fgm3"]) / own["fga"], 1) if own["fga"] else 0.0
    ts_denom = 2 * (own["fga"] + 0.44 * own["fta"])
    ts_pct = round(100 * own["pts"] / ts_denom, 1) if ts_denom else 0.0
    # TOV% de basketball-reference: denominador sin restar ORB (distinto del
    # estimador de posesiones de partido completo usado para ortg/drtg/pace).
    tov_denom = own["fga"] + 0.44 * own["fta"] + own["tov"]
    tov_pct = round(100 * own["tov"] / tov_denom, 1) if tov_denom else 0.0
    orb_denom = own["orb"] + opponent["drb"]
    orb_pct = round(100 * own["orb"] / orb_denom, 1) if orb_denom else 0.0

    own_poss = _estimate_possessions(own)
    opp_poss = _estimate_possessions(opponent)
    ortg = round(100 * own["pts"] / own_poss, 1) if own_poss else 0.0
    drtg = round(100 * opponent["pts"] / opp_poss, 1) if opp_poss else 0.0

    # Extras fieles a 04_team_stats.R: S_assist/S_steal/S_blocks/FT_rate/ast_to_ratio.
    ast_pct = round(100 * own["ast"] / own["fgm"], 1) if own["fgm"] else 0.0
    stl_pct = round(100 * own["stl"] / opp_poss, 1) if opp_poss else 0.0
    blk_pct = round(100 * own["blk"] / opp_poss, 1) if opp_poss else 0.0
    ft_rate = round(100 * own["ftm"] / own["fga"], 1) if own["fga"] else 0.0
    ast_to_ratio = round(own["ast"] / own["tov"], 2) if own["tov"] else None

    return {
        "team_id": team_id, "efg_pct": efg_pct, "ts_pct": ts_pct, "tov_pct": tov_pct, "orb_pct": orb_pct,
        "ortg": ortg, "drtg": drtg,
        "ast_pct": ast_pct, "stl_pct": stl_pct, "blk_pct": blk_pct,
        "ft_rate": ft_rate, "ast_to_ratio": ast_to_ratio,
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


def _quarter_stats_from_events(events: List[dict], home_id: str, away_id: str) -> List[dict]:
    """Puntos por cuarto (fiel a la parte "quarters" de 09_team_pace.R), a partir del PBP ya convertido.

    Solo disponible si se ha pasado `play_by_play_records` (igual que
    `lineups`) - sin PBP no hay forma de saber en qu\u00e9 cuarto se anot\u00f3 cada
    punto, as\u00ed que se deja vac\u00edo en vez de inventarlo.
    """
    scored: Dict[tuple, int] = {}
    for event in events:
        if event["type"] != "score" or not event["quarter"].startswith("Q"):
            continue  # solo cuartos regulares (Q1-Q4), no pr\u00f3rroga (OTn)
        quarter = int(event["quarter"][1:])
        if quarter > 4:
            continue
        key = (event["team_id"], quarter)
        scored[key] = scored.get(key, 0) + event["points"]

    rows = []
    for quarter in range(1, 5):
        home_pts = scored.get((home_id, quarter), 0)
        away_pts = scored.get((away_id, quarter), 0)
        if (home_id, quarter) not in scored and (away_id, quarter) not in scored:
            continue
        rows.append({"team_id": home_id, "quarter": quarter, "points_for": home_pts, "points_against": away_pts})
        rows.append({"team_id": away_id, "quarter": quarter, "points_for": away_pts, "points_against": home_pts})
    return rows


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
                "name": _format_player_name(row["Player"]),
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
    pace = round((_estimate_possessions(home_totals) + _estimate_possessions(away_totals)) / 2, 1)
    converted_pbp = _convert_play_by_play(play_by_play_records or [], team_ids_by_code)
    quarter_stats = _quarter_stats_from_events(converted_pbp, home_team["id"], away_team["id"])

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
        "pace": pace,
        "narrative": None,
        "team_stats": team_stats,
        "players": players,
        "lineups": [],  # se reconstruyen desde play_by_play si se ha pasado (ver parse_and_resolve)
        "shots": shots,
        "events": [],
        "score_progression": [],
        "quarter_stats": quarter_stats,
        "play_by_play": converted_pbp,
    }


def _parse_schedule_date(value: Any) -> Optional[str]:
    """`"Sep 24, 2026"` -> `"2026-09-24"`; `None` si no se puede parsear (columna
    `date` vacía/formato inesperado - un partido sin fecha no debe entrar en
    `upcoming_matchups`, que exige `match_date NOT NULL`)."""
    try:
        return datetime.strptime(str(value).strip(), "%b %d, %Y").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def build_scheduled_matchup(
    row: Dict[str, Any], clubs_by_code: Dict[str, Dict[str, Any]], own_code: str
) -> Optional[Dict[str, Any]]:
    """Convierte una fila (ya jugada o no) de `Schedule.get_schedule` en un registro
    listo para `upcoming_matchups`, o `None` si el Baskonia no juega ese partido.

    `Schedule.get_schedule` trae el calendario COMPLETO de la competición (20
    equipos en 2026-2027), de ahí el filtro - `pipeline.run_upcoming` ya
    descarta antes las filas jugadas (`played == "true"`), esta función solo
    se ocupa de qué partidos son del Baskonia. Mismo criterio que
    `ingest/acb/adapter.py::build_scheduled_matchup`.

    Args:
        row: una fila de `EuroleagueClient.fetch_season_game_codes(season)`
            (ya convertida a `dict`).
        clubs_by_code: `{code: club}` de `EuroleagueClient.fetch_clubs(season)`,
            para resolver nombre/escudo real del rival sin una llamada aparte.
        own_code: código de club del Baskonia en `clubs_by_code`
            (`pipeline._own_team_euroleague_code`).
    """
    home_code = _clean_id(row["homecode"])
    away_code = _clean_id(row["awaycode"])
    if own_code not in (home_code, away_code):
        return None

    is_home = home_code == own_code
    opponent_code = away_code if is_home else home_code
    opponent = clubs_by_code.get(opponent_code)
    if opponent is not None:
        opponent_name = opponent["name"]
        opponent_logo_url = (opponent.get("images") or {}).get("crest")
    else:
        # Sin fixture en `fetch_clubs` (no debería pasar salvo desajuste puntual
        # entre calendario y catálogo de clubes) - se usa el nombre del propio
        # calendario en vez de descartar el partido entero.
        opponent_name = str(row["awayteam" if is_home else "hometeam"]).title()
        opponent_logo_url = None

    return {
        "opponent_code": opponent_code,
        "opponent_name": opponent_name,
        "opponent_logo_url": opponent_logo_url,
        "match_date": _parse_schedule_date(row.get("date")),
        "is_home": is_home,
    }

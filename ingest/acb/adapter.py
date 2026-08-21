"""Adapta las respuestas de `AcbClient` (boxscore/tiros/play-by-play) al contrato común (`ingest.common.raw_game`).

Ver `client.py` para el resumen de qué endpoints/campos están verificados en
vivo.

Fidelidad a `04_team_stats.R`/`09_team_pace.R` de OpenACB (2026-08-21):
`ortg`/`drtg`/`ast_pct`/`stl_pct`/`blk_pct`/`ft_rate`/`ast_to_ratio` replican
sus fórmulas `oer`/`der`/`S_assist`/`S_steal`/`S_blocks`/`FT_rate`/
`ast_to_ratio`, con una diferencia: el script R deriva las posesiones
(`pos`) de un conteo exacto de viajes a la línea vía play-by-play
(`FT_trip`); aquí se usa la aproximación estándar de boxscore (Dean Oliver,
ver `_estimate_possessions`). `quarter_stats` replica la parte de puntos
por cuarto de `09_team_pace.R` (sus segmentos de 2 minutos y la eficiencia
post-tiempo muerto necesitan agrupar por ventana de tiempo dentro del
cuarto - fuera de alcance por ahora, no implementado).

Tiros/quintetos/marcador (2026-08-21, `MatchShots`/`PlayByPlay`): códigos
`playType` verificados en vivo cruzando cada tipo con el delta de
`playerStats`/`scoreHome`/`scoreAway` que produce (ver el turno en que se
decodificaron, no están documentados por ACB):
  92=tiro libre anotado, 93=2 anotado, 94=3 anotado, 100=mate anotado (2 pts,
  categoría aparte de 93 igual que "Mate" vs "Canasta de 2" en OpenACB),
  96=tiro libre fallado, 97=2 fallado, 98=3 fallado (92/96 sin coordenadas -
  se descartan para `shots`, que exige `pos_x`/`pos_y`), 599=quinteto
  inicial (10 eventos al principio del partido, 5 por equipo), 112=entra a
  pista, 115=sale de pista.
"""
from typing import Any, Dict, List

COMPETITION_NAME = "ACB"

# Rango de coordenadas observado en vivo (mm): posX = distancia al aro
# (0=canasta .. ~7300=tiro largo), posY = desplazamiento lateral
# (-7000..7000 aprox). Asunción documentada (no hay especificación oficial
# de ACB): se reescala con algo de margen a las constantes de abajo.
_DEPTH_RANGE_MM = (0.0, 7500.0)
_LATERAL_RANGE_MM = (-7500.0, 7500.0)

_MADE_SHOT_PLAYTYPES = {93, 94, 100}
_MISSED_SHOT_PLAYTYPES = {97, 98}
_SCORE_POINTS_BY_PLAYTYPE = {92: 1, 93: 2, 94: 3, 100: 2}
_STARTER_PLAYTYPE = 599
_SUB_IN_PLAYTYPE = 112
_SUB_OUT_PLAYTYPE = 115


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


def _parse_number(shirt_number: Any) -> Any:
    try:
        return int(shirt_number)
    except (TypeError, ValueError):
        return None


def _full_game_stats(team_boxscore: Dict[str, Any]) -> Dict[str, Any]:
    """`statsByPeriods[quarter=0]` es el total del partido completo (no por cuarto)."""
    period = next(p for p in team_boxscore["statsByPeriods"] if p["quarter"] == 0)
    return period["stats"]


def _team_totals(stats: Dict[str, Any]) -> Dict[str, float]:
    total = stats["total"]
    return {
        "pts": total["points"],
        "fgm": total["twoPointersMade"] + total["threePointersMade"],
        "fgm3": total["threePointersMade"],
        "fga": total["twoPointersAttempted"] + total["threePointersAttempted"],
        "ftm": total["freeThrowsMade"],
        "fta": total["freeThrowsAttempted"],
        "tov": total["turnovers"],
        "orb": total["offRebounds"],
        "drb": total["defRebounds"],
        "ast": total["assists"],
        "stl": total["steals"],
        "blk": total["blocks"],
    }


def _estimate_possessions(totals: Dict[str, float]) -> float:
    """Posesiones estimadas (fórmula estándar Dean Oliver: FGA - ORB + TOV + 0.44*FTA).

    `04_team_stats.R` calcula `pos` a partir de un contador exacto de viajes
    a la línea de tiros libres (`FT_trip`, derivado del play-by-play) en vez
    de `0.44*FTA` - no tenemos ese play-by-play para ninguna fuente (ver
    huecos declarados en `client.py`), así que se usa la aproximación
    estándar de boxscore. `oer`/`der` (`ortg`/`drtg` aquí) se calculan igual
    que en `04_team_stats.R` una vez estimadas las posesiones:
    `oer = puntos / pos * 100`, `der = puntos_rival / pos_rival * 100`.
    """
    return totals["fga"] - totals["orb"] + totals["tov"] + 0.44 * totals["fta"]


def _advanced_stats_for_team(team_id: str, own: Dict[str, float], opponent: Dict[str, float]) -> Dict[str, Any]:
    fga = own["fga"] or 0
    efg_pct = round(100 * (own["fgm"] + 0.5 * own["fgm3"]) / fga, 1) if fga else 0.0
    ts_denom = 2 * (own["fga"] + 0.44 * own["fta"])
    ts_pct = round(100 * own["pts"] / ts_denom, 1) if ts_denom else 0.0
    poss_denom = own["fga"] + 0.44 * own["fta"] + own["tov"]
    tov_pct = round(100 * own["tov"] / poss_denom, 1) if poss_denom else 0.0
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
    ft_rate = round(100 * own["ftm"] / fga, 1) if fga else 0.0
    ast_to_ratio = round(own["ast"] / own["tov"], 2) if own["tov"] else None

    return {
        "team_id": team_id, "efg_pct": efg_pct, "ts_pct": ts_pct, "tov_pct": tov_pct, "orb_pct": orb_pct,
        "ortg": ortg, "drtg": drtg,
        "ast_pct": ast_pct, "stl_pct": stl_pct, "blk_pct": blk_pct,
        "ft_rate": ft_rate, "ast_to_ratio": ast_to_ratio,
    }


def _advanced_stats_from_official(team_id: str, official: Dict[str, Any], own: Dict[str, float]) -> Dict[str, Any]:
    """Igual que `_advanced_stats_for_team` pero leyendo el cálculo OFICIAL de acb.com
    (`AdvancedStats/match-advanced-stats`) en vez de estimarlo - se prefiere
    siempre que esté disponible (ver `build_raw_game`). `ast_to_ratio` es la
    única métrica que no viene en este endpoint, se calcula igual que en el
    fallback (asistencias/pérdidas del boxscore).
    """
    four, rhythm = official["fourFactors"], official["gameRhythm"]
    ratings, ball, shooting = official["ratings"], official["ballHandling"], official["shooting"]
    ast_to_ratio = round(own["ast"] / own["tov"], 2) if own["tov"] else None
    return {
        "team_id": team_id,
        "efg_pct": four["efgPct"]["partido"], "ts_pct": shooting["tsPct"]["partido"],
        "tov_pct": four["tovPct"]["partido"], "orb_pct": four["orbPct"]["partido"],
        "ortg": ratings["oer"]["partido"], "drtg": ratings["der"]["partido"],
        "ast_pct": ball["astPct"]["partido"], "stl_pct": ball["stlPct"]["partido"],
        "blk_pct": ball["blkPct"]["partido"], "ft_rate": four["fTr"]["partido"],
        "ast_to_ratio": ast_to_ratio,
    }


def _quarter_stats(home_id: str, away_id: str, home_box: Dict[str, Any], away_box: Dict[str, Any]) -> list:
    """Puntos por cuarto para ambos equipos (fiel a la parte "quarters" de 09_team_pace.R)."""
    home_by_quarter = {
        p["quarter"]: p["stats"]["total"]["points"] for p in home_box["statsByPeriods"] if p["quarter"] in (1, 2, 3, 4)
    }
    away_by_quarter = {
        p["quarter"]: p["stats"]["total"]["points"] for p in away_box["statsByPeriods"] if p["quarter"] in (1, 2, 3, 4)
    }
    rows = []
    for quarter in sorted(set(home_by_quarter) & set(away_by_quarter)):
        home_pts, away_pts = home_by_quarter[quarter], away_by_quarter[quarter]
        rows.append({"team_id": home_id, "quarter": quarter, "points_for": home_pts, "points_against": away_pts})
        rows.append({"team_id": away_id, "quarter": quarter, "points_for": away_pts, "points_against": home_pts})
    return rows


def _rescale_shot_coords(pos_x_mm: float, pos_y_mm: float) -> tuple:
    """`posX` (distancia al aro) / `posY` (lateral) en mm -> escala 0-500 de `court_zones`."""
    depth_min, depth_max = _DEPTH_RANGE_MM
    lateral_min, lateral_max = _LATERAL_RANGE_MM
    y = 500 * (1 - (pos_x_mm - depth_min) / (depth_max - depth_min))  # cerca del aro = y alto (ver court_zones seed)
    x = 500 * (pos_y_mm - lateral_min) / (lateral_max - lateral_min)
    return x, y


def _convert_shots(shot_points: List[dict], home_id: str, away_id: str) -> List[dict]:
    """Tiros de campo con coordenadas reales (excluye tiros libres, sin `posX`/`posY`)."""
    shots = []
    for point in shot_points:
        play_type = point["playType"]
        if play_type not in _MADE_SHOT_PLAYTYPES and play_type not in _MISSED_SHOT_PLAYTYPES:
            continue  # tiro libre (92/96) u otro evento sin coordenadas reales
        x, y = _rescale_shot_coords(point["posX"], point["posY"])
        shots.append(
            {
                "player_id": str(point["playerLicenseId"]),
                "team_id": home_id if point["local"] else away_id,
                "x": x,
                "y": y,
                "made": play_type in _MADE_SHOT_PLAYTYPES,
            }
        )
    return shots


def _extract_starters(plays: List[dict], home_id: str, away_id: str) -> Dict[str, list]:
    starters = {"home": [], "away": []}
    for play in plays:
        if play["playType"] != _STARTER_PLAYTYPE:
            continue
        side = "home" if play["local"] else "away"
        starters[side].append(str(play["playerLicenseId"]))
    return starters


def _quarter_label(quarter: int) -> str:
    return f"Q{quarter}" if quarter <= 4 else f"OT{quarter - 4}"


def _convert_play_by_play(plays: List[dict], home_id: str, away_id: str) -> List[dict]:
    """Solo las jugadas relevantes para reconstruir quintetos: sustituciones y canastas anotadas."""
    events = []
    for play in sorted(plays, key=lambda p: p["order"]):
        play_type = play["playType"]
        team_id = home_id if play["local"] else away_id
        clock = f"{play['minute']:02d}:{play['second']:02d}"
        quarter = _quarter_label(play["quarter"])
        if play_type in (_SUB_IN_PLAYTYPE, _SUB_OUT_PLAYTYPE):
            events.append(
                {
                    "team_id": team_id, "type": "sub_in" if play_type == _SUB_IN_PLAYTYPE else "sub_out",
                    "quarter": quarter, "clock": clock, "player_id": str(play["playerLicenseId"]),
                }
            )
        elif play_type in _SCORE_POINTS_BY_PLAYTYPE:
            events.append(
                {
                    "team_id": team_id, "type": "score", "quarter": quarter, "clock": clock,
                    "points": _SCORE_POINTS_BY_PLAYTYPE[play_type],
                }
            )
    return events


def _score_progression(plays: List[dict]) -> List[dict]:
    """Marcador tras cada jugada que cambia el resultado, en orden cronológico (`order`)."""
    steps = []
    prev = (None, None)
    for play in sorted(plays, key=lambda p: p["order"]):
        current = (play["scoreHome"], play["scoreAway"])
        if current == prev:
            continue
        steps.append({"step": len(steps), "home": current[0], "away": current[1]})
        prev = current
    return steps


def build_raw_game(
    match: Dict[str, Any], boxscore: Dict[str, Any], season: int,
    shots: Dict[str, Any] = None, play_by_play: Dict[str, Any] = None, advanced_stats: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Ensambla el contrato común a partir de un partido del calendario + boxscore/tiros/play-by-play.

    Args:
        match: una fila de `AcbClient.fetch_season_finished_matches` (trae
            `id`/`homeTeamId`/`awayTeamId`/`homeScore`/`awayScore`/`startDateTime`).
        boxscore: `AcbClient.fetch_game_boxscore(match["id"])`.
        season: año de inicio de temporada (el mismo pasado a `fetch_season_finished_matches`).
        shots: `AcbClient.fetch_game_shots(match["id"])`, opcional - sin él, `shots` queda vacío.
        play_by_play: `AcbClient.fetch_game_play_by_play(match["id"])`, opcional - sin él,
            `lineups`/`score_progression` quedan vacíos (no hay forma de reconstruirlos).
        advanced_stats: `AcbClient.fetch_game_advanced_stats(match["id"])`, opcional - si se
            da, sus números OFICIALES (posesiones/pace/ortg/drtg/net_rating/four factors)
            sustituyen a la estimación propia (`_advanced_stats_for_team`/`_estimate_possessions`).
    """
    if not boxscore.get("matchFinished"):
        raise ValueError(f"AcbClient: boxscore del partido {match['id']} no está finalizado todavía")

    team_boxscores = boxscore["teamBoxscores"]
    home_box = next((tb for tb in team_boxscores if tb["team"]["id"] == match["homeTeamId"]), team_boxscores[0])
    away_box = next((tb for tb in team_boxscores if tb["team"]["id"] == match["awayTeamId"]), team_boxscores[1])

    home_team = {"id": str(home_box["team"]["id"]), "name": home_box["team"]["fullName"]}
    away_team = {"id": str(away_box["team"]["id"]), "name": away_box["team"]["fullName"]}

    home_stats = _full_game_stats(home_box)
    away_stats = _full_game_stats(away_box)
    home_totals = _team_totals(home_stats)
    away_totals = _team_totals(away_stats)

    if advanced_stats and "homeAdvancedStats" in advanced_stats and "awayAdvancedStats" in advanced_stats:
        home_official, away_official = advanced_stats["homeAdvancedStats"], advanced_stats["awayAdvancedStats"]
        team_stats = [
            _advanced_stats_from_official(home_team["id"], home_official, home_totals),
            _advanced_stats_from_official(away_team["id"], away_official, away_totals),
        ]
        pace = round(
            (home_official["gameRhythm"]["pace"]["partido"] + away_official["gameRhythm"]["pace"]["partido"]) / 2, 1
        )
    else:
        team_stats = [
            _advanced_stats_for_team(home_team["id"], home_totals, away_totals),
            _advanced_stats_for_team(away_team["id"], away_totals, home_totals),
        ]
        pace = round((_estimate_possessions(home_totals) + _estimate_possessions(away_totals)) / 2, 1)
    quarter_stats = _quarter_stats(home_team["id"], away_team["id"], home_box, away_box)

    players = []
    for team, stats in ((home_team, home_stats), (away_team, away_stats)):
        for row in stats["players"]:
            player = row["player"]
            fgm = row["twoPointersMade"] + row["threePointersMade"]
            fga = row["twoPointersAttempted"] + row["threePointersAttempted"]
            efg_pct = round(100 * (fgm + 0.5 * row["threePointersMade"]) / fga, 1) if fga else 0.0
            players.append(
                {
                    "player_id": str(player["id"]),
                    "team_id": team["id"],
                    "name": player.get("nickname") or f"{player['firstName']} {player['lastName']}",
                    "number": _parse_number(player.get("shirtNumber")),
                    "position": player.get("gameRole"),
                    "minutes": _parse_minutes(row.get("playTime")),
                    "pts": row["points"],
                    "reb": row["totalRebounds"],
                    "ast": row["assists"],
                    "efg_pct": efg_pct,
                    "starter": bool(row.get("isStarted")),
                }
            )

    shot_list = _convert_shots(shots["shotPoints"], home_team["id"], away_team["id"]) if shots else []
    plays = play_by_play["plays"] if play_by_play else []
    starters = _extract_starters(plays, home_team["id"], away_team["id"]) if plays else None
    converted_pbp = _convert_play_by_play(plays, home_team["id"], away_team["id"])
    score_progression = _score_progression(plays)

    return {
        "game_id": str(match["id"]),
        "date": str(match["startDateTime"])[:10],
        "season": season,
        "competition": COMPETITION_NAME,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": match["homeScore"],
        "away_score": match["awayScore"],
        "pace": pace,
        "narrative": None,
        "team_stats": team_stats,
        "players": players,
        "lineups": [],  # se reconstruyen desde play_by_play/starters (ver parse_and_resolve)
        "shots": shot_list,
        "events": [],
        "score_progression": score_progression,
        "quarter_stats": quarter_stats,
        "starters": starters,
        "play_by_play": converted_pbp,
    }

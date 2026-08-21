"""Adapta la respuesta de `AcbClient.fetch_game_boxscore` al contrato común (`ingest.common.raw_game`).

Ver `client.py` para el resumen de qué endpoints/campos están verificados en
vivo. Huecos conocidos (declarados, no inventados): `shots`/`lineups`/
`score_progression`/`events` quedan vacíos porque no se ha localizado un
endpoint de play-by-play/tiro con coordenadas para el backend nuevo de
acb.com (`api2.acb.com/api/matchdata`) - solo hay boxscore agregado por
partido/cuarto.

Fidelidad a `04_team_stats.R`/`09_team_pace.R` de OpenACB (2026-08-21):
`ortg`/`drtg`/`ast_pct`/`stl_pct`/`blk_pct`/`ft_rate`/`ast_to_ratio` replican
sus fórmulas `oer`/`der`/`S_assist`/`S_steal`/`S_blocks`/`FT_rate`/
`ast_to_ratio`, con una diferencia: el script R deriva las posesiones
(`pos`) de un conteo exacto de viajes a la línea vía play-by-play
(`FT_trip`); aquí se usa la aproximación estándar de boxscore (Dean Oliver,
ver `_estimate_possessions`) porque no hay PBP disponible. `quarter_stats`
replica la parte de puntos por cuarto de `09_team_pace.R`; sus segmentos de
2 minutos y la eficiencia post-tiempo muerto SÍ necesitan PBP con marcas de
tiempo y quedan fuera de alcance.
"""
from typing import Any, Dict

COMPETITION_NAME = "ACB"


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


def build_raw_game(match: Dict[str, Any], boxscore: Dict[str, Any], season: int) -> Dict[str, Any]:
    """Ensambla el contrato común a partir de un partido del calendario + su boxscore.

    Args:
        match: una fila de `AcbClient.fetch_season_finished_matches` (trae
            `id`/`homeTeamId`/`awayTeamId`/`homeScore`/`awayScore`/`startDateTime`).
        boxscore: `AcbClient.fetch_game_boxscore(match["id"])`.
        season: año de inicio de temporada (el mismo pasado a `fetch_season_finished_matches`).
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
        "lineups": [],  # sin endpoint de play-by-play localizado (ver docstring del módulo)
        "shots": [],  # idem
        "events": [],
        "score_progression": [],
        "quarter_stats": quarter_stats,
    }

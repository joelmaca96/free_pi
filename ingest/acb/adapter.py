"""Adapta la respuesta de `AcbClient.fetch_game_boxscore` al contrato común (`ingest.common.raw_game`).

Ver `client.py` para el resumen de qué endpoints/campos están verificados en
vivo. Huecos conocidos (declarados, no inventados): `shots`/`lineups`/
`score_progression`/`events` quedan vacíos porque no se ha localizado un
endpoint de play-by-play/tiro con coordenadas para el backend nuevo de
acb.com (`api2.acb.com/api/matchdata`) - solo hay boxscore agregado por
partido/cuarto.
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
    }


def _advanced_stats_for_team(team_id: str, own: Dict[str, float], opponent: Dict[str, float]) -> Dict[str, Any]:
    fga = own["fga"] or 0
    efg_pct = round(100 * (own["fgm"] + 0.5 * own["fgm3"]) / fga, 1) if fga else 0.0
    ts_denom = 2 * (own["fga"] + 0.44 * own["fta"])
    ts_pct = round(100 * own["pts"] / ts_denom, 1) if ts_denom else 0.0
    poss_denom = own["fga"] + 0.44 * own["fta"] + own["tov"]
    tov_pct = round(100 * own["tov"] / poss_denom, 1) if poss_denom else 0.0
    orb_denom = own["orb"] + opponent["drb"]
    orb_pct = round(100 * own["orb"] / orb_denom, 1) if orb_denom else 0.0
    return {
        "team_id": team_id, "efg_pct": efg_pct, "ts_pct": ts_pct, "tov_pct": tov_pct, "orb_pct": orb_pct,
        "ortg": None, "drtg": None,  # requieren posesiones estimadas por play-by-play, fuera de alcance
    }


def _estimate_possessions(totals: Dict[str, float]) -> float:
    return totals["fga"] - totals["orb"] + totals["tov"] + 0.44 * totals["fta"]


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
    }

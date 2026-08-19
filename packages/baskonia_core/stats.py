"""Cálculo de estadísticas avanzadas a partir de los datos de box score.

Fórmulas usadas (aproximaciones estándar de partido único, sin datos de
liga completa):
- eFG% = (FGM + 0.5 * 3PM) / FGA
- TS%  = PTS / (2 * (FGA + 0.44 * FTA))
- Posesiones de un equipo ≈ FGA - ORB + TOV + 0.4 * FTA (Dean Oliver,
  simplificada: no cruza ORB/DRB con el rival).
- ORtg/DRtg = puntos por cada 100 posesiones propias/del rival.
- Pace = media de posesiones estimadas de ambos equipos en el partido.
"""
from typing import Dict, Optional


def effective_fg_pct(fgm: Optional[float], fga: Optional[float], fg3m: Optional[float]) -> Optional[float]:
    """Calcula el eFG% (field goal % ajustado por el valor de los triples)."""
    if not fga:
        return None
    return ((fgm or 0) + 0.5 * (fg3m or 0)) / fga


def true_shooting_pct(pts: Optional[float], fga: Optional[float], fta: Optional[float]) -> Optional[float]:
    """Calcula el TS% (eficiencia de tiro real, incluye tiros libres)."""
    denom = 2 * ((fga or 0) + 0.44 * (fta or 0))
    if not denom:
        return None
    return (pts or 0) / denom


def estimate_possessions(
    fga: Optional[float], fta: Optional[float], orb: Optional[float], tov: Optional[float]
) -> Optional[float]:
    """Estima las posesiones de un equipo en un partido."""
    if fga is None:
        return None
    return fga - (orb or 0) + (tov or 0) + 0.4 * (fta or 0)


def team_game_ratings(
    team_totals: Dict[str, Optional[float]],
    opp_totals: Dict[str, Optional[float]],
    team_score: Optional[float],
    opp_score: Optional[float],
) -> Dict[str, Optional[float]]:
    """Calcula posesiones, pace, ORtg, DRtg y Net Rating de un equipo en un partido.

    Args:
        team_totals: Totales del equipo (fga, fta, orb, tov, y opcionalmente
            team_points, team_rebounds, team_assists, team_fg_attempted,
            team_ft_attempted).
        opp_totals: Totales del rival (fga, fta, orb, tov).
        team_score: Puntos anotados por el equipo.
        opp_score: Puntos anotados por el rival.

    Returns:
        Diccionario con 'possessions', 'pace', 'off_rating', 'def_rating',
        'net_rating' y los totales de equipo (team_points, team_rebounds,
        team_assists, team_fg_attempted, team_ft_attempted) si están
        disponibles en `team_totals`.
    """
    poss = estimate_possessions(team_totals.get("fga"), team_totals.get("fta"), team_totals.get("orb"), team_totals.get("tov"))
    opp_poss = estimate_possessions(opp_totals.get("fga"), opp_totals.get("fta"), opp_totals.get("orb"), opp_totals.get("tov"))

    off_rating = 100 * team_score / poss if poss and team_score is not None else None
    def_rating = 100 * opp_score / opp_poss if opp_poss and opp_score is not None else None
    net_rating = off_rating - def_rating if off_rating is not None and def_rating is not None else None
    pace = (poss + opp_poss) / 2 if poss is not None and opp_poss is not None else None

    result: Dict[str, Optional[float]] = {
        "possessions": poss,
        "pace": pace,
        "off_rating": off_rating,
        "def_rating": def_rating,
        "net_rating": net_rating,
    }
    # Propagar los totales de equipo (si la fuente los aporta) para que
    # `upsert_team_game_stats` pueda persistirlos en `team_game_stats`.
    for key in ("team_points", "team_rebounds", "team_assists",
                "team_fg_attempted", "team_ft_attempted"):
        if key in team_totals:
            result[key] = team_totals[key]
    return result


def project_matchup(
    team_pace: Optional[float],
    team_off_rating: Optional[float],
    team_def_rating: Optional[float],
    opp_pace: Optional[float],
    opp_off_rating: Optional[float],
    opp_def_rating: Optional[float],
) -> Optional[Dict[str, float]]:
    """Proyecta posesiones y marcador esperado de un enfrentamiento.

    Combina el pace y los ORtg/DRtg medios de temporada de cada equipo: las
    posesiones esperadas son la media de los dos paces, y el rating esperado de
    cada equipo la media entre su ataque y la defensa del rival.

    Args:
        team_pace: Pace medio del equipo de referencia.
        team_off_rating: ORtg medio del equipo de referencia.
        team_def_rating: DRtg medio del equipo de referencia.
        opp_pace: Pace medio del rival.
        opp_off_rating: ORtg medio del rival.
        opp_def_rating: DRtg medio del rival.

    Returns:
        Diccionario con 'projected_possessions', 'team_projected_rating',
        'opp_projected_rating', 'team_projected_score' y 'opp_projected_score',
        o `None` si falta cualquiera de los 6 valores de entrada.
    """
    values = (team_pace, team_off_rating, team_def_rating, opp_pace, opp_off_rating, opp_def_rating)
    if any(v is None for v in values):
        return None

    projected_possessions = (team_pace + opp_pace) / 2
    team_rating = (team_off_rating + opp_def_rating) / 2
    opp_rating = (opp_off_rating + team_def_rating) / 2
    return {
        "projected_possessions": projected_possessions,
        "team_projected_rating": team_rating,
        "opp_projected_rating": opp_rating,
        "team_projected_score": team_rating * projected_possessions / 100,
        "opp_projected_score": opp_rating * projected_possessions / 100,
    }

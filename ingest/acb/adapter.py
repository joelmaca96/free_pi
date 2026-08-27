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
from typing import Any, Dict, List, Optional

from ingest.common.zones import to_court_coords

COMPETITION_NAME = "ACB"

# `Competition/matches?competitionId=1&...` (el filtro que recorre
# `fetch_season_finished_matches`) devuelve TODOS los partidos de la
# organización "Liga Endesa" en sentido amplio - Copa del Rey incluida, con
# `competitionId=2` en la cabecera real del partido (`MatchHeader/
# match-header`, ver `client.py`). Sin esto, todo lo que trae esa lista se
# etiquetaba como "ACB" sin más. Catálogo verificado en vivo
# (`availableFilters.competitions` de esa misma respuesta): 1=Liga Endesa,
# 2=Copa del Rey, 3=Supercopa Endesa.
_COMPETITION_BY_ID = {
    1: COMPETITION_NAME,
    2: "Copa del Rey",
    3: "Supercopa",
}

# HALLAZGO (2026-08-24, corregido el mismo día): al ampliar el rastreo de
# calendario para no parar en el primer hueco de `weekId` (ver historia en
# `client.py::fetch_season_finished_matches`), empezaron a aparecer partidos
# reales de Baskonia con `competitionId=10` ("Minicopa Endesa" en el
# catálogo de la propia API - cantera/base, no primer equipo) mezclados en
# la misma lista de `competitionId=1`. Como entonces todo lo no reconocido
# caía en "ACB" por defecto, esos partidos de cantera se cargaban como
# partidos reales de Liga Endesa - y sus jugadores, al compartir
# `team_id='bas'` y a veces el mismo dorsal que un jugador del primer
# equipo, pisaban el `name` real de ese jugador vía el fallback de
# identidad por dorsal+equipo (`ingest/common/identity.py::
# resolve_or_create_player`, paso 2 - actualiza `name` sin comprobar que
# sea la misma persona). Por eso `client.fetch_game()` ahora rechaza
# (`is_out_of_scope_competition`) cualquier `competition_id` que NO esté en
# `_COMPETITION_BY_ID` **antes** de pedir boxscore/jugadores - "no
# reconocida" ya no cae en ACB por defecto, se descarta el partido entero.
# `None` (el header no se pudo obtener) es la única excepción: se trata
# como "desconocida pero no rechazada" y cae en ACB, igual que antes de
# este hallazgo - un fallo de red no debe tumbar partidos legítimos.
def is_out_of_scope_competition(competition_id: Optional[int]) -> bool:
    """`True` si `competition_id` es un valor real pero fuera de las competiciones que se cargan."""
    return competition_id is not None and competition_id not in _COMPETITION_BY_ID


def _competition_name(competition_id: Optional[int]) -> str:
    """Nombre de competición (fuente de verdad: `_COMPETITION_BY_ID`) para el esquema de scouting."""
    return _COMPETITION_BY_ID.get(competition_id, COMPETITION_NAME)

# Coordenadas de tiro en MILÍMETROS RELATIVOS AL ARO: `posX` = distancia al
# aro (0 = canasta), `posY` = desplazamiento lateral (negativo/positivo a cada
# lado). No hay especificación oficial de ACB, pero está VERIFICADO en vivo
# (2026-08-24, 566 tiros con coordenadas de 4 partidos reales) contra la
# verdad de campo del propio feed -el `playType` distingue tiro de 2 (93/97)
# de tiro de 3 (94/98)-: `hypot(posX, posY)` separa unos de otros justo en los
# 6.750 mm de la línea de triple (0 de 336 tiros de 2 por encima, 2 de 230
# tiros de 3 por debajo, ambos a 6.700 mm). Que `posX` sea la profundidad y
# `posY` el lateral -y no al revés- se ve en sus rangos: `posX` nunca es
# negativo (es una distancia) y llega a ~9.700, mientras que `posY` se queda
# en +-7.300, o sea el ancho de media cancha (7.500 mm).
#
# ANTES se normalizaban los dos ejes contra un rango fijo (0-7.500 mm de
# profundidad -> 0-500), lo que estiraba la profundidad un 76%: el aro
# quedaba en y=500 en vez de 455 y la línea de triple en y=50 en vez de 170,
# así que el 35% de los tiros se pintaba por encima del aro (detrás del
# tablero) y el 77% no caía en ninguna zona. Además cortaba en 7.500 mm los
# tiros más lejanos, que llegan a 9.700+. Ahora se ancla en el aro con la
# misma conversión que Euroliga (`ingest.common.zones.to_court_coords`).

# LOS MATES (100) NO TRAEN COORDENADAS: vienen con `posX=posY=0`, el mismo
# centinela que los tiros libres (verificado en vivo 2026-08-24). Se siguen
# cargando como tiro -son canastas de 2 reales, y dejarlos fuera sesgaría a la
# baja el acierto de la pintura-, pero eso los apila a todos exactamente sobre
# el aro (4,1% de los tiros ACB de la BD). Es la posición correcta a grandes
# rasgos -un mate ES en el aro-, pero no es una coordenada medida, así que se
# marcan con `located=False` (ver `shots.located` en `schema.sql`) y el mapa
# de tiros los pinta aparte, como un único símbolo con su recuento, en vez de
# como un tiro localizado más. No se dispersan a mano: inventar coordenadas
# que la fuente no da sería peor que la limitación.
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
        # Tiros libres en BRUTO, además de `ft_rate`. La tasa (FTM/FGA) no deja
        # reconstruir ni el volumen ni el acierto desde la línea: con solo
        # `ft_rate` no se puede responder "¿cuántos libres concede este equipo?".
        # El dato ya estaba en `own` (`_team_totals`) y se usaba únicamente para
        # derivar `ft_rate`; aquí simplemente deja de tirarse.
        "ftm": own["ftm"], "fta": own["fta"],
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
        # Igual que en el camino estimado: el recuento bruto sale del boxscore
        # (`own`), no de este endpoint — `match-advanced-stats` solo da la tasa.
        "ftm": own["ftm"], "fta": own["fta"],
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
    """`posX` (distancia al aro) / `posY` (lateral) en mm -> escala de `court_zones`."""
    return to_court_coords(lateral_cm=pos_y_mm / 10, depth_cm=pos_x_mm / 10)


def _convert_shots(shot_points: List[dict], home_id: str, away_id: str) -> List[dict]:
    """Tiros de campo (excluye tiros libres, sin `posX`/`posY`).

    Los mates llegan con el centinela `posX=posY=0` (ver `_MADE_SHOT_PLAYTYPES`):
    se marcan `located=False` y su posición reescalada cae, por construcción,
    justo en el aro. Se comprueba el centinela y no el `playType` porque lo que
    importa aguas abajo es si la coordenada es medida o no — si otro tipo de
    tiro apareciera algún día sin coordenadas, entra por el mismo sitio.
    """
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
                "located": (point["posX"], point["posY"]) != (0, 0),
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
    competition_id: Optional[int] = None,
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
        competition_id: `AcbClient.fetch_match_header(match["id"])["competitionId"]`, opcional -
            distingue Copa del Rey/Supercopa de "ACB" (ver `_competition_name`); sin él, cae
            en "ACB" por defecto (comportamiento previo a que existiera `fetch_match_header`).
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
                    # `efg_pct` excluye los tiros libres por definición, así que
                    # sin estas dos columnas no hay forma de leer el juego desde
                    # la línea de ningún jugador. `.get()` y no indexado: si un
                    # boxscore concreto no trae el campo, la columna queda en
                    # NULL (= "sin dato") en vez de tumbar la carga del partido.
                    "ftm": row.get("freeThrowsMade"),
                    "fta": row.get("freeThrowsAttempted"),
                    "starter": bool(row.get("isStarted")),
                    # Foto real de acb.com, de CUALQUIER jugador de la Liga
                    # Endesa (rival incluido) — verificado en vivo, 2026-08-27:
                    # el propio boxscore que ya se descarga por partido la
                    # trae, solo hacía falta leerla. `parse_and_resolve` la
                    # usa como HOTLINK (nunca se descarga a disco: el
                    # `robots.txt` de `static.acb.com` es `Disallow: /`,
                    # a diferencia de baskonia.com) y solo para rellenar un
                    # hueco, nunca para pisar la foto oficial de la plantilla
                    # propia (`ingest/baskonia_web`).
                    "photo_url": player.get("headshotImageUrl"),
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
        "competition": _competition_name(competition_id),
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


def build_scheduled_matchup(
    match: Dict[str, Any], teams_by_id: Dict[str, Dict[str, Any]], own_team_acb_id: str
) -> Optional[Dict[str, Any]]:
    """Convierte un partido de `AcbClient.fetch_season_scheduled_matches` en un registro
    listo para `upcoming_matchups`, o `None` si el Baskonia no juega ese partido.

    Esa lista trae TODOS los partidos programados de la competición (18 equipos,
    calendario completo), no solo los del Baskonia - de ahí el filtro. No hace la
    comprobación de competición vía `match_header` que sí hace `build_raw_game` para
    partidos ya jugados (ver historia de esa comprobación en `client.py`): esa
    contaminación (partidos de Copa del Rey/cantera etiquetados como ACB) se vio
    siempre en huecos de `weekId` de temporadas YA jugadas, reutilizados por otras
    competiciones - el calendario recién publicado de una temporada que aún no ha
    empezado es, por construcción de la competición (se publica de una vez, como
    liga regular de todos contra todos), solo Liga Endesa.
    """
    home_id = str(match["homeTeamId"])
    away_id = str(match["awayTeamId"])
    if own_team_acb_id not in (home_id, away_id):
        return None

    is_home = home_id == own_team_acb_id
    opponent_id = away_id if is_home else home_id
    opponent = teams_by_id.get(opponent_id, {"name": f"Equipo ACB {opponent_id}", "logo_url": None})
    start = match.get("startDateTime")

    return {
        "opponent_acb_id": opponent_id,
        "opponent_name": opponent["name"],
        "opponent_logo_url": opponent.get("logo_url"),
        "match_date": str(start)[:10] if start else None,
        "is_home": is_home,
    }

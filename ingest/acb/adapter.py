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

Tiros y tiempos muertos en `play_events` (2026-09-28): los códigos de tiro
de arriba (92/93/94/96/97/98/100) se emiten ADEMÁS como eventos tipados
(`ft_made`/`fg2_made`/`fg3_made`/`ft_missed`/`fg2_missed`/`fg3_missed`; el
mate 100 como `fg2_made` con `event_detail='dunk'`), y 113 = tiempo muerto
de EQUIPO (`timeout`, sin jugador, `local` dice de quién) — verificado en
vivo en el partido 104465 (7 tiempos muertos repartidos entre Q2 y Q4, todos
con `playerLicenseId=None`). `MatchShots/match-shots` trae en cada
`shotPoints[i]` el mismo `quarter`/`minute`/`second`/`scoreHome`/
`scoreAway` que el play-by-play (los 150 tiros de campo de ese partido casan
1 a 1 con su jugada por cuarto+reloj+tipo+jugador); hasta esta fecha se
descartaban. El marcador que trae es el de DESPUÉS del tiro (un 93 del local
a 0-0 llega con `scoreHome=2`).
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


def senior_acb_club_id(team: Dict[str, Any]) -> Optional[int]:
    """`clubId` estable de un equipo de ACB, o `None` si no es del primer equipo.

    VERIFICADO EN VIVO (2026-09-28, ediciones 89-91): todo objeto de equipo de
    la API -`teams[]` de `Competition/matches`, `teamBoxscores[].team` de
    `Result/boxscores` y `teams.home/away` de `MatchHeader/match-header`- trae
    `id` (cambia en CADA edición: Real Madrid 4239/4345/4407/4476), el nombre
    (cambia con el patrocinador) y `clubId`, que no cambia: Manresa es 10 como
    "BAXI Manresa" (4340 en 2024-25, 4414 en 2025-26) y como "Kids&Us Manresa"
    (4471 en 2026-27). Ver `ingest/common/identity.py::resolve_or_create_team`.

    Solo se devuelve para equipos de las competiciones que se cargan
    (`_COMPETITION_BY_ID`, según el `competitionId` DEL EQUIPO): los de cantera
    comparten el `clubId` del club (Liga U, `competitionId` 134: "Barça
    Atlètic" lleva el 2 del Barça, "Fundacion CB Canarias" el 28 de La Laguna
    Tenerife, "Unicaja Alhaurín de la Torre" el 14 de Unicaja), y fundirlos
    con el primer equipo sería justo el error que esto viene a evitar. Sin
    `competitionId` en el equipo (payload antiguo o de test) tampoco: sin él
    no se puede saber de qué equipo del club se trata.
    """
    club_id = team.get("clubId")
    if club_id is None or team.get("competitionId") not in _COMPETITION_BY_ID:
        return None
    try:
        return int(club_id)
    except (TypeError, ValueError):
        return None


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

# Play-by-play TIPADO (Fase 2, 2026-08-27): decodificado empíricamente
# cruzando cada `playType` con el delta de `playerStats` que trae ese mismo
# evento — verificado en vivo contra `PlayByPlay/play-by-play` real
# (partido 105370, temporada 2025-2026): el jugador de la jugada pasa de
# `steals=0` a `steals=1` en 103, `turnovers` en 106, `offRebounds` en 101,
# `defRebounds` en 104, `blocks` en 102, `foulsDrawn` en 110, y
# `personalFouls` en los seis códigos de `_FOUL_PERSONAL_PLAYTYPES`. Ver
# doc/features/ingestor/02_plan_stats_completas.md §Fase 2 para el detalle.
_EVENT_TYPE_BY_PLAYTYPE = {
    101: "oreb", 104: "dreb", 103: "steal", 106: "turnover", 102: "block",
    107: "assist", 108: "assist", 119: "assist", 110: "foul_drawn",
}
# Seis códigos de falta personal SIN semántica distinguible entre sí (no hay
# campo de descripción en el PBP que los separe) — se guarda el crudo en
# `event_detail`. No bloquea el conteo agregado: el total de faltas sigue
# viniendo de `personalFouls` del boxscore (`_team_totals`/`players` arriba),
# no de contar estas filas.
_FOUL_PERSONAL_PLAYTYPES = {161, 159, 160, 109, 537, 166}

# Tiros como eventos tipados (2026-09-28). Nombres EXACTOS del contrato de
# `play_events` que comparte con el cálculo de posesiones — ver el comentario
# de `play_events` en `schema.sql`.
_SHOT_EVENT_BY_PLAYTYPE = {
    92: ("ft_made", None), 96: ("ft_missed", None),
    93: ("fg2_made", None), 100: ("fg2_made", "dunk"), 97: ("fg2_missed", None),
    94: ("fg3_made", None), 98: ("fg3_missed", None),
}
# Tiempo muerto de EQUIPO (sin jugador; `local` dice qué equipo lo pide).
_TIMEOUT_PLAYTYPE = 113


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
        # Boxscore ampliado de EQUIPO (Fase 1, verificado en vivo 2026-08-27
        # contra `Result/boxscores` real: `stats.total` trae estos mismos
        # campos, además de los ya usados arriba). `.get()` y no indexado:
        # un `total` que no los traiga (fixture antiguo, o un hueco puntual
        # de la fuente) deja la columna en NULL en vez de tumbar la carga.
        "blk_against": total.get("receivedBlocks"),
        "pf": total.get("personalFouls"),
        "pf_drawn": total.get("foulsDrawn"),
        "plus_minus": total.get("plusMinus"),
        "pir": total.get("rating"),
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
        **_box_extras(own),
    }


# Boxscore ampliado de EQUIPO (Fase 1): común a los dos caminos (estimado y
# oficial), ya que en ninguno de los dos sale de la parte "avanzada" de la
# fuente — sale siempre de `_team_totals`, igual que `ftm`/`fta`.
def _box_extras(own: Dict[str, float]) -> Dict[str, Any]:
    return {
        "stl": own["stl"], "tov": own["tov"], "blk": own["blk"], "blk_against": own["blk_against"],
        "pf": own["pf"], "pf_drawn": own["pf_drawn"], "oreb": own["orb"], "dreb": own["drb"],
        "plus_minus": own["plus_minus"], "pir": own["pir"],
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
        **_box_extras(own),
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
                # Reloj y marcador del propio `shotPoints` (2026-09-28, ver el
                # docstring del módulo). `.get()`: un payload sin ellos deja
                # las columnas en NULL en vez de tumbar la carga. Las tres
                # banderas de contexto NO las da ACB: las deriva
                # `ingest/common/shot_context.py` del play-by-play tipado.
                "quarter": _quarter_label(point["quarter"]) if point.get("quarter") else None,
                "clock": _clock(point),
                "home_score": point.get("scoreHome"),
                "away_score": point.get("scoreAway"),
            }
        )
    return shots


def _clock(play: dict) -> Optional[str]:
    """`minute`/`second` de la fuente -> `'MM:SS'`; `None` si falta alguno."""
    minute, second = play.get("minute"), play.get("second")
    if minute is None or second is None:
        return None
    return f"{int(minute):02d}:{int(second):02d}"


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


def _quarter_player_stats(team_box: Dict[str, Any]) -> List[dict]:
    """Boxscore de jugador por cuarto (Fase 3, ACB-only): `statsByPeriods` trae
    exactamente la misma forma por cuarto que `_full_game_stats` para el total
    — verificado en vivo 2026-08-27 (`Result/boxscores`, mismo partido que el
    resto de esta fase)."""
    rows = []
    for period in team_box["statsByPeriods"]:
        quarter = period["quarter"]
        if quarter not in (1, 2, 3, 4):
            continue  # 0 = total del partido (ver `_full_game_stats`), 5+ = prórroga
        for row in period["stats"]["players"]:
            rows.append(
                {
                    "player_id": str(row["player"]["id"]),
                    "quarter": quarter,
                    "minutes": _parse_minutes(row.get("playTime")),
                    "pts": row.get("points"),
                    "reb": row.get("totalRebounds"),
                    "ast": row.get("assists"),
                    "stl": row.get("steals"),
                    "tov": row.get("turnovers"),
                    "blk": row.get("blocks"),
                    "pf": row.get("personalFouls"),
                    "oreb": row.get("offRebounds"),
                    "dreb": row.get("defRebounds"),
                    "ftm": row.get("freeThrowsMade"),
                    "fta": row.get("freeThrowsAttempted"),
                    "plus_minus": row.get("plusMinus"),
                    "pir": row.get("rating"),
                }
            )
    return rows


def _convert_player_advanced_stats(raw_by_player: Dict[str, Any]) -> List[dict]:
    """Convierte `{player_license: AdvancedStats/player-advanced-stats}` (Fase 4,
    ACB-only) al bloque `"player_advanced"` del contrato común. `["partido"]`
    porque esta fase mira el rendimiento EN ESE partido, no el acumulado de
    temporada/victorias/derrotas que el mismo endpoint también da — verificado
    en vivo 2026-08-27."""

    def _partido(block: Dict[str, Any], key: str) -> Optional[float]:
        value = (block or {}).get(key)
        return value.get("partido") if isinstance(value, dict) else None

    rows = []
    for player_id, data in raw_by_player.items():
        four_factors = data.get("fourFactors", {})
        rhythm = data.get("gameRhythm", {})
        ball = data.get("ballHandling", {})
        shooting = data.get("shooting", {})
        points = data.get("points", {})
        rebounds = data.get("rebounds", {})
        rows.append(
            {
                "player_id": player_id,
                "ast_ratio": _partido(ball, "astRatio"),
                "ast_pct": _partido(ball, "astPct"),
                "stl_ratio": _partido(ball, "stlRatio"),
                "stl_pct": _partido(ball, "stlPct"),
                "blk_pct": _partido(ball, "blkPct"),
                "tov_pct": _partido(ball, "tovPct") or _partido(four_factors, "tovPct"),
                "orb_pct": _partido(rebounds, "orbPct") or _partido(four_factors, "orbPct"),
                "drb_pct": _partido(rebounds, "drbPct"),
                "trb_pct": _partido(rebounds, "trbPct"),
                "ts_pct": _partido(shooting, "tsPct"),
                "three_par": _partido(shooting, "threePAr"),
                "ppt": _partido(shooting, "ppt"),
                "pp2ps": _partido(points, "pp2ps"),
                "pp3ps": _partido(points, "pp3ps"),
                "ppft": _partido(points, "ppft"),
                "possessions": _partido(rhythm, "possessions"),
                "pace": _partido(rhythm, "pace"),
            }
        )
    return rows


def _convert_play_events(plays: List[dict], home_id: str, away_id: str) -> List[dict]:
    """Eventos tipados (Fase 2): robos/pérdidas/tapones/rebotes ofensivo-defensivo/
    asistencias/faltas recibidas/faltas personales, con reloj y marcador; desde
    2026-09-28 también tiros (de campo y libres) y tiempos muertos de equipo."""
    events = []
    for play in sorted(plays, key=lambda p: p["order"]):
        play_type = play["playType"]
        if play_type in _FOUL_PERSONAL_PLAYTYPES:
            event_type, event_detail = "foul_personal", str(play_type)
        elif play_type in _EVENT_TYPE_BY_PLAYTYPE:
            event_type, event_detail = _EVENT_TYPE_BY_PLAYTYPE[play_type], None
        elif play_type in _SHOT_EVENT_BY_PLAYTYPE:
            event_type, event_detail = _SHOT_EVENT_BY_PLAYTYPE[play_type]
        elif play_type == _TIMEOUT_PLAYTYPE:
            event_type, event_detail = "timeout", None
        else:
            continue
        events.append(
            {
                "team_id": home_id if play["local"] else away_id,
                "player_id": str(play["playerLicenseId"]) if play.get("playerLicenseId") else None,
                "quarter": _quarter_label(play["quarter"]),
                "clock": f"{play['minute']:02d}:{play['second']:02d}",
                "event_type": event_type,
                "event_detail": event_detail,
                "home_score": play["scoreHome"],
                "away_score": play["scoreAway"],
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
    competition_id: Optional[int] = None, player_advanced_stats: Optional[Dict[str, Any]] = None,
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
        player_advanced_stats: `{player_license: AdvancedStats/player-advanced-stats}` (Fase 4,
            ACB-only), opcional - ya resuelto por el llamante (`AcbClient.fetch_game`, acotado
            a un subconjunto de jugadores por coste, ver ese método) antes de llegar aquí; sin
            él, `player_advanced` queda vacío.
    """
    if not boxscore.get("matchFinished"):
        raise ValueError(f"AcbClient: boxscore del partido {match['id']} no está finalizado todavía")

    team_boxscores = boxscore["teamBoxscores"]
    home_box = next((tb for tb in team_boxscores if tb["team"]["id"] == match["homeTeamId"]), team_boxscores[0])
    away_box = next((tb for tb in team_boxscores if tb["team"]["id"] == match["awayTeamId"]), team_boxscores[1])

    home_team = {"id": str(home_box["team"]["id"]), "name": home_box["team"]["fullName"]}
    away_team = {"id": str(away_box["team"]["id"]), "name": away_box["team"]["fullName"]}
    # `acb_club_id` solo si lo hay (ver `senior_acb_club_id`): el contrato común
    # (`ingest/common/raw_game.py`) no lo exige y las demás fuentes no lo traen.
    for team, box in ((home_team, home_box), (away_team, away_box)):
        club_id = senior_acb_club_id(box["team"])
        if club_id is not None:
            team["acb_club_id"] = club_id

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
                    # Boxscore ampliado de JUGADOR (Fase 1, verificado en vivo
                    # 2026-08-27 contra `Result/boxscores` real). `dunks` es
                    # ACB-only (el propio `.get()` deja `None` si algún día
                    # faltara, no rompe la carga).
                    "stl": row.get("steals"),
                    "tov": row.get("turnovers"),
                    "blk": row.get("blocks"),
                    "blk_against": row.get("receivedBlocks"),
                    "pf": row.get("personalFouls"),
                    "pf_drawn": row.get("foulsDrawn"),
                    "oreb": row.get("offRebounds"),
                    "dreb": row.get("defRebounds"),
                    "plus_minus": row.get("plusMinus"),
                    "pir": row.get("rating"),
                    "dunks": row.get("dunks"),
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
    play_events = _convert_play_events(plays, home_team["id"], away_team["id"])

    quarter_boxscore = _quarter_player_stats(home_box) + _quarter_player_stats(away_box)
    player_advanced = _convert_player_advanced_stats(player_advanced_stats) if player_advanced_stats else []

    # Metadata de partido (Fase 3): top-level de `Result/boxscores` (arena/
    # asistencia/árbitros) + `headCoach` por equipo en `teamBoxscores` — ya
    # viaja en la misma respuesta que se descarga hoy, verificado en vivo
    # 2026-08-27. `referees` es una lista de nombres; se guarda unida por
    # " · " (ver `games.referees` en `schema.sql`).
    referees = boxscore.get("referees")

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
        "play_events": play_events,
        "arena": boxscore.get("arena"),
        "attendance": boxscore.get("attendance"),
        "referees": " · ".join(referees) if referees else None,
        "home_coach": home_box.get("headCoach"),
        "away_coach": away_box.get("headCoach"),
        "quarter_boxscore": quarter_boxscore,
        "player_advanced": player_advanced,
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
        "opponent_acb_club_id": opponent.get("acb_club_id"),
        "match_date": str(start)[:10] if start else None,
        "is_home": is_home,
    }

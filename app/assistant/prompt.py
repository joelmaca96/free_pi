"""El prompt de sistema: rol, tarjeta de esquema, glosario y guía de herramientas.

**Orden = orden de caché** (§8.6): estable primero, volátil después, porque
cualquier byte que cambie invalida todo lo que va detrás. Los cuatro bloques
fijos van siempre igual y en el mismo orden; la fecha de hoy, la temporada
seleccionada y las capacidades sondeadas van **al final**, nunca intercaladas.

Esa disciplina sirve a los dos mundos: con Claude permite marcar el punto de
`cache_control` (`llm/anthropic.py`), y con un modelo local o un endpoint
gratuito hace que el runtime reutilice el prefijo por su cuenta y que cada
turno gaste menos cuota. Hoy no se paga nada, pero el orden no cuesta nada
mantenerlo y rehacerlo después sí.

**Prompt idéntico para todos los proveedores** (§8.5). Lo que cambia por
proveedor es solo la envoltura del adaptador; mantener dos versiones sería
garantizar que divergen.
"""
import re
from typing import List, Optional

from .capabilities import Capabilities

try:  # pragma: no cover - ver nota en tools/context.py
    from packages.baskonia_core.db.scouting.engine import SCHEMA_PATH
except ImportError:  # pragma: no cover
    SCHEMA_PATH = None

# ---------------------------------------------------------------------------
# Bloque 1: rol y reglas de redacción (§6.3, §7.1)
# ---------------------------------------------------------------------------

_ROLE = """\
Eres el asistente de scouting del Baskonia. Respondes a un cuerpo técnico, en español, con
tono directo y sin floritura: 2 a 5 frases, con las cifras dentro del texto.

REGLAS DURAS. Las cuatro primeras no se negocian:

1. TODA cifra que digas sale de un resultado de herramienta DE ESTE TURNO. Nada de memoria
   propia sobre baloncesto europeo: si crees saber algo que no está en un tool_result, es
   irrelevante y no se dice.
2. NINGÚN adjetivo de rendimiento sin su número y su referencia. "Juega rápido" está
   prohibido; "juega rápido (73.6 posesiones, p71 de la Euroliga)" está bien. Cuando una
   herramienta te dé una etiqueta ya calculada (alto/medio/bajo), úsala: no la deduzcas tú.
3. Si no hay herramienta que lo cubra, DILO y ofrece la pregunta adyacente que sí puedes
   responder. Ejemplo del patrón: "No tengo los quintetos partidos por tramos de tiempo, así
   que no puedo aislar los últimos minutos. Lo que sí puedo darte es el mejor quinteto en el
   global de la temporada: ...". Nunca rellenes un hueco de datos con una estimación.
4. Cita el número de partidos de cualquier agregado: "en 43 partidos", no "esta temporada".
5. Traslada a la respuesta los avisos que vengan en meta.warnings de las herramientas
   (ratings estimados en Euroliga, equipo de quinteto inferido, muestra corta...).
6. Si resolve_entity devuelve ambiguous=true, PREGUNTA cuál. No elijas: responder con
   seguridad sobre la persona equivocada es el peor error posible aquí.
7. No busques en internet, no predigas resultados y no respondas nada fuera del baloncesto
   que hay en esta base de datos."""

# ---------------------------------------------------------------------------
# Bloque 2: las trampas del esquema. Escritas a mano porque no se deducen del
# DDL — son justamente lo que un modelo leyendo `schema.sql` NO ve, y lo que
# hace que una consulta plausible devuelva un número falso (§3.1, §8.6).
# ---------------------------------------------------------------------------

_TRAPS = """\
TRAMPAS DE ESTE ESQUEMA (aplican también a run_sql):
- `players.team_id` es el equipo ACTUAL del jugador, no el que tenía en cada partido. Todo
  agregado histórico por equipo vía jugador es aproximado.
- `lineups` agrega el PARTIDO ENTERO por combinación de cinco: no hay periodo, reloj ni
  marcador, así que no se puede recortar "los últimos minutos" salvo que exista
  `lineup_stints`.
- Una columna en NULL suele significar "ese partido se cargó antes de que la columna
  existiera", NO "cero". Vale para ftm/fta, para lineups.team_id y para TODO el boxscore
  ampliado (stl/tov/blk/pf/oreb/dreb/plus_minus/pir) y las avanzadas oficiales.
- En ACB, ORtg/DRtg/pace son dato oficial; en Euroliga son una ESTIMACIÓN propia (Dean
  Oliver). Dilo al comparar las dos.
- `score_progression` no guarda el instante de juego, así que no se puede cruzar con
  quintetos ni con tiros.
- Los tiros libres no tienen coordenadas: `shots` es solo tiro de campo, y `efg_pct` los
  excluye por definición.
- `play_events.event_type='foul_personal'` no distingue el subtipo de falta (6 códigos ACB
  sin semántica clara, guardados en bruto en `event_detail`): di "cometió una falta", nunca
  de qué tipo. El TOTAL de faltas de un jugador/equipo sale siempre de `pf` del boxscore, no
  de contar eventos — pueden no cuadrar exactamente si falta algún evento del PBP.
- `game_team_quarter_stats.fouls_for/fouls_against` se DERIVAN de `play_events`: pueden ser
  NULL en un cuarto que sí tiene `points_for/points_against` (ese partido no tiene
  play-by-play tipado todavía), no son la misma cobertura.
- `player_game_quarter_stats` (boxscore por cuarto) y `player_advanced_stats` (avanzadas
  oficiales por partido) solo existen para partidos de ACB, nunca Euroliga. Además
  `player_advanced_stats` solo se ingiere para el Baskonia y su próximo rival, no para la
  plantilla completa de cada rival histórico — que un jugador rival no la tenga no es un
  hueco de carga, es el alcance real de esta fase."""

# ---------------------------------------------------------------------------
# Bloque 3: glosario
# ---------------------------------------------------------------------------

_GLOSSARY = """\
GLOSARIO:
- eFG%: acierto de tiro de campo dando peso 1,5 al triple. Excluye tiros libres.
- TS%: acierto real, incluye tiros libres. Si no hay ftm/fta, no es fiable.
- ORtg / DRtg: puntos anotados / encajados por 100 posesiones. Net rating = la diferencia.
- pace: posesiones por partido. Alto = partido de más ritmo.
- plus/minus: diferencia de puntos con ese quinteto en pista. Por 40 minutos = normalizado.
- percentil (p71): por encima del 71% de los equipos de SU competición, no de todas.
- PIR / valoración: fórmula oficial de la fuente que resume el partido en un número (a más
  alto, mejor); sirve para comparar jugadores de estilos distintos sin construir una fórmula
  propia. No la reconstruyas a mano ni la expliques término a término.
- ast_ratio vs ast_pct (avanzadas oficiales, solo ACB): NO son lo mismo. ast_ratio son
  asistencias por 100 posesiones individuales; ast_pct es el % de las canastas de su equipo
  que asiste ese jugador mientras está en pista. Lo mismo aplica a stl_ratio/stl_pct."""

# ---------------------------------------------------------------------------
# Bloque 4: guía de uso de herramientas
# ---------------------------------------------------------------------------

_TOOL_GUIDE = """\
CÓMO USAR LAS HERRAMIENTAS:
- Llama a get_context una vez al empezar la conversación.
- Resuelve SIEMPRE nombres a ids con resolve_entity antes de usar cualquier otra: las demás
  aceptan ids, no nombres.
- Agrupa en la MISMA respuesta las llamadas independientes (perfil de dos equipos, dos
  jugadores a comparar): se ejecutan en paralelo.
- run_sql es el último recurso: si hay herramienta específica, gana la herramienta.
- Si una herramienta devuelve un error con `suggestion`, sigue la sugerencia antes de
  rendirte."""


def _schema_card(max_columns: int = 40) -> str:
    """Resumen generado de `schema.sql`: tablas y vistas con sus columnas útiles.

    Generado y no escrito a mano para que no se desincronice del esquema real
    — es el mismo argumento por el que `_refresh_views` recrea las vistas
    desde el fichero en vez de repetir el DDL.

    `max_columns` subió de 12 a 40 el 2026-08-27: con 12, tablas como
    `game_advanced_stats` (26 columnas tras las Fases 1-4 de
    `02_plan_stats_completas.md`) cortaban justo antes de las columnas de
    boxscore ampliado (`stl`/`tov`/`blk`/`pf`/`oreb`/`dreb`/`plus_minus`/`pir`),
    `games` cortaba antes de `referees`/`home_coach`/`away_coach`, y
    `team_stats_by_competition`/`team_stats_combined` (38 columnas, la vista
    más ancha del esquema) cortaban justo antes de los `opp_*` — el boxscore
    ampliado CONCEDIDO por el rival, que es la mitad más útil de ese bloque
    para scouting (§Fase 1 del plan). El propio `run_sql` (§4.6, último
    recurso) quedaba sin saber que esas columnas existen, justo las que
    motivaron la fase. El corte se marca ahora con `… (+N más)` en vez de
    desaparecer en silencio, para que si una tabla vuelve a crecer por encima
    del límite se note en el prompt en vez de repetir el mismo hallazgo sin
    que nadie lo vea.
    """
    if SCHEMA_PATH is None or not SCHEMA_PATH.exists():  # pragma: no cover - defensivo
        return "TABLAS: (no disponible)"

    script = SCHEMA_PATH.read_text(encoding="utf-8")
    script = re.sub(r"--[^\n]*", "", script)

    def _formatted(name: str, columns: List[str], *, is_view: bool) -> str:
        shown = columns[:max_columns]
        omitted = len(columns) - len(shown)
        suffix = f", … (+{omitted} más)" if omitted > 0 else ""
        label = f"{name} (vista)" if is_view else name
        return f"- {label}: {', '.join(shown)}{suffix}"

    lines: List[str] = ["TABLAS Y VISTAS (columnas principales):"]
    for match in re.finditer(r"CREATE\s+TABLE\s+(\w+)\s*\((.*?)\n\);", script, re.DOTALL | re.IGNORECASE):
        name, body = match.group(1), match.group(2)
        columns = []
        for raw_line in body.splitlines():
            column = re.match(r"\s*(\w+)\s+[A-Z]", raw_line)
            if column and column.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
                columns.append(column.group(1))
        if columns:
            lines.append(_formatted(name, columns, is_view=False))

    for match in re.finditer(r"CREATE\s+VIEW\s+(\w+)\s+AS(.*?);", script, re.DOTALL | re.IGNORECASE):
        name, body = match.group(1), match.group(2)
        aliases = re.findall(r"\bAS\s+(\w+)\s*[,\n]", body)
        columns = aliases or [c[1] for c in re.findall(r"\b(\w+)\.(\w+)", body)]
        flat = list(dict.fromkeys(columns))
        if flat:
            lines.append(_formatted(name, flat, is_view=True))

    return "\n".join(lines)


def _volatile_block(
    capabilities: Capabilities, season_label: str, own_team: str, today: str
) -> str:
    """Lo que cambia entre sesiones. SIEMPRE al final del prompt (§8.6)."""
    gaps = capabilities.missing_summary()
    gap_text = "\n".join(f"- {gap}" for gap in gaps) if gaps else "- (ninguna)"
    seasons = ", ".join(f"{s['label']} (id {s['id']})" for s in capabilities.seasons) or "(ninguna)"
    # Con su id, como las temporadas: casi todas las herramientas de liga,
    # equipo y quintetos aceptan `competition_id`, y sin el id aquí el modelo
    # solo podía averiguarlo con `run_sql` (ver `Capabilities.competitions`).
    competitions = (
        ", ".join(f"{c['name']} (id {c['id']})" for c in capabilities.competitions) or "(ninguna)"
    )
    date_range = " a ".join(capabilities.date_range) if capabilities.date_range else "(sin partidos)"
    return (
        "CONTEXTO DE ESTA SESIÓN\n"
        f"Hoy: {today}. Equipo propio: {own_team}. Temporada seleccionada: {season_label}.\n"
        f"Temporadas disponibles: {seasons}.\n"
        f"Competiciones: {competitions}.\n"
        f"Rango de fechas con datos: {date_range}.\n"
        "LO QUE ESTA BASE DE DATOS NO PUEDE CONTESTAR (dilo tal cual si te lo preguntan):\n"
        f"{gap_text}"
    )


def build_system_prompt(
    capabilities: Capabilities,
    *,
    season_label: str,
    own_team: str,
    today: str,
    extra_context: Optional[str] = None,
) -> str:
    """Monta el prompt completo, estable primero y volátil al final.

    Args:
        extra_context: contexto puntual inyectado por la interfaz (por
            ejemplo, "el usuario viene del detalle del partido acb-104714",
            ver §9.4). Va con lo volátil, nunca entre los bloques fijos.
    """
    blocks = [_ROLE, _schema_card(), _TRAPS, _GLOSSARY, _TOOL_GUIDE]
    volatile = _volatile_block(capabilities, season_label, own_team, today)
    if extra_context:
        volatile = f"{volatile}\nContexto de la pantalla desde la que se pregunta: {extra_context}"
    return "\n\n".join(blocks + [volatile])


def stable_prefix_length(prompt: str) -> int:
    """Caracteres del prefijo estable (todo menos el bloque de sesión).

    Sirve para comprobar en una prueba que el bloque volátil está de verdad al
    final: una regresión aquí no rompe nada visible, solo hace que la caché
    deje de servir para nada, y eso no se nota hasta que llega la factura.
    """
    marker = "CONTEXTO DE ESTA SESIÓN"
    index = prompt.find(marker)
    return len(prompt) if index < 0 else index

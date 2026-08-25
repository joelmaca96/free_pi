"""`run_sql`: el escape para la cola larga (§4.6). Último recurso, y se dice.

Las ~25 herramientas del catálogo cubren el grueso de lo que se le pregunta a
esta base de datos. Lo que queda —"¿cuántos partidos ha ganado el Baskonia
remontando un -10 al descanso?"— o se contesta con SQL o no se contesta.

Dos decisiones sobre esta herramienta que están en la descripción que ve el
modelo, no solo aquí:

- **Es el último recurso.** Si hay herramienta que lo cubra, la herramienta
  gana: sus cifras salen de SQL fijo y probado, las de aquí no.
- **Las trampas semánticas del esquema siguen siendo suyas.** `lineups` no
  tiene equipo de forma fiable, `players.team_id` es el equipo actual, una
  columna en `NULL` significa "ese partido es anterior a la columna" y no
  "cero", y los ratings de Euroliga son estimados. Un modelo escribiendo SQL
  libre las pisa todas y devuelve un número plausible y falso — que en
  scouting es peor que un "no lo sé" (§3.1).

Toda invocación se registra junto a la pregunta que la originó (ver
`app/assistant/agent.py`): ese registro es la lista priorizada de qué
herramienta propia falta escribir, y si resulta que casi nunca acierta, es el
argumento para retirar la herramienta del producto (§15, decisión abierta 4).
"""
from .. import sql_guard
from .base import ToolContext, artifact, fail, ok, register, schema


@register(
    "run_sql",
    family="sql",
    description=(
        "ÚLTIMO RECURSO: una consulta SELECT de solo lectura sobre la base de datos, para lo que "
        "ninguna otra herramienta cubre. Si existe una herramienta específica, úsala en su lugar. "
        "Ojo con las trampas del esquema: players.team_id es el equipo ACTUAL del jugador, una "
        "columna NULL significa 'partido anterior a esa columna' y no 'cero', y en Euroliga "
        "ORtg/DRtg/pace son estimados. Máximo 200 filas."
    ),
    parameters=schema(
        {
            "sql": {"type": "string", "description": "Una sola sentencia SELECT o WITH."},
            "purpose": {
                "type": "string",
                "description": "Qué pretendes averiguar, en una frase. Se registra para saber qué herramienta falta.",
            },
        },
        required=["sql", "purpose"],
    ),
    artifact="table",
)
def run_sql(ctx: ToolContext, sql: str, purpose: str) -> dict:
    """Ejecuta la consulta bajo las guardas de `sql_guard` (§11.2)."""
    try:
        result = sql_guard.run(sql, ctx.engine)
    except sql_guard.SQLGuardError as exc:
        return fail(
            "consulta rechazada",
            detail=str(exc),
            suggestion="Reescríbela cumpliendo la regla, o usa una herramienta específica del catálogo.",
        )

    rows = [dict(zip(result["columns"], row)) for row in result["rows"]]
    warnings = []
    if result["limit_added"]:
        warnings.append(f"Se ha añadido LIMIT {sql_guard.MAX_ROWS}: puede haber más filas de las que ves.")
    if result["row_count"] >= sql_guard.MAX_ROWS:
        warnings.append("El resultado llega al tope de filas: no lo presentes como si fuera el total.")

    return ok(
        rows,
        source=", ".join(result["sources"]) or "consulta libre",
        scope=purpose,
        warnings=warnings or None,
        artifact=artifact("table", rows, title="Consulta"),
    )

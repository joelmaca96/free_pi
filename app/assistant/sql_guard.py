"""Guardas de `run_sql`: lo único del sistema donde el modelo escribe SQL.

El resto del asistente funciona con SQL fijo y probado (§3.1). Esta pieza
existe solo para la cola larga —lo que no cubre ninguna herramienta— y por
tanto es la única superficie donde una consulta puede ser cualquier cosa.
Las guardas son **acumulativas, no alternativas** (§11.2): cada una tapa un
agujero distinto y ninguna sustituye a otra.

1. Una sola sentencia.
2. Tiene que empezar por `SELECT` o `WITH`.
3. Lista negra de tokens (`ATTACH`, `PRAGMA`, DDL, DML, `load_extension`...).
4. Lista blanca de tablas y vistas: solo las que existen de verdad en ESTA
   base de datos.
5. `LIMIT` forzado si no lo trae.
6. Manejador de progreso que aborta por encima de ~2 segundos.
7. Conexión de solo lectura de verdad (`mode=ro` + `PRAGMA query_only`).

Y el resultado se trunca antes de entrar en el contexto: un `SELECT *` sobre
`shots` son decenas de miles de filas.

La app **nunca** escribe (§11.1) y en la Raspberry Pi el volumen está montado
`:ro`, así que una escritura desde aquí fallaría en producción y funcionaría
en desarrollo — la peor combinación posible. Por eso la conexión de solo
lectura es explícita y no confiada al montaje.
"""
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

#: Tope de filas que se devuelven (y que, por tanto, pueden entrar en el
#: contexto del modelo).
MAX_ROWS = 200

#: Segundos de reloj tras los cuales la consulta se aborta. Una consulta de
#: scouting sobre esta base de datos tarda milisegundos; pasar de dos
#: segundos significa que barre algo que no debería.
TIMEOUT_SECONDS = 2.0

#: Cada cuántas instrucciones de la máquina virtual de SQLite se comprueba el
#: reloj. Suficientemente pequeño para cortar a tiempo, suficientemente
#: grande para no penalizar una consulta normal.
_PROGRESS_STEP = 10_000

# Palabras que no pueden aparecer en ninguna posición. `PRAGMA` y `ATTACH`
# están aquí aunque la conexión sea de solo lectura: `ATTACH` de una base de
# datos escribible sortearía el `mode=ro`, y varios `PRAGMA` cambian el
# comportamiento del motor sin ser una escritura.
_FORBIDDEN = (
    "attach", "detach", "pragma", "insert", "update", "delete", "drop", "create",
    "alter", "replace", "vacuum", "reindex", "analyze", "begin", "commit",
    "rollback", "load_extension", "writefile", "readfile", "edit",
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
# Tablas/vistas citadas: lo que sigue a FROM o a JOIN.
_SOURCE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z_0-9]*)", re.IGNORECASE)
# Alias de CTE: `WITH x AS (...)`, `, y AS (...)`. No son tablas reales y no
# deben exigirse en la lista blanca (ni colarse como si lo fueran).
_CTE_RE = re.compile(r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z_0-9]*)\s+as\s*\(", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)


class SQLGuardError(ValueError):
    """Consulta rechazada por una guarda. El mensaje se le devuelve al modelo tal cual."""


@dataclass
class GuardedQuery:
    """Consulta ya validada y lista para ejecutar."""

    sql: str
    sources: List[str]
    limit_added: bool


def _strip_comments(sql: str) -> str:
    """Quita comentarios `--` y `/* */`.

    Sin esto, `SELECT 1 --\\nDROP TABLE x` pasa la comprobación de tokens con
    el DDL escondido en un comentario que SQLite sí ignora pero que confunde
    a cualquier análisis posterior.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


def allowed_sources(engine: Engine) -> set:
    """Tablas y vistas que existen de verdad en esta base de datos.

    Se sondea, no se lee de `schema.sql`, por el mismo motivo que
    `capabilities.py`: lo que promete el DDL y lo que hay desplegado no
    siempre coinciden.
    """
    inspector = inspect(engine)
    return set(inspector.get_table_names()) | set(inspector.get_view_names())


def validate(sql: str, engine: Engine) -> GuardedQuery:
    """Aplica las siete guardas y devuelve la consulta lista para ejecutar.

    Raises:
        SQLGuardError: con un mensaje que explica QUÉ guarda saltó — el
            modelo lo recibe como resultado de herramienta y puede corregir
            (§7.4), que es más útil que un rechazo genérico.
    """
    if not sql or not sql.strip():
        raise SQLGuardError("La consulta está vacía.")

    cleaned = _strip_comments(sql).strip().rstrip(";").strip()

    # (1) Una sola sentencia. Se comprueba DESPUÉS de quitar el `;` final,
    # así que un punto y coma que sobre en medio es un intento de encadenar.
    if ";" in cleaned:
        raise SQLGuardError("Solo se admite UNA sentencia: quita el punto y coma y lo que venga detrás.")

    # (2) Solo lectura por la forma de la consulta.
    first_word = (_TOKEN_RE.match(cleaned) or _TOKEN_RE.search(cleaned or "x"))
    if first_word is None or first_word.group(0).lower() not in ("select", "with"):
        raise SQLGuardError("La consulta tiene que empezar por SELECT o WITH.")

    # (3) Lista negra de tokens.
    tokens = {t.lower() for t in _TOKEN_RE.findall(cleaned)}
    forbidden = sorted(tokens & set(_FORBIDDEN))
    if forbidden:
        raise SQLGuardError(f"Palabras no permitidas en la consulta: {', '.join(forbidden)}.")

    # (4) Lista blanca de tablas y vistas.
    ctes = {name.lower() for name in _CTE_RE.findall(cleaned)}
    sources = [name for name in _SOURCE_RE.findall(cleaned) if name.lower() not in ctes]
    allowed = {name.lower() for name in allowed_sources(engine)}
    unknown = sorted({name for name in sources if name.lower() not in allowed})
    if unknown:
        raise SQLGuardError(
            f"Estas tablas o vistas no existen en esta base de datos: {', '.join(unknown)}."
        )

    # (5) LIMIT forzado.
    limit_added = _LIMIT_RE.search(cleaned) is None
    final_sql = f"{cleaned} LIMIT {MAX_ROWS}" if limit_added else cleaned

    return GuardedQuery(sql=final_sql, sources=sorted(set(sources)), limit_added=limit_added)


def _open_readonly(engine: Engine) -> Tuple[sqlite3.Connection, Optional[object]]:
    """Conexión de solo lectura sobre la misma base de datos que el engine.

    Con un fichero real se abre una conexión propia con `mode=ro`: es una
    garantía del sistema operativo/SQLite, no una promesa del código.

    Con `:memory:` (los tests, y solo los tests) no hay fichero que abrir y
    una conexión nueva sería otra base de datos vacía, así que se reutiliza
    la del engine con `PRAGMA query_only=ON` y se restaura al terminar. Es
    una guarda más débil —de ahí que sea el camino secundario— pero cubre lo
    que el camino real no puede cubrir en pruebas.

    Returns:
        `(conexión sqlite3, conexión-a-devolver-al-pool o None)`.
    """
    database = engine.url.database
    if database and database != ":memory:":
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        return connection, None

    pooled = engine.raw_connection()
    raw = pooled.driver_connection
    raw.execute("PRAGMA query_only=ON")
    return raw, pooled


def run(sql: str, engine: Engine) -> dict:
    """Valida y ejecuta una consulta de solo lectura, acotada en filas y en tiempo.

    Returns:
        `{'columns': [...], 'rows': [[...]], 'row_count': n, 'limit_added':
        bool, 'sources': [...]}`.

    Raises:
        SQLGuardError: consulta rechazada, abortada por tiempo, o error de
            SQLite (sintaxis, columna inexistente). Los tres se le cuentan al
            modelo con la misma forma para que pueda corregir.
    """
    guarded = validate(sql, engine)
    connection, pooled = _open_readonly(engine)
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def _abort_if_slow() -> int:
        # Devolver distinto de cero aborta la consulta en curso. Es la única
        # forma de cortar un barrido en SQLite: no hay `statement_timeout`.
        return 1 if time.monotonic() > deadline else 0

    try:
        connection.set_progress_handler(_abort_if_slow, _PROGRESS_STEP)
        cursor = connection.execute(guarded.sql)
        columns = [description[0] for description in cursor.description or []]
        rows = cursor.fetchmany(MAX_ROWS)
    except sqlite3.OperationalError as exc:
        if time.monotonic() > deadline:
            raise SQLGuardError(
                f"Consulta abortada por tardar más de {TIMEOUT_SECONDS:g} s. "
                "Acótala (filtra por temporada o equipo) o usa una herramienta específica."
            ) from exc
        raise SQLGuardError(f"SQLite rechazó la consulta: {exc}") from exc
    except sqlite3.Error as exc:
        raise SQLGuardError(f"SQLite rechazó la consulta: {exc}") from exc
    finally:
        connection.set_progress_handler(None, 0)
        if pooled is None:
            connection.close()
        else:
            connection.execute("PRAGMA query_only=OFF")
            pooled.close()

    return {
        "columns": columns,
        "rows": [list(row) for row in rows],
        "row_count": len(rows),
        "limit_added": guarded.limit_added,
        "sources": guarded.sources,
    }

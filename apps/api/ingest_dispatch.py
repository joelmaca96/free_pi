"""Frontera controlada entre `apps/api` e `ingest/`: despacho por fuente.

**Único** fichero de `apps/api/` autorizado a importar módulos de `ingest/`
(ver `local/features/014-auto-fetch-datos-faltantes/01_design.md`, Decisión 1,
aprobada en gate humano): el refresco de un partido y el descubrimiento de
partidos ausentes se ejecutan con `fastapi.BackgroundTasks`/una ruta síncrona
**en el mismo proceso** de la API, así que la API necesita los dos entrypoints
de orquestación de ingesta.

La excepción se concentra aquí, en vez de esparcirla por
`routers/refresh.py`/`routers/discovery.py`, para que sea auditable en un solo
sitio: `tests/test_architecture.py::test_api_does_not_import_requests_or_ingest`
comprueba por nombre completo de módulo que ningún otro fichero de `apps/api/`
importa nada de `ingest/`. Por eso los imports son `import ingest.acb.pipeline`
(no `from ingest.acb import pipeline`): así el nombre importado es inequívoco
para esa comprobación por AST.

Los dos módulos exponen el mismo contrato (ver `01_design.md` §2):
    `run_single_game(engine, season, external_id, client=None) -> {"loaded": [...], "failed": [...]}`
    `discover_missing_games(engine, season, client=None) -> list[str|int]`
"""
import ingest.acb.pipeline
import ingest.euroleague.pipeline

# Prefijo de `games.id` (`f"{source}-{external_id}"`) -> módulo de pipeline.
PIPELINE_MODULES = {
    "acb": ingest.acb.pipeline,
    "euroleague": ingest.euroleague.pipeline,
}

SUPPORTED_SOURCES = frozenset(PIPELINE_MODULES)


def coerce_external_id(source: str, external_id: str):
    """Convierte el id externo al tipo que espera el pipeline de cada fuente.

    Euroliga identifica el partido con un `game_code` entero (columna `game` del
    calendario); ACB usa el `matchId` como cadena.

    Args:
        source: prefijo de fuente (`"acb"`, `"euroleague"`).
        external_id: id externo tal como viene en `games.id`.

    Returns:
        `int` para Euroliga, `str` para el resto de fuentes soportadas.
    """
    return int(external_id) if source == "euroleague" else external_id

"""Registro de herramientas: qué ve el modelo y cómo se ejecuta lo que pide.

**El catálogo no se recorta** (§8.4). Un perfil reducido era la salida fácil
para modelos flojos, pero recorta capacidad justo donde está el valor del
encargo ("cualquier tipo de pregunta") y deja al asistente respondiendo peor
por una limitación que ni el usuario ve ni puede corregir. Si un modelo no
aguanta el catálogo entero, el que sobra es el modelo.

Lo que sí se optimiza es cuánto ocupa y cuán fácil es elegir:

- Esquemas compactos: una línea por herramienta, parámetros mínimos. El
  catálogo completo cabe en ~1,5-2k tokens.
- Nombres con familia (`player_*`, `team_*`, `league_*`, `lineup_*`): reduce
  la elección a dos pasos, familia y luego corte.
- Descripciones que dicen **cuándo NO** usar la herramienta. Evita más
  errores de elección que cualquier reducción de catálogo.
- `ASSISTANT_TOOL_LOADING=by_family` **pagina** el catálogo si la evaluación
  demuestra que el completo confunde al modelo local: se exponen las
  herramientas de contexto más `load_tools`, y al pedir una familia se cargan
  sus esquemas. Ninguna herramienta desaparece del sistema — cambia cuándo se
  ve el esquema, no qué se puede preguntar.
"""
import inspect as _inspect
import json
import logging
import os
from typing import Any, Dict, List, Optional

from ..llm.base import ToolSpec
from . import context as _context  # noqa: F401  (importar registra sus herramientas)
from . import league as _league  # noqa: F401
from . import lineups as _lineups  # noqa: F401
from . import player as _player  # noqa: F401
from . import sql_escape as _sql_escape  # noqa: F401
from . import team as _team  # noqa: F401
from .base import MAX_ROWS, REGISTRY, Tool, ToolContext, ToolInvocation, fail

logger = logging.getLogger(__name__)

__all__ = ["MAX_ROWS", "REGISTRY", "Tool", "ToolCatalog", "ToolContext", "ToolInvocation"]

#: Familias que siempre están cargadas, incluso en modo `by_family`: sin
#: resolver no se puede llamar a nada más.
_ALWAYS_LOADED = ("context",)

_LOAD_TOOLS = "load_tools"


def available_tools(ctx: ToolContext) -> List[Tool]:
    """Herramientas que esta base de datos puede sostener de verdad.

    Una herramienta con `requires` cuya capacidad esté apagada NO se registra
    (§4.4): que exista y falle siempre es peor que su ausencia, porque el
    modelo insiste en vez de decir que no puede.
    """
    capabilities = ctx.capabilities.to_dict()
    return [
        tool
        for tool in REGISTRY.values()
        if tool.requires is None or capabilities.get(tool.requires)
    ]


class ToolCatalog:
    """El catálogo de un turno: qué esquemas se enseñan y cómo se ejecutan.

    Args:
        ctx: contexto del turno (engine, temporada, capacidades...).
        loading: `'eager'` (todo el catálogo, por defecto) o `'by_family'`
            (paginado, ver docstring del módulo). Sale de
            `ASSISTANT_TOOL_LOADING`.
    """

    def __init__(self, ctx: ToolContext, loading: Optional[str] = None):
        self.ctx = ctx
        self.loading = (loading or os.getenv("ASSISTANT_TOOL_LOADING", "eager")).strip() or "eager"
        self.tools = {tool.name: tool for tool in available_tools(ctx)}
        self.families = sorted({tool.family for tool in self.tools.values()})
        self._loaded_families = set(_ALWAYS_LOADED) if self.loading == "by_family" else set(self.families)

    # ---------------------------------------------------------------- specs --

    def specs(self) -> List[ToolSpec]:
        """Esquemas que se le pasan al modelo en la petición de este turno."""
        visible = [
            ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)
            for tool in self.tools.values()
            if tool.family in self._loaded_families
        ]
        if self.loading == "by_family":
            visible.append(self._load_tools_spec())
        return visible

    def _load_tools_spec(self) -> ToolSpec:
        pending = [f for f in self.families if f not in self._loaded_families]
        catalogue = ", ".join(
            f"{family} ({sum(1 for t in self.tools.values() if t.family == family)})"
            for family in self.families
        )
        return ToolSpec(
            name=_LOAD_TOOLS,
            description=(
                "Carga las herramientas de una familia para poder usarlas. Familias: "
                f"{catalogue}. Pendientes de cargar: {', '.join(pending) or 'ninguna'}."
            ),
            parameters={
                "type": "object",
                "properties": {"family": {"type": "string", "enum": self.families}},
                "required": ["family"],
                "additionalProperties": False,
            },
        )

    # ------------------------------------------------------------- ejecución --

    def execute(self, call_id: str, name: str, arguments: Dict[str, Any]) -> ToolInvocation:
        """Ejecuta una llamada y devuelve la invocación lista para la traza.

        Nunca lanza: cualquier fallo se convierte en un resultado de error
        (§7.4). Una excepción que suba desde aquí tumbaría el turno entero, y
        el modelo se queda sin poder ni reintentar ni explicar.
        """
        if name == _LOAD_TOOLS:
            return self._execute_load_tools(call_id, arguments)

        tool = self.tools.get(name)
        if tool is None:
            known = ", ".join(sorted(self.tools))
            return self._invocation(
                call_id, name, arguments,
                fail("herramienta desconocida", detail=f"No existe {name!r}.", suggestion=f"Disponibles: {known}"),
            )

        rejected = self._reject_unknown_arguments(tool, arguments)
        if rejected is not None:
            return self._invocation(call_id, name, arguments, rejected)

        try:
            result = tool.fn(self.ctx, **arguments)
        except TypeError as exc:
            result = fail(
                "argumentos inválidos",
                detail=str(exc),
                suggestion=f"Revisa el esquema de {name}: los parámetros obligatorios son {tool.parameters.get('required')}.",
            )
        except Exception as exc:  # noqa: BLE001 - a propósito: ninguna herramienta puede tumbar el turno
            logger.exception("herramienta %s falló", name)
            result = fail(
                "error interno de la herramienta",
                detail=f"{type(exc).__name__}: {exc}",
                suggestion="Prueba otra herramienta o acota la pregunta.",
            )
        return self._invocation(call_id, name, arguments, result, artifact_type=tool.artifact)

    def invalid_arguments(self, call_id: str, name: str, detail: str) -> ToolInvocation:
        """Invocación de error para un `tool_call` cuyos argumentos no se pudieron parsear.

        Se devuelve como resultado de herramienta —y no como excepción— para
        que el modelo pueda reintentar la misma llamada bien formada. Un
        `tool_call` sin su `tool_result` deja el historial inconsistente
        (ver `app/assistant/agent.py`).
        """
        return self._invocation(
            call_id,
            name,
            {},
            fail(
                "argumentos ilegibles",
                detail=detail,
                suggestion="Vuelve a llamar a la herramienta con los argumentos en JSON válido.",
            ),
        )

    def _execute_load_tools(self, call_id: str, arguments: Dict[str, Any]) -> ToolInvocation:
        family = arguments.get("family")
        if family not in self.families:
            result = fail(
                "familia desconocida",
                detail=f"{family!r} no existe.",
                suggestion=f"Familias: {', '.join(self.families)}",
            )
        else:
            self._loaded_families.add(family)
            names = sorted(t.name for t in self.tools.values() if t.family == family)
            result = {
                "data": {"family": family, "tools": names},
                "meta": {"source": "catálogo de herramientas"},
            }
        return self._invocation(call_id, _LOAD_TOOLS, arguments, result)

    @staticmethod
    def _reject_unknown_arguments(tool: Tool, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Rechaza parámetros que la función no acepta, con un mensaje accionable.

        Un modelo pequeño se inventa parámetros plausibles (`temporada`,
        `equipo`); dejarlos llegar a la función produce un `TypeError` cuyo
        mensaje no dice qué se podía haber usado en su lugar.
        """
        accepted = set(_inspect.signature(tool.fn).parameters) - {"ctx"}
        unknown = sorted(set(arguments) - accepted)
        if not unknown:
            return None
        return fail(
            "argumentos inválidos",
            detail=f"{tool.name} no acepta: {', '.join(unknown)}.",
            suggestion=f"Parámetros válidos: {', '.join(sorted(accepted)) or 'ninguno'}.",
        )

    # ----------------------------------------------------------------- traza --

    def _invocation(
        self,
        call_id: str,
        name: str,
        arguments: Dict[str, Any],
        result: Dict[str, Any],
        artifact_type: str = "none",
    ) -> ToolInvocation:
        is_error = "error" in result
        return ToolInvocation(
            call_id=call_id,
            name=name,
            arguments=arguments,
            result=result,
            error=is_error,
            artifact=result.get("artifact") if not is_error and artifact_type != "none" else None,
            summary=summarize(result),
            tags=list((result.get("meta") or {}).get("warnings") or []),
        )


def summarize(result: Dict[str, Any]) -> str:
    """Resumen de una línea del resultado, para cerrar el `st.status` de la traza (§9.2).

    La traza es lo que separa una caja negra de algo auditable, y en scouting
    el usuario quiere poder desconfiar: tiene que poder leer de un vistazo qué
    devolvió cada herramienta sin desplegar el JSON.
    """
    if "error" in result:
        # Sin emoji a propósito: este texto acaba también en el log del
        # servidor, y una consola Windows en cp1252 revienta al escribirlo.
        return f"sin resultado · {result['error']}: {result.get('detail', '')}".strip()

    meta = result.get("meta") or {}
    data = result.get("data")
    if isinstance(data, list):
        head = f"{len(data)} filas"
    elif isinstance(data, dict):
        head = ", ".join(list(data)[:3])
    else:
        head = str(data)[:60]

    pieces = [head]
    if meta.get("scope"):
        pieces.append(meta["scope"])
    if meta.get("gp") is not None:
        pieces.append(f"{meta['gp']} partidos")
    return " · ".join(str(p) for p in pieces if p)


def serialize_result(result: Dict[str, Any]) -> str:
    """Resultado -> JSON compacto para el `tool_result` que ve el modelo.

    El artefacto NO viaja al modelo: son los datos crudos que pinta la página
    (a veces cientos de tiros) y en el contexto solo ocupan sitio. El modelo
    ya tiene el resumen en `data`/`meta`, que es lo que necesita para redactar.
    """
    payload = {key: value for key, value in result.items() if key != "artifact"}
    return json.dumps(payload, ensure_ascii=False, default=str)

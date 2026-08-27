"""Prueba de humo del proveedor de modelo: ¿sabe este modelo usar herramientas?

Media hora que evita el modo de fallo más caro del plan (§13, fase 0):
descubrir a mitad de la implementación que el modelo del momento no hace tool
calling de verdad, sino que "escribe JSON si se lo pides" — que es una fuente
de fallos permanente y no merece la pena (§8.1).

Manda UNA pregunta trivial con UNA herramienta de juguete y comprueba tres
cosas, que son exactamente los requisitos duros del proveedor:

1. Que llama a la herramienta (no que la describa en prosa).
2. Que los argumentos llegan como JSON parseable.
3. Que con el resultado en la mano redacta una respuesta en español.

Es el mismo guion que se vuelve a usar meses después al migrar a un backend
propio (§16.3, paso 2): si el modelo candidato no pasa esto, se descarta en
minutos en vez de en días.

Uso (con `ASSISTANT_LLM_*` ya en el entorno o en `.env`):
    .venv/Scripts/python.exe tools/assistant_smoke_test.py
    .venv/Scripts/python.exe tools/assistant_smoke_test.py --stream
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.baskonia_core import config  # noqa: E402,F401  (carga el .env)
from app.assistant.llm import LLMError, build_llm_client, provider_label  # noqa: E402
from app.assistant.llm.base import ToolSpec  # noqa: E402

# Herramienta de juguete: una sola, trivial y con un argumento obligatorio.
# Deliberadamente NO es una del catálogo real — lo que se está probando es el
# proveedor, no el asistente, y mezclar las dos cosas hace que un fallo no
# diga cuál de las dos falló.
TOY_TOOL = ToolSpec(
    name="get_player_points",
    description="Puntos que anotó un jugador en su último partido.",
    parameters={
        "type": "object",
        "properties": {"player_id": {"type": "string", "description": "Id del jugador."}},
        "required": ["player_id"],
        "additionalProperties": False,
    },
)

SYSTEM = (
    "Eres un asistente de baloncesto. Responde en español, en una frase. "
    "Usa las herramientas disponibles para obtener cualquier cifra: nunca la inventes."
)
QUESTION = "¿Cuántos puntos hizo howard en su último partido?"
TOY_RESULT = {"player_id": "howard", "points": 21, "minutes": 20.29}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stream", action="store_true", help="Pide la respuesta en streaming.")
    args = parser.parse_args()

    try:
        client = build_llm_client()
    except LLMError as exc:
        print(f"FALLO: {exc}")
        return 2

    if client is None:
        print(
            "FALLO: no hay proveedor configurado (ver .env.example). Hace falta "
            "ASSISTANT_LLM_MODEL, más ASSISTANT_LLM_BASE_URL con el proveedor "
            "openai_compat o ASSISTANT_LLM_API_KEY con el proveedor anthropic."
        )
        return 2

    print(f"Proveedor: {provider_label()}")
    messages = [{"role": "user", "content": QUESTION}]
    sink = (lambda piece: print(piece, end="", flush=True)) if args.stream else None

    started = time.monotonic()
    try:
        first = client.chat(messages, [TOY_TOOL], system=SYSTEM, on_text=sink)
    except LLMError as exc:
        print(f"FALLO: {exc}")
        return 2
    first_elapsed = time.monotonic() - started

    # (1) ¿Llama a la herramienta?
    if not first.tool_calls:
        print("\nFALLO (1/3): el modelo NO ha llamado a la herramienta. Ha respondido:")
        print(f"  {first.text[:400]!r}")
        print("Este modelo no sirve para el asistente: busca uno con tool calling nativo (§8.1).")
        return 1
    call = first.tool_calls[0]
    print(f"OK (1/3): ha llamado a {call.name} en {first_elapsed:.1f}s")

    # (2) ¿Argumentos parseables y con lo obligatorio?
    if call.arguments_error:
        print(f"FALLO (2/3): argumentos ilegibles -> {call.arguments_error}")
        return 1
    if "player_id" not in call.arguments:
        print(f"FALLO (2/3): faltan argumentos obligatorios. Ha mandado: {call.arguments}")
        return 1
    print(f"OK (2/3): argumentos válidos -> {call.arguments}")

    # (3) ¿Redacta con el resultado en la mano?
    # `provider_state` se reenvía igual que hace el agente: sin él, un modelo
    # que piensa (Claude 4.6+) rechaza la segunda vuelta por perder sus
    # bloques `thinking` firmados, y el fallo parecería del modelo.
    assistant_message = {"role": "assistant", "content": first.text, "tool_calls": first.tool_calls}
    if first.provider_state is not None:
        assistant_message["provider_state"] = first.provider_state
    messages.append(assistant_message)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": json.dumps(TOY_RESULT, ensure_ascii=False),
        }
    )
    started = time.monotonic()
    try:
        second = client.chat(messages, [TOY_TOOL], system=SYSTEM, on_text=sink)
    except LLMError as exc:
        print(f"FALLO (3/3): {exc}")
        return 2
    second_elapsed = time.monotonic() - started

    if not second.text.strip():
        print("FALLO (3/3): no ha redactado nada con el resultado de la herramienta.")
        return 1

    print(f"OK (3/3): responde en {second_elapsed:.1f}s")
    print(f"\nRespuesta: {second.text.strip()}")
    if "21" not in second.text:
        # No es un fallo duro (puede escribirlo con letra), pero sí una señal
        # de que el modelo no se está apoyando en el dato que le has dado.
        print("AVISO: la cifra 21 no aparece en la respuesta; revisa si está usando el tool_result.")
    print(f"\nTotal: {first_elapsed + second_elapsed:.1f}s para una pregunta con una vuelta de herramienta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

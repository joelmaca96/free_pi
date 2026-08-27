"""Lista los modelos que ofrece el proveedor configurado en `ASSISTANT_LLM_*`.

Existe por un motivo muy concreto: los nombres de modelo cambian cada pocos
meses en todos los proveedores, y escribir uno de memoria en `.env` produce un
404 que no dice qué poner en su lugar. Esto lo pregunta al proveedor.

Es el paso previo a `tools/assistant_smoke_test.py`: primero se averigua qué
modelos hay, y luego se comprueba que el elegido sabe usar herramientas de
verdad (que es un requisito duro y no todos lo cumplen, ver
`local/features/005-chatbot/01_design.md` §8.1).

Uso:
    .venv/Scripts/python.exe tools/list_models.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.baskonia_core import config  # noqa: E402,F401  (carga el .env)


def _list_anthropic(api_key: str) -> list:
    """Modelos de Claude. Ojo: listar NO consume saldo y funciona con la cuenta
    a cero, así que ver la lista completa aquí no significa poder usarlos —
    eso solo lo dice `assistant_smoke_test.py`, que sí manda un mensaje."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key, timeout=30)
    return sorted(model.id for model in client.models.list())


def _list_openai_compat(base_url: str, api_key: str) -> list:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key or "no-key-needed", timeout=30)
    return sorted(model.id for model in client.models.list())


def main() -> int:
    provider = os.getenv("ASSISTANT_LLM_PROVIDER", "openai_compat").strip() or "openai_compat"
    base_url = os.getenv("ASSISTANT_LLM_BASE_URL", "").strip()
    api_key = os.getenv("ASSISTANT_LLM_API_KEY", "").strip()

    if provider == "anthropic":
        if not api_key:
            print("FALLO: con ASSISTANT_LLM_PROVIDER=anthropic hace falta ASSISTANT_LLM_API_KEY.")
            return 2
        source, fetch = "anthropic (api.anthropic.com)", lambda: _list_anthropic(api_key)
        missing_sdk = "FALLO: falta la dependencia `anthropic` (pip install anthropic)."
    else:
        if not base_url:
            print("FALLO: define ASSISTANT_LLM_BASE_URL en el .env (ver .env.example).")
            return 2
        source, fetch = base_url, lambda: _list_openai_compat(base_url, api_key)
        missing_sdk = "FALLO: falta la dependencia `openai` (pip install -r app/requirements.txt)."

    print(f"Proveedor: {source}\n")
    try:
        models = fetch()
    except ImportError:
        print(missing_sdk)
        return 2
    except Exception as exc:  # noqa: BLE001 - aquí interesa el mensaje del proveedor tal cual
        print(f"FALLO al listar modelos: {exc}")
        if not api_key:
            print("Si el proveedor pide clave, ponla en ASSISTANT_LLM_API_KEY.")
        return 1

    if not models:
        print("El proveedor no ha devuelto ningún modelo.")
        return 1

    for model_id in models:
        print(f"  {model_id}")
    print(
        f"\n{len(models)} modelos. Pon el que elijas en ASSISTANT_LLM_MODEL y comprueba que "
        "sabe usar herramientas:\n  .venv/Scripts/python.exe tools/assistant_smoke_test.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

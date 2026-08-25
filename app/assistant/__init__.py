"""Asistente de scouting: bucle de agente con herramientas sobre `data/baskonia.db`.

Diseño completo en `local/features/005-chatbot/01_design.md`. Las tres piezas
que conviene tener en la cabeza antes de tocar nada aquí:

- **El modelo no escribe SQL** (§3.1). Las cifras las produce SQL fijo y
  probado (`tools/`, `app/data/queries*.py`); el modelo solo elige qué
  herramienta llamar y redacta. Es lo que hace que un modelo pequeño pueda
  acertar la respuesta aunque razone regular.
- **Nada por encima de `llm/` sabe qué proveedor hay debajo** (§3.4). Las
  herramientas, la resolución, las guardas, el verificador y la interfaz se
  escriben una vez y sobreviven a cambiar de endpoint — que es lo que
  convierte la migración de §16 en media jornada.
- **La app no escribe en la base de datos, nunca** (§11.1), vistas incluidas:
  lo que haga falta va en `schema.sql` y lo aplica `ingest/`.
"""
import sys
from pathlib import Path

# Mismo arranque que `app/data/db.py`: `app/` no vive en la raíz del repo, así
# que hay que poder importar `packages.*` (aquí, `packages.baskonia_core.names`
# — la normalización de nombres compartida con la ingesta, ver
# `app/assistant/resolve.py`). Se hace en el `__init__` del paquete y no en
# cada módulo porque este import se ejecuta antes que cualquier submódulo,
# vengan por `app.assistant.*` (pytest, con la raíz en sys.path) o por
# `assistant.*` (Streamlit, con `app/` en sys.path).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

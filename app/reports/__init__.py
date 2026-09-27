"""Informes descargables (hoy: solo `postgame_ppt.py`).

Separado de `app/assistant/` a propósito, aunque el generador de puntos
destacados llame al mismo LLM: esto no es el chat (sin herramientas, sin
historial, sin traza) ni un componente de pantalla — es una pieza de
generación de fichero que una página invoca desde un botón. `app/screens/`
no sabe nada de `python-pptx`; solo llama a `generate_postgame_ppt`.
"""

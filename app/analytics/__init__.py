"""Cálculo derivado que no es ni consulta ni pintura.

`app/data/queries.py` saca filas de la base de datos y `app/components/`
las dibuja; lo que queda en medio —modelos, medias de referencia,
regularización— vive aquí. La regla es que **nada de este paquete importa
Streamlit ni SQLAlchemy**: son funciones puras sobre `pandas`/`numpy`, así
que se prueban como funciones normales y las puede reutilizar igual la
interfaz, el asistente o un informe.
"""

"""Paquete de dominio compartido de baskonia-pipeline.

Contiene la lógica de negocio reutilizable por las aplicaciones futuras
(API, SPA) y por el pipeline de ingesta: configuración (`config`), cálculo
de estadísticas (`stats`), agregados e insights (`insights`) y el modelo de
datos con su almacenamiento (`db`).

Este paquete NO importa nada de la raíz del proyecto ni de `apps/` (regla de
frontera verificada por `tests/test_architecture.py`).
"""

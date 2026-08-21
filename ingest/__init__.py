"""Pipeline de ingestión de datos para Scouting Baskonia (F0 desde cero).

Tres fuentes independientes, cada una en su propio subpaquete, todas cargando
al esquema común de `packages.baskonia_core.db.scouting`:

- `ingest.acb` — competición ACB (scraping directo de la API de acb.com).
- `ingest.euroleague` — Euroliga, vía la librería `euroleague_api`.
- `ingest.baskonia_web` — plantilla/fotos oficiales de baskonia.com.

`ingest.common` contiene lo compartido: acceso a la BD, resolución de
identidad (jugadores/equipos entre fuentes) y upserts idempotentes.
`ingest.run_all` orquesta las tres.
"""

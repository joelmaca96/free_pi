"""Backend API REST de solo lectura (FastAPI).

Expone los 18 endpoints del contrato nuevo (feature 012 `api-nuevo-modelo-datos`,
ver `local/features/012-api-nuevo-modelo-datos/01_design.md`) leyendo la BD de
scouting (`data/baskonia.db`) a través de `ScoutingRepository`/
`create_scouting_engine` de `packages/baskonia_core/db/scouting`. Identidad por
`team_id`/`game_id` TEXT (`'bas'`, `'g1'`, `'acb-105370'`…), sin slug. La UI es
la SPA React en `apps/web/`.
"""

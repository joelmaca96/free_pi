# Workflow Config — baskonia-pipeline

Adaptador de proyecto para el pipeline agéntico ([AGENTIC_WORKFLOW.md](AGENTIC_WORKFLOW.md)).
**Único fichero que hay que reescribir al portar el pipeline a otro proyecto.**

> Nota de mantenimiento: este fichero se regeneró el 2026-08-23 (Feature Lead) porque la
> versión anterior describía una arquitectura con backend API FastAPI (`apps/api/`) y SPA React
> (`apps/web/`) que se retiró por completo: el proyecto es únicamente el pipeline de ingesta de
> datos. Si vuelve a quedar desincronizado con el código real, el Feature/Audit Lead debe
> regenerarlo antes de arrancar cualquier pipeline.

## Identidad del proyecto

- Producto: pipeline de ingesta batch de datos de baloncesto (ACB, Euroliga, web oficial
  baskonia.com) que persiste equipos/jugadores/partidos/box scores/estadísticas avanzadas/
  tiros/quintetos en SQLite (`data/baskonia.db`). No hay capa de servicio ni interfaz de
  usuario: es exclusivamente una aplicación de captura de datos.
- Plataforma / runtime: Python 3 (venv en `.venv/`, sin compilación — interpretado).
  Persistencia con SQLAlchemy Core (no ORM) sobre SQLite.
- Comunicación / interfaces clave: HTTP contra fuentes externas reales desde `ingest/` —
  `api2.acb.com` (API real del frontend de acb.com, auth `x-apikey`), librería `euroleague_api`
  (Euroliga), `www.baskonia.com` (JSON embebido de la web oficial, roster/fotos).

## Workspace (estructura de repos / carpetas)

| Carpeta | Rol |
|---|---|
| `ingest/acb/` | Fuente ACB: `client.py` (HTTP a `api2.acb.com`, ver historia de endpoints en el propio fichero), `parser.py`/`adapter.py` (mapeo a contrato común), `pipeline.py` (`run(engine, season, client=None) -> {"loaded": [...], "failed": [...]}`), `cli.py` |
| `ingest/euroleague/` | Fuente Euroliga vía paquete `euroleague_api`: mismo patrón `client.py`/`parser.py`/`adapter.py`/`pipeline.py` (`run(engine, season)`); rate-limit **no resuelto a escala de temporada completa** (ver `doc/features/ingestor/01_estado.md` §2.3) |
| `ingest/baskonia_web/` | Roster/fotos del Baskonia desde `baskonia.com` (JSON embebido); `pipeline.py` (`run(engine)`, sin parámetro `season` — solo escribe `players`/`player_external_ids`) |
| `ingest/common/` | Contrato compartido: `raw_game.py` (dict "raw game" único que produce cada adaptador + `parse_and_resolve()`), `identity.py` (resolución equipo/jugador entre fuentes vía `*_external_ids` + alias a mano), `loader.py` (upsert idempotente contra el esquema de scouting), `lineups.py`/`game_clock.py` (quintetos desde play-by-play, agnóstico de fuente), `db.py` (`get_engine()`), `logging_utils.py` |
| `ingest/run_all.py` | Orquestador CLI (`python -m ingest.run_all --season <año> [--skip acb\|euroleague\|baskonia_web]`): orden `baskonia_web → acb → euroleague`; un módulo que falla no detiene a los demás, resumen `{módulo: {"ok": bool\|None, "summary"/"error": ...}}`. **No existe hoy ningún entrypoint por-entidad** (ingestar solo un partido/jugador/equipo concreto) |
| `packages/baskonia_core/` | Dominio compartido: `config.py` (config central, `DATABASE_URL`), `db/scouting/` (`schema.sql`, `engine.py` → `create_scouting_engine()`/`init_scouting_db()`) — esquema y arranque de la base de datos, sin capa de lectura ni de servicio |
| `data/` | `baskonia.db` (SQLite real). Esquema real = `packages/baskonia_core/db/scouting/schema.sql`, poblado por `ingest/`. **No usar en tests** (usar SQLite en memoria) |
| `tests/` | `ingest/` (offline, mocks/fixtures — sin red real, por fuente + compartido), `test_scouting_db.py` (carga del esquema) |
| `tools/` | `init_scouting_db.py` (crea/recrea el esquema de scouting) |
| `doc/` | `arquitectura/04_db_map.md` (análisis histórico de un esquema anterior, ya retirado), `features/ingestor/01_estado.md` (foto de estado del pipeline de ingesta, no de diseño), `acb_endpoints.md` (endpoints de acb.com capturados, no todos integrados aún) |

Es un solo repositorio git (monorepo simple, sin submódulos), 100% Python (`ingest/`,
`packages/`). El pipeline de features puede usar operaciones git (`git mv`, `git revert`)
cuando aporten valor.

## Capas (regla de dependencia)

```
ingest/{acb,euroleague,baskonia_web}/  →  ingest/common/  →  packages/baskonia_core/db/scouting/
```

- `ingest/<fuente>/client.py` es la única capa con red de cada fuente (HTTP/librería externa);
  `parser.py`/`adapter.py` traducen la respuesta cruda al contrato común
  (`ingest/common/raw_game.py`); `pipeline.py` orquesta fetch→parse→load para una fuente y
  expone siempre `run(engine, season, ...)` devolviendo `{"loaded": [...ids...], "failed":
  [...ids...]}` (excepción: `baskonia_web.pipeline.run(engine)` no tiene `season`, es un
  snapshot de plantilla actual). Un ítem roto (partido/equipo) **no debe tumbar el resto** — se
  captura la excepción, se loguea y se añade a `failed`.
- `ingest/common/loader.py` es el único punto de escritura sobre el esquema de scouting: upsert
  idempotente (natural keys con `ON CONFLICT DO UPDATE`; borrar-y-reinsertar para tablas sin
  clave natural como `lineups`/`shots`/`key_events`).
- `packages/baskonia_core/db/scouting/` es solo esquema + arranque del engine — no contiene
  lógica de lectura/agregación ni expone nada a un consumidor externo. Cualquier necesidad de
  consultar los datos ya persistidos se resuelve con SQL/SQLAlchemy directo sobre el engine, no
  añadiendo una capa de servicio nueva salvo que se pida explícitamente.
- Dominios/capas de trabajo a efectos de paquetes de trabajo (WP) del Architect: **ingesta por
  fuente** (`ingest/acb`, `ingest/euroleague`, `ingest/baskonia_web`), **ingesta común**
  (`ingest/common/`), **esquema de datos** (`packages/baskonia_core/db/scouting/`), **docs**
  (`doc/`, `README.md`).

## Normas de código

- Fichero normativo: no hay guía de estilo explícita ni linter Python configurado (no hay
  `ruff`/`black`/`flake8`); la referencia son las convenciones ya presentes en el código
  (`ingest/*/pipeline.py`, `packages/baskonia_core/db/scouting/engine.py`): type hints en
  firmas públicas, docstrings en **español** estilo Google (`Args:`/`Returns:`/`Raises:` cuando
  aplica), comentarios explicando *por qué* (no *qué*) cuando hay una decisión no obvia (bugs de
  fuente ya conocidos, endpoints verificados en vivo, rate-limits). `# noqa: BLE001` es el
  patrón aceptado para el `except Exception` deliberado en los `pipeline.py` (un ítem roto no
  debe tumbar el lote).
- Formato / linters: ninguno instalado.

## Build

```bash
# Verificación mínima tras cualquier cambio
.venv/Scripts/python.exe -m py_compile <ficheros tocados>
.venv/Scripts/python.exe -c "import packages.baskonia_core.db.scouting, ingest.run_all"
```

Condición de salida: los imports y `py_compile` no lanzan excepción.

## Tests

- Framework: `pytest` (`pytest.ini` en la raíz, `pythonpath = . tools packages`, `testpaths =
  tests`). Comando: `.venv/Scripts/python.exe -m pytest` (o `pytest -q`) desde la raíz del
  repo. **Nunca contra `data/baskonia.db` real** — SQLite en memoria
  (`sqlite:///:memory:`) con el esquema de scouting (ver fixtures de `tests/ingest/conftest.py`
  y `tests/test_scouting_db.py`).
- Ubicaciones: `tests/ingest/` (por fuente: `test_acb.py`, `test_acb_client.py`,
  `test_euroleague.py`, `test_baskonia_web.py`, más `test_identity.py`/`test_lineups.py`/
  `test_loader.py`/`test_run_all.py`/`test_raw_game_lineups.py` para lo compartido) — **100%
  offline, con mocks/fixtures que replican payloads reales verificados en vivo, nunca red real
  en tests**. `tests/test_scouting_db.py` valida la carga del esquema.
- Patrón para tests nuevos (ingesta de una fuente real): nunca golpear la red real de
  acb.com/euroleague_api/baskonia.com en un test — grabar/adaptar un fixture del payload real
  (ya hay precedentes en `tests/ingest/`) y mockear el cliente HTTP.

## Documentación a mantener

| Qué cambia | Documento a actualizar |
|---|---|
| Estado de una fuente de ingesta (`ingest/acb`, `ingest/euroleague`, `ingest/baskonia_web`, `ingest/common`) | `doc/features/ingestor/01_estado.md` |
| Esquema de `packages/baskonia_core/db/scouting/schema.sql` | `doc/arquitectura/04_db_map.md` (+ `db_attributes.mmd`/`db_relations.mmd` si cambia el grafo de tablas) |
| Estado general del proyecto | `README.md` |
| Nuevas variables de entorno de ingesta | `README.md` y `.env.example` |

Los ficheros `01_estado.md` bajo `doc/features/` son **fotos de estado actuales**, no diarios de
diseño — al actualizarlos, sobrescribir la parte que cambió en vez de solo añadir al final.

## Conocimiento del proyecto (leer antes de diseñar)

1. `README.md` — visión general del pipeline de ingesta.
2. `doc/features/ingestor/01_estado.md` — estado real por fuente de ingesta (qué tabla llena
   cada una, qué falta, rate-limits conocidos — imprescindible antes de tocar `ingest/`).
3. `packages/baskonia_core/db/scouting/schema.sql` + `doc/arquitectura/04_db_map.md` — esquema
   real disponible antes de proponer cualquier columna/tabla nueva.
4. `ingest/run_all.py` + un `pipeline.py` existente (p.ej. `ingest/acb/pipeline.py`) — firma y
   contrato de retorno (`run(engine, season) -> {"loaded": [...], "failed": [...]}`) que
   cualquier entrypoint de ingesta nuevo (incluido uno por-entidad) debe respetar.

## Roster de especialistas (subagentes vía `agent/runSubagent`)

Vacío: este proyecto no tiene agentes de dominio locales en `.github/agents/`. El Feature
Developer implementa directamente en las capas relevantes (**ingesta por fuente**, **ingesta
común**, **esquema de datos**, **docs** — ver "Capas" arriba), apoyándose en la skill
`karpathy-guidelines` al generar código, sin delegar en subagentes de dominio salvo que el
proyecto crezca y se registren agentes locales.

## Pipelines

- Directorio de features: `local/features/<NNN>-<slug>/`
- Directorio de auditorías: `local/audits/<NNN>-<slug>/`
- Directorio de troubleshooting: `local/troubleshoot/<NNN>-<slug>/`
- Numeración: secuencial de 3 dígitos por directorio (mirar el mayor existente y sumar 1).

# Workflow Config — baskonia-pipeline

Adaptador de proyecto para el pipeline agéntico ([AGENTIC_WORKFLOW.md](AGENTIC_WORKFLOW.md)).
**Único fichero que hay que reescribir al portar el pipeline a otro proyecto.**

> Nota de mantenimiento: este fichero se regeneró el 2026-08-21 (Feature Lead) porque la versión
> anterior describía la arquitectura pre-migración (`scraper/`, `db/`, `main.py`, `app.py` a nivel
> raíz), retirada por las features 008-013. Si vuelve a quedar desincronizado con el código real,
> el Feature/Audit Lead debe regenerarlo antes de arrancar cualquier pipeline.

## Identidad del proyecto

- Producto: PoC de herramienta de scouting para asistentes de entrenador del Baskonia. Pipeline de
  ingesta batch (ACB, Euroliga, web oficial baskonia.com) que persiste equipos/jugadores/partidos/
  box scores/estadísticas avanzadas/tiros/quintetos en SQLite (`data/baskonia.db`), expuestos vía
  API FastAPI (`apps/api/`) de solo lectura y consumidos por una SPA React (`apps/web/`).
- Plataforma / runtime: Python 3 (venv en `.venv/`, sin compilación — interpretado) para
  ingest/API; Node.js/TypeScript (Vite + React) para la SPA, sí compilada (`tsc -b` + `vite
  build`). Persistencia con SQLAlchemy Core (no ORM) sobre SQLite.
- Comunicación / interfaces clave: HTTP contra fuentes externas reales desde `ingest/` —
  `api2.acb.com` (API real del frontend de acb.com, auth `x-apikey`), librería `euroleague_api`
  (Euroliga), `www.baskonia.com` (JSON embebido de la web oficial, roster/fotos). API REST propia
  (`apps/api/`) consumida por la SPA vía `openapi-fetch` + TanStack Query; el contrato se congela
  en `openapi.json` (versionado en la raíz, regenerado con `tools/export_openapi.py` y propagado a
  `apps/web/src/api/schema.d.ts` con `npm run gen:api`).

## Workspace (estructura de repos / carpetas)

| Carpeta | Rol |
|---|---|
| `ingest/acb/` | Fuente ACB: `client.py` (HTTP a `api2.acb.com`, ver historia de endpoints en el propio fichero), `parser.py`/`adapter.py` (mapeo a contrato común), `pipeline.py` (`run(engine, season, client=None) -> {"loaded": [...], "failed": [...]}`), `cli.py` |
| `ingest/euroleague/` | Fuente Euroliga vía paquete `euroleague_api`: mismo patrón `client.py`/`parser.py`/`adapter.py`/`pipeline.py` (`run(engine, season)`); rate-limit **no resuelto a escala de temporada completa** (ver `doc/features/ingestor/01_estado.md` §2.3) |
| `ingest/baskonia_web/` | Roster/fotos del Baskonia desde `baskonia.com` (JSON embebido); `pipeline.py` (`run(engine)`, sin parámetro `season` — solo escribe `players`/`player_external_ids`) |
| `ingest/common/` | Contrato compartido: `raw_game.py` (dict "raw game" único que produce cada adaptador + `parse_and_resolve()`), `identity.py` (resolución equipo/jugador entre fuentes vía `*_external_ids` + alias a mano), `loader.py` (upsert idempotente contra el esquema de scouting), `lineups.py`/`game_clock.py` (quintetos desde play-by-play, agnóstico de fuente), `db.py` (`get_engine()`), `logging_utils.py` |
| `ingest/run_all.py` | Orquestador CLI (`python -m ingest.run_all --season <año> [--skip acb\|euroleague\|baskonia_web]`): orden `baskonia_web → acb → euroleague`; un módulo que falla no detiene a los demás, resumen `{módulo: {"ok": bool\|None, "summary"/"error": ...}}`. **No existe hoy ningún entrypoint por-entidad** (ingestar solo un partido/jugador/equipo concreto) |
| `apps/api/` | Backend FastAPI: `main.py` (`create_app()`), `deps.py` (DI: `get_repository`, `get_team_id`, `get_game_id`, `season_label_param`), `errors.py` (excepciones de dominio → `application/problem+json` RFC 9457), `mappers.py`, `middleware.py`, `settings.py`, `schemas/` (Pydantic), `routers/{games,players,teams,matchups,meta}.py` |
| `apps/web/` | SPA React + TypeScript (Vite): `src/api/` (cliente `openapi-fetch` + `hooks.ts` con TanStack Query + `schema.d.ts` generado), `src/features/{resumen,anteriores,proximos,plantilla}/` (pantallas), `src/components/`, `src/lib/` (formateo/helpers puros) |
| `packages/baskonia_core/` | Dominio compartido: `errors.py` (`DomainError` y subclases: `TeamNotFound`, `GameNotFound`, `InvalidFilter`), `dates.py`, `config.py`, `stats.py`/`insights.py` (cálculo puro, legado parcial), `db/scouting/` (`schema.sql`, `engine.py` → `create_scouting_engine()`, `repository.py` → `ScoutingRepository`, capa de lectura **canónica y única** de la API), `db/models.py`+`storage.py` (ORM BBR **legado**, retirado de la API en la feature 012 — no tocar salvo que se pida explícitamente revivirlo), `services/` (lógica extraída de la UI Streamlit ya eliminada — legado) |
| `data/` | `baskonia.db` (SQLite real). Esquema real = `packages/baskonia_core/db/scouting/schema.sql`, poblado por `ingest/`. **No usar en tests** (usar SQLite en memoria) |
| `tests/` | `api/` (contrato HTTP, `TestClient`), `ingest/` (offline, mocks/fixtures — sin red real), `parity/` (arnés de paridad F0, legado de la migración), `test_architecture.py` (reglas de capas), `test_insights.py`/`test_stats.py`/`test_storage.py`/`test_services.py` (legado) |
| `apps/web/src/**/*.test.tsx` | Tests de la SPA (Vitest + Testing Library + MSW) |
| `tools/` | `export_openapi.py` (regenera `openapi.json` desde la app real — `tests/api/test_contract.py` falla si difieren), `init_scouting_db.py` |
| `doc/` | `arquitectura/` (diseño de la migración: `01_design.md`, `02_migration.md`, `03_deplyment_design.md`, `04_db_map.md`), `features/{ingestor,scouting_conversacional,spa-contrato-nuevo}/01_estado.md` (fotos de estado por área, no de diseño), `acb_endpoints.md` (endpoints de acb.com capturados, no todos integrados aún), `ideas/` |
| `openapi.json` | Contrato HTTP congelado, versionado en la raíz de repo |

Es un solo repositorio git (monorepo simple, sin submódulos): Python (`ingest/`, `apps/api/`,
`packages/`) + un paquete Node (`apps/web/`). El pipeline de features puede usar operaciones git
(`git mv`, `git revert`) cuando aporten valor.

## Capas (regla de dependencia)

```
ingest/{acb,euroleague,baskonia_web}/  →  ingest/common/  →  packages/baskonia_core/db/scouting/  →  apps/api/  →  apps/web/
```

- `ingest/<fuente>/client.py` es la única capa con red de cada fuente (HTTP/librería externa);
  `parser.py`/`adapter.py` traducen la respuesta cruda al contrato común
  (`ingest/common/raw_game.py`); `pipeline.py` orquesta fetch→parse→load para una fuente y expone
  siempre `run(engine, season, ...)` devolviendo `{"loaded": [...ids...], "failed": [...ids...]}`
  (excepción: `baskonia_web.pipeline.run(engine)` no tiene `season`, es un snapshot de plantilla
  actual). Un ítem roto (partido/equipo) **no debe tumbar el resto** — se captura la excepción, se
  loguea y se añade a `failed`.
- `ingest/common/loader.py` es el único punto de escritura sobre el esquema de scouting desde
  ingesta: upsert idempotente (natural keys con `ON CONFLICT DO UPDATE`; borrar-y-reinsertar para
  tablas sin clave natural como `lineups`/`shots`/`key_events`).
- `packages/baskonia_core/db/scouting/repository.py` (`ScoutingRepository`) es la **única** capa
  de lectura para `apps/api/` — los routers **nunca** hacen SQL directo ni importan
  `engine`/`Session` de SQLAlchemy salvo a través del repositorio (excepción puntual ya existente:
  `apps/api/routers/games.py` resuelve `season_label` con una query `text()` directa cuando el
  repositorio no la devuelve — no lo tomes como precedente para nuevo código, es deuda conocida).
  `db/models.py`/`storage.py`/`services/` son el ORM legado de BBR, ya retirado de la API — no
  crecer sobre esa capa.
- `apps/api/errors.py` es el único sitio donde una excepción se traduce a respuesta HTTP: los
  routers lanzan excepciones de dominio (`packages/baskonia_core/errors.py`,
  `DomainError`/`TeamNotFound`/`GameNotFound`/`InvalidFilter`) y `errors.py` las mapea a
  `application/problem+json` (RFC 9457) con `{"type","title","status","detail","instance",
  "request_id"}`. No inventar un formato de error nuevo ni devolver `HTTPException` a mano en un
  router salvo que ya sea el patrón (ver excepción anterior).
- **Contrato "degradado limpio, no inventa datos"** (`doc/features/spa-contrato-nuevo/01_estado.md`,
  feature 013): ante un dato ausente en BD, la API devuelve `null`/lista vacía — nunca un valor
  fabricado. Cualquier feature de auto-fetch/backfill debe preservar esto como último recorte: si
  el fetch a la fuente real falla o la fuente no tiene el dato, se sigue devolviendo
  `null`/vacío, nunca un valor inventado.
- `apps/web/` consume la API exclusivamente vía `src/api/hooks.ts` (TanStack Query sobre
  `openapi-fetch`), tipado por `schema.d.ts` (generado, no editar a mano — regenerar con
  `npm run gen:api` tras cambiar `openapi.json`).
- Dominios/capas de trabajo a efectos de paquetes de trabajo (WP) del Architect: **ingesta por
  fuente** (`ingest/acb`, `ingest/euroleague`, `ingest/baskonia_web`), **ingesta común**
  (`ingest/common/`), **modelo de datos / lectura** (`packages/baskonia_core/db/scouting/`),
  **API** (`apps/api/`), **SPA** (`apps/web/`), **docs** (`doc/`, `README.md`).

## Normas de código

- Fichero normativo: no hay guía de estilo explícita ni linter Python configurado (no hay
  `ruff`/`black`/`flake8`); la referencia son las convenciones ya presentes en el código
  (`ingest/*/pipeline.py`, `apps/api/deps.py`, `apps/api/errors.py`,
  `packages/baskonia_core/db/scouting/repository.py`): type hints en firmas públicas, docstrings
  en **español** estilo Google (`Args:`/`Returns:`/`Raises:` cuando aplica), comentarios
  explicando *por qué* (no *qué*) cuando hay una decisión no obvia (bugs de fuente ya conocidos,
  endpoints verificados en vivo, rate-limits). `# noqa: BLE001` es el patrón aceptado para el
  `except Exception` deliberado en los `pipeline.py` (un ítem roto no debe tumbar el lote).
- TypeScript/React (`apps/web/`): `tsc -b` en modo estricto (ver `tsconfig.json`); hooks de datos
  centralizados en `src/api/hooks.ts`, tipos derivados de `schema.d.ts` generado — no declarar
  tipos de respuesta de API a mano en componentes.
- Formato / linters: ninguno instalado en Python. En `apps/web/` no hay ESLint/Prettier
  configurado en `package.json` (verificar antes de asumir uno). Mantener consistencia visual con
  el código adyacente (Python: 4 espacios; TS: el estilo ya presente en `src/features/`).

## Build

```bash
# Backend (ingest/ + apps/api/ + packages/): verificación mínima tras cualquier cambio
.venv/Scripts/python.exe -m py_compile <ficheros tocados>
.venv/Scripts/python.exe -c "import apps.api.main, packages.baskonia_core.db.scouting.repository, ingest.run_all"

# Si cambia el contrato público de apps/api/ (schemas/routers): regenerar el OpenAPI congelado
.venv/Scripts/python.exe tools/export_openapi.py
cd apps/web && npm run gen:api   # regenera src/api/schema.d.ts desde openapi.json

# Frontend (apps/web/): sí hay build real, debe compilar sin errores TS
cd apps/web && npm run build   # tsc -b && vite build
```

Condición de salida: los imports y `py_compile` no lanzan excepción; `npm run build` termina en 0
sin errores de TypeScript; si se tocó el contrato de la API, `tests/api/test_contract.py` sigue
pasando (falla si el `openapi.json` versionado difiere del generado en caliente).

## Tests

- Framework backend: `pytest` (`pytest.ini` en la raíz, `pythonpath = . tools packages`,
  `testpaths = tests`). Comando: `.venv/Scripts/python.exe -m pytest` (o `pytest -q`) desde la
  raíz del repo. **Nunca contra `data/baskonia.db` real** — SQLite en memoria
  (`sqlite:///:memory:`) con el esquema de scouting (ver fixtures de `tests/conftest.py` /
  `tests/ingest/conftest.py`).
- Ubicaciones: `tests/api/` (contrato HTTP vía `TestClient`, incluye `test_contract.py` que
  congela `openapi.json`), `tests/ingest/` (por fuente: `test_acb.py`, `test_acb_client.py`,
  `test_euroleague.py`, `test_baskonia_web.py`, más `test_identity.py`/`test_lineups.py`/
  `test_loader.py`/`test_run_all.py` para lo compartido) — **100% offline, con mocks/fixtures que
  replican payloads reales verificados en vivo, nunca red real en tests**. `tests/parity/` es el
  arnés de paridad de la migración (legado, no tocar salvo que la migración lo requiera).
  `tests/test_architecture.py` valida las reglas de capas de arriba.
- Framework frontend: `vitest` + `@testing-library/react` + `msw` (mock de red). Comando:
  `cd apps/web && npm run test` (o `npm run test:watch`). Tests junto al código
  (`*.test.tsx`/`*.test.ts`).
- Patrón para tests nuevos (ingesta de una fuente real): nunca golpear la red real de
  acb.com/euroleague_api/baskonia.com en un test — grabar/adaptar un fixture del payload real (ya
  hay precedentes en `tests/ingest/`) y mockear el cliente HTTP.

## Documentación a mantener

| Qué cambia | Documento a actualizar |
|---|---|
| Estado de una fuente de ingesta (`ingest/acb`, `ingest/euroleague`, `ingest/baskonia_web`, `ingest/common`) | `doc/features/ingestor/01_estado.md` |
| Contrato público de `apps/api/` (routers/schemas) o consumo desde `apps/web/` | `doc/features/spa-contrato-nuevo/01_estado.md`; regenerar `openapi.json` (`tools/export_openapi.py`) y `apps/web/src/api/schema.d.ts` (`npm run gen:api`) |
| Esquema de `packages/baskonia_core/db/scouting/schema.sql` | `doc/arquitectura/04_db_map.md` (+ `db_attributes.mmd`/`db_relations.mmd` si cambia el grafo de tablas) |
| Estado general del proyecto / roadmap | `README.md` sección "Estado actual" (bitácora fechada) y sección 7 (roadmap, checkboxes `[ ]`→`[x]`) |
| Nuevas variables de entorno de ingesta/API | `README.md` (si documenta configuración) y `.env.example` si existe |

Los ficheros `01_estado.md` bajo `doc/features/` son **fotos de estado actuales**, no diarios de
diseño — al actualizarlos, sobrescribir la parte que cambió en vez de solo añadir al final.

## Conocimiento del proyecto (leer antes de diseñar)

1. `README.md` — visión del PoC, sección "Estado actual" (bitácora fechada, más fiable para el
   estado del pipeline de ingesta que la sección 2 "Arquitectura", que aún describe una ruta
   `apps/ingest/` **ya movida** a `ingest/` en la raíz — confiar en `doc/features/ingestor/
   01_estado.md` y en el árbol real del repo sobre el texto narrativo de la sección 2 si difieren).
2. `doc/features/ingestor/01_estado.md` — estado real por fuente de ingesta (qué tabla llena cada
   una, qué falta, rate-limits conocidos — imprescindible antes de tocar `ingest/`).
3. `doc/features/spa-contrato-nuevo/01_estado.md` — contrato actual API↔SPA, qué se eliminó en la
   feature 013 (incluida la eliminación deliberada del scouting bajo demanda `fetch_opponent_
   scouting()` y por qué) y el principio "degradado limpio, no inventa datos".
4. `packages/baskonia_core/db/scouting/schema.sql` + `doc/arquitectura/04_db_map.md` — esquema
   real disponible antes de proponer cualquier columna/tabla nueva.
5. `apps/api/deps.py`, `apps/api/errors.py`, un router existente (`apps/api/routers/games.py`) —
   patrón de DI, manejo de errores y forma de un endpoint antes de añadir uno nuevo.
6. `ingest/run_all.py` + un `pipeline.py` existente (p.ej. `ingest/acb/pipeline.py`) — firma y
   contrato de retorno (`run(engine, season) -> {"loaded": [...], "failed": [...]}`) que cualquier
   entrypoint de ingesta nuevo (incluido uno por-entidad) debe respetar.
7. `local/features/013-spa-contrato-nuevo/` (00_request/01_design/03_review) — histórico de por
   qué se retiró el scouting bajo demanda anterior; relevante para no reintroducir a ciegas un
   patrón ya descartado.

## Roster de especialistas (subagentes vía `agent/runSubagent`)

Vacío: este proyecto no tiene agentes de dominio locales en `.github/agents/`. El Feature
Developer implementa directamente en las capas relevantes (**ingesta por fuente**, **ingesta
común**, **modelo de datos / lectura**, **API**, **SPA**, **docs** — ver "Capas" arriba),
apoyándose en la skill `karpathy-guidelines` al generar código, sin delegar en subagentes de
dominio salvo que el proyecto crezca y se registren agentes locales.

## Pipelines

- Directorio de features: `local/features/<NNN>-<slug>/`
- Directorio de auditorías: `local/audits/<NNN>-<slug>/`
- Directorio de troubleshooting: `local/troubleshoot/<NNN>-<slug>/`
- Numeración: secuencial de 3 dígitos por directorio (mirar el mayor existente y sumar 1; hoy el
  mayor en `local/features/` es `013-spa-contrato-nuevo`, la próxima feature es `014-...`).

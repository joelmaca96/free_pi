# baskonia-pipeline

> Pipeline de captura de datos de baloncesto (ACB, Euroliga y la web oficial del Baskonia)
> hacia una base de datos SQLite de scouting, más una interfaz Streamlit de solo lectura sobre
> esos datos (`app/`). La ingesta (`ingest/`) sigue siendo el núcleo: descarga, normaliza y
> persiste; la interfaz solo consulta lo que la ingesta ya dejó en `data/baskonia.db`.

---

## 1. Qué hace

Tres módulos de ingesta independientes, cada uno con su propio cliente HTTP, adaptador al
contrato común y parser, orquestados por `ingest/run_all.py`:

- **`ingest/acb/`** — Liga Endesa vía la API real de acb.com (`api2.acb.com`). La fuente más
  completa: calendario, estadísticas avanzadas oficiales, boxscore por jugador y por cuarto,
  quintetos reconstruidos desde play-by-play, tiros con coordenadas reales.
- **`ingest/euroleague/`** — Euroliga vía la librería `euroleague_api`. Mismo tipo de datos que
  ACB, con ORtg/DRtg/pace estimados (fórmula Dean Oliver) al no haber un endpoint oficial
  equivalente.
- **`ingest/baskonia_web/`** — plantilla y fotos del Baskonia desde `baskonia.com` (JSON
  embebido de la web oficial). Solo escribe `players`/`player_external_ids`; además descarga a
  disco (`data/player_photos/` por defecto) la foto real de cada jugador, para no depender de
  baskonia.com al servir la interfaz.

Un módulo que falla no detiene a los demás: `run_all` reporta `{"loaded": [...], "failed":
[...]}` por módulo.

Piezas compartidas en `ingest/common/`:

- `raw_game.py` — contrato único "raw game dict" que produce cada adaptador de fuente;
  `parse_and_resolve()` resuelve identidad y lo vuelca al esquema.
- `identity.py` — resolución de equipo/jugador entre fuentes (`team_external_ids`/
  `player_external_ids`, con fallback a nombre normalizado / dorsal+equipo).
- `loader.py` — upsert idempotente contra el esquema de scouting (natural keys con `ON
  CONFLICT DO UPDATE`; borrar-y-reinsertar para tablas sin clave natural como
  `lineups`/`shots`/`key_events`).
- `lineups.py` + `game_clock.py` — reconstrucción de quintetos a partir de play-by-play,
  agnóstica de fuente.

El estado detallado por fuente (qué tabla llena cada una, qué falta, limitaciones conocidas)
vive en [doc/features/ingestor/01_estado.md](doc/features/ingestor/01_estado.md) — es la
referencia más fiable, más que la narrativa de este README si llegaran a discrepar.

---

## 2. Modelo de datos

El esquema vive en
[`packages/baskonia_core/db/scouting/schema.sql`](packages/baskonia_core/db/scouting/schema.sql)
(fuente de verdad; ver también [doc/arquitectura/04_db_map.md](doc/arquitectura/04_db_map.md)
para un análisis histórico de un esquema anterior ya retirado). Tablas principales: `seasons`,
`competitions`, `teams` (+ `team_external_ids`), `players` (+ `player_external_ids`), `games`,
`game_advanced_stats`, `game_team_quarter_stats`, `player_game_stats`, `lineups` +
`lineup_players`, `game_zone_stats`, `shots`, `key_events`, `score_progression`,
`upcoming_matchups`, más vistas de agregación por competición y combinadas.

`packages/baskonia_core/db/scouting/engine.py` crea el engine (SQLite con `foreign_keys=ON` y
`busy_timeout`) y `init_scouting_db()` ejecuta `schema.sql` (DDL + datos semilla) si el esquema
no existe todavía.

---

## 3. Configuración (`.env`)

Copiar `.env.example` a `.env` y ajustar si hace falta:

| Variable | Descripción | Default |
|---|---|---|
| `DATABASE_URL` | URL de la base de datos | `sqlite:///data/baskonia.db` |
| `USER_AGENT` | User-Agent para las peticiones HTTP | Chrome/120 |
| `REQUEST_DELAY` | Segundos entre peticiones (fuentes que lo requieran) | `20` |
| `SEASON` | Año de inicio de temporada por defecto | `2026` |
| `TEAMS` / `LEAGUES` | Configuración heredada de un pipeline anterior; ver `ingest/run_all.py` para el flujo vigente | — |
| `LAST_N_GAMES` | Nº de partidos recientes por equipo (uso histórico) | `10` |

---

## 4. Cómo ejecutar

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # ajustar si hace falta

# Crea el esquema si no existe
python tools/init_scouting_db.py

# Ingesta completa de una temporada (baskonia_web -> acb -> euroleague, en ese orden)
python -m ingest.run_all --season 2025
python -m ingest.run_all --season 2025 --skip euroleague   # omite un módulo (repetible)

# O cada fuente por separado
python -m ingest.acb.cli --season 2025
python -m ingest.euroleague.cli --season 2025
python -m ingest.baskonia_web.cli
```

`tools/init_scouting_db.py --force` borra y recrea el esquema (con los datos semilla) — solo
para desarrollo/tests, nunca contra `data/baskonia.db` con datos reales sin backup previo.

---

## 5. Interfaz web (`app/`)

Streamlit de un solo proceso, de **solo lectura** sobre `data/baskonia.db` — no crea el
esquema ni escribe nunca; si la base de datos no está inicializada, falla con un mensaje claro
en vez de intentar poblarla (eso sigue siendo trabajo de `ingest/`/`tools/init_scouting_db.py`).
Cinco pantallas: estado del equipo (récord, calendario, carga de minutos), plantilla (galería
+ detalle por jugador), próximo rival (scouting completo), partidos anteriores (selector +
detalle: parciales, avanzadas, boxscore, tiros, quintetos) y **asistente** (chat en lenguaje
natural sobre los datos cargados).

### Asistente de scouting (`app/assistant/`)

Chat con herramientas: el modelo **no escribe SQL** — elige qué herramienta llamar de un
catálogo de ~25 (jugador, equipo, liga, quintetos, comparación) cuyas cifras salen de SQL fijo
y probado, y redacta a partir de ellas. Toda respuesta lleva su procedencia y su traza de
consultas, y las cifras que no aparezcan en ningún resultado de herramienta se marcan como sin
verificar.

Es **opcional**: sin `ASSISTANT_LLM_BASE_URL`/`ASSISTANT_LLM_MODEL` la pestaña lo explica y las
otras cuatro funcionan igual. Cambiar de proveedor de modelo (LLM local, Groq, Cerebras,
Cloudflare Workers AI, OpenRouter, y Claude en el futuro) es cambiar variables de entorno, no
código — ver [`.env.example`](.env.example) y el diseño.

```bash
# Comprobar que el modelo configurado sabe usar herramientas, antes de nada
.venv/Scripts/python.exe tools/assistant_smoke_test.py
```

Diseño completo en
[`local/features/005-chatbot/01_design.md`](local/features/005-chatbot/01_design.md).

```bash
# Desarrollo local (desde la raíz del repo, con el venv activado)
.venv/Scripts/streamlit.exe run app/Home.py
```

```bash
# Despliegue (p.ej. Raspberry Pi) — Streamlit + túnel de Cloudflare, sin
# exponer puertos en el router. Ver deploy/cloudflared/README.md.
docker compose up -d --build
```

Diseño completo (arquitectura, contratos de datos, decisiones) en
[`local/features/001-interfaz-baskonia/01_design.md`](local/features/001-interfaz-baskonia/01_design.md).

---

## 6. Tests

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest                  # ejecuta toda la suite
```

Suite 100% offline (mocks/fixtures que replican payloads reales verificados en vivo, sin red
real). El asistente no rompe esa propiedad: el bucle de agente se prueba con un cliente de LLM
falso que implementa el mismo `Protocol` que los adaptadores reales, y los adaptadores con
respuestas HTTP grabadas de cada dialecto. Ubicaciones:

- `tests/ingest/` — por fuente (`test_acb.py`, `test_acb_client.py`, `test_euroleague.py`,
  `test_baskonia_web.py`) y compartido (`test_identity.py`, `test_lineups.py`,
  `test_loader.py`, `test_run_all.py`, `test_raw_game_lineups.py`).
- `tests/app/` — piezas de la interfaz con lógica pura (`test_avatar.py`, `test_court.py`,
  `test_queries.py`) y `tests/app/assistant/` (resolución de entidades, herramientas, guardas
  de SQL, bucle de agente con un cliente de LLM falso, verificador de cifras y topes de uso).
- `tests/test_scouting_db.py` — carga del esquema (`schema.sql`) contra SQLite en memoria.

Los tests nunca tocan `data/baskonia.db` real.

---

## 7. Notas / Riesgos

- **Rate limiting de Euroliga sin resolver a escala de temporada completa**: `euroleague_api`
  no aplica throttling propio; el backoff actual no basta para un backfill fiable de ~400
  partidos de una sentada (ver `doc/features/ingestor/01_estado.md` §2.3).
- **Equipos duplicados por cambio de patrocinador entre temporadas**: la normalización de
  nombres (`ingest/common/identity.py`) cubre los alias conocidos a mano; un patrocinador
  nuevo no listado crea un equipo duplicado hasta que se añada su alias.
- **No exponer credenciales/API keys en el código**; usar variables de entorno. Ver
  [doc/acb_endpoints.md](doc/acb_endpoints.md) para el detalle de los endpoints de acb.com
  usados (incluida la `x-apikey` observada, que puede rotar).

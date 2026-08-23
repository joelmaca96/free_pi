# baskonia-pipeline

> Pipeline de captura de datos de baloncesto (ACB, Euroliga y la web oficial del Baskonia)
> hacia una base de datos SQLite de scouting. Es una aplicación de **ingesta de datos**: no
> incluye interfaz de usuario ni API — solo descarga, normaliza y persiste.

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
  embebido de la web oficial). Solo escribe `players`/`player_external_ids`.

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

## 5. Tests

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest                  # ejecuta toda la suite
```

Suite 100% offline (mocks/fixtures que replican payloads reales verificados en vivo, sin red
real). Ubicaciones:

- `tests/ingest/` — por fuente (`test_acb.py`, `test_acb_client.py`, `test_euroleague.py`,
  `test_baskonia_web.py`) y compartido (`test_identity.py`, `test_lineups.py`,
  `test_loader.py`, `test_run_all.py`, `test_raw_game_lineups.py`).
- `tests/test_scouting_db.py` — carga del esquema (`schema.sql`) contra SQLite en memoria.

Los tests nunca tocan `data/baskonia.db` real.

---

## 6. Notas / Riesgos

- **Rate limiting de Euroliga sin resolver a escala de temporada completa**: `euroleague_api`
  no aplica throttling propio; el backoff actual no basta para un backfill fiable de ~400
  partidos de una sentada (ver `doc/features/ingestor/01_estado.md` §2.3).
- **Equipos duplicados por cambio de patrocinador entre temporadas**: la normalización de
  nombres (`ingest/common/identity.py`) cubre los alias conocidos a mano; un patrocinador
  nuevo no listado crea un equipo duplicado hasta que se añada su alias.
- **No exponer credenciales/API keys en el código**; usar variables de entorno. Ver
  [doc/acb_endpoints.md](doc/acb_endpoints.md) para el detalle de los endpoints de acb.com
  usados (incluida la `x-apikey` observada, que puede rotar).

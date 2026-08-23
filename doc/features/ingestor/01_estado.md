# Estado del ingestor de datos (`ingest/`)

Documento de estado (no de diseño): qué hay implementado hoy en el pipeline de ingesta y qué
falta. Refleja el código en `ingest/` a fecha 2026-08-21. Para el histórico de decisiones/
descubrimientos ver la memoria de repo; esto es solo la foto actual.

## 1. Arquitectura

Tres módulos independientes (`ingest/acb/`, `ingest/euroleague/`, `ingest/baskonia_web/`), cada
uno con su propio cliente HTTP + adaptador al contrato común + parser fino, orquestados por
`ingest/run_all.py` (orden: `baskonia_web` → `acb` → `euroleague`; un módulo que falla no detiene
a los demás, se reporta `{"loaded": [...], "failed": [...]}` por módulo).

- `ingest/common/raw_game.py` — contrato único "raw game dict" que produce cada adaptador de
  fuente; `parse_and_resolve()` resuelve identidad (equipos/jugadores/temporada) y lo vuelca al
  esquema. Un solo sitio para esa lógica, no duplicada por fuente.
- `ingest/common/identity.py` — resolución de equipo/jugador entre fuentes vía
  `team_external_ids`/`player_external_ids` (con fallback a nombre normalizado / dorsal+equipo).
- `ingest/common/loader.py` — upsert idempotente contra `packages/baskonia_core/db/scouting`
  (natural keys con `ON CONFLICT DO UPDATE` donde existen; borrar-y-reinsertar para tablas sin
  clave natural como `lineups`/`shots`/`key_events`).
- `ingest/common/lineups.py` + `game_clock.py` — reconstrucción de quintetos a partir de
  play-by-play (sustituciones + puntos), fuente-agnóstica.
- Esquema destino: `packages/baskonia_core/db/scouting/schema.sql` (ver ese fichero para la
  lista completa de tablas/columnas).

Suite completa: **223 passed, 2 skipped** (offline al 100%, sin red real en tests — mocks/
fixtures que replican las formas de payload verificadas en vivo).

## 2. Estado por fuente

### 2.1 `baskonia_web` — roster/fotos del Baskonia — ✅ completo

- Scrapea `https://www.baskonia.com/plantilla` (JSON embebido `<script id="serverApp-state">`,
  no HTML de tarjetas). Respeta `robots.txt`, User-Agent identificable.
- Escribe **solo** `players` + `player_external_ids` (`source='baskonia_web'`); nunca toca
  partidos/boxscores. Upsert idempotente; marca `active=0` (no borra) a jugadores que
  desaparecen de la plantilla scrapeada.
- Campos reales: nombre, dorsal (puede ser `None` en fichajes recientes), posición (español),
  foto (`None` si es la silueta genérica del sitio, no una foto real), fecha de nacimiento,
  nacionalidad.
- **Falta / limitación conocida:** `height_cm` siempre `NULL` — esa página no publica altura en
  ningún campo (no es un bug, es una ausencia real de dato en la fuente).

### 2.2 `acb` — Liga Endesa vía la API real de acb.com — ✅ el más completo de los tres

Usa la API real del frontend actual de acb.com (`api2.acb.com/api/{seasondata,matchdata}`,
auth `x-apikey`) — **no** la de OpenACB (`openapilive`), que está muerta (409 confirmado incluso
replicando la petición exacta del propio scraper R de OpenACB).

Implementado y poblando datos reales:

| Tabla | Estado |
|---|---|
| `games` | ✅ calendario completo por temporada (recorrido de `weekId` hacia atrás desde la última semana disponible hasta el límite real de la edición) |
| `game_advanced_stats` | ✅ **con los números OFICIALES de acb.com** (`AdvancedStats/match-advanced-stats`: posesiones/pace/ortg/drtg/net_rating/four factors/ast·stl·blk %), no una estimación propia — solo `ast_to_ratio` se calcula desde el boxscore (no viene en ese endpoint) |
| `game_team_quarter_stats` | ✅ puntos anotados/encajados por cuarto (desde el boxscore por cuarto) |
| `player_game_stats` | ✅ boxscore completo por jugador |
| `lineups` + `lineup_players` | ✅ reconstruidos desde play-by-play real (quinteto inicial + sustituciones, `playType` decodificado en vivo) |
| `shots` | ✅ con coordenadas reales (`MatchShots/match-shots`), tiros libres excluidos (no traen coordenadas) |
| `game_zone_stats` | ✅ agregado automáticamente desde `shots` por el loader |
| `score_progression` | ✅ derivado del play-by-play (deduplicado por cambio de marcador) |

**Falta / limitaciones conocidas:**
- **Clutch stats** (últimos 5 min ± 5 puntos) y **segmentos de 2 minutos**/eficiencia
  post-tiempo-muerto: el play-by-play ya tiene marcas de tiempo (`quarter`/`minute`/`second`),
  así que es **técnicamente posible** implementarlo — simplemente no se ha hecho.
- **On/off de quintetos** (impacto de combinaciones de jugadores más allá de minutos/±): no
  implementado, requeriría agregación adicional sobre los quintetos ya reconstruidos.
- Un puñado de partidos concretos devuelven `500` real desde el propio endpoint de boxscore de
  acb.com (~10 de ~280 en una temporada) — no es un bug nuestro, son huecos de datos del lado
  del servidor; el orquestador los reporta como fallidos y sigue con el resto.
- No hay endpoint de fotos/roster vía esta fuente (se cubre por `baskonia_web` para el Baskonia;
  para el resto de equipos no hay fuente de fotos).

### 2.3 `euroleague` — Euroliga vía `euroleague_api` — ⚠️ funciona, pero no a escala de temporada completa

- Librería Python `euroleague_api` (métodos reales verificados por introspección directa del
  paquete instalado, no adivinados): `Schedule.get_schedule`,
  `BoxScoreData.get_players_boxscore_stats`, `ShotData`, `PlayByPlay`.
- Igual que ACB: `games`/`game_advanced_stats` (efg/ts/tov/orb% + ortg/drtg/pace estimados con
  la fórmula Dean Oliver, aquí sí porque **no** hay un endpoint oficial equivalente al de ACB)/
  `player_game_stats`/`shots` (con coordenadas reales)/`lineups` (reconstruidos desde
  play-by-play, igual que ACB).
- **Falta / limitaciones conocidas:**
  - **Rate limiting sin resolver a escala de temporada completa.** `euroleague_api` no aplica
    ningún throttling propio; se añadió una pausa (3 s/partido) + reintento con backoff (3
    intentos, 10 s), verificado con un puñado de partidos, pero en una ejecución real de
    temporada completa (~400 partidos) el resultado fue **58 cargados / 344 fallidos** — el
    backoff actual **no es suficiente** para completar un backfill de temporada entera de forma
    fiable. Pendiente: backoff más agresivo/exponencial, o una estrategia de reanudación
    (checkpoint de qué partidos ya se cargaron) en vez de reintentar toda la temporada.
  - `stl_pct`/`blk_pct` en `game_advanced_stats` usan columnas (`Steals`/`BlocksFavour`)
    **asumidas, no verificadas en vivo** (a diferencia de todo lo demás en este módulo) —
    degradan a 0 en silencio si el nombre real difiere.
  - `quarter_stats` solo se rellena si se pasa `play_by_play_records` explícitamente (no hay
    boxscore-por-cuarto como en ACB; se deriva sumando eventos de anotación por cuarto).
  - `position` de jugador siempre `None` (no viene en el boxscore de `euroleague_api`).
  - No hay endpoint de estadísticas avanzadas oficiales equivalente al de ACB — todo
    (ortg/drtg/pace) es estimación propia (Dean Oliver), no dato oficial de Euroliga.

## 3. Qué falta en conjunto (independiente de la fuente)

- **`apps/api` y la SPA ya consumen el esquema nuevo** que este ingestor rellena: la feature
  012 (`api-nuevo-modelo-datos`) reescribió `apps/api/` contra el esquema de scouting y la
  feature 013 (`spa-contrato-nuevo`) adaptó `apps/web/` a ese contrato (ver
  `doc/features/spa-contrato-nuevo/01_estado.md`). Los datos que carga este ingestor llegan hoy
  a `apps/web` vía la API.
- **Equipos duplicados por cambio de patrocinador entre temporadas**: la normalización de
  nombres (`ingest/common/identity.py`, `_KNOWN_TEAM_ALIASES`) cubre los casos conocidos
  detectados hasta ahora (p.ej. "Kosner Baskonia"/"Bitci Baskonia"), pero es una lista
  mantenida a mano, no una solución genérica — un patrocinador nuevo no listado crearía un
  equipo duplicado hasta que se añada su alias.
- **Euroliga a escala de temporada completa** (ver 2.3) es el hueco más urgente si se quiere un
  backfill fiable de ambas competiciones de una sentada.
- **Clutch stats / on-off / segmentos de 2 min** (ver 2.2): con los datos ya disponibles de ACB
  es la ampliación más barata de construir si se necesita más adelante.

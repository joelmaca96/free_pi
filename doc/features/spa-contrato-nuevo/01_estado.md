# Estado de la SPA (`apps/web/`) — contrato nuevo de la API

Documento de estado (no de diseño): qué consume hoy la SPA React (`apps/web/`) de la API
FastAPI (`apps/api/`) y qué cambió la feature 013 (`spa-contrato-nuevo`) para adaptarla al
contrato nuevo. Refleja el código en `apps/web/` a fecha 2026-08-21. Para el histórico de
decisiones ver `local/features/013-spa-contrato-nuevo/01_design.md`; esto es solo la foto actual.

## 1. Contexto

La feature 012 (`api-nuevo-modelo-datos`) reescribió `apps/api/` contra el esquema de scouting
(`packages/baskonia_core/db/scouting/schema.sql`), cambiando el **contrato público** que consume
la SPA. La feature 013 adaptó todas las pantallas, componentes y hooks de la SPA a ese contrato
nuevo y, además, amplió la API para que el endpoint `/games/{game_id}/boxscore` devuelva
`team_id` en cada fila (cambio aditivo). Tras la 013, `npm run build` en `apps/web` compila sin
errores de TypeScript y la SPA funciona contra la API real.

## 2. Contrato que consume la SPA (contrato nuevo)

Identidad y filtros (cambios respecto al contrato viejo de la sección 5 de
`doc/arquitectura/01_design.md`):

| Concepto | Contrato nuevo (schema.d.ts) |
|---|---|
| Identidad de equipo | `team_id` **TEXT** (`'bas'`, `'rm'`, `'fcb'`…); `TeamRef { id, name }`. **Sin `slug`** |
| Identidad de partido | `game_id` **TEXT** (`'g1'`, `'acb-105370'`…) |
| Filtro temporada | `season_label` (str `'2025-2026'`) en `SeasonFilter = { seasonLabel }` |
| Filtro competición | `competition` (str en la URL) ↔ `CompetitionOption.id` (**number** en la API) |
| Boxscore | `useBoxscore(gameId)` (1 arg); filas de **ambos equipos** con `team_id` (campo nuevo de la 013) |

Tipos principales que consume la SPA (de `apps/web/src/api/schema.d.ts`):

- **`BoxScoreRow`**: `game_id`, `player_id`, `name`, **`team_id`** (nuevo en la 013), `minutes`,
  `pts`, `reb`, `ast`, `efg_pct`. (Antes: `player_name`, `points`, `rebounds`, `assists`,
  `ts_pct`; sin `team_id`.)
- **`PlayerFormItem`** — **por partido** (no agregado por jugador): `game_id`, `game_date`,
  `pts`, `reb`, `ast`, `efg_pct`.
- **`LoadItem`**: `player_id`, `name`, `total_minutes`. (Antes: `player_name`, `games`,
  `avg_minutes`.)
- **`RosterPlayer`** — con **medias directas** de temporada: `gp`, `min_avg`, `pts_avg`,
  `reb_avg`, `ast_avg`, `efg_pct`. **Sin campo `form`**.
- **`Projection`**: `predicted_net_rating`, `predicted_pace`, `predicted_ortg`,
  `expected_margin`. (Antes: `projected_possessions`, `team_projected_score`,
  `opp_projected_score`.)
- **`DifficultyOpponent`**: `match_date`, `predicted_net_rating`, `predicted_pace`,
  `predicted_ortg`, `opponent_name`, `has_scouting_data`, `key_player_note`, `h2h_*`.
  (Antes: `date`, `net_rating`.)
- **`GameItem`** / **`GameAdvanced`**: `GameAdvanced` con `ortg`/`drtg`/`net_rating`/`efg_pct`/
  `ts_pct`/four factors (sin `pace`/`off_rating`/`def_rating`); `pace` vive en `GameItem.pace`.
- **`GameDetailResponse`**: `id` (string), `season_label`, `competition_name`, `home_team`/
  `away_team` (`TeamRef`), `game_date`, `home_score`/`away_score`, `pace`, `narrative`,
  `baskonia` (`BaskoniaBlock`), `advanced[]`, `lineups[]`, `zone_stats[]`, `key_events[]`.
- **`HeadToHeadGame`**: `id` (string), `date`, `competition_name`, `team_score`,
  `opponent_score`, `result`.

Hooks disponibles (`apps/web/src/api/hooks.ts`): `useTeams`, `useTeam(teamId)`,
`useFilters(teamId)`, `useTeamSummary(teamId, filter)`, `useTeamGames(teamId, filter, …)`,
`useRoster(teamId, filter)`, `usePlayerForm(teamId, filter, lastN)`,
`usePlayerLoad(teamId, windowDays)`, `useNarrative(teamId)`,
`useScheduleDifficulty(teamId, nextN)`, `useProjection(teamId, opponentId, filter)`,
`useHeadToHead(teamId, opponentId, filter)`, `useBoxscore(gameId)`.

## 3. Cambios de la feature 013

### 3.1 API — `team_id` en el boxscore (cambio aditivo)

- `packages/baskonia_core/db/scouting/repository.py` — `get_game_boxscore`: el SELECT incluye
  `p.team_id` (el `JOIN players p` ya existía).
- `apps/api/schemas/games.py` — `BoxScoreRow` añade `team_id: str`.
- `apps/api/mappers.py` — `boxscore_row` mapea `team_id=row["team_id"]`.
- Regenerados `openapi.json` y `apps/web/src/api/schema.d.ts` (cambio aditivo en `BoxScoreRow`).

### 3.2 SPA — adaptación al contrato nuevo

- **`BoxscoreTable.tsx`**: `useBoxscore(gameId)` (1 arg); filtra filas por `r.team_id === teamId`
  y renderiza **dos tablas** (una por equipo) con el `TeamLogo`/nombre en el encabezado. Se
  eliminó la columna "TS%" (la API no la da en el boxscore). `parseMinutes` acepta
  `number | string | null | undefined` (la API devuelve `minutes` numérico).
- **`GameDetail.tsx`**: `GameDetailGame` con `id: string`, `opponentId`, `opponentName`,
  `teamScore`, `opponentScore`, `pace`, `netRating`; props `{ game, selfId, selfName }`; usa
  `useTeamGames(game.opponentId, { seasonLabel: null })` para el advanced del rival.
- **`TeamOverviewPanel.tsx`**: prop `teamSlug` → `teamId`; `filter` pasa a
  `SeasonFilter = { seasonLabel }`. **Eliminada** la sección "Rachas (hot/cold)" (usa
  `useStreaks`, eliminado). `useNarrative(teamId)` (1 arg). "Forma reciente" reescrita como
  **serie temporal por partido** (BarChart de PTS + tabla por partido). Tabla de carga con
  columnas `Jugador` y `MIN totales`. **Eliminada** la StatCard "Pace" (la API ya no la da en
  `AdvancedSummary`). `GameRow` usa `ortg`/`drtg`; `pace` de `GameItem.pace`.
- **`PlantillaScreen.tsx`**: `teamSlug` → `teamId`; la tarjeta de jugador muestra las **medias
  directas** del roster (`gp`, `min_avg`, `pts_avg`, `reb_avg`, `ast_avg`, `efg_pct`).
  **Eliminado** `usePlayerForm` de "toda la temporada" y el mapeo `seasonStatsByPlayer`.
- **`ProximosScreen.tsx`**: `teamSlug` → `teamId`; `useScheduleDifficulty(teamId, nextN)` (2
  args); `useProjection(teamId, opponentId, filter)` (3 args); `useHeadToHead(teamId,
  opponentId, filter)` (3 args); `selectedId` pasa a `string`; `game.opponent.id`. La sección
  "Scouting: {rival}" muestra directamente `TeamOverviewPanel teamId={game.opponent.id}`.
- **`ResumenScreen.tsx`** / **`AnterioresScreen.tsx`**: `teamSlug` → `teamId`;
  `filter = { seasonLabel: filters.seasonLabel }`; `selectedId` pasa a `string`;
  `game.opponent.id`; `GameDetail` recibe `selfId`/`selfName`.
- **`Filters.tsx`**: `LeagueSelect` acepta `CompetitionOption.id` como `number` (el `value` del
  `<option>` se serializa a string; cambio de tipo, no de comportamiento).
- **`TeamLogo.tsx`**: prop `slug` → `teamId`; mapeo `TEAM_ID_TO_LOGO_SLUG`
  (`bas`→`vitoria`, `rm`→`real-madrid`, `fcb`→`barcelona`, `bay`→`bayern-muenchen`,
  `baxi`→`manresa`, `jb`→`joventut`, `val`→`valencia`, `gc`→`gran-canaria`,
  `uni`→`unicaja-malaga`) con fallback `TEAM_ID_TO_LOGO_SLUG[teamId] ?? teamId`.
- **`Layout.tsx`** / **`routes.tsx`**: `teamSlug` → `teamId`; parámetro de ruta `/:teamSlug` →
  `/:teamId`.

### 3.3 Eliminado (componentes muertos y secciones)

- **`ScoutRivalPanel.tsx`** — eliminado (usaba hooks eliminados `useEnqueueScout`/
  `useScoutStatus`/`invalidateTeamData`). El **scouting bajo demanda** (botón "Descargar datos
  de `<rival>`" que lanzaba `fetch_opponent_scouting()`) ya no existe en la SPA: la sección
  "Scouting: {rival}" muestra directamente `TeamOverviewPanel`.
- **`ExportButton.tsx`** — eliminado. Los botones de export **PDF/PPTX** ("Informe en PDF",
  "Generar ppt para Paolo") ya no existen (los endpoints `/reports/scouting.pdf` y
  `/reports/roster.pptx` se eliminaron en la feature 012).
- **Sección "Rachas (hot/cold)"** — eliminada (endpoint `useStreaks` eliminado en la 012).

## 4. Desviaciones conocidas

- **`parseMinutes`** (`apps/web/src/lib/boxscore.ts`): firma ampliada a
  `number | string | null | undefined` porque la API devuelve `minutes` numérico (no "MM:SS").
- **`streakBadge`/`StreakKind`** (`apps/web/src/lib/format.ts`): quedaron sin uso tras eliminar
  la sección "Rachas". Se conservan como export (no rompen el build); limpiables en una pasada
  posterior.
- **Forma reciente**: `usePlayerForm` devuelve la forma **por partido de un único jugador** (el
  primero de la plantilla si no se pasa `player_id`). La sección se reescribió como serie
  temporal por partido (degradado documentado en el diseño).

## 5. Verificación

- `npm run build` en `apps/web` (tsc -b + vite build): **OK** (689 módulos, sin errores TS).
  Solo warning preexistente de chunk > 500 kB.
- `pytest` (backend): **verde** (233 passed, 2 skipped), incluidos los tests ampliados de
  boxscore con `team_id`.
- Gate de contrato: grep en `apps/web/src` de `teamSlug`, `useStreaks`, `useEnqueueScout`,
  `useScoutStatus`, `invalidateTeamData`, `opponent.slug`, `player_name`, `avg_pts`,
  `avg_minutes`, `projected_possessions`, `team_projected_score`, `opp_projected_score`,
  `.form`, `off_rating`, `def_rating`, `avg_pace` → **sin coincidencias**.

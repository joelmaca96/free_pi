# Plan: traer todas las estadísticas disponibles de ACB/Euroliga e integrarlas en la app

Documento de plan (no de estado): qué falta por ingerir de las dos fuentes reales y cómo traerlo,
fase a fase, con la superficie de UI/asistente y el análisis de scouting que habilita cada bloque.
Escrito 2026-08-27, tras una investigación en vivo contra `api2.acb.com` y `live.euroleague.net`
(peticiones reales, no documentación de terceros). Complementa a [01_estado.md](01_estado.md) — su
§3 "Qué falta en conjunto" es el resumen ejecutivo de estos huecos; este documento es el plan
ejecutable para cerrarlos.

## Contexto

Hoy se descarta aproximadamente la mitad de cada boxscore por jugador (faltas cometidas/recibidas,
robos, tapones, pérdidas, rebote ofensivo/defensivo separado, +/-, valoración/PIR), y endpoints
enteros nunca se llaman: estadísticas avanzadas oficiales **por jugador** (no solo equipo), el
jugada-a-jugada completo (solo se decodifican 9 de 37 códigos `playType` en ACB; el adapter de
Euroliga descarta explícitamente "pérdidas, faltas, rebotes" de su PBP,
[ingest/euroleague/adapter.py:236](../../../ingest/euroleague/adapter.py#L236)), boxscore por
cuarto, y metadata de partido (árbitros, asistencia, pabellón, entrenadores) — que además **ya
viaja en las respuestas que se descargan hoy**, solo no se lee.

Objetivo: ingerir todo lo que las fuentes dan de verdad (nada inventado ni estimado salvo lo que ya
se estima hoy, p.ej. `ortg`/`drtg` de Euroliga), integrarlo en las páginas Streamlit y en el
asistente conversacional, y documentar para cada bloque de datos qué pregunta de scouting responde.

Se ejecuta en **6 fases independientes y desplegables por separado** (cada una dejando tests en
verde y la app funcional), en vez de una única migración masiva: el propio código ya sigue esta
disciplina (columnas nullable, sondeo de capacidades, degradación en vez de rotura) y romperla para
ir más rápido introduciría justo el tipo de fallo silencioso que el proyecto ha ido corrigiendo
(`ftm`/`fta` huérfanas 2026-08-24, columnas Euroliga "asumidas no verificadas").

Convención de commits/PRs: una fase = una o varias PRs pequeñas, cada una con su migración,
adapter, tests, vista/query y superficie de UI/asistente correspondiente — nunca "todo el esquema
primero, la UI después".

---

## Mecanismo a reutilizar en TODAS las fases (ya existe, no se toca su forma)

- **Columna nueva en tabla existente** → tupla en `_ADDITIVE_COLUMN_MIGRATIONS`
  ([engine.py:78-91](../../../packages/baskonia_core/db/scouting/engine.py#L78-L91)) + la misma
  columna `NULLABLE` en `schema.sql`. Nunca `NOT NULL` sin `DEFAULT` (SQLite lo exige en
  `ADD COLUMN`).
- **Tabla nueva** → `CREATE TABLE` en `schema.sql` + su nombre en `_ADDITIVE_TABLES`
  ([engine.py:65](../../../packages/baskonia_core/db/scouting/engine.py#L65)) y en `TABLE_NAMES`
  (modo `--force`). El DDL se extrae por regex de `schema.sql`, una sola fuente de verdad.
- **Vista nueva/ampliada** → solo editar `schema.sql` (se recrea sola en cada arranque); vista
  *nueva* además a `VIEW_NAMES`
  ([engine.py:44-56](../../../packages/baskonia_core/db/scouting/engine.py#L44-L56)).
- **Carga**: columnas con clave natural real (`player_game_stats`, `game_advanced_stats`) → añadir a
  `_upsert_*` en `ingest/common/loader.py` (INSERT + `:params` + `ON CONFLICT ... DO UPDATE`, 3
  sitios a tocar). Tablas "detalle" nuevas sin clave natural (eventos, boxscore por cuarto) →
  patrón "borrar por `game_id` y reinsertar" como `_replace_key_events`/`_replace_shots`.
- **Contrato común**: campo opcional nuevo en la dataclase de `ingest/common/schema_types.py` →
  `.get(...)` en `parse_and_resolve` (`ingest/common/raw_game.py`) → el dict que arma
  `build_raw_game` de **cada** adapter (`ingest/acb/adapter.py`, `ingest/euroleague/adapter.py`).
- **Sondeo de capacidad** (para que el asistente nunca prometa un dato que esta BD concreta no
  tiene todavía): nuevo booleano en `Capabilities`
  ([capabilities.py:24-57](../../../app/assistant/capabilities.py#L24-L57)), comprobando **columna
  presente Y con dato real** (no solo `ALTER TABLE` ya aplicado), más su frase en
  `missing_summary()`. Las tools que dependan de él llevan `requires="nombre_flag"` en
  `@register(...)`.
- **Tests**: clonar `test_init_backfills_columns_added_after_a_db_was_created` /
  `test_init_creates_tables_added_after_a_db_was_created`
  ([tests/test_scouting_db.py](../../../tests/test_scouting_db.py)) por cada columna/tabla nueva;
  sumar campos a `_sample_game()` y sus aserciones en `tests/ingest/test_loader.py`; test de tool
  con degradación "sin dato" igual que el de `ftm`/`fta` en
  `tests/app/assistant/test_tools.py`.

---

## Fase 0 — Quick win sin ingesta nueva (medio día)

`game_advanced_stats` **ya tiene** `ast_pct`, `stl_pct`, `blk_pct`, `ft_rate`, `ast_to_ratio`
(tasas de equipo) cargadas desde 2026-08-2x, pero ninguna query las selecciona
([app/data/queries.py:352-353](../../../app/data/queries.py#L352-L353) las omite del `SELECT`).

- Añadir esas 5 columnas al `SELECT` de `queries.game_advanced_stats` y
  `queries.team_advanced_profile`.
- Mostrarlas en `proximo_rival.py` "Perfil avanzado" (ya tiene un bloque de `st.metric` en
  columnas, [líneas 219-250](../../../app/pages/proximo_rival.py#L219-L250) — solo añadir métricas
  a la tupla existente).
- **Análisis que habilita ya, hoy**: % de canastas asistidas (estilo de equipo — ¿juego de balón o
  individual?), % de posesiones rivales robadas/taponadas (presión defensiva), ratio
  asistencia/pérdida real de equipo.

---

## Fase 1 — Boxscore ampliado por jugador y equipo (núcleo, mayor valor)

Todo confirmado en vivo en ambas fuentes, sin llamadas HTTP adicionales (ya viaja en el boxscore
que se descarga hoy):

| Columna nueva | ACB (campo real) | Euroliga (campo real) |
|---|---|---|
| `stl` | `steals` | `Steals` *(confirma que el nombre "asumido" en el código era correcto)* |
| `tov` | `turnovers` | `Turnovers` |
| `blk` | `blocks` | `BlocksFavour` |
| `blk_against` | `receivedBlocks` | `BlocksAgainst` |
| `pf` (faltas cometidas) | `personalFouls` | `FoulsCommited` *(sic, typo real de la API)* |
| `pf_drawn` (faltas recibidas) | `foulsDrawn` | `FoulsReceived` |
| `oreb` / `dreb` | `offRebounds` / `defRebounds` | `OffensiveRebounds` / `DefensiveRebounds` |
| `plus_minus` | `plusMinus` | `Plusminus` |
| `pir` (valoración) | `rating` | `Valuation` |
| `dunks` *(solo ACB, columna nullable)* | `dunks` | — |

**Schema**: mismas columnas en `player_game_stats` (nivel jugador) y `game_advanced_stats` (nivel
equipo, agregando los totales de equipo que cada fuente ya da en `stats.total`/`totr`) — todas
`INTEGER NULLABLE` salvo `pir` (puede ser `INTEGER`).

**Vistas** (`player_stats_combined`, `player_stats_by_competition`, `team_stats_combined`,
`team_stats_by_competition`): replicar el patrón exacto ya usado para `ftm`/`fta` — `SUM`/`AVG` +
`COUNT(columna) AS gp_x` (cobertura parcial explícita, NULL ≠ 0). Para equipo, replicar también el
self-join `opp_*` que ya hace `team_stats_combined`
([schema.sql:445-472](../../../packages/baskonia_core/db/scouting/schema.sql#L445-L472)) para
robos/tapones/pérdidas/faltas **concedidas** (rival del mismo `game_id`), no solo propias.

**Adapters**: `ingest/acb/adapter.py` (bucle `for row in stats["players"]`,
[líneas 393-424](../../../ingest/acb/adapter.py#L393-L424)) y `ingest/euroleague/adapter.py`
([líneas 321-342](../../../ingest/euroleague/adapter.py#L321-L342)) — añadir las claves nuevas al
dict de cada jugador y a `_team_totals`/equivalente de equipo.

**App**:
- `player_dialog.py` bloque "Medias"
  ([líneas 107-133](../../../app/components/player_dialog.py#L107-L133)): añadir columnas
  Rob/Tap/PP/Reb.Of/Reb.Def/Faltas/PIR/+/- al `st.columns(N)` existente.
- Boxscore de partido en `partidos_anteriores.py`
  ([líneas 176-191](../../../app/pages/partidos_anteriores.py#L176-L191)): ampliar
  `column_order`/`column_config`.
- Tarjetas de jugador (`plantilla.py`, roster de `proximo_rival.py`): añadir PIR junto a
  `pts_avg`.
- Récords de temporada en `player_dialog.py`
  ([líneas 144-150](../../../app/components/player_dialog.py#L144-L150)): extender a "máx.
  robos/tapones".

**Asistente**: ampliar `player_averages`, `player_game`, `team_style`, `game_boxscore`
(`app/assistant/tools/player.py`, `team.py`) con el bloque nuevo tras sondear
`Capabilities.box_extras` (mismo patrón que el bloque `free_throws` ya condicionado). Sumar las
claves nuevas a `LEADER_METRICS` (`app/data/queries_assistant.py:70-82`) para que entren en
`league_leaders`/`league_percentiles` sin tools adicionales.

**Análisis que habilita**:
- **Disciplina defensiva**: faltas cometidas vs recibidas por jugador/equipo — ¿juega limpio o al
  límite? ¿provoca faltas rivales (buen indicador de agresividad ofensiva real)?
- **Perfil defensivo individual**: robos+tapones normalizados por minuto — separa "defensor
  activo" de "defensor posicional".
- **Especialización de rebote**: ratio oreb/dreb — ala-pívots que cazan rechaces vs bases que
  cierran el rebote defensivo.
- **+/- individual real** (no solo por quinteto agregado, que ya existía vía
  `lineups.plus_minus`): impacto neto cuando ESE jugador está en pista, cruzable con memoria de
  quintetos.
- **PIR/valoración**: métrica única de rendimiento ya calculada oficialmente por la fuente,
  comparable entre jugadores de estilos distintos sin construir una fórmula propia.
- **Pérdidas por jugador**: identifica manejadores de balón de riesgo, cruzable con asistencias
  para un ratio AST/TOV individual real (hoy solo existe a nivel de equipo).

---

## Fase 2 — Play-by-play tipado: faltas y eventos con reloj exacto

**Tabla nueva** `play_events`: `game_id, team_id, player_id NULLABLE, quarter, game_clock,
seconds, event_type, event_detail NULLABLE, home_score, away_score`. Se llena con el mismo patrón
"borrar por `game_id` y reinsertar" que `key_events`/`shots` (`ingest/common/loader.py`,
`_replace_key_events`).

**No reutilizar `key_events`**: es texto editorial libre (`label`), no datos tipados consultables
por tipo de evento ni jugador — confirmado al inspeccionarla.

**Decodificación de `playType`/`PLAYTYPE`** (a completar en los adapters, siguiendo la disciplina
de comentarios "verificado en vivo" que ya usa el repo):

- ACB: ya decodificados 92/93/94/96/97/98/100 (tiros), 112/115/599 (subs/quinteto). Añadir,
  verificado empíricamente cruzando deltas de `playerStats` en el PBP real: `101`=rebote
  ofensivo, `104`=rebote defensivo, `103`=robo, `106`=pérdida, `102`=tapón, `107`/`108`/`119`=
  asistencia, `110`=falta recibida, y **`161`/`159`/`160`/`109`/`537`/`166`=falta personal** (6
  subtipos sin diferenciar todavía — se guarda el código crudo en `event_detail`; no bloquea el
  conteo agregado, que sigue viniendo de `personalFouls` del boxscore como fuente de verdad para
  el total).
- Euroliga: `PLAYTYPE` ya es texto legible — `CM`=falta cometida, `RV`=falta recibida, `ST`=robo,
  `TO`=pérdida, `FV`/`AG`=tapón dado/recibido, `O`/`D`=rebote of/def, `AS`=asistencia,
  `CCH`=challenge del entrenador. El propio comentario del adapter
  ([línea 236](../../../ingest/euroleague/adapter.py#L236)) ya admite que se descartan a
  propósito — solo hay que dejar de descartarlos.

**Derivado, no nueva llamada a fuente**: `game_team_quarter_stats.fouls_for`/`fouls_against`
(columnas nuevas nullable) se calculan agregando `play_events` por `game_id, team_id, quarter` en
el propio loader — unifica ACB y Euroliga con una sola fuente de verdad aunque el boxscore-por-
cuarto de Euroliga no dé faltas (ver Fase 3, asimetría).

**App**: gráfico de faltas por cuarto junto al de puntos por cuarto ya existente en
`partidos_anteriores.py`
([líneas 124-141](../../../app/pages/partidos_anteriores.py#L124-L141), mismo
`mark_bar()+xOffset`) y en "Rendimiento por cuarto" de `proximo_rival.py`
([líneas 332-373](../../../app/pages/proximo_rival.py#L332-L373)). Timeline de eventos de falta
con reloj en el tab Quintetos de `partidos_anteriores.py`
([líneas 242-246](../../../app/pages/partidos_anteriores.py#L242-L246)), mismo formato que
`key_events`.

**Asistente**: tool nueva `team_foul_quarter_profile` (clon exacto de `team_quarter_profile`,
[app/assistant/tools/team.py:221-245](../../../app/assistant/tools/team.py#L221-L245), artifact
`"bar"`) y `game_play_events`/extensión de `game_boxscore` para listar eventos de un partido.

**Análisis que habilita**:
- **Momento de acumulación de faltas** (la pregunta original): ¿el equipo/jugador se mete en
  problemas de faltas pronto (bonus temprano, titular que sale del partido)?
- **Foul trouble individual**: jugador con 2 faltas en el primer cuarto → impacto en minutos de
  rotación forzados, cruzable con `lineup_stints`.
- **Momentum**: rachas de pérdidas/robos concentradas en tramos del partido (¿se descontrola el
  manejo de balón en el último cuarto bajo presión?), visible con el mismo `seconds` que ya usa
  `lineup_stints` para tramos de quinteto.

---

## Fase 3 — Metadata de partido y boxscore por cuarto extendido

**Metadata** (`arena`, `attendance`, `referees`, `home_coach`, `away_coach` — columnas nullable en
`games`, 1:1 con el partido igual que el resto de columnas de esa tabla): **ya viaja en las
respuestas que se descargan hoy**, no requiere llamada HTTP nueva en ACB (top-level de
`Result/boxscores`: `arena`, `attendance`, `referees`; `headCoach`/`assistantCoaches` por equipo en
`teamBoxscores`). En Euroliga hay que **verificar en vivo** si `GameMetadata.get_game_metadata`
(ya se llama hoy, `ingest/euroleague/client.py:85-86`) mapea al endpoint `Header` que expone
`Referee1/2/3`, `Stadium`, `Capacity`, `CoachA/B`, `FoultsA/B` — si no, es una llamada nueva
(`Header`) a envolver en el cliente. Marcar explícitamente como spike de verificación antes de dar
por hecho el "sin coste".

**App**: cabecera de partido en `partidos_anteriores.py` con árbitro(s)/asistencia/pabellón. Con
suficiente muestra acumulada, agregado opcional futuro "¿cómo pita cada árbitro?" (faltas medias
pitadas por partido) — mencionado, no priorizado en esta fase.

**Boxscore por cuarto extendido (ACB-only, asimetría real)**: ACB da boxscore completo por
jugador **por cuarto** (`statsByPeriods`, quarters 1-4, mismos ~22 campos que el total). Euroliga
solo da puntos de equipo por cuarto (`ByQuarter`/`EndOfQuarter`), no boxscore de jugador por
cuarto — confirmado en la librería instalada. Tabla nueva `player_game_quarter_stats` (mismas
columnas que `player_game_stats` + `quarter`), poblada **solo cuando la fuente es ACB**
(capability `quarter_player_stats`, gated); las filas de partidos de Euroliga simplemente no
existen, igual que `position` de jugador ya es siempre `NULL` en Euroliga hoy.

**Análisis que habilita**:
- **Árbitro**: preparación de partido — algunos colegiados dejan jugar más, otros pitan más
  faltas; relevante para planificar rotaciones con margen de faltas.
- **Rendimiento individual por cuarto** (ACB): ¿qué jugador empieza fuerte y decae, o al revés
  (impacto desde el banquillo en el último cuarto)? — hoy solo existe a nivel de equipo.

---

## Fase 4 — Estadísticas avanzadas oficiales por jugador (ACB-only)

`AdvancedStats/player-advanced-stats` nunca se llama hoy. Da, por jugador y partido, con contexto
`partido`/`temporada`/`win`/`loss` ya diferenciado por la fuente: `astRatio`, `astPct`,
`stlRatio`, `stlPct`, `blkPct`, `tovPct`, `orbPct`/`drbPct`/`trbPct` (rebote separado en %),
`tsPct`, `threePAr`, `ppt`, `pp2ps`/`pp3ps`/`ppft` (puntos por tipo de tiro), posesiones/pace
individual.

**Coste a controlar**: es **una llamada HTTP por jugador y partido** (~20-24 por partido con
ambos equipos), sobre un cliente que ya throttla a 0.5s/petición — multiplicaría el volumen de
peticiones por partido. Decisión de diseño: en esta fase, ingerir **solo jugadores del Baskonia y
del próximo rival cuando aplique**, no la plantilla completa de cada rival histórico; ampliar más
adelante si hace falta. Euroliga no tiene equivalente per-partido (su `PlayerStats` oficial es
agregado de temporada, no por partido) — tabla nueva `player_advanced_stats`, gated por
competición ACB, con la misma asimetría ya aceptada en el proyecto (igual que `ortg`/`drtg` de
Euroliga son estimación propia y los de ACB son dato oficial).

**App**: bloque adicional en `player_dialog.py` con contexto temporada/victorias/derrotas —
"¿rinde distinto cuando el equipo gana o pierde?". **Asistente**: tool
`player_advanced_profile`.

**Análisis que habilita**: contexto W/L por jugador (¿su eficiencia sube o baja en derrotas?),
ritmo de juego individual, fuente de puntos (tiro exterior vs interior vs línea de personal) ya
normalizada oficialmente, sin construir la fórmula a mano.

---

## Fase 5 — Explícitamente fuera de alcance (documentar, no ingerir)

Para que "traer todo" no derive en ingerir duplicados sin valor:

- `Overview/lead-tracker`, `match-leaders`, `match-team-comparison`, `lineup` de ACB: formato de
  presentación (strings ya formateados tipo `"57,1%"`) que duplica datos que las fases 1-4 ya
  traen en bruto — sin ganancia real salvo el margen de puntos con granularidad de segundos de
  `lead-tracker`, de valor marginal frente a `score_progression` ya existente.
- `player_stats.py`/`team_stats.py` (líderes de liga oficiales de Euroliga): una vez la Fase 1
  ingiere robos/tapones/pérdidas/faltas por jugador, `league_leaders`/`league_percentiles` (ya
  existentes en el asistente) los calculan sobre datos propios — traer además el ranking oficial
  de la fuente sería una segunda fuente de verdad para el mismo número, con el riesgo de
  desincronía que el proyecto ya ha sufrido (ver `stl_pct`/`blk_pct` "asumidos" de Euroliga).

---

## Riesgos y dependencias a vigilar

- **Rate limiting de Euroliga a escala de temporada** ya está documentado como roto
  ([01_estado.md](01_estado.md), 58/400 partidos cargados en la última prueba real). Las fases
  2-3 añaden llamadas por partido (Header) — hacerlo **después** de resolver el backoff, no antes,
  para no agravar un problema ya conocido.
- **Subtipos de falta ACB sin semántica clara** (6 códigos, sin campo de descripción distinguible
  en el PBP): se guarda el código crudo; no bloquea ningún análisis agregado porque el total de
  faltas sigue viniendo del boxscore, no de contar eventos.
- **Verificar antes de asumir** (disciplina del propio repo): el mapeo Euroliga `GameMetadata` →
  árbitros/pabellón/asistencia (Fase 3) y la semántica exacta de los 6 códigos de falta ACB (Fase
  2) son los dos puntos que requieren una comprobación en vivo explícita antes de escribir el
  comentario "verificado en vivo" correspondiente — no se dan por buenos solo por este documento.

## Verificación end-to-end por fase

1. Tests unitarios del patrón ya descrito (migración aditiva idempotente, loader, tool con
   degradación) — `pytest` sobre `tests/test_scouting_db.py`, `tests/ingest/`,
   `tests/app/assistant/`.
2. Reingesta real de 1-2 partidos ya cargados (`run_single_game`/equivalente) contra la BD de
   desarrollo, para confirmar que la migración aditiva no rompe datos existentes y que las
   columnas nuevas se rellenan.
3. Lanzar la app y comprobar visualmente cada superficie tocada: ficha de jugador, boxscore de
   partido, perfil avanzado de rival, chat del asistente con una pregunta que dispare la tool
   nueva (p.ej. "¿en qué cuarto acumula más faltas el Unicaja?").
4. `tools/assistant_smoke_test.py` para confirmar que el catálogo de tools ampliado no rompe el
   bucle de tool-calling ni dispara el presupuesto de tokens por turno (`agent.py`, prefijo fijo
   ~7k tokens/vuelta — vigilar tras Fase 2 si el catálogo crece mucho; considerar
   `ASSISTANT_TOOL_LOADING=by_family` si hace falta).

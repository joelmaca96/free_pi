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
  `normalize_name` vive ahora en `packages/baskonia_core/names.py` (se reexporta desde aquí):
  el asistente de scouting necesita normalizar EXACTAMENTE igual que la ingesta, e `ingest/` no
  viaja en la imagen de la interfaz.
- `ingest/common/loader.py` — upsert idempotente contra `packages/baskonia_core/db/scouting`
  (natural keys con `ON CONFLICT DO UPDATE` donde existen; borrar-y-reinsertar para tablas sin
  clave natural como `lineups`/`shots`/`key_events`).
- `ingest/common/lineups.py` + `game_clock.py` — reconstrucción de quintetos a partir de
  play-by-play (sustituciones + puntos), fuente-agnóstica. Desde 2026-08-24 devuelve además
  los **tramos** sin agregar (`lineup_stints`: apertura, cierre, puntos a favor/en contra y
  marcador de entrada), que es lo que permite recortar por tiempo y por marcador — ver §2.5.
- Esquema destino: `packages/baskonia_core/db/scouting/schema.sql` (ver ese fichero para la
  lista completa de tablas/columnas).

Suite completa: **245 passed** (incluye `tests/app/assistant/`: resolución de entidades,
herramientas del chat, guardas de SQL, bucle de agente con cliente de LLM falso y la propia
página con `AppTest`)

Antes de esta entrega: **223 passed, 2 skipped** (offline al 100%, sin red real en tests — mocks/
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

**Fotos descargadas a disco (2026-08-24):** hasta ahora `photo_url` solo guardaba la URL remota
de baskonia.com — la interfaz (`app/components/avatar.py`) hacía *hotlink* directo a esa URL,
así que pintar la plantilla dependía de que baskonia.com estuviera arriba y accesible desde el
navegador del visitante. `scraper.download_player_photos` descarga ahora el binario real a
`BASKONIA_WEB_PHOTOS_DIR` (por defecto `data/player_photos/`, mismo volumen que monta `app/`
de solo lectura en `docker-compose.yml`) y lo persiste en la columna nueva
`players.photo_local_path` (`schema.sql`) — `photo_url` se mantiene intacta como origen. Nombra
los ficheros por `external_id` (el id de baskonia.com, estable entre ejecuciones) y es
idempotente: si el fichero ya existe en disco, no vuelve a pedirlo por red. Un fallo puntual
(URL caída, robots.txt lo deniega, `Content-Type` que no es imagen) se registra y se salta ese
jugador sin tumbar el resto de la carga. `pipeline.run(engine, download_photos=True)` la llama
por defecto; `--skip-photos` en `cli.py` la desactiva para una ejecución rápida sin tráfico de
imágenes.

**Hallazgo corregido el mismo día, al ejecutar la descarga por primera vez contra la BD
real:** `photo.data.attributes.url` viene como ruta relativa (`/uploads/xxx.png`) y este
módulo la resolvía contra `www.baskonia.com` (el host de `ROSTER_URL`) — da `200` pero
devuelve el HTML de la propia SPA Angular, no la imagen (esa ruta no está servida ahí, la
SPA la intercepta como ruta de cliente más). El backend real de medios (Strapi) es
`cms.deportivoalaves.com` — compartido con la web del Deportivo Alavés, mismo grupo
propietario —, confirmado en vivo con varios ficheros jpg/png distintos antes de fijarlo.
Corregido con `scraper.MEDIA_BASE_URL` (overridable con `BASKONIA_WEB_MEDIA_BASE_URL`).
**Esto significa que el hotlink directo que hacía la interfaz antes de tener descarga local
(`photo_url` tal cual en un `<img src>`) también estaba roto desde siempre** — nadie lo
había notado porque nunca se había intentado descargar/renderizar de verdad ese campo.

**Capa de app actualizada (2026-08-24):** `app/components/avatar.py` ahora prefiere
`photo_local_path` sobre `photo_url` — lee el fichero descargado y lo embebe en el `<img
src="data:...">` como `data:` URI (Streamlit no expone `data/` como estático), con `photo_url`
como fallback si la copia local no existe todavía o no se puede leer. La ruta real dentro del
contenedor de la interfaz no es la que guardó el ingestor (que corre fuera de Docker, ver
`deploy/systemd/`) sino `PLAYER_PHOTOS_DIR` (`docker-compose.yml`, mismo patrón que
`DATABASE_URL`) + el nombre de fichero — `avatar.py` solo se fía del nombre, no de la ruta
completa guardada en `photo_local_path`. Cubierto por `tests/app/test_avatar.py` (primer test
de la capa `app/`, sin red ni Streamlit real: construcción de HTML pura).

### 2.2 `acb` — Liga Endesa vía la API real de acb.com — ✅ el más completo de los tres

Usa la API real del frontend actual de acb.com (`api2.acb.com/api/{seasondata,matchdata}`,
auth `x-apikey`) — **no** la de OpenACB (`openapilive`), que está muerta (409 confirmado incluso
replicando la petición exacta del propio scraper R de OpenACB).

Implementado y poblando datos reales:

| Tabla | Estado |
|---|---|
| `games` | ✅ calendario completo por temporada (recorrido de `weekId` hacia atrás desde la última semana disponible hasta el límite real de la edición) |
| `game_advanced_stats` | ✅ tiros libres en bruto (`ftm`/`fta`) además de `ft_rate` desde 2026-08-25, y **con los números OFICIALES de acb.com** (`AdvancedStats/match-advanced-stats`: posesiones/pace/ortg/drtg/net_rating/four factors/ast·stl·blk %), no una estimación propia — solo `ast_to_ratio` se calcula desde el boxscore (no viene en ese endpoint) |
| `game_team_quarter_stats` | ✅ puntos anotados/encajados por cuarto (desde el boxscore por cuarto) |
| `player_game_stats` | ✅ boxscore completo por jugador, con `ftm`/`fta` desde 2026-08-25 |
| `lineups` + `lineup_players` | ✅ reconstruidos desde play-by-play real (quinteto inicial + sustituciones, `playType` decodificado en vivo); desde 2026-08-24 con `lineups.team_id` explícito |
| `lineup_stints` + `lineup_stint_players` | ✅ (2026-08-24) tramos con reloj y marcador, del mismo recorrido de eventos. Reingesta de 2025-2026 hecha el 2026-08-25: **34.496 tramos** cargados y `lineups.team_id` poblado en las 27.900 filas |
| `shots` | ✅ con coordenadas reales (`MatchShots/match-shots`), tiros libres excluidos (no traen coordenadas) |
| `game_zone_stats` | ✅ agregado automáticamente desde `shots` por el loader |
| `score_progression` | ✅ derivado del play-by-play (deduplicado por cambio de marcador) |
| `games.competition_id` | ✅ (2026-08-24) distingue ACB/Copa del Rey/Supercopa vía `MatchHeader/match-header` — ver hallazgo abajo |

**Hallazgo corregido (2026-08-24):** `Competition/matches?competitionId=1&...` (el listado que
recorre `fetch_season_finished_matches`) trae **todos** los partidos de la organización "Liga
Endesa" en sentido amplio, Copa del Rey incluida — verificado en vivo con los 3 partidos reales
de Baskonia del 20-22 feb 2026 (eliminatoria a un partido en días consecutivos vs Tenerife/
Barça/Real Madrid), que llegaban con `competition_id=1` (ACB) pese a ser Copa del Rey. Se
detecta ahora vía `AcbClient.fetch_match_header()` (`MatchHeader/match-header?matchId=...`, ya
documentado pero sin integrar antes de hoy): catálogo real
(`availableFilters.competitions` de `Competition/matches`) = `1` Liga Endesa, `2` Copa del Rey,
`3` Supercopa Endesa → mapeados en `ingest/acb/adapter.py::_competition_name`. Igual que
`advanced_stats`, si `match-header` falla la petición cae en "ACB" por defecto (comportamiento
previo), no rompe la carga del partido.

**Falta / limitaciones conocidas:**
- ~~**Boxscore incompleto pese a que la fuente da mucho más**~~ **RESUELTO (2026-08-27),
  Fases 0-4 completas de [02_plan_stats_completas.md](02_plan_stats_completas.md).**
  `ingest/acb/adapter.py` mapea ahora `personalFouls`/`foulsDrawn`/`steals`/`turnovers`/`blocks`/
  `receivedBlocks`/`offRebounds`/`defRebounds`/`dunks`/`plusMinus`/`rating` por jugador Y por
  equipo (`player_game_stats`/`game_advanced_stats`, columnas nuevas nullable), boxscore **por
  cuarto** (`statsByPeriods` → `player_game_quarter_stats`, ACB-only) y metadata de partido
  (`arena`/`attendance`/`referees`/`headCoach` por equipo → `games.arena`/etc). `AdvancedStats/
  player-advanced-stats` se llama ahora, acotado a Baskonia + próximo rival por coste
  (`AcbClient.fetch_player_advanced_stats`, `pipeline.py::_advanced_stats_scope`) →
  `player_advanced_stats`. El play-by-play tipado vive en la tabla nueva `play_events`
  (`game_id, team_id, player_id, quarter, game_clock, seconds, event_type, event_detail`):
  decodificados y verificados en vivo (partido real 105370, cruzando deltas de `playerStats`
  evento a evento) robo=103, pérdida=106, tapón=102, rebote of/def=101/104, asistencia=107/108/119,
  falta recibida=110, y **falta personal en 6 códigos sin diferenciar entre sí
  (161/159/160/109/537/166, guardados en `event_detail`, el total sigue viniendo de
  `personalFouls` del boxscore)**. `game_team_quarter_stats.fouls_for/fouls_against` se derivan
  de `play_events` en el loader (`ingest/common/loader.py::_quarter_foul_stats`), no del adapter.
  Todo esto integrado en la app (fichas de jugador/equipo, boxscore de partido, faltas por cuarto,
  cabecera de árbitros/asistencia) y en el asistente (bloques nuevos en `player_averages`/
  `player_game`/`team_profile`/`team_style`, tools nuevas `team_foul_quarter_profile`/
  `game_play_events`/`player_advanced_profile`), con sondeo de capacidad (`box_extras`/
  `play_events`/`game_metadata`/`quarter_player_stats`/`player_advanced_stats` en
  `app/assistant/capabilities.py`) para que el asistente nunca prometa un bloque que esta BD
  concreta no tiene todavía. **Cierre del hueco que quedaba abierto en el asistente
  (2026-08-27):** `game_metadata`/`quarter_player_stats` se sondeaban desde el principio de esta
  fase pero ninguna tool los exponía todavía — `game_boxscore` incluye ahora un bloque
  `metadata` (árbitros/asistencia/pabellón/entrenadores) cuando el partido concreto lo tiene, y
  la tool nueva `player_quarter_profile` da el rendimiento medio de un jugador por cuarto a lo
  largo de la temporada (¿arranca fuerte y decae, o al revés?), ambas con su aviso de "esta BD/
  este partido no lo tiene" cuando corresponde en vez de fallar en silencio.
  **Segundo hallazgo, en el prompt de sistema (`app/assistant/prompt.py`):** la tarjeta de
  esquema autogenerada (§8.6) truncaba cada tabla/vista a sus primeras 12 columnas SIN
  marcarlo — `game_advanced_stats` (26 columnas) cortaba justo antes del boxscore ampliado
  completo, `games` antes de `referees`/`home_coach`/`away_coach`, y
  `team_stats_by_competition`/`team_stats_combined` (38 columnas, la vista más ancha del
  esquema) antes de los `opp_*` — exactamente las columnas que motivaron estas fases, invisibles
  para `run_sql` (§4.6) pese a estar cargadas. Subido el tope a 40 (el prompt sigue en ~9k
  caracteres, muy por debajo del techo de 20k de `test_the_prompt_stays_small_enough_for_a_32k_window`)
  y el corte, si algún día vuelve a producirse, se marca ahora con `… (+N más)` en vez de
  desaparecer en silencio. `_TRAPS`/`_GLOSSARY` también ampliados con las trampas reales de
  estas fases (falta personal sin subtipo diferenciable, `fouls_for/fouls_against` derivado de
  `play_events` con cobertura distinta a los puntos por cuarto, alcance ACB-only + solo
  Baskonia/próximo rival de `player_advanced_stats`, y la distinción `ast_ratio` vs `ast_pct`).
- **Clutch stats** (últimos 5 min ± 5 puntos): **resuelto (2026-08-24)**. El dato de tiempo ya
  circulaba por el play-by-play y se descartaba al agregar; ahora `reconstruct_lineups` devuelve
  también los tramos y el loader los escribe en `lineup_stints`. Los partidos ya ingeridos
  quedan **sin tramos hasta que se reingieran** — el asistente lo detecta y apaga la herramienta
  `clutch_lineups` mientras tanto, en vez de ofrecer una respuesta que no puede sostener.
- **Segmentos de 2 minutos** / eficiencia post-tiempo-muerto: siguen sin implementarse, aunque
  con `lineup_stints` el trabajo restante es menor.
- **Tiros libres (`ftm`/`fta`): resuelto (2026-08-25).** Las columnas existían desde el
  2026-08-24 pero **ningún adapter las emitía**: `_team_totals` sumaba `ftm`/`fta` solo para
  derivar `ft_rate` y los descartaba, y el boxscore por jugador ni los miraba — así que
  `player_game_stats.fta` seguía en NULL en las 17.455 filas por muchas reingestas que se
  hicieran. Corregido en los cuatro puntos: los dos caminos de equipo de ACB (estimado y
  oficial — `match-advanced-stats` solo da la TASA, el recuento sigue saliendo del boxscore),
  el jugador de ACB, y equipo + jugador de Euroliga.

  Un matiz que los tests fijan: un campo AUSENTE en la fuente se carga como `NULL`, no como 0.
  `0` significa "no tiró ni un libre" y `NULL` "esta fuente no lo dio para este partido"; son
  cosas distintas y `gp_ft` de las vistas existe justamente para separarlas.
- **On/off de quintetos** (impacto de combinaciones de jugadores más allá de minutos/±):
  cubierto parcialmente por `app/data/queries_assistant.py::player_pair_impact` (rendimiento con
  dos jugadores juntos frente a por separado), calculado sobre los quintetos ya reconstruidos.
- Un puñado de partidos concretos devuelven `500` real desde el propio endpoint de boxscore de
  acb.com (~10 de ~280 en una temporada) — no es un bug nuestro, son huecos de datos del lado
  del servidor; el orquestador los reporta como fallidos y sigue con el resto.
- No hay endpoint de fotos/roster vía esta fuente (se cubre por `baskonia_web` para el Baskonia;
  para el resto de equipos no hay fuente de fotos).
- **`fetch_season_finished_matches` paraba en el primer hueco de `weekId` — corregido
  (2026-08-24), con una vuelta atrás importante ver más abajo.** Un usuario reportó un partido
  real de Baskonia de abril (`matchId=104679`, BAXI Manresa) ausente. Verificado en vivo: el
  espacio de `weekId` de una edición **no es contiguo** — huecos reales de decenas de semanas
  seguidas devolviendo 400 (edición 90: 2891-2945 inválidas, pero 2810-2890 vuelven a tener
  partidos reales) separan bloques con partidos legítimos. La primera versión de este método
  paraba en el primer 400, perdiendo esos bloques enteros. `selectedFilters.season` tampoco
  sirve como límite: hace eco del `edition_id` pedido incluso en `weekId` claramente ajenos
  (se comprobó con valores tan bajos como 500/1000/2000). Corregido tolerando hasta 80
  `weekId` inválidos seguidos + un techo absoluto de 300 peticiones + una ventana de fecha de
  seguridad (`AcbClient._season_date_bounds`) — ver historia completa en
  `ingest/acb/client.py::fetch_season_finished_matches`.
- **Ese mismo hueco escondía partidos de OTRAS competiciones (cantera), no solo de ACB —
  hallazgo más grave, corregido el mismo día.** Al ampliar el rastreo, aparecieron partidos
  reales de Baskonia con `competitionId=10` ("Minicopa Endesa", cantera) y `134`
  (sin identificar en el catálogo) mezclados en la misma lista de `competitionId=1`. Antes de
  este hallazgo, cualquier `competition_id` no reconocido caía en "ACB" por defecto — así que
  esos partidos de cantera se cargaban como partidos reales de Liga Endesa, y sus jugadores,
  al compartir `team_id='bas'` y a veces el mismo dorsal que un jugador del primer equipo,
  **pisaban el nombre real de ese jugador** vía el fallback de identidad por dorsal+equipo
  (`ingest/common/identity.py::resolve_or_create_player`, paso 2 — no comprueba que sea la
  misma persona). Auditoría completa en vivo de los 258 partidos que había en `data/
  baskonia.db` etiquetados "ACB": **239 eran en realidad de otra competición (92.6%)** — solo
  quedaban 19 partidos ACB reales. Purgados los 239 (y todas sus filas dependientes:
  boxscore/quintetos/tiros/etc.), y `AcbClient.fetch_game()` ahora rechaza
  (`adapter.is_out_of_scope_competition`) cualquier partido cuya competición real no esté en
  `adapter._COMPETITION_BY_ID` **antes** de pedir boxscore/jugadores — "no reconocida" ya no
  cae en ACB por defecto, se descarta el partido entero (test:
  `test_fetch_game_rejects_out_of_scope_competition` en `tests/ingest/test_acb_client.py`).
  **Nota para quien siga esta traza:** la nota anterior de este documento ("solo 9 partidos
  totales de Baskonia, hueco real de la fuente") quedó **superada por este hallazgo** — parte
  de esos "9" (p.ej. `105165`) eran justo de esta contaminación, no partidos reales de ACB.

**Calendario futuro + escudos (`run_upcoming`, ver también 2.3):** además del backfill de
partidos finalizados, `ingest/acb/pipeline.py::run_upcoming` refresca `upcoming_matchups` con
el calendario NO jugado del Baskonia (Liga Endesa/Copa del Rey/Supercopa — `Competition/
matches` trae las tres, ver hallazgo de cantera arriba) y backfillea `teams.logo_url` del
Baskonia y de cada rival con el campo `logo` real que trae ACB en ese mismo payload —
primera fuente de escudo verificada del proyecto (`local/features/003-vista-plantilla/
01_design.md` §9 documentaba ese hueco como sin resolver). Idempotente (borra e inserta el
calendario de esa `season`+`competition_id` en cada llamada — acotado también por
`competition_id` desde el 2026-08-24, para no borrar el calendario de Euroliga de la misma
temporada, ver 2.3). CLI: `python -m ingest.acb.cli --season 2026 --upcoming-only` (para una
temporada que aún no ha empezado, donde `run()` no encontraría ningún partido finalizado).

### 2.3 `euroleague` — Euroliga vía `euroleague_api` — ⚠️ funciona, pero no a escala de temporada completa

- Librería Python `euroleague_api` (métodos reales verificados por introspección directa del
  paquete instalado, no adivinados): `Schedule.get_schedule`,
  `BoxScoreData.get_players_boxscore_stats`, `ShotData`, `PlayByPlay`.
- Igual que ACB: `games`/`game_advanced_stats` (efg/ts/tov/orb% + ortg/drtg/pace estimados con
  la fórmula Dean Oliver, aquí sí porque **no** hay un endpoint oficial equivalente al de ACB)/
  `player_game_stats`/`shots` (con coordenadas reales)/`lineups` (reconstruidos desde
  play-by-play, igual que ACB).

**Calendario futuro + escudos (`run_upcoming`, 2026-08-24, mismo patrón que ACB arriba):**
`euroleague_api` no envuelve ningún endpoint de calendario futuro NI de clubes/escudos —
`Schedule.get_schedule(season)` ya trae el calendario COMPLETO de la temporada (jugado y no
jugado) en una sola llamada, sin paginar por semana como ACB; los escudos se piden aparte con
`EuroleagueClient.fetch_clubs(season)`, una llamada `requests` directa (no envuelta por la
librería) al mismo backend que usa por debajo (`api-live.euroleague.net/v2/competitions/
{code}/seasons/{code}{season}/clubs`, encontrado probando en vivo — sin `robots.txt`, es una
API JSON). El `code` de club (p.ej. `"BAS"`) es el identificador propio del backend y NO
cambia con el patrocinador (a diferencia del id numérico de ACB) — `_own_team_euroleague_code`
prueba primero el enlace ya guardado en `team_external_ids` (si algún partido de Euroliga se
cargó antes) y solo cae a resolver por nombre normalizado (`_KNOWN_TEAM_ALIASES`, con el
nombre real 2026-2027 "Kosner Baskonia Vitoria-Gasteiz" añadido) si la BD es nueva. Verificado
en vivo contra `data/baskonia.db`: **38 partidos futuros cargados** para la temporada
2026-2027, con escudo real de los 20 clubes de la competición. Acotado por `(season_id,
competition_id)` igual que ACB — necesario desde que hay dos fuentes escribiendo en
`upcoming_matchups` para la misma temporada, si no una pisaría el calendario de la otra. CLI:
`python -m ingest.euroleague.cli --season 2026 --upcoming-only`.

- **Falta / limitaciones conocidas:**
  - **Rate limiting sin resolver a escala de temporada completa.** `euroleague_api` no aplica
    ningún throttling propio; se añadió una pausa (3 s/partido) + reintento con backoff (3
    intentos, 10 s), verificado con un puñado de partidos, pero en una ejecución real de
    temporada completa (~400 partidos) el resultado fue **58 cargados / 344 fallidos** — el
    backoff actual **no es suficiente** para completar un backfill de temporada entera de forma
    fiable. Pendiente: backoff más agresivo/exponencial, o una estrategia de reanudación
    (checkpoint de qué partidos ya se cargaron) en vez de reintentar toda la temporada.
  - `stl_pct`/`blk_pct` en `game_advanced_stats` usan columnas `Steals`/`BlocksFavour` —
    **verificadas en vivo el 2026-08-27** (petición real a `live.euroleague.net/api/Boxscore`,
    fuera de `euroleague_api`): son los nombres reales. Ya **no** están "asumidas, no
    verificadas" como decía esta nota hasta ahora.
  - `quarter_stats` solo se rellena si se pasa `play_by_play_records` explícitamente (no hay
    boxscore-por-cuarto como en ACB; se deriva sumando eventos de anotación por cuarto) — y a
    diferencia de ACB, el propio `Boxscore` de Euroliga (`ByQuarter`/`EndOfQuarter`) tampoco da
    boxscore por cuarto A NIVEL DE JUGADOR, solo puntos de equipo: asimetría real entre fuentes,
    no un hueco de implementación.
  - `position` de jugador siempre `None` (no viene en el boxscore de `euroleague_api`).
  - No hay endpoint de estadísticas avanzadas oficiales equivalente al de ACB **por partido** —
    todo (ortg/drtg/pace) es estimación propia (Dean Oliver), no dato oficial de Euroliga. Sí hay
    (verificado en vivo 2026-08-27, módulos `player_stats.py`/`team_stats.py`/`game_stats.py` de
    `euroleague_api`, nunca importados por `ingest/euroleague/client.py`) estadísticas oficiales
    de LIGA agregadas por temporada (`traditional`/`advanced`/`misc`/`scoring`, con líderes) —
    otra granularidad, no un boxscore de partido, ver [02_plan_stats_completas.md
    §Fase 5](02_plan_stats_completas.md#fase-5--explícitamente-fuera-de-alcance-documentar-no-ingerir)
    para por qué no se prioriza traerlo aparte.
  - ~~**Boxscore por jugador incompleto igual que en ACB**~~ **RESUELTO (2026-08-27), mismas
    Fases 0-4 que ACB.** `ingest/euroleague/adapter.py` mapea ahora `FoulsCommited`/
    `FoulsReceived` (sic, typo real de la API confirmado en vivo), `BlocksAgainst`, `Valuation`
    (PIR), `Plusminus`, `OffensiveRebounds`/`DefensiveRebounds` por jugador y por equipo. El
    jugada-a-jugada (`PLAYTYPE`) ya no se descarta: `CM`/`RV`/`ST`/`TO`/`FV`/`O`/`D`/`AS` se
    decodifican a `play_events` (`AG`, el lado "espejo" de `FV`, se omite a propósito para no
    duplicar el tapón; `CCH` no es estadística de caja). Metadata de partido (árbitros/
    aforo/entrenadores) sale de `GameMetadata.get_game_metadata` (endpoint `Header`), que **ya
    se llamaba** antes de esta fase — confirmado en vivo que no hacía falta ninguna llamada
    nueva: `Referee1/2/3`, `Stadium`, `Capacity`, `CoachA/B` estaban en la respuesta sin leerse.
    Boxscore de jugador por cuarto (Fase 3) y avanzadas oficiales por partido (Fase 4) siguen
    siendo ACB-only — Euroliga no los publica (asimetría real de fuente, no un hueco de
    implementación). Detalle completo: [02_plan_stats_completas.md](02_plan_stats_completas.md).

## 3. Qué falta en conjunto (independiente de la fuente)

- ~~**Boxscore ampliado (faltas, robos, tapones, pérdidas, rebote of/def, +/-, PIR) por jugador y
  equipo, en las DOS fuentes por igual**~~ **RESUELTO 2026-08-27, Fases 0-4 completas de
  [02_plan_stats_completas.md](02_plan_stats_completas.md) — ver §2.2/§2.3 arriba para el detalle
  por fuente.** Quedan explícitamente fuera de alcance por diseño (Fase 5 del plan, no huecos):
  `lead-tracker`/`match-leaders`/`match-team-comparison`/`lineup` de ACB (formato de presentación
  que duplica datos ya traídos en bruto) y los líderes de liga oficiales de Euroliga (segunda
  fuente de verdad para números que `league_leaders` ya calcula sobre datos propios).
- **Equipos duplicados por cambio de patrocinador entre temporadas**: la normalización de
  nombres (`ingest/common/identity.py`, `_KNOWN_TEAM_ALIASES`) cubre los casos conocidos
  detectados hasta ahora (p.ej. "Kosner Baskonia"/"Bitci Baskonia"), pero es una lista
  mantenida a mano, no una solución genérica — un patrocinador nuevo no listado crearía un
  equipo duplicado hasta que se añada su alias.
- **Euroliga a escala de temporada completa** (ver 2.3) es el hueco más urgente si se quiere un
  backfill fiable de ambas competiciones de una sentada.
- **Clutch stats / on-off / segmentos de 2 min** (ver 2.2): con los datos ya disponibles de ACB
  es la ampliación más barata de construir si se necesita más adelante.
- ~~**`court_zones` no cubre la cancha entera → `game_zone_stats` es un subconjunto sesgado**~~
  **RESUELTO 2026-08-27 (reteselado).** Las 6 zonas semilla originales de `schema.sql` eran
  rectángulos sueltos con huecos grandes entre ellos (hallazgo 2026-08-24, ver historia arriba:
  52-79% de los tiros de `data/baskonia.db` sin zona según la competición). Se añadieron 3 zonas
  nuevas ("Media dist. central", "Triple ala izq./der.") y se ensancharon las dos de media
  distancia ("Ala izq./der.") para cubrir casi toda la superficie ofensiva realista
  (x:15-485, y:50-460) — ver el comentario sobre `court_zones` en `schema.sql` para la
  geometría completa y qué zonas siguen siendo anclas del dibujo de
  `app/components/court.py`. Sigue siendo una aproximación de rectángulos, no la línea de
  triple real: "Ala izq./der." mezclan tiros de 2 largos y triples de ala en la misma zona
  (`app/pages/proximo_rival.py` lo advierte junto a la tabla). Una BD ya inicializada gana la
  geometría nueva sola (`init_scouting_db` → `_sync_court_zones`, mismo mecanismo que las
  columnas aditivas), pero los tiros YA CARGADOS necesitan `tools/retile_court_zones.py
  --apply` para reclasificarse contra ella y regenerar `game_zone_stats` — sin eso,
  `shots.zone_id` se queda con la clasificación vieja indefinidamente (se fija una sola vez, al
  ingerir).
  - De paso, se separaron los mates (antes contados dentro de "Pintura", inflando su acierto
    con ~100% de mates mezclado con tiros de media/corta distancia reales) en una zona propia
    ("Mate", id 10): `ingest/common/raw_game.py` se la asigna directamente a todo tiro
    `located=False`, sin pasar por `classify_zone` (su centinela cae exactamente dentro de
    "Pintura" — sin `ORDER BY` en `classify_zone`, el solape no resolvía de forma fiable).
  - Se expuso también `ft_pct`/`ftm`/`fta` (ya calculados en las vistas desde el 2026-08-24,
    pero sin usar en `app/data/queries.py` ni en ninguna página) en el detalle de partido, el
    perfil avanzado de equipo y las medias de jugador — tiro libre no es un dato de zona de
    cancha (se tira siempre desde el mismo punto fijo) y forzarlo dentro de `shots`/
    `court_zones` habría exigido ingerirlo con otro centinela sin coordenadas reales; la vía
    correcta era esta, no una zona más.

## 2026-09-28 — Posesiones por tramo (`lineup_stints.possessions_for`/`possessions_against`)

Cierra en la ingesta el hueco "Posesiones por tramo" de
[`../propuestas/00_indice.md`](../propuestas/00_indice.md): hasta aquí `lineup_stints` guardaba
puntos y no posesiones, y todo el On/Off/duplas/RAPM iba por 40 minutos.

- **Esquema**: dos columnas REAL nullable en `lineup_stints`, en `schema.sql` y en
  `engine.py::_ADDITIVE_COLUMN_MIGRATIONS` (una BD ya cargada las gana sola con
  `init_scouting_db`, en NULL).
- **Cálculo** (`ingest/common/possessions.py`): posesiones ≈ FGA + 0,44·FTA − OREB + TOV —la
  misma fórmula Dean Oliver que ya usan los adaptadores para `game_advanced_stats`— aplicada a la
  ventana de cada tramo, para el equipo del tramo (`possessions_for`) y para su rival en la misma
  ventana (`possessions_against`). Cuenta los tipos de `play_events` del contrato con la ingesta de
  tiros: `fg2_made`, `fg2_missed`, `fg3_made`, `fg3_missed`, `ft_made`, `ft_missed`, más `oreb` y
  `turnover`. Opción `ft_mode="trips"`: viajes a la línea contados (libres del mismo equipo y
  segundo; un 2+1 o un técnico de un libre no abren posesión) en vez del 0,44.
- **Fronteras**: tramos semiabiertos `[start, end)` (el evento del segundo de un cambio va al
  quinteto que entra), salvo reloj `00:00`, que va al quinteto que acabó el periodo (la bocina y
  el cambio del descanso comparten segundo). Si la regla no encuentra tramo, se prueba la otra;
  si tampoco (hueco sin quinteto de cinco), el evento cuenta solo en el total del partido.
- **Total por partido y reescalado**: se estima el total de cada equipo con todos sus eventos y
  se compara con la referencia `100 · puntos / ortg` de `game_advanced_stats` (oficial en ACB con
  `AdvancedStats`, Dean Oliver en el resto); la discrepancia va al log (aviso por encima del 10%).
  Por defecto (`rescale=True`) las posesiones de los tramos se multiplican por
  `referencia / estimado`, así que si los tramos cubren el partido suman la referencia y el net
  rating agregado cuadra con `ortg − drtg`. Absorbe también las posesiones de fin de periodo sin
  evento que la fórmula no ve. Sin referencia o con factor fuera de [0,75, 1,33] (dato roto), se
  queda la estimación cruda.
- **NULL, no 0**: un partido sin NINGÚN tiro tipado en `play_events` (todo lo ingerido hasta hoy)
  deja las dos columnas en NULL, para que la capacidad distinga "sin dato" de "cero".
- **Loader**: `load_game` llama a `_update_stint_possessions` justo después de `_replace_stints`
  (con `play_events` ya escritos), igual que las faltas por cuarto se derivan de `play_events`.
  Lee de la BD, así que es idempotente y da lo mismo que el backfill. Un fallo del cálculo se
  registra y no tumba la carga del partido.
- **Backfill sin red**: `tools/backfill_stint_possessions.py` (dry-run por defecto, `--apply`
  para escribir, `--no-rescale`, `--ft-mode trips`, `--game <id>` repetible) recalcula todos los
  partidos con tramos desde lo ya guardado y resume cuántos tienen tiros tipados, cuántos se
  reescalaron y la discrepancia media contra la referencia. **Solo sirve de algo tras reingerir
  con la ingesta de tiros en `play_events`**; antes, todo sale "sin tiros tipados".
- **Interfaz**: capacidad `stint_possessions` (columna presente Y algún valor no NULL) en
  `app/assistant/capabilities.py`. `queries_assistant.player_on_off` añade `on_possessions`,
  `on_net_100`, `off_possessions`, `off_net_100`, `on_off_100`, y `player_combos` añade
  `possessions`/`net_rating_100` (NaN sin dato; net rating = ORtg − DRtg sobre las sumas de los
  MISMOS tramos con posesiones). `app/screens/quintetos.py` los enseña junto al +/- por 40 solo
  con la capacidad encendida. El RAPM (`app/analytics/impact.py`) no cambia (ver la nota en §5
  de la propuesta 12).
- **Limitación conocida**: los PUNTOS de un segundo con canasta y cambio a la vez se reparten
  por el orden de la fuente (`ingest/common/lineups.py`) y las posesiones por el reloj; en esos
  segundos puntos y posesiones pueden caer en tramos contiguos distintos (error de una posesión
  que se compensa al agregar). Un tramo muy corto puede quedar con 0 posesiones y puntos, o al
  revés: los per-100 solo tienen sentido agregados (jugador, pareja, trío), no por tramo suelto.

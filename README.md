# baskonia-pipeline

> **PoC** — Aplicación para asistentes de entrenador del Baskonia.
> Pipeline de captura de datos de Basketball-Reference + visión de la app final
> que presenta estadísticas avanzadas de enfrentamientos anteriores.

---

## 1. Contexto del proyecto

Este repositorio es un **prueba de concepto (PoC)** de una aplicación destinada a los
**asistentes de entrenador del Baskonia**. El objetivo final es ofrecer una herramienta
que presente **todos los datos necesarios para preparar los partidos**, cargando datos de
**enfrentamientos anteriores** y presentándolos con **estadísticas avanzadas**.

### Visión de la app final
- **Dashboard web** con gráficos y tablas interactivas.
- **Informes exportables** (PDF/documentos) para los asistentes.
- Presentación de estadísticas avanzadas:
  - **Eficiencia**: eFG%, TS%, PER.
  - **Ritmo y posesiones**: pace, posesiones por partido.
  - **Rating ofensivo/defensivo**: ORtg / DRtg.
  - **Plus/Minus y Net Rating**: impacto en pista.

### Estado actual (2026-08-18) — ⚠️ IMPORTANTE

> El pipeline de scraping **ya se ejecutó con éxito, la base de datos tiene datos
> reales, y ya calcula y guarda estadísticas avanzadas**. Todo lo demás
> (arquitectura de la app, interfaz, infraestructura) sigue **por construir**.

**✅ Implementado y validado:**
- Pipeline de scraping de Basketball-Reference, ejecutado en `.venv` local con datos reales.
- Modelo de datos SQLAlchemy y almacenamiento idempotente (upserts).
- `data/baskonia.db` poblada: 5 equipos, 34 jugadores, 8 partidos, 190 líneas de box score, 16 filas de estadísticas avanzadas por equipo/partido (incluye ya los 2 enfrentamientos directos Vitoria-Bilbao).
- **Estadísticas avanzadas calculadas y persistidas** (`stats.py`):
  - Por jugador/partido: `efg_pct`, `ts_pct` (columnas en `boxscores`).
  - Por equipo/partido: posesiones, `pace`, `off_rating`, `def_rating`, `net_rating` (tabla nueva `team_game_stats`).
  - Se recalculan de forma idempotente (se omite si ya existen para ambos equipos del partido) y se hace *backfill* de filas antiguas sin volver a descargar nada.
- **Roster/calendario de los equipos ya no se vuelven a descargar en cada ejecución**: si el equipo ya existe en la BD, se reconstruye su lista de partidos desde la propia base de datos. Solo se refrescan con el flag explícito `--refresh-teams`.
- Corregidos dos bugs encontrados durante la primera ejecución:
  - `main.py` obtenía partidos y box scores pero nunca los guardaba (faltaban las llamadas a `upsert_game`/`upsert_boxscore`).
  - `parse_boxscore` buscaba tablas con id `box-<equipo>-game-basic`, que no existe; BBR oculta las tablas reales (`box-score-home` / `box-score-visitor`) dentro de comentarios HTML.
- Optimización: si un partido ya tiene box score guardado en la BD, no se vuelve a descargar (ahorra peticiones y respeta el rate-limit de 20s).
- Migración ligera de esquema en `init_db()`: añade columnas nuevas a tablas SQLite ya existentes con `ALTER TABLE` (no hay Alembic, pero tampoco hace falta recrear la BD al añadir campos).
- **Corregido el bug que impedía detectar los enfrentamientos directos** (el objetivo
  principal del PoC): `_select_boxscores()` comparaba el nombre de display del rival
  normalizado contra el *slug* del equipo con igualdad exacta (p.ej. `"surnebilbaobasket"`
  nunca es igual a `"bilbao"`), así que nunca disparaba con datos reales de BBR. Se
  cambió a coincidencia por subcadena. El mismo bug existía al resolver el equipo
  rival al guardar partidos, y creaba un `Team` duplicado por cada variante de nombre
  ("Surne Bilbao Basket" vs "Bilbao"); se añadió `_merge_duplicate_teams()` para
  fusionar automáticamente esos duplicados con el equipo canónico en cada ejecución.
- **App rediseñada para estar centrada en el Baskonia** (`app.py`), en vez del
  selector genérico "Equipo A / Equipo B" del PoC inicial. Tres pestañas:
  resumen del Baskonia, navegación por cualquier **partido anterior** (no solo
  los últimos 3 o los enfrentamientos vs Bilbao) y **próximos enfrentamientos**
  con **scouting bajo demanda**: si el próximo rival aun no está en la base de
  datos, un botón lo descarga en el momento (roster, calendario y box scores
  recientes) respetando el rate-limit.
- **Slug real del rival extraído del calendario** (`parser.py` →
  `parse_schedule_games`, campo `opponent_slug`): BBR incluye en cada fila del
  calendario un enlace a la página del equipo rival
  (`/international/teams/<slug real>/<año>.html`). Antes se inventaba un slug
  normalizando el nombre de display (p.ej. `"clubjoventutbadalona"`), que no
  es un slug real de BBR y no permite scrapear a ese equipo por su cuenta.
  `resolve_opponent_team()` usa ahora el slug real y **migra automáticamente**
  los equipos ya creados con el slug "falso" de ejecuciones anteriores (p.ej.
  `lalagunatenerife` → `canarias`), conservando su historial ya capturado.
- **Calendario completo persistido, incluidos los partidos aun no jugados**
  (`persist_schedule()`): antes solo se guardaban los partidos seleccionados
  para descargar box score (últimos N + enfrentamientos directos); el resto
  del calendario se descartaba tras usarlo, así que no había forma de listar
  "próximos enfrentamientos". Ahora se guarda como `Game` con `home_score`/
  `away_score`/`boxscore_url` a `NULL` hasta que se juegue.
- `LAST_N_GAMES` (antes constante fija a 3 en `main.py`) es ahora configurable
  vía `.env` (`config.LAST_N_GAMES`, default `10`), para poder navegar más
  partidos anteriores del Baskonia en la GUI.
- **Corregida la mezcla de partidos aplazados en "Próximos enfrentamientos"**:
  BBR marca un partido aplazado con `notes = "Postponed"` y deja esa fila sin
  resultado **para siempre**, aunque el partido se haya jugado más adelante en
  otra fecha como fila aparte del calendario (comprobado con datos reales: el
  aplazamiento de Baskonia-Gran Canaria del 13 dic 2025 se jugó finalmente el
  8 feb 2026, y ambas filas siguen en el calendario). Como antes "próximo
  enfrentamiento" se definía solo como "sin resultado guardado", estos
  aplazamientos con fecha ya pasada se mostraban mezclados con los partidos
  genuinamente pendientes. Ahora se captura y persiste `notes` (columna nueva
  en `games`) y `upcoming_games()` excluye tanto las filas con `notes` no
  vacío como cualquier fecha ya pasada.
- **Nueva fuente: web oficial del Baskonia** (`scraper/baskonia_official.py`),
  para lo que BBR no cubre (ver sección 4.1):
  - **Calendario de la temporada 26/27** (Liga Endesa, Euroleague, Supercopa;
    Euskal Kopa cuando la publiquen), vía la API JSON que la propia web
    consulta. Se persiste igual que el calendario de BBR (`persist_schedule()`),
    así que "Próximos enfrentamientos" ya funciona con datos reales de verdad
    (73 partidos verificados en pruebas, del 19 sept 2026 en adelante).
  - **Plantilla actual** (nombre, posición, dorsal, foto), vía la misma API.
    Alimenta la pestaña "Plantilla" de la GUI.
  - Ejecutado siempre en `python main.py` (barato, un par de peticiones);
    envuelto en `try/except` para que un fallo de esta fuente no tumbe el
    resto del pipeline.
- **Corregido un bug de encoding real en `BBRClient`** (afectaba a datos ya
  guardados, no solo a esta feature): BBR sirve sus páginas en UTF-8 pero sin
  declarar el charset en `Content-Type` (`text/html` a secas), así que
  `requests` decodificaba con el *default* de la RFC (ISO-8859-1),
  corrompiendo cualquier nombre con acentos o caracteres especiales (p.ej.
  "Río Breogán" quedaba guardado como mojibake). Se fuerza `response.encoding
  = "utf-8"` en `BBRClient.get()`; también se repararon los ~20 valores ya
  corrompidos en `data/baskonia.db` (nombres de equipo/jugador) con un script
  puntual (no forma parte del pipeline).
- **Corregido un crash del PDF con nombres no-Latin1**: fpdf2 con la fuente
  base (Helvetica) solo soporta Latin-1; un nombre con "Ž", "š", "ć"... (que
  ahora aparecen correctamente gracias al fix de encoding anterior) hacía
  saltar `FPDFUnicodeEncodingException` y reventaba toda la pestaña, no solo
  el PDF. `_pdf_safe()` en `app.py` aproxima esos caracteres a su equivalente
  ASCII antes de escribirlos al PDF.
- **Botón "Generar ppt para Paolo"** en la pestaña "Plantilla"
  (`build_roster_pptx()` en `app.py`, vía `python-pptx`): una diapositiva por
  jugador con su foto, nombre y 4 estadísticas (PTS, eFG%, TS%, pérdidas;
  medias de los últimos N partidos). Cada estadística se colorea en verde
  ("para bien") o rojo ("para mal") comparándola contra la media del equipo;
  pérdidas es la única al revés (menos es mejor). Se descartó usar Plus/Minus
  como 4ª estadística pese a estar ya calculada en `insights.py`: los box
  scores internacionales de BBR no tienen columna "+/-" (solo la NBA la
  tiene), así que siempre habría salido "sin datos" — verificado contra una
  página de box score real antes de montar nada encima. Las fotos se
  normalizan a PNG con Pillow porque python-pptx no soporta WEBP (el formato
  del icono de fallback de baskonia.com para fichajes sin foto subida
  todavía, y con el que sí revienta si no se convierte).
- **Corregido el bug de raíz por el que `Game.league` reflejaba la liga fija
  del equipo de origen en vez de la competición real de cada partido**
  (`main.py` creaba cada `Team` con `league="acb"` a fuego y copiaba ese
  mismo valor a todos sus partidos; verificado contra la BD real que los 139
  partidos ya jugados tenían `league='acb'` al 100%, aunque 38 de ellos son
  de Euroliga). `parser.py` extrae ahora la competición real del `id` de
  cada tabla de calendario de BBR (`<slug>-SPA-regular-season` → `acb`,
  `<slug>-ELG-regular-season` → `euroleague`, también aplica a `-playoffs`)
  y la enhebra como clave `"league"` de cada partido devuelto por
  `parse_schedule_games()`; `upsert_game()` (`db/storage.py`) corrige
  también partidos ya existentes (antes ignoraba `.league` en el branch de
  actualización, así que ni siquiera una ejecución normal del pipeline podía
  arreglarla). Ejecutado un backfill idempotente con copia de seguridad
  previa de la BD (`python main.py --fix-league`, ver sección 6): de los 139
  partidos ya jugados, 38 pasaron de `acb` a `euroleague` (antes 139 `acb` /
  0 `euroleague`; ahora **101 `acb` / 38 `euroleague`**). Limitación
  conocida sin corregir (no bloqueante, ver sección 1): la tabla `SPA` de
  BBR agrupa liga regular ACB con playoffs/Copa bajo el mismo código de
  competición, así que `league='acb'` no distingue esas fases.
- **Análisis diferencial de scouting (las 6 ideas de la sección 7.3) + dos
  selectores globales de temporada y competición** (`stats.py`/`insights.py`/
  `app.py`, sin cambios de esquema): **(1) rachas (hot/cold)** — doble
  z-score por jugador (volumen de PTS y eficiencia TS%,
  `insights.player_form_zscore()`) de los últimos N partidos frente a su
  propia media/desviación de la temporada seleccionada, marcado 🔥/❄️/➖ con
  umbral ±1.0 (`insights.ZSCORE_HOT_THRESHOLD`/`ZSCORE_COLD_THRESHOLD`,
  constantes ajustables en `insights.py`; con los datos reales de hoy y
  N=5 el rango de z-score no llega a activar ninguna marca — no es un bug,
  es consecuencia de comparar la media de N partidos con la desviación por
  partido sin normalizar por `√N`, ver decisión de producto pendiente); **(2)
  perfil de tiro** — columnas `3PA%`/`FTr` (proporción de intentos de 3 y
  tasa de tiros libres sobre el total de intentos) en "Forma reciente" y en
  la ficha de jugador; **(3) dificultad del próximo tramo de calendario** —
  Net Rating medio de los próximos N rivales ya scouteados
  (`insights.schedule_difficulty()`); **(4) proyección simple del próximo
  partido** — posesiones y marcador esperado combinando pace/ORtg/DRtg
  medios de ambos equipos (`stats.project_matchup()` +
  `insights.project_next_matchup()`); **(5) scouting narrativo automático** —
  párrafo en español que combina ritmo, balance, perfil de tiro, máximo
  anotador y racha (`insights.scouting_narrative()`; umbrales
  `_NARRATIVE_PACE_FAST/_SLOW` y `_NARRATIVE_FG3A_RATE_HIGH/_LOW`, también
  constantes ajustables en `insights.py`); **(6) gestión de carga/fatiga** —
  minutos acumulados por jugador en una ventana de días ajustable (1-30,
  default 14, `app.games_in_window()`/`player_load_df()`), deliberadamente
  **sin** filtro de temporada/competición (una ventana de días nunca cruza
  el hueco real de ~3 meses entre temporadas de este proyecto). La
  "temporada" se deriva en tiempo de consulta a partir de `Game.date` (sin
  columna ni migración nueva — regla mes ≥ 7 → empieza esa temporada,
  `insights.season_start_year()`/`list_seasons()`/`current_season()`) y se
  enhebra por casi toda la superficie de `app.py`; únicas excepciones
  deliberadas: `upcoming_games()` (calendario pendiente, se quedaría vacío
  al elegir una temporada cerrada), `validate_data()` (calidad de datos
  transversal) y la propia carga/fatiga (idea 6, ver arriba). La
  "competición" es un segundo selector global, ortogonal al de temporada
  (`Game.league`, ya fiable tras el fix descrito arriba), combinado con
  "Temporada" por intersección (AND) en el mismo conjunto de funciones;
  hereda la misma limitación de la tabla `SPA` de BBR descrita en el punto
  anterior (elegir "ACB" sigue mezclando liga regular con playoffs/Copa del
  Rey) y hoy no muestra ninguna estadística avanzada al elegir "Euroliga"
  porque el 0% de `boxscores`/`team_game_stats` capturados hasta ahora es de
  esa competición — degradado limpio vía `Optional[...]`/`None`, no un fallo
  del filtro (pendiente de ampliar la captura si se quiere ver Euroliga con
  datos, fuera de alcance de esta feature). Ver también sección 6 (GUI) y
  sección 7.3.

**❌ Pendiente (no existe nada de esto todavía):**
- Arquitectura de la aplicación final.
- Interfaz de usuario (dashboard web, informes) que consuma `boxscores`/`team_game_stats`.
- Infraestructura (servidor, despliegue, base de datos de producción, ejecución periódica automatizada).
- PER (requiere medias de liga completas; fuera de alcance del PoC por ahora).

### Siguiente paso recomendado

El caso de uso central del PoC ya no es solo "preparar un Baskonia vs Bilbao":
la **GUI web** (`streamlit run app.py`) está centrada en el Baskonia y cubre
también cualquier rival de la temporada — partidos anteriores, próximos
enfrentamientos con datos reales de la 26/27, plantilla actual con fichas de
jugador, y scouting bajo demanda del próximo rival. Se sigue sin ampliar
cobertura a **temporadas pasadas** (la plantilla rota entre temporadas y lo
relevante es la forma reciente), pero sí a más rivales dentro de la
temporada actual.

**Limitación de datos ya resuelta** (histórico, por si se pregunta por qué
existe `scraper/baskonia_official.py`): se verificó dos veces (17 y 18 de
agosto) que BBR no publica el calendario de la temporada siguiente hasta que
empieza, ni cubre Supercopa/Euskal Kopa. Se resolvió añadiendo la web oficial
del Baskonia como fuente complementaria (ver sección 4.1) — ya no es una
limitación pendiente.

**Limitaciones que sí siguen abiertas:**
- **Emparejamiento de equipos entre fuentes**: BBR y baskonia.com no siempre
  usan el mismo nombre para el mismo club (nombre de patrocinador distinto,
  o traducción distinta: "Bayern München" vs "Bayern Munich", "Baxi Manresa"
  vs "Kids&Us Manresa", "Hiopos Lleida" vs "Ilerna Lleida", "Hapoel IBI Tel
  Aviv" vs "Hapoel Tel Aviv", "Maccabi Rapyd Tel Aviv" vs "Maccabi Tel Aviv",
  "EA7 Emporio Armani Milano" vs "Olimpia Milano", "Virtus Olidata Bologna"
  vs "Virtus Bolonia", "Crvena zvezda Meridianbet" vs "KK Crvena Zvezda").
  `resolve_opponent_team()` empareja por subcadena tras normalizar acentos,
  lo que arregla los casos de tilde/mayúscula, pero no los de patrocinador
  distinto: para esos crea un equipo nuevo (duplicado del ya existente en
  BBR). Verificado con los 73 partidos reales de la 26/27: de 15 posibles
  duplicados, 11 sobreviven a la normalización actual (8 son duplicados de
  patrocinador, 3 son rivales genuinamente nuevos). Para esos equipos
  concretos, el botón de scouting bajo demanda fallará con un aviso legible
  (`fetch_opponent_scouting` no puede adivinar su slug real de BBR).
- **Emparejamiento de jugadores entre fuentes**: mismo problema a nivel de
  jugador — `upsert_player()` empareja por nombre exacto, así que
  "Clément Frisch" (baskonia.com, con tilde) y "Clement Frisch" (BBR, sin
  tilde) quedan como dos filas `Player` distintas en vez de fusionarse. La
  ficha del jugador mostrará "sin datos" en forma reciente/temporada aunque
  sí exista historial de box scores bajo el nombre sin tilde.
- **`upcoming_games()` no distingue competición al mostrar el próximo
  partido**: la Supercopa (Euskal Kopa cuando la haya) aparece mezclada con
  Liga Endesa/Euroleague en la misma lista, ordenada solo por fecha.
- **La tabla `SPA` de BBR mezcla liga regular ACB con playoffs/Copa bajo el
  mismo código de competición**: `_table_competition()` (`parser.py`) solo
  distingue familias de competición (`SPA` → `acb`, `ELG` → `euroleague`),
  no fases dentro de la misma. Confirmado con datos reales: `real-madrid` y
  `barcelona` tienen 4 partidos jugados con `league='acb'` cada uno y
  `valencia` 5 (liga regular + playoffs/Copa del Rey fusionados bajo el
  mismo `id` de tabla de BBR). Hoy `league='acb'` no permite filtrar solo
  liga regular. El selector de **Competición** de la GUI (sección 6, feature
  de análisis diferencial de 7.3) hereda esta misma limitación: distingue
  ACB vs Euroliga vs Supercopa, no sub-fases dentro de ACB — elegir "ACB" en
  el selector sigue mezclando liga regular con playoffs/Copa del Rey.

Queda un frente razonable para seguir (no implementado todavía):
- **Automatizar la ejecución periódica** del pipeline (tarea programada) para
  que la GUI muestre siempre datos al día sin lanzar `main.py` a mano.

**✅ Tests automatizados implementados** (2026-08-19): suite `pytest` en
`tests/` que protege contra regresiones en las capas donde han aparecido los
bugs más sutiles (emparejamiento de nombres, parsing de BBR, cálculo de
estadísticas). Ver sección 6.2.

**✅ Fase F0 de la migración a la nueva arquitectura** (2026-08-19): arnés de
paridad `tools/parity_dump.py` que congela el comportamiento actual de las
funciones de datos de `app.py`/`insights.py` como oráculo objetivo, antes de
mover nada. Vuelca las 13 salidas de paridad a JSON canónico (claves
ordenadas, flotantes a 4 decimales, `NaN`→`null`) para una combinación
`(team, season, league, last_n)`, con fecha de referencia inyectable
(`--reference-date`) para ser reproducible. Línea base versionada en
`tests/parity/baseline/` (4 combinaciones que cubren los casos límite
conocidos). Ver `doc/arquitectura/02_migration.md`.

---

## 2. Arquitectura

> ⚠️ El árbol siguiente corresponde **únicamente al pipeline de scraping**,
> que es lo único implementado. La arquitectura de la app final (backend,
> frontend, infraestructura) **no existe todavía** y está por definir.

```
baskonia-pipeline/
├── requirements.txt      # requests, beautifulsoup4, pandas, SQLAlchemy, python-dotenv
├── .env.example          # plantilla de configuración (copiada a .env)
├── baskonia_core.py      # PUENTE DE MIGRACIÓN (F1): reexporta el dominio compartido
├── main.py               # orquestador principal del pipeline
├── report.py             # CLI de consulta: imprime lo ya guardado, sin red
├── app.py                # GUI (Streamlit) para usuarios sin conocimientos técnicos
├── scraper/
│   ├── __init__.py
│   ├── client.py             # BBRClient: HTTP con rate-limit (20s), reintentos y UTF-8 forzado
│   ├── parser.py             # parsea tablas HTML de BBR → estructuras limpias
│   ├── bbr.py                # construye URLs de BBR y orquesta llamadas
│   └── baskonia_official.py  # API JSON de baskonia.com: calendario 26/27 y plantilla actual
└── packages/
    └── baskonia_core/        # dominio compartido (F1 de la migración)
        ├── __init__.py
        ├── config.py         # config central (UA, rate-limit, DB, temporada, equipos)
        ├── insights.py       # forma reciente por jugador, stats por-36, validaciones
        ├── stats.py          # cálculo de eFG%, TS%, posesiones, pace, ORtg/DRtg
        └── db/
            ├── __init__.py
            ├── models.py     # SQLAlchemy: teams, players, games, boxscores, team_game_stats
            └── storage.py    # upserts idempotentes
```

### Flujo del pipeline (`main.py`)
1. Obtiene la **clasificación** de las ligas configuradas.
2. Para cada equipo de interés, obtiene **roster** y **calendario** de BBR —
   **solo si el equipo aún no está en la BD**, o si se pasa `--refresh-teams`.
   Si ya existe, reconstruye su lista de partidos desde la propia base de datos.
3. **Persiste el calendario completo** (`persist_schedule()`): todos los
   partidos, jugados y por jugar, como filas `Game` (sin resultado ni
   box score los que aún no se han jugado). Esto es lo que permite listar
   "próximos enfrentamientos" en la GUI sin descargar nada de ellos todavía.
4. **Calendario y plantilla oficiales de baskonia.com** (`scraper/baskonia_official.py`,
   solo para el Baskonia): completa "próximos enfrentamientos" con lo que BBR
   aún no publica (temporada siguiente, Supercopa, Euskal Kopa) y trae la
   plantilla actual con fotos. Se ejecuta siempre (barato, un par de
   peticiones), y un fallo aquí no interrumpe el resto del pipeline.
5. **Filtra los box scores** de BBR a capturar:
   - Enfrentamientos directos entre los equipos de interés.
   - Últimos N partidos jugados de cada equipo (`config.LAST_N_GAMES`, default `10`).
6. Guarda todo en la base de datos de forma **idempotente** (upserts); si el
   box score de un partido ya está guardado, no se vuelve a descargar.
7. Calcula y guarda **estadísticas avanzadas** (`stats.py`): eFG%/TS% por
   jugador y pace/ORtg/DRtg/Net Rating por equipo y partido.

### Scouting bajo demanda de un rival (`fetch_opponent_scouting()`)
Cuando desde la GUI se elige un próximo enfrentamiento contra un rival del
que todavía no hay datos, `fetch_opponent_scouting()` hace lo mínimo
necesario para poder mostrar su forma reciente: si el rival no tiene roster
guardado, descarga su página de equipo (roster + calendario completo,
también persistido con `persist_schedule()`); luego descarga el box score de
sus últimos N partidos jugados que aun no estén en la base de datos. Reutiliza
`_capture_and_store_boxscore()`, la misma función que usa el pipeline batch,
así que el cálculo de eFG%/TS%/pace/ORtg/DRtg es idéntico en ambos casos.

---

## 3. Configuración (`.env`)

| Variable | Descripción | Default |
|---|---|---|
| `USER_AGENT` | UA realista de Chrome (BBR bloquea bots sin UA realista) | Chrome/120 |
| `REQUEST_DELAY` | Segundos entre peticiones (BBR banea IPs si abusas) | `20` |
| `TIMEOUT` | Timeout de cada petición | `30` |
| `MAX_RETRIES` | Reintentos ante fallos transitorios | `3` |
| `DATABASE_URL` | URL de la base de datos | `sqlite:///data/baskonia.db` |
| `SEASON` | Año de finalización de temporada | `2026` |
| `TEAMS` | Slugs de BBR de los equipos de interés (el primero es el equipo en el que se centra la GUI) | `vitoria,bilbao` |
| `LEAGUES` | Ligas a capturar | `acb,euroleague` |
| `LAST_N_GAMES` | Nº de partidos recientes de cada equipo cuyo box score se descarga | `10` |

---

## 4. Fuente de datos: Basketball-Reference (BBR)

- Base: `https://www.basketball-reference.com`
- Internacional: `/international/`
- Liga ACB: `/international/spain-liga-acb/<año>.html`
- EuroLeague: `/international/euroleague/<año>.html`
- Equipo: `/international/teams/<slug>/<año>.html`
- Box score: `/international/boxscores/<fecha>-<equipo>.html`
- Jugador: `/international/players/<nombre>-<id>.html`

### Lógica de selección de box scores
Del calendario completo de cada equipo (ver más abajo), solo se descarga el
box score de:
1. **Enfrentamientos directos** entre los equipos de `TEAMS`.
2. **Los últimos `LAST_N_GAMES` partidos jugados** de cada equipo.

Implementado en `main.py` → `_select_boxscores()`:
- `parse_schedule_games(html)` (en `parser.py`) extrae el calendario estructurado:
  `date`, `opponent`, `opponent_slug`, `boxscore_url`, `is_home`, `points`, `opp_points`.
- `_select_boxscores()` filtra enfrentamientos directos (oponente en `TEAMS`)
  + últimos `LAST_N_GAMES` partidos jugados (con `boxscore_url`) de cada equipo.
- Deduplica por `boxscore_url`.
- `fetch_team()` en `bbr.py` devuelve también `'html'` crudo para `parse_schedule_games`.

**`opponent_slug`**: la celda del rival en la tabla de calendario de BBR
incluye un enlace a `/international/teams/<slug real>/<año>.html`. Se extrae
con una regex sobre ese `href` y es el slug **real** de BBR del rival (p.ej.
`"LDLC ASVEL"` → `villeurbanne`), no una normalización del nombre de display.
`resolve_opponent_team()` lo usa para crear/reutilizar el `Team` del rival
correctamente, y permite scrapearlo por su cuenta más adelante (scouting bajo
demanda) sin adivinar su slug.

**Competición real por partido (`league`)**: el `id` de cada tabla de
calendario de BBR internacional codifica también la competición real de
todas sus filas (p.ej. `vitoria-SPA-regular-season`, `vitoria-ELG-regular-season`,
`vitoria-SPA-playoffs`), no solo la fase (`regular-season`/`playoffs`, que el
parser ya distinguía antes). `_table_competition()` en `parser.py` extrae ese
código con una regex (`SPA` → `acb`, `ELG` → `euroleague`; un código no
reconocido se guarda tal cual en minúsculas, sin fallar) y
`parse_schedule_games()` la resuelve una vez por tabla (todas las filas de
una tabla comparten competición) y la añade como clave `"league"` a cada
partido devuelto. `Team.league` (fijo, `"acb"` para todos los equipos hoy)
solo se usa como valor de reserva cuando el `id` de la tabla no sigue este
patrón. Limitación conocida: la tabla `SPA` mezcla liga regular con
playoffs/Copa bajo el mismo código (ver sección 1).

### 4.1 Web oficial del Baskonia (baskonia.com)

`scraper/baskonia_official.py`. Solo para completar lo que BBR no cubre para
el Baskonia (ver "Limitación de datos" en la sección 1): calendario de la
temporada siguiente y competiciones de copa, y la plantilla actual con fotos.
**No** es una fuente de box scores/estadísticas avanzadas: eso lo sigue dando
únicamente BBR.

baskonia.com es una SPA en Angular sin datos en el HTML inicial (verificado
con `requests` normal: la página de calendario no trae ni una fecha ni un
nombre de rival). Los datos se sirven desde una API JSON pública que la
propia web consulta al cargar (descubierta interceptando las peticiones de
red con Playwright, no documentada oficialmente):

- Base: `https://cms.deportivoalaves.com/api` — un CMS Strapi **compartido**
  con otros clubes del grupo (el Deportivo Alavés, fútbol); por eso las
  peticiones filtran explícitamente por `Kosner Baskonia`.
- Calendario: `/games-items`, filtrado por `homeTeam`/`awayTeam` = `Kosner
  Baskonia` y `gameDate >= hoy` (evita depender de un id de temporada, que
  cambia cada año). Da fecha, rival, local/visitante, resultado (si ya se
  jugó) y competición (`Liga Endesa`, `Euroleague`, `Supercopa`, `Euskalkopa`,
  `Copa del rey`, `Preseason Baskonia`).
- Plantilla: `/team-members`, filtrado por `team.name = Kosner Baskonia` y
  `team_member_role.key = Player`. Da nombre, apellido, dorsal, posición
  (`team_member_position.label`, en castellano) y foto (`photo`, se usa el
  formato `small`).
- No requiere autenticación: es la misma petición que hace cualquier
  visitante al cargar la web.

**Fusión con los datos de BBR**: no hay un identificador común entre fuentes,
así que se empareja por nombre normalizado (`resolve_opponent_team()` para
equipos, nombre exacto para jugadores) — ver limitaciones conocidas en la
sección 1.

---

## 5. Modelo de datos (SQLAlchemy)

| Tabla | Descripción | Campos clave |
|---|---|---|
| `teams` | Equipos | `slug` (único), `name`, `league` (liga fija "de referencia" del equipo, `"acb"` para todos hoy; solo se usa como valor de reserva de `games.league` cuando no se puede determinar la competición real de un partido — no representa la competición real de cada partido) |
| `players` | Jugadores | `name`, `team_id`, `position`, `number`, `photo_url` (solo lo rellena la plantilla oficial de baskonia.com) |
| `games` | Partidos | `date`, `league` (competición **real** de ese partido concreto — `acb`/`euroleague`/`supercopa`—, capturada del `id` de la tabla de calendario de BBR o del calendario oficial de baskonia.com, no la liga fija del equipo; ver sección 4), `home/away_team_id`, `home/away_score`, `boxscore_url`, `notes` — `home_score`/`away_score`/`boxscore_url` a `NULL` mientras el partido no se haya jugado (calendario pendiente); `notes` guarda anotaciones de BBR como `"Postponed"` (partido aplazado que nunca tendrá resultado en esa fila) |
| `boxscores` | Stats por jugador y partido | `game_id`, `team_id`, `player_name`, `minutes`, `points`, `rebounds`, `offensive_rebounds`, `defensive_rebounds`, `assists`, `steals`, `blocks`, `turnovers`, `fg/fg3/ft` (made/attempted), `plus_minus`, `efg_pct`, `ts_pct` |
| `team_game_stats` | Estadísticas avanzadas por equipo y partido | `game_id`, `team_id`, `possessions`, `pace`, `off_rating`, `def_rating`, `net_rating` |

- `init_db()` crea el esquema, aplica una migración ligera (`ALTER TABLE ADD COLUMN`
  para columnas nuevas en tablas SQLite ya existentes) y devuelve una `sessionmaker`.
- `storage.py` implementa **upserts idempotentes** para todas las entidades.
- `stats.py` calcula eFG%/TS% (por jugador) y posesiones/pace/ORtg/DRtg/Net Rating
  (por equipo y partido) — ver fórmulas y simplificaciones asumidas en el propio módulo.

---

## 6. Cómo ejecutar

```bash
cd baskonia-pipeline
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # ajustar si hace falta
python main.py
python main.py --refresh-teams   # fuerza releer roster/calendario de los equipos
python main.py --fix-league      # backfill: corrige Game.league de partidos ya guardados
```

> El rate-limit de 20s hace que cada petición tarde ~20s. Con la selección
> filtrada (solo enfrentamientos directos + últimos 3 por equipo) son pocas
> peticiones, así que es rápido.
>
> Los partidos cuyo box score ya esté guardado en la base de datos **no se
> vuelven a descargar** en ejecuciones posteriores (se comprueba por `game_id`
> antes de pedir la página).
>
> El roster y el calendario de un equipo **tampoco se vuelven a descargar**
> una vez que el equipo ya existe en la base de datos; usa `--refresh-teams`
> para forzar la actualización cuando de verdad haga falta.
>
> `--fix-league` hace copia de seguridad de la base de datos
> (`data/baskonia.db.bak-<timestamp>`) y vuelve a descargar el calendario de
> los equipos que ya tienen roster propio guardado (hoy: `vitoria`, `bilbao`,
> `gran-canaria`) para corregir `Game.league` de partidos ya persistidos con
> la competición real de cada uno (en vez de la liga fija del equipo de
> origen — ver secciones 1 y 4). Es una acción independiente: no descarga
> box scores ni la plantilla oficial, y es idempotente (ejecutarlo varias
> veces no cambia el resultado ni duplica filas).

### Consultar lo ya guardado (sin red)

```bash
python report.py                          # usa los equipos de TEAMS (.env)
python report.py vitoria bilbao            # equipos concretos (slugs de BBR)
python report.py --last-n 3                # forma reciente sobre 3 partidos en vez de 5
python report.py --export informe.txt      # vuelca el mismo informe a un fichero
```

Imprime, por equipo:
- Partidos guardados con `pace`/`ORtg`/`DRtg`/`Net Rating`.
- **Forma reciente por jugador** (medias de los últimos N partidos, `--last-n`,
  default 5): minutos, PTS, PTS por-36-minutos y eFG%/TS% medios — pensado para
  ver quién llega en buena/mala racha, más útil que el histórico de temporadas
  pasadas dado que la plantilla rota entre temporadas.
- Para los enfrentamientos directos entre ambos equipos: box score completo
  (PTS, REB, AST, PTS/36, eFG%, TS%) de cada jugador.
- **Avisos de calidad de datos**: compara la suma de puntos del box score con
  el resultado guardado del partido y marca jugadores sin minutos registrados.

Solo lee de la base de datos, no hace peticiones a BBR. Con `--export PATH` el
mismo informe se vuelca a un fichero de texto en vez de imprimirse en pantalla.

### GUI para usuarios sin conocimientos técnicos

```bash
streamlit run app.py
```

Abre una página web local (por defecto `http://localhost:8501`) **centrada en
el Baskonia** (primer slug de `config.TEAMS`). La cabecera es **global a las
cuatro pestañas**: escudo, selector numérico de N partidos (forma reciente) y,
desde la feature de análisis diferencial de la sección 7.3, dos selectores
adicionales — **Temporada** (`season_selector`: temporadas con al menos un
partido guardado de este equipo, formato `AAAA-AA`, preseleccionada la más
reciente con partidos ya jugados) y **Competición** (`league_selector`: ligas
ya vistas de este equipo + opción "Todas" por defecto). Ambos se derivan en
tiempo de consulta sin columnas nuevas en la BD (`insights.list_seasons()`/
`current_season()` a partir de `Game.date`; `insights.list_leagues()` a
partir de `Game.league`) y se combinan por intersección (AND); se enhebran
por casi toda la GUI, con dos excepciones deliberadas: "Próximos
enfrentamientos" sigue listando **todo** el calendario pendiente
independientemente del filtro (para no vaciar la pestaña al elegir una
temporada ya cerrada) y la sección de carga/fatiga (ver pestaña 1) usa una
ventana de días, no de temporada. Cuatro pestañas:

1. **Resumen**: **resumen automático** en texto generado a partir de las
   stats ya calculadas del equipo — ritmo, balance, perfil de tiro, máximo
   anotador y racha (`insights.scouting_narrative()`; no se pinta nada si el
   equipo no tiene ninguna estadística avanzada en la temporada/competición
   seleccionada); estadísticas avanzadas medias (Pace/ORtg/DRtg/Net/eFG%/TS%);
   **últimos N partidos jugados** (gráfico de ORtg/DRtg) separado de
   **enfrentamientos directos** contra los otros equipos de `TEAMS` (pueden
   quedar fuera de los últimos N si el enfrentamiento fue hace tiempo, o
   solaparse si es reciente — se muestran en tablas distintas a propósito,
   para no dar la impresión equivocada de que no hay enfrentamientos
   directos recientes); forma reciente por jugador (gráfico de PTS, y tabla
   con columnas de perfil de tiro `3PA%`/`FTr` — proporción de intentos de 3
   y tasa de tiros libres sobre el total de intentos); **rachas (hot/cold)**:
   doble z-score por jugador (volumen de PTS y eficiencia TS%) de los
   últimos N partidos frente a su media/desviación de la temporada
   seleccionada, marcado 🔥/❄️/➖ con umbral ±1.0 (ver "Estado actual" sobre
   por qué apenas se activa con los datos de hoy); y **carga de minutos**
   (gestión de fatiga): minutos acumulados por jugador en una ventana de
   días ajustable (1-30, default 14), independiente del filtro de
   temporada/competición.
2. **Partidos anteriores**: selector con **cualquier** partido ya jugado del
   Baskonia (no solo los últimos N o los enfrentamientos vs Bilbao); al
   elegir uno, muestra pace/Net Rating y el box score completo de ambos
   equipos.
3. **Próximos enfrentamientos**: lista el calendario pendiente — combina lo
   que ya tenga BBR con el calendario oficial de baskonia.com para la 26/27
   (ver sección 4.1); esta lista no se acota por temporada/competición (ver
   excepción explicada más arriba). Antes del selector de rival,
   **dificultad del próximo tramo de calendario**: Net Rating medio de los
   próximos N rivales ya scouteados, calculado sobre la temporada/
   competición seleccionada de cada rival (`insights.schedule_difficulty()`).
   Si el rival elegido ya tiene datos en la base de datos, además de su
   scouting (misma vista que "Resumen" pero del rival, con las mismas
   subsecciones nuevas de rachas/carga) se muestra la **proyección del
   partido** (posesiones y marcador esperado combinando pace/ORtg/DRtg
   medios de ambos equipos en la temporada seleccionada,
   `insights.project_next_matchup()` — no aparece si a alguno de los dos le
   falta algún valor) y los **últimos 2 enfrentamientos directos** contra el
   Baskonia (constante `H2H_LAST_N` en `app.py`, independiente del N de
   "forma reciente"). Si es la primera vez que aparece, un botón
   **"Descargar datos de `<rival>`"** lanza la descarga bajo demanda (roster
   + calendario + box score de sus últimos N partidos,
   `fetch_opponent_scouting()` en `main.py`) respetando el rate-limit de
   20s — la GUI avisa del tiempo estimado antes de lanzarla. Si el rival no
   se pudo emparejar con un equipo real de BBR (ver limitaciones de la
   sección 1), el botón falla con un aviso legible en vez de reventar.
4. **Plantilla**: mosaico de fotos de la plantilla actual (baskonia.com,
   sección 4.1) — un icono genérico de silueta para fichajes sin foto
   subida todavía. Al elegir un jugador, su ficha muestra posición, dorsal,
   forma reciente y estadísticas de la temporada (mismo cálculo que
   `insights.player_recent_form`, con `last_n` grande para "toda la
   temporada"). Botón **"Generar ppt para Paolo"**: descarga un `.pptx`
   (`python-pptx`) con una diapositiva por jugador — foto, nombre y sus 4
   estadísticas más relevantes (PTS, eFG%, TS%, pérdidas) coloreadas en
   verde/rojo según estén por encima o por debajo de la media del equipo.

Cada pestaña con datos de un enfrentamiento incluye un botón **"Informe en
PDF"** (`build_pdf_report()`, vía `fpdf2`) descargable, pensado para que el
cuerpo técnico lo lleve impreso o lo comparta sin abrir la app. Los nombres
con caracteres fuera de Latin-1 (p.ej. "Žalgiris") se aproximan a ASCII antes
de escribirlos al PDF (`_pdf_safe()`): la fuente base de fpdf2 no los soporta
y antes hacía saltar toda la pestaña, no solo el PDF.

Todas las fechas se muestran en **castellano** (`format_date_es()` en
`app.py`): BBR guarda las fechas en inglés ("Sun, Nov 23, 2025") y la GUI las
convierte a "domingo, 23 de noviembre de 2025" en tablas, gráficos, títulos
de partido y el propio PDF.

Solo el botón de descarga bajo demanda de un rival hace peticiones a BBR; el
resto de la GUI solo lee `data/baskonia.db`.

Muestra el escudo de cada equipo (cabecera, pestañas, box scores) si existe
la imagen en `assets/logos/<slug>.{png,jpg,jpeg,svg}` (ver
[assets/logos/README.md](assets/logos/README.md)); si no, usa un icono de
baloncesto genérico como respaldo. El nombre a mostrar de cada equipo puede
sobrescribirse en `config.TEAM_DISPLAY_NAMES` (por defecto, el slug `vitoria`
de BBR se muestra como "Baskonia").

---

## 6.1 Despliegue en el NAS (Raspberry Pi) + Cloudflare Tunnel

> ⚠️ **Este despliegue es exclusivamente para el PoC** — para que un colega
> pueda ver e interactuar con la app de forma rápida y sin coste. **No es un
> despliegue de producción.** La app definitiva (si el PoC avanza a producto
> real) debería desplegarse de manera más profesional: contenedores
> orquestados, alta disponibilidad, base de datos gestionada, CI/CD,
> monitorización, autenticación y un dominio propio con certificado — ver
> "Despliegue profesional" al final de esta sección.

> Despliegue del PoC en el NAS doméstico (Raspberry Pi) para que un colega
> pueda ver e interactuar con la app desde fuera de la red local, sin exponer
> puertos del router.

### Arquitectura

```
[ Colega ] ──HTTPS──▶ [ Cloudflare Tunnel ] ──▶ [ cloudflared (RPi) ] ──▶ [ Streamlit :8501 (RPi) ]
```

- **Streamlit** sirve la app en `http://localhost:8501` dentro de la RPi.
- **`cloudflared`** (Cloudflare Tunnel) crea un túnel saliente hacia
  Cloudflare y expone la app con una URL pública `https://<nombre>.trycloudflare.com`
  (o un dominio propio si se configura). No hace falta abrir puertos en el router.
- El túnel es **gratuito** (plan free de Cloudflare) y da HTTPS automático.

### Requisitos en la RPi
- Raspberry Pi con **Docker** (recomendado) o Python 3.10+ instalado.
- `cloudflared` instalado (binario ARM64).

### Opción A — Con Docker (recomendada)

**1. `Dockerfile`** (en la raíz del repo):

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Dependencias del sistema necesarias para pandas/numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Puerto de Streamlit
EXPOSE 8501

# Arranca la app (sin auto-reload, en modo servidor)
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
```

**2. `docker-compose.yml`** (opcional, para levantar app + túnel juntos):

```yaml
services:
  app:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - ./data:/app/data   # persiste la BD fuera del contenedor
    restart: unless-stopped

  tunnel:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate --url http://app:8501
    depends_on:
      - app
    restart: unless-stopped
```

**3. Levantar:**

```bash
docker compose up -d --build
```

El log del contenedor `tunnel` muestra la URL pública `https://<nombre>.trycloudflare.com`
para compartir con el colega.

### Opción B — Sin Docker (Python directo + systemd)

**1. Clonar e instalar en la RPi:**

```bash
cd /opt
git clone <repo> baskonia-pipeline
cd baskonia-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ajustar si hace falta
```

**2. Servicio `systemd` para Streamlit** (`/etc/systemd/system/baskonia.service`):

```ini
[Unit]
Description=Baskonia Pipeline (Streamlit)
After=network.target

[Service]
WorkingDirectory=/opt/baskonia-pipeline
ExecStart=/opt/baskonia-pipeline/.venv/bin/streamlit run app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now baskonia
```

**3. Túnel Cloudflare** (servicio `systemd` aparte, o en el mismo):

```bash
# Instalar cloudflared (ARM64)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
sudo mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared

# Probar el túnel (muestra la URL pública)
cloudflared tunnel --url http://localhost:8501
```

Para un túnel persistente con URL estable, configurar un **túnel con nombre**
(`cloudflared tunnel create <nombre>` + `cloudflared tunnel route dns`) y un
servicio `systemd` que lo mantenga levantado.

### Notas
- **Datos**: la BD `data/baskonia.db` debe copiarse a la RPi (o montarse como
  volumen en Docker) para que la app tenga los partidos.
- **Arquitectura ARM**: la RPi es ARM64; `pandas`/`numpy`/`streamlit` tienen
  wheels para ARM64, así que no debería haber problemas de instalación.
- **Seguridad**: el túnel `trycloudflare.com` es temporal (la URL cambia al
  reiniciar). Para una URL estable y más control, usar un dominio propio con
  Cloudflare y un túnel con nombre. Si se quiere proteger la app, añadir
  autenticación (p.ej. `st.secrets` o un proxy con auth).
- **Actualización del pipeline**: para que la GUI muestre datos al día, hay
  que ejecutar `python main.py` periódicamente en la RPi (tarea `cron` o
  `systemd timer`) — ver "Automatizar la ejecución periódica" en la sección 7.

### Despliegue profesional (app definitiva)

> El despliegue del NAS con Cloudflare Tunnel es **solo para el PoC**. Si la
> app avanza a producto real, el despliegue debería ser mucho más robusto:

- **Contenedores orquestados**: Kubernetes (p.ej. EKS/GKE) o un PaaS
  gestionado (Railway, Render, Fly.io) en lugar de un contenedor suelto en la
  RPi.
- **Alta disponibilidad**: múltiples réplicas de la app detrás de un
  balanceador, con *health checks* y *auto-scaling*.
- **Base de datos gestionada**: PostgreSQL/MySQL gestionado (con backups y
  réplicas) en lugar de SQLite en un volumen local.
- **CI/CD**: pipeline de build, test y despliegue automático (GitHub Actions,
  GitLab CI) con entornos de staging y producción.
- **Monitorización y logging**: métricas (Prometheus/Grafana), trazas y logs
  centralizados (p.ej. OpenTelemetry + un backend de logs).
- **Autenticación y autorización**: control de acceso real (SSO/OIDC) en vez
  de una URL pública abierta.
- **Dominio propio y TLS**: dominio de la organización con certificado
  gestionado, en lugar de una URL temporal `trycloudflare.com`.
- **Seguridad**: secretos gestionados (Vault, secretos del proveedor),
  *rate limiting* y WAF en el borde.

---

## 6.2 Tests automatizados

Suite de tests `pytest` que protege contra regresiones en las capas donde han
aparecido los bugs más sutiles del proyecto (emparejamiento de nombres entre
fuentes, parsing de HTML de BBR, cálculo de estadísticas avanzadas). No hay
ningún test que haga peticiones de red: todos usan HTML de ejemplo embebido o
una base de datos SQLite en memoria aislada por test (nunca tocan
`data/baskonia.db`).

```bash
pip install -r requirements.txt   # incluye pytest
python -m pytest                  # ejecuta toda la suite
python -m pytest tests/test_parser.py -k "schedule"   # filtrar por test
```

### Cobertura

| Fichero | Qué cubre |
|---|---|
| `tests/test_parser.py` | Parsing de BBR: clasificación, roster, calendario (slug real del rival, competición real por tabla, local/visitante, notas "Postponed") y box scores (tablas ocultas en comentarios HTML). |
| `tests/test_stats.py` | Cálculo de eFG%, TS%, posesiones, ORtg/DRtg/Net Rating/Pace y proyección de enfrentamiento. |
| `tests/test_insights.py` | Funciones puras (fechas de temporada, minutos, per-36) y agregaciones sobre BD (forma reciente, resumen avanzado, rachas por z-score, dificultad de calendario, proyección, narrativa, carga de minutos). |
| `tests/test_main.py` | `_normalize_team_name`, `_select_boxscores` (últimos N + enfrentamientos directos + dedup + emparejamiento por subcadena) y `resolve_opponent_team` (creación, reutilización por slug, emparejamiento por subcadena, migración de slugs falsos). |
| `tests/test_storage.py` | Upserts idempotentes de `db/storage.py` (equipos, jugadores, partidos, box scores, stats avanzadas) y cálculo automático de eFG%/TS% en `upsert_boxscore`. |
| `tests/test_parity_dump.py` | Arnés de paridad (Fase F0 de la migración): determinismo del volcado canónico (dos ejecuciones → hash idéntico) y presencia/validez de la línea base en `tests/parity/baseline/`. |

### Fixtures compartidas (`tests/conftest.py`)

- `session`: sesión SQLAlchemy sobre una BD SQLite en memoria, aislada por test.
- `teams`: dos equipos de ejemplo (Baskonia y Bilbao).
- `played_game`: un partido jugado con box scores y stats avanzadas de ambos
  equipos, listo para los tests de agregación.

---

## 7. Pendiente / Próximos pasos

### 7.1 Tareas inmediatas del pipeline (scraping)
- [x] Ejecutar el pipeline (validado en `.venv` local, datos reales en `data/baskonia.db`).
- [x] Verificar que los box scores se guardan correctamente en SQLite (144 filas).
- [x] Guardar también los partidos (tabla `games`) con el resultado.
- [x] Calcular y guardar estadísticas avanzadas (eFG%, TS%, pace, ORtg/DRtg, Net Rating).
- [x] No volver a descargar roster/calendario de equipos ya conocidos salvo `--refresh-teams`.
- [x] Exponer las estadísticas guardadas mediante un script/CLI de consulta (`report.py`).
- [x] Corregir la detección de enfrentamientos directos (coincidencia por subcadena en
      vez de igualdad exacta) y fusionar equipos duplicados creados por el bug anterior.
- [x] Forma reciente por jugador (medias últimos N partidos) y stats por-36-minutos (`insights.py`).
- [x] Informe exportable a fichero de texto (`report.py --export`).
- [x] Validaciones básicas de calidad de datos (resultado vs box score, minutos faltantes).
- [x] GUI (Streamlit) para usuarios sin conocimientos técnicos (`app.py`).
- [x] Resolver el equipo rival por su slug real de BBR (extraído del enlace del
      calendario) en vez de adivinarlo normalizando el nombre de display; migra
      automáticamente los equipos ya creados con el slug "falso" de antes de este fix.
- [x] Capturar la competición real de cada partido en `Game.league` desde el
      `id` de la tabla de calendario de BBR, en vez de la liga fija del
      equipo de origen — ya **no** es "una suposición basada en el equipo de
      origen" (ver "Estado actual"). Backfill ejecutado sobre los 139
      partidos ya jugados (`python main.py --fix-league`). Limitación
      conocida: la tabla `SPA` de BBR no distingue liga regular de
      playoffs/Copa (ver sección 1).
- [ ] Revisar cobertura de `parse_schedule_games` en más temporadas/formatos de calendario.
- [x] Calendario de la temporada 26/27 y competiciones de copa (Supercopa,
      Euskal Kopa) vía la web oficial del Baskonia, ya que BBR no las publica
      todavía (`scraper/baskonia_official.py`, ver sección 4.1).
- [x] Corregido bug de encoding en `BBRClient` (ISO-8859-1 en vez de UTF-8 real)
      que corrompía nombres con acentos; reparados los ~20 valores ya
      guardados afectados.
- [ ] Emparejar equipos/jugadores entre BBR y baskonia.com cuando el nombre de
      patrocinador difiere (11 equipos y al menos 1 jugador conocidos
      afectados; ver limitaciones en la sección 1). Necesitaría una tabla de
      alias mantenida a mano, o un ID de club canónico compartido — no hay
      forma de resolverlo solo con coincidencia de texto.

### 7.2 Roadmap de la app final (todo por construir)
> Nada de esto existe todavía. Es la visión del PoC completo.

**Arquitectura de la aplicación**
- [x] Fase F0 de la migración: línea base de paridad (`tools/parity_dump.py` + `tests/parity/baseline/`), ver `doc/arquitectura/02_migration.md`.
- [ ] Definir la arquitectura de la app (backend, frontend, capas).
- [ ] Diseñar el modelo de datos de la app sobre los datos scrapeados.

**Estadísticas avanzadas**
- [x] Cálculo de estadísticas avanzadas (eFG%, TS%, pace, ORtg/DRtg, Net Rating).
- [ ] PER (requiere medias de liga completas; no implementado).
- [x] Presentación de enfrentamientos anteriores (head-to-head) — pestaña "Partidos anteriores"/"Próximos enfrentamientos" en `app.py`.

**Interfaz de usuario**
- [x] GUI básica (Streamlit) con tablas y gráficos interactivos (`app.py`).
- [x] GUI centrada en el Baskonia: navegación por cualquier partido anterior y
      calendario de próximos enfrentamientos con scouting bajo demanda del rival.
- [x] Pestaña "Plantilla": mosaico de fotos de la plantilla actual y ficha por
      jugador (posición, dorsal, forma reciente y de temporada).
- [ ] Dashboard web "de producto" (framework propio, no Streamlit) si el PoC avanza a producto real.
- [x] Generación de informes exportables en PDF (`app.py` → `build_pdf_report()`, botón de descarga en la GUI; `report.py --export` sigue disponible para texto plano).

**Infraestructura**
- [x] Despliegue documentado en el NAS (Raspberry Pi) con Cloudflare Tunnel (sección 6.1).
- [ ] Servidor / despliegue (ejecutado de forma efectiva en el NAS).
- [ ] Base de datos de producción.
- [ ] Automatización del pipeline (ejecución periódica).

### 7.3 Ideas de análisis diferencial (implementadas)

Brainstorm de mejoras orientadas a análisis/estadísticas, no solo tablas de
datos. Ninguna requirió scraping nuevo — las 6 se calculan sobre datos que el
pipeline ya guardaba (`boxscores`, `team_game_stats`, calendario oficial).
Implementadas las 6 en `stats.py`/`insights.py`/`app.py`, acotables a una
**temporada** y una **competición** concretas mediante los dos selectores
globales nuevos de la cabecera de la GUI (ver "Estado actual" y sección 6).

- [x] **Detector de rachas (hot/cold streaks)**: comparar la media de los
      últimos 3-5 partidos de cada jugador contra su propia media de
      temporada (z-score simple) para marcar automáticamente quién está en
      racha y quién bajo de forma. Cálculo puro sobre `player_recent_form`.
      Implementado como doble z-score (PTS y TS%), no solo uno — ver "Estado
      actual".
- [x] **Perfil de tiro / selección de tiro**: ratios a partir de columnas ya
      guardadas pero no expuestas como ratio (`fg3_attempted/fg_attempted`,
      tasa de tiros libres) — de qué manera tira cada jugador, útil para
      scouting real.
- [x] **Dificultad del próximo tramo de calendario**: media de Net Rating de
      los próximos N rivales (los ya scouteados) usando el calendario real
      de `baskonia_official.fetch_upcoming_games()`, para avisar de rachas
      de calendario duras o asequibles.
- [x] **Proyección simple del próximo partido**: estimar posesiones/marcador
      esperado combinando pace + ORtg/DRtg de ambos equipos (fórmula
      estándar de posesiones). Sería la única funcionalidad predictiva de la
      app; todo lo demás hoy es descriptivo/histórico.
- [x] **Scouting narrativo automático**: resumen en texto generado a partir
      de las stats ya calculadas del rival ("juega rápido, tira mucho de 3,
      su base anota de más...") en vez de solo tablas sueltas.
- [x] **Gestión de carga/fatiga**: minutos acumulados por jugador en una
      ventana de días (no de partidos) como proxy de fatiga para rotaciones.

**Descartado por falta de datos** (no es que falte implementarlo, es que la
fuente no lo soporta): PER (necesita medias de liga completas) y cualquier
métrica basada en +/- o en quién comparte pista — los box scores
internacionales de BBR no traen esa columna (solo la NBA la tiene,
verificado contra una página de box score real en la sección 1).

---

## 8. Notas / Riesgos

- **BBR puede bloquear** si se hacen demasiadas peticiones → respetar `REQUEST_DELAY`.
- El parser asume estructura de tablas de BBR (ids `roster`/`per_game`, calendario por
  `id` de competición, y en box scores `box-score-home` / `box-score-visitor`,
  ocultas dentro de comentarios HTML — BBR las esconde así para dificultar el scraping).
- No se instalaron dependencias en este entorno (el usuario lo ejecutará en otro).
- **No exponer credenciales/API keys en el código**; usar variables de entorno.
- **La API de baskonia.com no es pública/documentada**: se descubrió
  interceptando las peticiones que hace la propia web (`cms.deportivoalaves.com`,
  un CMS compartido con otros clubes del grupo). Puede cambiar de forma o de
  URL sin aviso; `scraper/baskonia_official.py` está aislado y envuelto en
  `try/except` en `main.py` precisamente por eso — un cambio ahí no debería
  tumbar el resto del pipeline, solo dejar de alimentar "próximos
  enfrentamientos"/"plantilla" hasta que se actualice el scraper.

# Reskin "Nocturne + Baskonia" — alcance aplicado y huecos pendientes

Origen: proyecto de Claude Design [`Baskonia Scouting`](https://claude.ai/design/p/9870856c-9aff-4060-b81b-e26edd0aeb54)
(sistema de diseño `Nocturne`, ficheros `Scouting Baskonia.dc.html` y
`Sistema de Diseño Scouting.dc.html`), importado el 2026-08-20.

Este documento resume qué se aplicó a `apps/web` y qué se dejó fuera porque
requiere datos o endpoints que la API no expone todavía. Sirve de punto de
partida cuando se retome el rediseño más allá del reskin visual.

## Qué se aplicó (reskin visual + layout)

- **Tokens de diseño** (`apps/web/src/index.css`, `apps/web/tailwind.config.js`):
  paleta oscura Nocturne (`--color-bg #161826`, `--color-surface #232532`,
  acento `--color-accent #9184d9`), rampas tonales `neutral-100..900` /
  `accent-100..900`, tipografía Inter, radios y sombras del sistema. El rojo
  Baskonia (`#e11d48`, clase `brand`) se reserva para identidad de marca y
  semántica de atención (victorias, marca lateral), tal y como especifica el
  `readme.md` del sistema — nunca como fondo extenso.
- **Layout**: `Layout.tsx` pasó de cabecera + pestañas horizontales a sidebar
  fija de 232px (4 secciones con iconos) + cabecera de filtros pegajosa,
  replicando la estructura de `Scouting Baskonia.dc.html`.
- **Componentes de UI**: clases `.card`/`.elev-sm`, `.btn-primary/-secondary`,
  `.input`, `.field`, `.tag-*`, `.table`, `.badge-*` (victoria/derrota,
  hot/cold) en `index.css`, aplicadas a `StatCard`, `StatTable`,
  `ExportButton`, `PanelState` (loading/error/empty), `GameDetail`,
  `BoxscoreTable`, `ScoutRivalPanel`, `Filters` y a las 4 pantallas
  (`ResumenScreen`/`TeamOverviewPanel`, `AnterioresScreen`, `ProximosScreen`,
  `PlantillaScreen`).
- **Nuevos componentes**: `ResultBadge` (píldora V/D) y `StreakBadge`
  (🔥/❄️/➖), usado este último ya en la tabla de rachas de
  `TeamOverviewPanel`.
- **Gráficos**: `BarChart` (ECharts) recoloreado con la paleta Nocturne
  (acento morado / gris neutro) y fondo/tooltip oscuros.
- Sin cambios de datos, rutas ni contratos de API — solo clases y estructura
  visual sobre los componentes existentes.

## Qué NO se implementó (requiere backend nuevo)

Piezas del mockup (`Scouting Baskonia.dc.html`) que no tienen hoy
equivalente de datos en `apps/web/src/api/schema.d.ts` / `apps/api`:

| Elemento del mockup | Qué falta en el backend |
| --- | --- |
| **Mapa de tiros en cancha** (sección "Mapa de tiros" en Partidos anteriores) | Coordenadas de tiro por jugada (`shot_x`, `shot_y`, `made`) — hoy el box score solo trae agregados (PTS/REB/AST/eFG%), no shots individuales. Necesita un endpoint tipo `GET /games/{id}/shots` y que el scraper/ingesta capture coordenadas (Basketball-Reference no siempre las expone; la web de la ACB/Euroliga sí en algunos casos). |
| **Scatter de rachas volumen vs. eficiencia** (z-score PTS vs. z-score TS%, "Rachas — volumen vs eficiencia") | El endpoint `streaks` ya calcula z-scores de PTS y TS% por separado (`StreaksScreen`/`useStreaks`); falta solo combinarlos en un único punto x/y en el frontend — **no es un hueco de backend**, es implementable ya. Anotado aquí porque el mockup lo trata como "no implementado todavía". |
| **Quintetos con mejor rendimiento (+/-)** ("Quintetos con mejor rendimiento") | No existe hoy ningún endpoint de lineups/quintetos. Requiere reconstruir combinaciones de 5 jugadores en pista a partir de play-by-play (que tampoco se ingesta actualmente) y calcular minutos + plus/minus por quinteto. Es la pieza de mayor esfuerzo de backend de todo el mockup. |
| **Carga de minutos como barra visual con umbral** ("Carga de minutos — ventana de N días") | El dato ya existe (`usePlayerLoad` / `player_load`), solo falta pintarlo como `.card` + barra en vez de tabla — **no es un hueco de backend**. |
| **Evolución del partido (línea de marcador por cuarto/minuto)** ("Evolución del partido") | Requiere el "play-by-play" o al menos el marcador por cuarto (`q1_score`, `q2_score`, …) del box score, que no se ingesta hoy (solo el resultado final por partido). |
| **Eventos clave del partido** ("Eventos clave") | Requiere play-by-play o, como mínimo, un feed de eventos destacados (mates, triples decisivos, rachas de parciales) — no hay tabla ni scraper para esto. |
| **Dificultad de calendario como barras de color por partido** ("Dificultad del próximo tramo") | El dato (`schedule_difficulty` / `useScheduleDifficulty`) ya existe; falta solo la representación visual de barras — no es hueco de backend. |
| **Insights automáticos con icono** (bloque "Insights clave" en Partidos anteriores) | No hay endpoint de insights por partido individual — existe `narrative` a nivel de equipo/temporada (`useNarrative`), pero no un desglose en 2-3 insights estructurados por partido concreto. Requeriría extender el generador de narrativa (o el LLM que la produce) para devolver una lista de insights con título/descripción en vez de un párrafo único. |
| **Exportar PDF / Generar PPT para Paolo** (botones ya presentes pero deshabilitados) | Ya identificado y documentado en el propio código (`ExportButton.tsx`): endpoints 17/18 devuelven `501` hasta la fase F6 (`apps/api/routers/reports.py`). No es nuevo de este reskin, solo se mantiene deshabilitado con el mismo estilo. |

## Otras diferencias menores del mockup no replicadas (decisión de diseño, no de datos)

- El filtro "Competición" del mockup usa un control segmentado (`Todas / ACB /
  Euroliga`, clase `.seg`); se mantuvo como `<select>` porque `LeagueSelect`
  recibe una lista arbitraria de ligas desde la API, no solo 2-3 fijas. Se
  podría migrar a `.seg` si en la práctica el número de ligas se mantiene
  siempre bajo (≤3).
- El pie de la sidebar del mockup ("Fuentes de datos: Basketball-Reference ·
  web oficial Baskonia · API ACB") no se añadió — es texto estático sin
  dato dinámico detrás; añadir es trivial si se quiere.
- La cabecera del mockup muestra un título/subtítulo dinámico por sección
  (`{{ sectionTitle }}` / `{{ sectionSubtitle }}`, ej. "Resumen del equipo" /
  "Estadísticas de forma y rendimiento"); se dejó el nombre del equipo fijo
  en `<h1>` porque hoy `Layout` no sabe en qué pestaña está sin duplicar la
  lista `TABS` — fácil de añadir con `useLocation()` si se quiere ese detalle.

## Cómo retomarlo

1. Las piezas marcadas "no es un hueco de backend" arriba (scatter combinado,
   barras de carga de minutos, barras de dificultad) se pueden implementar
   ya, sin tocar `apps/api` — son solo nuevos componentes de visualización
   sobre datos existentes.
2. Las piezas que sí requieren backend (mapa de tiros, quintetos, evolución
   por cuarto, eventos clave, insights por partido) necesitan primero decidir
   la fuente de datos (¿nuevo scraper de play-by-play? ¿lo expone la propia
   ACB/Euroliga?) antes de diseñar el endpoint — conviene tratarlas como
   iniciativas de producto independientes, no como "arreglar el reskin".

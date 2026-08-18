# Solicitud — 001-analisis-diferencial-7-3

## Petición verbatim del usuario

> Mira el README.md, la seccion 7.3 e implementa las funcionalidades ahí descritas

## Alcance resuelto (aclaración ya proporcionada, no volver a preguntar)

La sección 7.3 del README (`README.md`, líneas 695-731, "Ideas de análisis diferencial") lista 6
ideas marcadas `[ ]` (pendientes). El README dice que está "pendiente de que el usuario priorice
cuál(es) implementar", pero esa priorización ya está resuelta por el usuario: **implementar las 6**,
no un subconjunto. Si el diseño (etapa 1) concluye que conviene fasear la entrega en varios PRs/
iteraciones, debe proponerse como parte de la aprobación en el gate de diseño, pero el objetivo
entregable de esta feature son las 6 ideas.

Las 6 ideas (todas parten de datos ya persistidos; ninguna requiere scraping nuevo):

1. **Detector de rachas (hot/cold streaks)**: z-score de los últimos 3-5 partidos de cada jugador
   vs. su media de temporada, sobre `player_recent_form` (`insights.py`).
2. **Perfil / selección de tiro**: ratios `fg3_attempted/fg_attempted` y tasa de tiros libres, a
   partir de columnas ya guardadas en `boxscores` pero no expuestas como ratio.
3. **Dificultad del próximo tramo de calendario**: media de Net Rating de los próximos N rivales
   usando el calendario real de `scraper/baskonia_official.fetch_upcoming_games()` (ya persistido
   por `main.py`/`persist_schedule`).
4. **Proyección simple del próximo partido**: posesiones/marcador esperado combinando pace +
   ORtg/DRtg de ambos equipos (fórmula estándar de posesiones, reutilizando `stats.py`).
5. **Scouting narrativo automático**: resumen en texto generado a partir de stats ya calculadas
   del rival.
6. **Gestión de carga/fatiga**: minutos acumulados por jugador en una ventana de días (no de
   partidos), como proxy de fatiga para rotaciones.

**Fuera de alcance** (subsección "Descartado por falta de datos" del propio README): PER y
cualquier métrica basada en +/- — la fuente (BBR internacional) no tiene esos datos. No implementar.

## Integración esperada

Las nuevas funcionalidades deben integrarse en la app Streamlit existente (`app.py`) y en la capa
analítica (`stats.py`/`insights.py`), respetando el estilo y las capas actuales (ver
`.github/workflow.config.md` — sección "Capas"). El reparto exacto de ficheros/funciones lo decide
el `feature-architect` en `01_design.md`.

## Requisitos de proceso

- El `feature-developer` debe invocar la skill `karpathy-guidelines` al generar código.
- Documentación (etapa 4) y tests (etapa 5): a confirmar con el usuario al arranque (ver
  `STATUS.md`).

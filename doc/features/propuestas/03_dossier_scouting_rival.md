# 03. Dossier de scouting del rival, en un botón

**Estado:** propuesta, sin implementar · **Fecha:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

## 1. El problema del entrenador

El entrenador no prepara el partido delante de un dashboard: lo prepara en la reunión del día
antes, proyectando algo. Hoy la página **Próximo rival** ya tiene casi todo el contenido de un
scouting (perfil avanzado, balance y forma, cara a cara, jugadores clave, quintetos, rendimiento
por cuarto, mapa de tiros) — pero vive dentro de la aplicación, y a la reunión no va la
aplicación.

El diferencial aquí **no es analítico, es de formato**: convertir lo que ya se calcula en un
`.pptx` que se puede proyectar, anotar y mandar por WhatsApp al cuerpo técnico. Es la función que
hace que todas las demás se usen.

Además, el proyecto ya demostró que sabe hacerlo: el "PPT para Paolo"
(`app/reports/postgame_ppt.py`) es exactamente este patrón, pero **de después del partido y de
nuestros jugadores**. Esta propuesta es el mismo mecanismo mirando hacia delante y hacia el rival.

## 2. Qué se ve

Un botón en `app/pages/proximo_rival.py`: **"Generar dossier"** → descarga de `.pptx`. Estructura
propuesta, una diapositiva por bloque:

1. **Portada.** Escudos, competición, fecha, local/visitante, cara a cara histórico y balance.
2. **Identidad del rival.** Ritmo, ORtg/DRtg/Net, eFG/TS, %pérdidas, %rebote ofensivo — cada uno
   con su percentil en la liga, que ya está resuelto en la vista `team_style_percentiles`. Un
   equipo no se describe con números absolutos, se describe con "de los tres más rápidos de la
   liga y de los peores en rebote ofensivo".
3. **Cómo atacan.** Mapa de tiros por zona (volumen y acierto contra la media de la liga; ver
   [02_calidad_de_tiro.md](02_calidad_de_tiro.md)), y de dónde vienen sus puntos.
4. **Cómo defienden.** Lo mismo del otro lado: qué conceden por zona — es la base del documento
   [08_donde_castigar_al_rival.md](08_donde_castigar_al_rival.md).
5. **Una diapositiva por jugador importante** (5 a 8): foto, línea de temporada, percentiles,
   mapa de tiros propio, y 3-4 notas de texto ("tira el 47% desde la esquina derecha, el 28%
   desde la izquierda"; "provoca 5,1 faltas por 40 minutos").
6. **Rotación y quintetos.** Quintetos más usados con minutos y diferencia, y sus patrones de
   rotación por cuarto.
7. **Claves del partido.** 5 puntos, generados con la lógica de umbrales de
   [09_umbrales_de_victoria.md](09_umbrales_de_victoria.md) o, si no, por el LLM.

## 3. Datos: de dónde sale

Prácticamente **todo ya está resuelto en funciones existentes**; esta propuesta es sobre todo
trabajo de ensamblado y maquetación, no de consulta nueva:

| Bloque | Función que ya existe |
|--------|-----------------------|
| Cabecera, cara a cara | `queries.next_matchup`, `queries.team_record` (`app/data/queries.py`) |
| Identidad y percentiles | `queries.team_advanced_profile`, `queries_assistant.team_style_row` |
| Jugadores clave | `queries.team_roster_production`, `queries_assistant.player_percentile_row` |
| Mapas de tiro | `queries.player_shots_season`, `queries.court_zones` |
| Quintetos | `queries_assistant.team_lineups`, `queries.season_lineups` |
| Por cuartos | `queries.game_quarter_stats` y `game_team_quarter_stats` (5.896 filas) |
| Fotos y escudos | `app/components/avatar.py`, `queries.team_logo_url` |

Cobertura: los 737 partidos de la temporada 2025-2026 y los clubes de ACB y Euroliga, así que el
dossier se puede generar para cualquier rival de ambas competiciones, no solo para los de la ACB.

## 4. Construcción

Reutilizar el andamiaje de `app/reports/postgame_ppt.py` en vez de escribir otro generador:

- `_add_title_slide` / `_add_player_slide` / `_strip_shape_chrome` y las constantes de marca
  (`_ACCENT`, `_DARK`, 16:9) son directamente aprovechables; conviene extraerlas a un
  `app/reports/_deck.py` común antes de duplicarlas.
- El patrón de **dos capas** de ese módulo es el acierto que hay que copiar tal cual: reglas
  fijas como suelo (`_rule_based_highlights`) y LLM opcional por encima (`_llm_highlights`), con
  una sola llamada para todos los jugadores de golpe. Así el botón **funciona sin LLM
  configurado** y mejora cuando lo hay, que es el principio de toda la interfaz.
- La generación va en `st.session_state` + `st.download_button`, igual que en
  `app/pages/partidos_anteriores.py:138-172` (el `download_button` provoca *rerun*, así que el
  `.pptx` no puede construirse en el mismo paso en que se descarga).
- **Los mapas de tiro hay que rasterizarlos**: python-pptx no dibuja Altair. Opciones: exportar
  el gráfico a PNG (requiere `vl-convert-python`, dependencia nueva) o pintar la pista con formas
  nativas de python-pptx a partir de `court_zones` (sin dependencia, más trabajo, y encima queda
  editable dentro de PowerPoint). **Recomendada la segunda**, que además permite al cuerpo
  técnico mover cosas en la diapositiva.

## 5. Limitaciones conocidas

- **`player_advanced_stats` tiene solo 462 filas** y es de ACB: no se puede basar una diapositiva
  de jugador en las avanzadas oficiales para un rival de Euroliga. Usar `player_game_stats` +
  percentiles, que sí cubren todo.
- **`player_game_quarter_stats` cubre 335 de 737 partidos** (solo ACB): el detalle por cuartos a
  nivel de jugador no está disponible para rivales de Euroliga. A nivel de equipo sí
  (`game_team_quarter_stats`).
- Las **fotos** de jugador solo están descargadas para el Baskonia
  (`ingest/baskonia_web`), así que las diapositivas de rivales van con el *badge* de iniciales de
  `avatar.py`. No es un fallo, es el comportamiento previsto.
- Si el rival tiene pocos partidos en la base (recién ascendido, o filtro por competición muy
  estrecho), el dossier debe **decir el tamaño de muestra** en cada bloque en vez de presentar
  medias de tres partidos como si fueran identidad.

## 6. Encaje en el código

- `app/reports/_deck.py` (extraído de `postgame_ppt.py`) + `app/reports/scouting_ppt.py` nuevo.
- Botón en `app/pages/proximo_rival.py`; opcionalmente el mismo botón para cualquier equipo desde
  el asistente.
- Tests siguiendo `tests/app/test_postgame_ppt.py`, que ya existe y cubre el patrón
  reglas/LLM/fallback.
- `python-pptx` ya es dependencia (`app/requirements.txt`). Sin dependencias nuevas si se opta
  por dibujar la pista con formas nativas.

## 7. Esfuerzo y orden

Medio-alto, pero casi todo es maquetación y ensamblado con piezas existentes, no analítica nueva.
Es la propuesta con **más riesgo de dedicarle tiempo infinito al diseño** y conviene acotarla:
v1 con seis diapositivas y sin mapa de tiros, y a partir de ahí crecer.

Va la tercera porque es la que hace que se usen las dos primeras.

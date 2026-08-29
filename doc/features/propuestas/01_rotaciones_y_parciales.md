# 01. Gráfico de rotaciones y parciales explicados

**Estado:** IMPLEMENTADA (2026-08-28) · **Fecha de la propuesta:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

Vive en la pestaña **Rotaciones** de `app/pages/partidos_anteriores.py`, con el gráfico en
`app/components/rotation_chart.py`, las consultas en `app/data/queries.py` (`game_stints`,
`game_score_steps`, `detect_runs`/`game_runs`, `window_lineup`, `game_window_events`) y la
herramienta `game_runs` del asistente en `app/assistant/tools/team.py`. Se implementó tal como
está descrita abajo, con dos ajustes: la escalera del marcador se cierra con el resultado
oficial de `games` (los tiros no son eventos tipados, así que la última canasta puede caer
después del último evento) y las barras del timeline van en tinta neutra, no en verde, para no
competir con el verde del margen a favor.

Cifras verificadas contra `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

Cualquier web le dice que perdió por 8. Ninguna le dice que **el partido se fue en cuatro
minutos** —entre el 25 y el 29 del segundo tiempo—, con un quinteto concreto en pista, y que
tres de esas posesiones acabaron en pérdida en la salida de presión.

Reconstruir eso hoy es trabajo manual de vídeo. La base de datos ya tiene los tres ingredientes
(quién estaba en pista, en qué segundo, y qué pasó en ese segundo); solo falta juntarlos en una
pantalla.

## 2. Qué se ve

Una sola vista, dos capas:

1. **Timeline de rotaciones.** Una fila por jugador, barras horizontales con sus tramos en pista,
   eje X = minuto de partido. Detrás, como fondo del gráfico, el **margen del marcador**
   sombreado (verde por encima de 0, rojo por debajo). El entrenador ve de un vistazo qué
   combinaciones coinciden con las subidas y bajadas.
2. **Parciales detectados.** Sobre el mismo eje, marcas en los tramos donde hubo un parcial
   relevante (definición en §4). Al hacer clic en uno se despliega, debajo:
   - el quinteto que estaba en pista (los cinco, con minutos acumulados del tramo),
   - **la lista de eventos** de esa ventana ordenada por reloj: pérdidas, robos, rebotes
     ofensivos concedidos, asistencias, faltas — cada una con jugador y marcador en ese momento.

La segunda capa es la que convierte el gráfico en una explicación. Un timeline de rotaciones sin
el "qué pasó" es decoración; con él, es la diapositiva de la reunión.

Complemento barato de la misma vista: **el mismo timeline para el rival**, en un desplegable,
para preparar sus patrones de rotación (cuándo descansa a su base, con qué quinteto abre el
último cuarto).

## 3. Datos: de dónde sale

Todo está cargado, en los 737 partidos. No hace falta tocar la ingesta.

| Tabla | Filas | Qué aporta |
|-------|-------|------------|
| `lineup_stints` | 34.496 (737/737 partidos, ~46,8 por partido contando ambos equipos) | `team_id`, `start_seconds`, `end_seconds`, `points_for`, `points_against`, `margin_start` |
| `lineup_stint_players` | 172.480 (5 por tramo) | Quién estaba en pista en cada tramo |
| `play_events` | 172.762 | `seconds`, `quarter`, `game_clock`, `event_type`, `player_id`, `home_score`, `away_score` |

Reparto de `play_events` por tipo: `dreb` 35.499 · `foul_drawn` 31.612 · `foul_personal` 29.933 ·
`assist` 26.694 · `turnover` 18.696 · `oreb` 16.249 · `steal` 10.127 · `block` 3.952.

`start_seconds`/`end_seconds` y `play_events.seconds` están **en la misma escala** (segundos desde
el inicio del partido, `ingest/common/game_clock.py::game_clock_to_seconds`), así que cruzar
tramo y eventos es un `BETWEEN`, sin conversiones ni supuestos.

## 4. Cálculo

**Margen continuo.** No usar `score_progression`: según `schema.sql` es interpolación sintética
entre 0 y el resultado final, no marcador real. El marcador real y fiable es
`play_events.home_score`/`away_score`, que viene del play-by-play de la fuente. Se toma como
escalera (`step`) sobre `seconds`, no como línea interpolada.

**Detección de parciales.** Ventana deslizante sobre esa escalera: un parcial es una ventana de
duración ≤ D en la que el margen se mueve ≥ P puntos. Valores de partida: D = 180 s, P = 8. Se
quedan los máximos locales (no solapar dos parciales que son el mismo), y se ordenan por
magnitud. Los dos parámetros van en la interfaz, no en el código: cada entrenador tiene su
umbral de "parcial preocupante".

**Atribución.** Para cada parcial, los tramos de `lineup_stints` que solapan la ventana, y de
ellos el que más segundos aporta = "el quinteto del parcial". Importante recortar el solape
igual que hace `queries_assistant.clutch_lineups` (`end - max(start, inicio_ventana)`): un tramo
que empieza en el minuto 20 y llega al final **no** son 20 minutos de ese parcial.

**Eventos de la ventana.** `SELECT ... FROM play_events WHERE game_id = ? AND seconds BETWEEN ? AND ?`,
etiquetando cada evento como propio o del rival por `team_id`.

## 5. Limitaciones conocidas

- **No hay posesiones por tramo** (ver §"Lo que falta" del índice): el parcial se mide en puntos
  y en tiempo, no en posesiones. Para esta vista es suficiente —el entrenador razona en puntos y
  minutos—, pero no confundirlo con un net rating.
- **Los tiros no tienen tiempo.** `shots` no guarda ni cuarto ni reloj, así que en la lista de
  eventos de un parcial **no se pueden incluir los tiros**: aparecen pérdidas, robos, rebotes,
  asistencias, tapones y faltas, y los puntos se ven por el salto del marcador. Es la mayor
  carencia de esta función y merece la pena decirlo en la propia interfaz en vez de que el
  entrenador lo deduzca.
- `key_events` está **vacía** (0 filas) pese a existir en el esquema y consumirse en
  `queries.game_key_events`: no sirve como fuente de "momentos clave", hay que derivarlos.
- Un tramo puede solapar el final de cuarto; los tramos de prórroga tienen `end_seconds` > 2400 y
  entran con normalidad.

## 6. Encaje en el código

- **Consultas nuevas** en `app/data/queries.py`: `game_stints(engine, game_id, team_id)`,
  `game_score_steps(engine, game_id)`, `game_runs(engine, game_id, window_s, min_swing)`.
  El patrón de recorte por ventana ya está escrito en
  `app/data/queries_assistant.py::clutch_lineups` — reutilizar, no reinventar.
- **Gráfico** en un componente nuevo `app/components/rotation_chart.py` con Altair (ya es
  dependencia, `app/requirements.txt`): capa de barras (`mark_bar` con `x`/`x2`) + capa de área
  para el margen, eje X compartido. La selección de un parcial, con `st.selectbox` sobre los
  parciales detectados (más simple y robusto que una selección interactiva de Altair).
- **Dónde vive:** pestaña nueva en `app/pages/partidos_anteriores.py`, que ya reparte el partido
  en `tab_resumen, tab_box, tab_tiros, tab_quintetos` (línea 181). Es su sitio natural: la vista
  es siempre de UN partido.
- **Asistente:** una herramienta `game_runs` en `app/assistant/tools/` que devuelva los parciales
  del partido deja al chat responder "¿dónde se decidió el partido?" con la misma lógica, sin
  duplicarla.

## 7. Esfuerzo y orden

Medio. Lo caro no es el SQL (una tarde), es el gráfico: el timeline con margen de fondo tiene
más iteración de diseño que lógica. Sin dependencias nuevas ni cambios de esquema.

Es **la primera de la lista** porque es la que mejor demuestra el diferencial del proyecto: la
misma pantalla que ninguna web de estadística puede construir, porque ninguna tiene los tramos.

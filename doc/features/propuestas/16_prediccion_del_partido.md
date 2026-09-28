# 16. Predicción del partido y "qué la mueve"

**Estado:** IMPLEMENTADA (2026-09-28) · **Origen:** A2 de la [hoja de ruta](14_hoja_de_ruta.md) ·
**Índice:** [00_indice.md](00_indice.md)

Código: `app/analytics/prediction.py` (cálculo puro), `app/data/queries_prediction.py` (datos y
caché), `app/components/prediction.py` (sección "Predicción del partido" en "Próximo rival", justo
antes de "Objetivos del partido"), una frase en la portada del dossier (`app/reports/scouting_ppt.py`)
y la herramienta `game_prediction` del asistente (`app/assistant/tools/prediction.py`). Tests:
`tests/app/test_prediction.py`, `tests/app/assistant/test_prediction_tool.py`.

## 1. El problema del entrenador

Antes del partido la pregunta es "¿cómo lo vemos?", y la respuesta habitual es una sensación.
`upcoming_matchups.predicted_net_rating` existe en el esquema para contestarla, pero **ninguna
ingesta lo rellena**. Un porcentaje suelto tampoco sirve de mucho en la reunión: lo que se discute
es **por qué** — "¿somos favoritos por nivel, o solo porque jugamos en casa y ellos vienen de
Belgrado con dos días de descanso?".

## 2. Qué se ve

- **Margen esperado** del Baskonia y **probabilidad de victoria**, más el nivel ajustado de los dos.
- **Qué la mueve**, en frases y en barras, en puntos que suman exactamente el margen:
  "−1,9 por nivel ajustado por calendario (Valencia es mejor en lo que va de temporada)",
  "+3,8 por jugar en casa", "−1,5 porque llegamos con 2 días de descanso y ellos con 4".
- **Cuánto acierta**, siempre a la vista: error medio y % de ganadores acertados en el backtest de
  la temporada, junto a la referencia tonta ("gana siempre el local").
- Aviso permanente de que es un modelo (no sabe de lesiones ni bajas) y aviso de poca muestra si
  alguno de los dos equipos tiene menos de 8 partidos en el ajuste.
- Desplegable con el nivel ajustado de toda la liga.
- En el dossier `.pptx`, la misma predicción en una frase en la portada.
- En el asistente, `game_prediction` ("¿somos favoritos el domingo?", "¿cómo vemos el partido
  contra el Madrid fuera?"): sin rival usa el próximo partido del calendario.

La aplicación sigue siendo de **solo lectura**: la predicción se calcula al vuelo y se cachea; no
se escribe `predicted_net_rating`.

## 3. Cálculo

Una regresión ridge sobre el **margen final** de cada partido de la temporada (todas las
competiciones juntas: ACB y Euroliga quedan en la misma escala a través de los clubes que juegan
las dos):

    margen = rating_local − rating_visitante + campo·[no neutral] + descanso·(días_local − días_visitante)

1. **Nivel ajustado por calendario (tipo SRS)**: una columna por equipo (+1 local, −1 visitante).
   Descuenta la fuerza de los rivales enfrentados. Ridge `TEAM_RIDGE = 6` partidos equivalentes
   (σ del margen de un partido ≈ 11 puntos, σ de la fuerza real de un equipo ≈ 4,5 → σ²/τ² ≈ 6):
   un equipo con pocos partidos se acerca a un equipo medio. Los ratings se centran en 0.
2. **Ventaja de campo**: **estimada de la liga**, con un ridge débil (`HOME_RIDGE = 30` partidos
   equivalentes) hacia `HOME_PRIOR = 3` puntos — sin él, con un puñado de partidos todo el margen se
   cargaba al campo (un único partido ganado de 10 por el local daba "+10 por jugar en casa"); con una
   temporada entera el prior apenas mueve nada. Copa del Rey y Supercopa
   se tratan como sede neutral (`NEUTRAL_COMPETITIONS`): ni cuentan para estimarla ni se aplica.
3. **Descanso**: días desde el partido anterior del equipo en cualquier competición (criterio de la
   propuesta [04](04_fatiga_y_calendario.md)), recortados a 1-4 días (el primer partido de la
   temporada cuenta como descansado). El efecto por día **se estima de la liga en la misma
   regresión**, con dos frenos porque con una temporada la señal es débil: ridge
   `REST_RIDGE = 120` (≈1 punto/día a priori) y restricción de signo y tamaño `[0, 1,5]` puntos por
   día. Con menos de 20 partidos con diferencia de descanso, el efecto se deja en 0 y la pantalla lo
   dice.
4. **Probabilidad**: aproximación normal Φ(margen/σ), con σ la desviación de los residuos
   (grados de libertad efectivos del ridge) encogida hacia 11 puntos con 20 partidos ficticios, para
   que no dé probabilidades del 100% con poca muestra.
5. **Backtest sin mirar al futuro** (`backtest`): para cada fecha con al menos 40 partidos
   anteriores, se ajusta con lo anterior y se predicen los partidos de ese día. MAE, % de ganadores
   acertados, % de "gana el local" y Brier.

El ajuste de un partido solo usa partidos **anteriores a su fecha**, y el descanso de cada equipo se
cuenta hasta ese día (`queries_prediction.team_last_game_date`, en cualquier temporada).

## 4. Validación hecha

- Liga sintética de 12 equipos con fuerza, campo (+3) y descanso (+1/día) conocidos y ruido σ = 10:
  el modelo recupera el orden (correlación > 0,9), el signo y tamaño de la ventaja de campo y un
  efecto de descanso positivo; la descomposición suma exactamente el margen; la probabilidad es
  monótona en el margen; en el backtest acierta más ganadores que "gana siempre el local".
- Simulación de 18 equipos / 700 partidos: correlación 0,96 con la fuerza real, MAE ≈ 9 puntos,
  69% de ganadores (frente al 59% de "gana el local").
- Pantalla probada con `AppTest` sobre una base de datos sintética.

## 5. Limitaciones

- **Una temporada, solo marcadores**: el modelo no sabe de lesiones, bajas, rotaciones ni de si un
  partido ya no se jugaba nada. Es la línea base contra la que discutir, no un pronóstico.
- **Sin prior de la temporada anterior**: a principio de temporada casi todo es "equipo medio" +
  campo. Mejora natural: encoger hacia el rating de la temporada anterior (misma idea que A6 para el
  RAPM).
- **La probabilidad no incluye la incertidumbre de los ratings**: algo optimista lejos del 50%.
- **Pendiente con datos reales**: correr `prediction.backtest` sobre `data/baskonia.db` (no disponible
  donde se implementó) y, si compensa, activar la recencia (`half_life_days`) o ajustar
  `TEAM_RIDGE`/`REST_RIDGE`.
- **Descanso ≠ viaje**: dos días en casa y dos días volviendo de Estambul cuentan igual (mismo
  límite que la propuesta 04).
- **Sede neutral solo por nombre de competición** (`Copa del Rey`, `Supercopa`, los nombres que pone
  `ingest/acb/adapter.py`): la Final Four de Euroliga también es en sede neutral y va con el nombre
  "Euroliga", así que se trata como si hubiera local. Son 2-4 partidos por temporada.
- **Identidades duplicadas de un mismo club** (`barca` en ACB y `fcb` en Euroliga, ver
  [04](04_fatiga_y_calendario.md)) reparten sus partidos entre dos ratings encogidos hacia la media,
  le cuentan mal el descanso (cada identidad ve la mitad de su calendario) y quitan uno de los
  puentes ACB–Euroliga que anclan las dos escalas. No se arregla aquí (es de la ingesta). La
  herramienta del asistente falla con "sin datos" si el rival pedido no tiene partidos en la temporada
  (antes lo daba por "equipo medio"), la pantalla solo llega aquí con un rival con datos.
- **Temporada de scouting de reserva**: si el rival aún no ha jugado en la temporada seleccionada, el
  ajuste es el de su última temporada con datos (la misma que el resto de "Próximo rival", que ya lo
  avisa arriba): son los niveles de entonces, no los de este verano.
- El backtest se cachea aparte (`queries_prediction.season_backtest`, por temporada y fecha): son
  ~1-2 s con una temporada de ~700 partidos y ~170 fechas (un reajuste por fecha) y no depende del
  rival ni de la pista.
- Queda por hacer el enganche con los umbrales de victoria (09): "para pasar del 45% al 60% hay que
  ganar el rebote por X".

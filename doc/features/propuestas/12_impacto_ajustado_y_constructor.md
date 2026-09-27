# 12. Impacto ajustado (RAPM) y constructor de quintetos

**Estado:** IMPLEMENTADA (2026-09-27) · **Índice:** [00_indice.md](00_indice.md)

Código: `app/analytics/impact.py` (cálculo puro), `queries.season_stint_rows` +
`queries_assistant.season_impact` (datos y caché), `app/components/impact.py` (pantalla, en
"On/off y duplas") y las herramientas `lineup_builder` del asistente
(`app/assistant/tools/rotations.py`). Tests: `tests/app/test_impact.py`,
`tests/app/assistant/test_rotation_tools.py`.

## 1. El problema del entrenador

El On/Off (propuesta [07](07_onoff_y_duplas.md)) dice cómo le va al equipo con un jugador en
pista, pero **no le separa de con quién juega**: el suplente que comparte minutos con los
titulares sale inflado y el titular que se come los minutos contra el mejor quinteto rival sale
castigado. Es la trampa más común del +/- y la propia pantalla lo avisa (§5 de la 07).

Y la pregunta de verdad del día de partido no es "¿quién tiene mejor On/Off?", sino
**"hoy no tengo a X, ¿qué quinteto saco?"** o **"¿con quién rodeo a Y?"**. Eso hoy se decide
mirando una matriz de duplas; con datos, se puede proponer.

## 2. Qué se ve

- **Impacto ajustado (RAPM)** del equipo seleccionado, en barras y en tabla, **con el On/Off al
  lado**. Donde discrepan mucho, el On/Off está contaminado por el contexto: ese es el hallazgo.
- **Constructor de quintetos**: el entrenador marca quién está disponible (quita lesionados, no
  convocados o a quien quiere dosificar), opcionalmente a quién hay que incluir y si exige un base
  y un pívot. Salen los 10 quintetos con mejor proyección, **cada uno con los minutos reales que
  esos cinco han jugado juntos y su +/- real**. Con 0 minutos, la proyección es una idea para
  probar, y la pantalla lo dice.
- En el asistente, `lineup_builder` contesta "¿qué quinteto saco sin Kotsar?" con la misma lógica.

## 3. Cálculo

1. **De tramos por equipo a segmentos de diez jugadores** (`build_segments`). `lineup_stints` corta
   el tramo de un equipo solo cuando cambia ese equipo. Se cruzan los tramos del local y del
   visitante y se parten en cada frontera de cualquiera de los dos. **El marcador de cada frontera
   es exacto** (toda frontera es apertura o cierre de algún tramo, y cada tramo guarda su margen de
   entrada y sus puntos), así que la diferencia de cada segmento no es un reparto a ojo.
2. **Regresión ridge ponderada por minutos** (`fit_rapm`): una columna por jugador (+1 local,
   −1 visitante), objetivo = diferencia del local por 40 minutos, más un término de ventaja de
   campo sin penalizar. Se resuelve con ecuaciones normales acumuladas con `bincount` (nunca se
   materializa la matriz segmentos × jugadores): la temporada entera tarda décimas de segundo.
3. **λ = 1200 minutos**, justificado por un argumento bayesiano (ruido de un minuto de partido
   ≈ 5 pts² de varianza de margen; desviación real del impacto de un jugador ≈ 2,5 por 40). Con
   datos simulados de impacto conocido, la validación cruzada por partido
   (`cross_validate_ridge`) elige ese mismo orden de magnitud. **Pendiente: correr
   `cross_validate_ridge` contra `data/baskonia.db` y ajustar la constante si la real prefiere
   otra** (ver §5).
4. **Constructor** (`best_lineups`): enumera las combinaciones de 5 entre los disponibles (15
   jugadores = 3.003 combinaciones, instantáneo), puntúa con la suma de RAPM y adjunta lo observado
   (`observed_lineups`).

Unidades: **+/- por 40 minutos**, como el resto de la interfaz (sin posesiones por tramo, ver §5
de la 07).

## 4. Validación hecha

- Tests con datos sintéticos de impacto conocido: el modelo recupera al mejor y al peor, y al
  "suplente pegado a titulares" le devuelve su valor real.
- Simulación de 400 partidos / 80 jugadores (impacto real σ = 3): correlación 0,76 entre RAPM
  estimado e impacto real, y la validación cruzada elige λ ≈ 1200 entre {300…4800}.
- Pantalla probada con `AppTest` y en navegador sobre una base de datos sintética.

## 5. Limitaciones

- **Modelo aditivo**: la proyección de un quinteto no capta química ni encaje (dos creadores que
  necesitan el balón, dos pívots sin tiro). Por eso va siempre junto a lo observado.
- **Una temporada es poca muestra para RAPM**: por debajo de 300 minutos el número se marca como
  insuficiente. Mejora natural: usar la temporada anterior como *prior* (RAPM con prior), o
  agrupar dos temporadas con peso decreciente.
- La posición solo existe para la plantilla propia (baskonia_web): con un rival, la restricción
  "un base y un pívot" se desactiva sola.
- Todo en diferencia de puntos, no en posesiones.

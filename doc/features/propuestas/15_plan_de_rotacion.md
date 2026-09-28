# 15. Plan de rotación contra el rival (cruce 12 × 13)

**Estado:** IMPLEMENTADA (2026-09-28) · **Índice:** [00_indice.md](00_indice.md) · **Origen:** A1 de la
[hoja de ruta](14_hoja_de_ruta.md)

Código: `app/analytics/rotation_plan.py` (cálculo puro, sobre `rotation_patterns` e `impact`),
`app/components/rotation_plan.py` (sección en "Próximo rival", tras "Patrón de rotación"),
`scouting_ppt.rotation_plan_bullets` (diapositiva del dossier) y la herramienta
`rotation_plan_vs_rival` del asistente (`app/assistant/tools/rotation_plan.py`). Tests:
`tests/app/test_rotation_plan.py`, `tests/app/assistant/test_rotation_plan_tool.py`,
`tests/app/test_scouting_ppt.py`.

## 1. El problema del entrenador

La propuesta [13](13_patron_de_rotacion.md) dice **cuándo** flojea el rival (cuando sienta a su
base, en su peor tramo de reloj) y la [12](12_impacto_ajustado_y_constructor.md) sabe **puntuar
quintetos** propios. Por separado, el entrenador tiene que cruzarlas de cabeza. La pregunta real es
una sola:

> "En los minutos 8-12, cuando se sienta su base, su diferencia cae a −6 por 40: ¿qué sacamos
> nosotros ahí?"

Y no contra un rival medio, sino **contra los cinco que el rival suele tener en pista en esos
minutos**.

## 2. Qué se ve (en "Próximo rival", tras "Patrón de rotación")

1. **Disponibles**: la plantilla propia con minutos suficientes, marcada por defecto; el entrenador
   quita lesionados, no convocados o a quien quiera dosificar. Casilla "al menos un base y un
   pívot" si hay posiciones cargadas.
2. **Una frase por ventana**, lista para la pizarra: la ventana y por qué (descanso de X, o uno de
   sus peores tramos), cómo le va al rival ahí (y sin ese jugador en la temporada, si el On/Off es
   fiable), su quinteto habitual en esos minutos, nuestro mejor quinteto disponible con su
   proyección contra ellos y sus minutos reales juntos, y **cuánto mejora lo que solemos tener en
   pista en esos minutos**.
3. **Una pestaña por ventana** con los tres mejores quintetos: proyección contra su quinteto,
   mejora sobre lo habitual, minutos reales y +/- real.
4. En el **dossier `.pptx`**, una diapositiva "Plan de rotación contra X" con una línea corta por
   ventana (disponibles por defecto: el dossier se genera antes de saber quién está lesionado).
5. En el **asistente**, `rotation_plan_vs_rival` ("¿qué sacamos cuando sientan a su base?"),
   con `unavailable` e `is_home` (por defecto, el del calendario si es el próximo rival).

## 3. Cálculo

1. **Ventanas de ataque** (`attack_windows`), por prioridad y con un tope de cinco:
   - Descansos habituales (`rotation_patterns.player_rotation_table`) de los **tres jugadores de
     más minutos** del rival.
   - Sus **dos peores bloques de 4 minutos** con diferencia negativa y muestra suficiente
     (`block_performance`), salvo los que caen en su mayor parte dentro de un descanso ya listado
     (sería la misma ventana dos veces).
2. **Rendimiento del rival en la ventana**: `block_performance` a resolución de un minuto, sumando
   exactamente los minutos de la ventana (mismo reparto de puntos por tiempo que la 13).
3. **Su quinteto habitual en la ventana** (`typical_five`): los cinco con mayor proporción media en
   pista en esos minutos (`minute_shares`), **sin el jugador cuyo descanso define la ventana**
   (la premisa es que está sentado). Se enseña la presencia media de esos cinco: cuánto de fijo es.
4. **Proyección** de nuestro quinteto `L` contra su quinteto `R`:
   `Σ RAPM(L) − Σ RAPM(R) ± ventaja de campo` (+ en casa, − fuera; la estima el propio ajuste).
   El RAPM de los dos equipos sale del **mismo ajuste** (`season_impact` de la temporada de
   scouting del rival, todas las competiciones): la regresión ya incluye a los rivales, así que la
   resta es coherente con el modelo.
5. **Candidatos y quintetos**: `impact.best_lineups` con los disponibles (corte por defecto:
   `min(100 min, 25 % de los minutos del jugador más usado)`, para que a principio de temporada
   el plan no salga vacío) y lo observado (`impact.observed_lineups`).
6. **Lo habitual nuestro** en la ventana: los cinco disponibles más presentes en esos minutos
   (nuestros propios `minute_shares`), proyectados igual. `gain_vs_usual` = mejor − habitual.

## 4. Lo que el modelo aditivo NO hace (y se dice en pantalla)

**El orden de nuestros quintetos es el mismo en todas las ventanas**: restar el valor del quinteto
rival es restar una constante. El modelo aditivo no puede decir "contra su segunda unidad conviene
más tamaño". Lo que sí cambia de ventana a ventana, y es lo accionable, es:

- el **margen esperado** (contra su quinteto con o sin la estrella), y
- la **distancia entre lo que solemos tener en pista en esos minutos y lo mejor disponible**. Si en
  el descanso de su base nosotros tenemos habitualmente la segunda unidad, la decisión es
  "adelanta/atrasa el descanso de X para tener a Y y Z en pista entonces". Por debajo de +1 por 40
  el plan dice que lo habitual ya vale.

## 5. Validación hecha

- Tests con partidos sintéticos donde se sabe quién descansa, cuándo pierde el rival y qué valor
  tiene cada jugador: ventanas, quinteto rival sin el que descansa, proyecciones exactas en casa y
  fuera, disponibles, lo observado, frases largas y compactas, y de extremo a extremo contra la BD
  (`team_stint_rows` + `season_impact`).
- Pantalla probada con `AppTest` sobre una base de datos sintética (incluido quitar un disponible),
  y la diapositiva y la herramienta sobre la misma base.

## 6. Limitaciones

- Todas las de la [12](12_impacto_ajustado_y_constructor.md) (aditivo, sin química, una temporada
  es poca muestra) y la [13](13_patron_de_rotacion.md) (minutos de reloj, reparto proporcional de
  puntos dentro de cada tramo).
- **Su quinteto habitual es una moda, no una certeza**: en una ventana con presencia media del 60 %
  el rival alterna varios quintetos. Alternativa no implementada: proyectar contra la presencia
  ponderada de todos sus jugadores (Σ presencia × RAPM), más robusta pero menos legible en la
  pizarra.
- Si la temporada de scouting es una anterior (rival sin partidos aún en la seleccionada), nuestra
  plantilla y nuestra rotación habitual también son de esa temporada: el selector de disponibles
  permite corregirlo, pero los fichajes nuevos no aparecen.
- Las ventanas usan el patrón de **toda la temporada** (no el selector "últimos N" de la 13).
- Posiciones solo para la plantilla propia; no se usan las del rival.

## 7. Siguiente paso natural

Con las ventanas y los quintetos ya cruzados, el **planificador de minutos** (A3 de la
[hoja de ruta](14_hoja_de_ruta.md)) puede usar este plan como restricción: "en los minutos 8-12,
estos cinco", y repartir el resto con topes de carga.

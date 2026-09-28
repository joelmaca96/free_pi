# 12. Impacto ajustado (RAPM) y constructor de quintetos

**Estado:** IMPLEMENTADA (2026-09-27) · RAPM con prior de la temporada anterior (A6 de la
[hoja de ruta](14_hoja_de_ruta.md)): IMPLEMENTADO (2026-09-28), ver §6 · **Índice:** [00_indice.md](00_indice.md)

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
  insuficiente. Mitigado con el prior de la temporada anterior (§6); el aviso de muestra se
  mantiene igual, porque sigue midiendo cuántos minutos de ESTA temporada hay detrás.
- La posición solo existe para la plantilla propia (baskonia_web): con un rival, la restricción
  "un base y un pívot" se desactiva sola.
- Todo en diferencia de puntos, no en posesiones.
- **Campo neutral**: `games` no marca los partidos en pista neutral (Copa, Final Four), así que
  el ajuste les aplica igualmente la ventaja de campo al equipo que figura como local
  (`home_team_id`). Son pocos partidos por temporada y el término de campo es de control (no se
  enseña como dato de jugador); el efecto sobre el RAPM de cada jugador es despreciable.
- **Canasta y cambio en el mismo segundo**: si los tramos de los dos equipos no coinciden en el
  marcador de una frontera (el orden de la fuente decide a qué quinteto va la canasta),
  `build_segments` se queda con uno; el error es de una canasta entre dos segmentos contiguos y
  se compensa en la suma. En simulación a través de la reconstrucción real de la ingesta
  (`test_segments_match_the_real_score_of_ingested_games`), sin empates de segundo cada segmento
  cuadra exactamente con el marcador; con empates forzados, ~0,5% de los segmentos se desvían.
- **Tramos solapados del mismo equipo** (dato roto): se toma el primero por hora de inicio; no
  se ha visto en los datos servidos.

## 6. RAPM con prior de la temporada anterior (A6)

**Problema.** El ridge encoge a todo el mundo hacia 0 ("jugador medio"). Con una temporada, un
jugador de rotación con 300-800 minutos se queda a medio camino de 0 aunque la temporada pasada
ya se supiera que era bueno (o malo): se pierde información que existe.

**Cálculo** (`impact.fit_rapm(segments, prior=..., prior_weight=..., newcomer_prior=...)`): el
ridge se encoge hacia un punto de partida β₀ en lugar de hacia 0,

    min ‖W^½(y − Xβ)‖² + λ‖β − β₀‖²   ⇒   (XᵀWX + λI)β = XᵀWy + λβ₀

Es la media a posteriori con la temporada anterior como media a priori. Con pocos minutos este
año manda el punto de partida; con muchos, los datos (a λ = 1200 minutos, mitad y mitad).

- **β₀ = 0,7 × RAPM de la temporada anterior** (`PRIOR_WEIGHT`). El ruido de la estimación
  anterior ya lo descuenta su propio ridge (quien jugó poco llega casi a 0); el 0,7 descuenta lo
  que cambia de verdad de un año a otro (edad, rol, sistema, fichajes). Es el orden de la
  correlación año a año publicada para +/- ajustado regularizado en la NBA (0,6-0,8).
- **Quien no jugó la temporada anterior** parte de 0 (`NEWCOMER_PRIOR`), igual que sin prior.
  Es habitual usar un "nivel de reemplazo" algo negativo (−1 a −2) para los recién llegados; se
  deja en 0 por neutralidad (el fichaje que viene de otra liga no es un jugador de reemplazo) y es
  configurable por llamada.
- **Mismo λ** que sin prior. En rigor, con un buen prior la dispersión del impacto real alrededor
  del punto de partida es menor y λ podría subir; se deja igual por prudencia.
- `prior=None` es **exactamente** el cálculo de antes (test de igualdad exacta).
- La temporada anterior se ajusta **sin prior** (solo un año hacia atrás, no una cadena) con
  `impact.prior_from_fit`.

**Datos y caché** (`queries_assistant.season_impact(engine, season_id, competition_id,
use_prior=True)`): la temporada anterior es la inmediatamente anterior **por el año de inicio
de su etiqueta** ('2024-2025' → 2024) con partidos cargados (`queries_assistant.previous_season`).
No por `id`: el `id` es autoincremental y sigue el orden de ingesta, así que una temporada
histórica cargada después de la actual tendría `id` mayor y, por `id`, tomaría de prior la
temporada siguiente (auditoría 2026-09-28). Con el mismo filtro de competición: si la temporada
anterior no tiene tramos en esa competición, no hay prior (no se busca más atrás). Su ajuste es la misma función con `use_prior=False`, así que tiene su
propia entrada de caché y se calcula una sola vez. Sin temporada anterior, o sin tramos en ella,
el resultado es idéntico al de antes. Devuelve además `prior_season` (`{"id", "label"}` o `None`).

**Qué se ve.**

- En "Impacto ajustado (RAPM)", la casilla **"Usar la temporada anterior como punto de
  partida"** (activada por defecto si hay temporada anterior; desactivada y apagada si no). Con
  ella, dos columnas más: **Punto de partida** (el β₀ de cada jugador, en blanco si no jugó la
  temporada anterior) y **Solo esta temporada** (el RAPM sin prior), para ver cuánto ha movido el
  prior a cada uno. El pie de la tabla dice de qué temporada sale.
- El **constructor** y el **planificador de minutos** (propuesta 17) usan el mismo ajuste: leen
  la misma casilla a través de `components.impact.page_season_impact` (antes de la auditoría del
  2026-09-28 el planificador ignoraba la casilla y seguía con prior al desmarcarla). Las páginas
  sin casilla (plan de rotación en "Próximo rival", informe PPT, herramientas del asistente sin
  `use_prior`) usan el valor por defecto: con prior si hay temporada anterior.
- `lineup_builder` del asistente acepta `use_prior` (por defecto `true`), devuelve
  `rapm_no_prior`/`prior` por jugador y `prior_season`, y avisa en `warnings` cuando hay prior.
  Rechaza (`fail`) fijos sin tramos con el equipo (un id de otro equipo salía en sus quintetos
  como jugador medio) y más de cinco fijos, y avisa cuando no hay quintetos que proponer.
- Glosario: `rapm_prior` ("Punto de partida") y `rapm_no_prior` ("Solo esta temporada").

**Alternativa descartada: agrupar temporadas** (un solo ajuste con las dos temporadas y peso
decreciente en la anterior). Usa directamente con quién jugó cada uno el año pasado, pero da UN
coeficiente por jugador para los dos años (supone que no ha cambiado), duplica el tamaño del
ajuste, hace que la caché dependa de dos temporadas a la vez y mezcla en un número el rendimiento
con dos equipos distintos. El prior mantiene un ajuste por temporada, independiente y cacheado, y
se reduce a un vector más en la ecuación.

**Validación** (`tests/app/test_impact.py`):

- Con poca muestra (~40 min), el valor final llega a más del 90% del camino hacia el prior; con
  ~9.400 minutos, a menos del 25%, y un prior absurdo no le da la vuelta al signo.
- Dos temporadas simuladas (6 equipos × 9 jugadores, impacto real σ = 3 que cambia σ = 1 de un año
  a otro; en la segunda, tres de cada plantilla con ~150 minutos): con el prior baja el error de
  los jugadores con pocos minutos frente a su impacto real, el de la liga entera y el error de
  predicción fuera de muestra (`cross_validate_ridge(prior=...)`, pliegues por partido). Mejora
  en las 8 semillas probadas.
- Barrido de `prior_weight` en esa simulación (8 semillas, error absoluto medio frente al impacto
  real; con cambio año a año σ = 1):

  | peso | < 300 min | 300-800 min |
  |---|---|---|
  | 0 (sin prior) | 2,36 | 2,14 |
  | 0,5 | 2,05 | 1,83 |
  | **0,7** | **1,96** | **1,73** |
  | 1 | 1,87 | 1,61 |

  Con cambio año a año σ = 2,5 el orden es el mismo (2,77 → 2,45 → 2,37 en < 300 min). La
  simulación prefiere pesos altos porque no tiene fichajes, cambios de rol ni lesiones; 0,7 se
  queda por prudencia y porque casi toda la ganancia está entre 0 y 0,7.
- Pantalla probada con `AppTest` sobre una base de datos sintética de dos temporadas.

**Pendiente**: calibrar `PRIOR_WEIGHT` (y de paso λ con prior) con `cross_validate_ridge(prior=...)`
sobre `data/baskonia.db`, igual que el λ sin prior (§3).

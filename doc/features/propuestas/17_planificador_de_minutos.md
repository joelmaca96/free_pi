# 17. Planificador de minutos con carga

**Estado:** IMPLEMENTADA (2026-09-28) · **Índice:** [00_indice.md](00_indice.md) · **Hoja de ruta:** A3
en [14_hoja_de_ruta.md](14_hoja_de_ruta.md)

Código: `app/analytics/minutes_plan.py` (cálculo puro), `app/components/minutes_plan.py` (sección
"Planificador de minutos" en "On/off y duplas", solo para el equipo propio) y la herramienta
`minutes_plan` del asistente (`app/assistant/tools/minutes_plan.py`). Tests:
`tests/app/test_minutes_plan.py`, `tests/app/assistant/test_minutes_plan_tool.py`.

Se apoya en el RAPM y el modelo aditivo de la [12](12_impacto_ajustado_y_constructor.md) y en la carga de
calendario de la [04](04_fatiga_y_calendario.md).

## 1. El problema del entrenador

El constructor de quintetos (12) dice **qué cinco** sacar. Pero la decisión que se toma de verdad antes de
cada partido es **cuántos minutos juega cada uno**: hay 200 minutos de jugador (5 × 40) que repartir, y
en una semana de tres partidos el reparto que maximiza el marcador de hoy es justo el que funde a los
titulares para el de pasado mañana. Hoy eso se decide cruzando de cabeza el RAPM, la carga de la
semana y la posición.

## 2. Qué se ve

En "On/off y duplas", justo debajo del constructor y **solo con el equipo propio seleccionado**:

- **Fecha del partido**: por defecto el próximo del calendario; si no hay uno cercano (temporada
  cerrada), uno hipotético tres días después del último jugado.
- **Tabla editable** por jugador: disponible (por defecto, quien ha jugado alguno de los cinco últimos
  partidos del equipo), RAPM, minutos en los 7 días previos, media reciente, **mínimo** y **tope**. El tope
  viene sugerido por la carga (§3.2); el que el entrenador cambia pasa a ser "del entrenador" y ya no se
  toca.
- **RAPM**: el mismo ajuste que las secciones de RAPM y constructor de encima — lee su casilla "Usar la
  temporada anterior como punto de partida" (`st.session_state["impact_use_prior"]`), así que los números
  de las tres secciones cuadran siempre. En el asistente, parámetro `use_prior` (por defecto sí), igual que
  `lineup_builder`.
- **Criterio**: esperanza (RAPM) o prudente (penaliza muestra corta, §3.3).
- **Cobertura de base y pívot**: activada si se conoce la posición de todos los disponibles. Si no hay
  ningún disponible de una de las dos posiciones (los dos pívots fuera), esa cobertura es imposible y no se
  exige (se avisa); pantalla y asistente usan la misma regla (`applicable_position_floors`).
- **Resultado**: margen proyectado del plan, el del reparto reciente (reescalado a 200) y el del otro
  criterio; tabla con minutos del plan, media reciente, cambio y **qué lo limita** ("tope por carga",
  "no disponible", "cobertura de posición (Pívot)", "completa los 200 min"…), y barras plan frente a
  media reciente.

En el asistente, `minutes_plan` responde "¿cómo reparto los minutos sin Kotsar?" con frases del tipo
**"Marcus Howard: 30 → 28 min (tope por descanso corto: 1 día de descanso tras 31 min)"**.

## 3. Cálculo

### 3.1 Optimización

Modelo aditivo, el del constructor: cada minuto de un jugador sustituye a un jugador medio, así que

    margen proyectado = Σ_i RAPM_i · m_i / 40

Se maximiza con `Σ m_i = 200`, `mín_i ≤ m_i ≤ tope_i ≤ 40`, no disponibles a 0 y, por posición,
`Σ_{i∈g} m_i ≥ 40`.

- **El suelo de 40 minutos por posición es exacto**, no una aproximación: es necesario (un base en pista
  siempre ⇒ los bases suman ≥ 40) y suficiente por la regla envolvente de McNaughton — se ponen los
  minutos en fila en una cinta de 200 con los bases seguidos y los pívots seguidos, y se corta en cinco
  tiras de 40; con nadie por encima de 40 minutos, un bloque contiguo de ≥ 40 cubre todos los instantes.
  Lo que no dice es qué rotación concreta usar.
- **El algoritmo voraz es óptimo** (demostración en la docstring del módulo): los grupos de posición son
  disjuntos (una etiqueta por jugador, igualdad exacta como en `impact._position_ok`), así que dentro de
  cada grupo se llenan primero los de más valor y, entre grupos, el problema es separable y cóncavo, donde
  el reparto marginal voraz cumple KKT. Implementación: arrancar en los mínimos, cubrir cada suelo con
  los mejores de su grupo y repartir el resto por valor decreciente. Sin `scipy`: numpy y Python.
- Los tests lo comparan con **fuerza bruta** sobre 200 casos pequeños aleatorios (con grupos, suelos,
  mínimos y topes) y comprueban total, topes, mínimos, suelos y no disponibles.

### 3.2 Topes por carga (`LoadRules`, todo parametrizable)

1. **Tope general**: 32 minutos (editable en pantalla). Por debajo de 40 a propósito: sin tope, el modelo
   aditivo le da 40 minutos a los cinco mejores.
2. **Carga**: lo que le falta para cruzar el aviso de **140 minutos en 7 días** de "Carga acumulada"
   (`estado_equipo`, mismo umbral por defecto), contando los partidos de los 7 días anteriores a la fecha;
   nunca por debajo de 12 (dejarlo en 0 es desmarcar "Disponible", decisión del entrenador).
3. **Descanso corto**: con ≤ 2 días de descanso y ≥ 30 minutos en el último partido, como mucho 28.

Manda el menor, redondeado hacia abajo. Si con los topes sugeridos **no hay reparto posible** (semana de
tres partidos con plantilla corta), `relax_caps` los sube lo justo — primero por posición, luego para el
total — sin tocar los del entrenador, y la pantalla lo avisa: es la señal de que hay que dosificar a alguien
del todo o tirar del fondo de armario. Antes, un tope **sugerido** por debajo del **mínimo** que ha fijado
el entrenador sube hasta ese mínimo (el mínimo es decisión suya); si el que choca es un tope suyo, se
respeta y el plan dice que mínimo y tope no casan.

En la pantalla, la tabla editable se reinicia (vuelve a las sugerencias) al cambiar fecha, tope general,
temporada, competición o la casilla del prior: `st.data_editor` guarda las ediciones por posición de fila y
la tabla se ordena por RAPM, así que conservarlas con otro orden las aplicaría a otros jugadores.

### 3.3 Opción prudente

El RAPM es la media a posteriori del ridge. La opción prudente maximiza **RAPM − κ·σ_post** con
`σ_post ≈ τ·√(λ/(n+λ))` (τ = 2,5, λ = `impact.RIDGE_LAMBDA`, n = minutos en el ajuste, κ = 0,5): un
jugador con poca muestra solo gana minutos si su ventaja esperada compensa la incertidumbre. El margen
que se enseña es siempre con el RAPM, para comparar los dos criterios en la misma moneda.

**Con el prior de la temporada anterior** la fórmula sigue siendo la que implica el modelo: el prior solo
cambia la media a priori (β₀ = 0,7·RAPM anterior en vez de 0) y `fit_rapm` mantiene el mismo λ, y en el
ridge bayesiano la varianza a posteriori no depende de la media a priori. Es conservadora para quien trae
prior (alrededor de un buen punto de partida la incertidumbre real es menor que τ), la misma prudencia que
`fit_rapm` asume al no cambiar λ.

## 4. Validación hecha

- Tests unitarios: total 200, topes, mínimos, no disponibles, suelos por posición, "Ala-pívot" no cuenta
  como pívot, infactibilidad explicada, óptimo igual a fuerza bruta, carga de calendario (ventana
  semiabierta, partidos sin jugar a 0), reglas de tope, relajación de topes, criterio prudente.
- Herramienta del asistente probada con tramos sembrados (no disponibles, tope y mínimo del entrenador,
  descanso corto, fecha inválida, ids fuera de la plantilla, posición sin disponibles, `use_prior`, salida
  JSON válida sin `NaN`).
- Sección de pantalla con `AppTest` en la suite (`test_screen_section_uses_the_rapm_of_the_prior_checkbox`):
  la tabla enseña el RAPM con o sin prior según la casilla de la sección de RAPM.
- Pantalla probada con `AppTest` sobre una base de datos sintética (con y sin semana cargada, los dos
  criterios, y ausente con un rival seleccionado).

## 5. Limitaciones

- **Modelo aditivo**, sin química ni encaje; reparte **totales**, no la rotación minuto a minuto.
- **Posición por etiqueta exacta**: un "Ala-pívot" que puede jugar de cinco no cubre minutos de pívot. Con
  un solo pívot disponible, la cobertura puede obligarle a 40 minutos (subiendo su tope sugerido): ahí el
  entrenador debe desmarcar la cobertura o fijarle un tope. Mejora natural: posiciones múltiples por
  jugador, que rompen la disjunción y pedirían un LP pequeño.
- **La carga es de calendario y de minutos de partido**, sin entrenamientos, viajes ni datos médicos. No es
  prevención de lesiones.
- **Sin prórroga** (200 minutos exactos) y sin faltas personales (la propuesta 06 cubriría el riesgo de
  eliminación).
- **RAPM con poca muestra**: usar el criterio prudente. Con el *prior* de la temporada anterior, la σ a
  posteriori de la opción prudente es conservadora para quien lo trae (§3.3): penaliza igual a un
  suplente con 300 minutos este año tenga o no una temporada anterior que lo respalde.
- Con topes o mínimos no enteros (solo posibles desde el asistente) el reparto puede tener fracciones de
  minuto; en pantalla los topes y mínimos son enteros y el plan también.
- La "media reciente" es la de los partidos jugados de los cinco últimos del equipo; el margen "con el
  reparto reciente" la reescala a 200 minutos, lo que es una aproximación.

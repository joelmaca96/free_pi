# 14. Hoja de ruta: de visor de datos a ayuda a la decisión

**Estado:** A1 → [15](15_plan_de_rotacion.md), A2 → [16](16_prediccion_del_partido.md),
A3 → [17](17_planificador_de_minutos.md), A4 → [18](18_momentos_clave.md) y A6 → §6 de
[12](12_impacto_ajustado_y_constructor.md) IMPLEMENTADAS (2026-09-28); A5 y el bloque B, pendientes ·
**Fecha:** 2026-09-27 · **Índice:** [00_indice.md](00_indice.md)

Revisión del repositorio tras implementar las propuestas 01-13. Con las 13 hechas, la aplicación
ya **explica** (rotaciones, calidad de tiro, on/off, señales) y empieza a **recomendar**
(constructor de quintetos, ventanas de ataque). Lo que sigue va en esa dirección: cada idea
termina en una decisión que el cuerpo técnico toma, no en una tabla más. Ordenadas por
valor/esfuerzo.

## A. Nuevas funciones

### A1. Plan de rotación contra el rival (cruce 12 × 13) — esfuerzo bajo

"Cuando el rival sienta a su base (min 8-12), su diferencia cae a −6 por 40: estos son nuestros
tres mejores quintetos disponibles para ese tramo". Todo el cálculo ya existe
(`rotation_patterns.player_rotation_table` + `impact.best_lineups`); falta la pantalla que los
cruce y una diapositiva en el dossier. Variante: pesar el RAPM de nuestros jugadores **contra el
quinteto que el rival suele tener en pista en ese tramo** (el modelo ya tiene al rival en la
regresión; solo hay que proyectar contra sus cinco, no contra un rival medio).

### A2. Predicción del partido y "qué la mueve" — esfuerzo medio

`upcoming_matchups.predicted_net_rating` existe en el esquema y **ninguna ingesta lo rellena**.
Propuesta: rating de equipo ajustado por calendario (SRS: net rating descontando la fuerza de
los rivales enfrentados, mismo ridge que el RAPM pero a nivel de equipo), más ventaja de campo
estimada (ya sale del RAPM) y ajuste por descanso (propuesta 04, `performance_by_rest`). Salida:
margen esperado y probabilidad de victoria, **con la descomposición** ("+2,1 por jugar en casa,
−1,5 porque llegamos con 2 días de descanso y ellos con 4"). Encaja con los umbrales de victoria
(09): "para pasar del 45% al 60% hay que ganar el rebote por X".

### A3. Planificador de minutos con carga — esfuerzo medio

El constructor (12) propone quintetos; el siguiente paso es un **reparto de minutos** para el
partido que maximice la diferencia proyectada con topes de carga por jugador (propuesta 04:
minutos en 7/14 días) y mínimos por posición. Es un problema lineal pequeño (15 jugadores × 40
minutos); resoluble con `scipy.optimize.linprog` o incluso a mano (greedy). Útil sobre todo en
semanas dobles de Euroliga.

### A4. Momentos clave del partido (probabilidad de victoria en directo) — esfuerzo medio

Para "Partidos anteriores": un modelo de probabilidad de victoria según margen y tiempo restante
(logística sobre `play_events`/`lineup_stints` de toda la liga) permite ordenar los tramos por
**cuánto movieron la probabilidad de ganar**, no por el tamaño del parcial. Un 8-0 en el minuto
3 pesa mucho menos que un 5-0 en el 38. Da la lista de "las 5 jugadas que decidieron el partido"
y la atribuye a quintetos (WPA por quinteto). Base natural para la exportación de clips aparcada
en el índice.

### A5. Tendencias de fin de cuarto y salidas de descanso — esfuerzo bajo

Con `block_performance` a resolución de 1-2 minutos se puede medir cómo sale el rival de cada
descanso (min 11-12, 21-22, 31-32) y cómo cierra cada cuarto. Tendencias que un entrenador
trabaja en pizarra y que hoy no se ven.

### A6. RAPM con prior de la temporada anterior — esfuerzo bajo

Una temporada es poca muestra (propuesta 12 §5). El ridge puede encogerse hacia el valor de la
temporada anterior en lugar de hacia 0 (RAPM con prior): mejora mucho a los jugadores con
300-800 minutos, que son justo los de rotación.

## B. Mejoras sobre lo que ya existe

- **Calibrar λ del RAPM con los datos reales**: correr `impact.cross_validate_ridge` sobre
  `data/baskonia.db` (no disponible en el entorno donde se implementó) y ajustar
  `RIDGE_LAMBDA` si la validación prefiere otro valor.
- **Fatiga del rival con minutos nulos**: en `app/screens/proximo_rival.py` ("Fatiga y
  descanso"), si ningún jugador del rival tiene media de minutos (`min_avg` todo `NULL`, p.ej. un
  rival con tramos pero sin boxscore) la comparación `>` revienta la página entera. Con los datos
  actuales no ocurre, pero conviene `pd.to_numeric(..., errors="coerce")` antes de comparar. La
  misma línea existe en `app/assistant/tools/fatigue.py`.
- **Dividir `queries.py` y `queries_assistant.py`** (≈2.200 líneas cada uno) por dominio
  (partido, equipo, jugador, quintetos, árbitros). Hoy son el punto de fricción al añadir
  cualquier función.
- **Posiciones de los rivales**: `players.position` está vacío para casi todos los jugadores que
  no son del Baskonia. Con ella se activaría la restricción "un base y un pívot" del constructor
  para cualquier equipo y mejoraría la similitud (11).

## C. Datos que abrirían el siguiente escalón

Ya listados en el índice y siguen vigentes: tipo de jugada, defensor, reloj de posesión y
posesiones por tramo. De ellos, **posesiones por tramo** es el más barato (se pueden estimar en
la ingesta a partir de `play_events`: tiros, pérdidas y libres) y convertiría todo lo de
quintetos, RAPM incluido, de "por 40 minutos" a "por 100 posesiones".

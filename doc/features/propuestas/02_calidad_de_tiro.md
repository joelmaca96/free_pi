# 02. Calidad de tiro (xPPS): separar la decisión del acierto

**Estado:** IMPLEMENTADA la v1 por zonas (2026-08-28) · **Fecha de la propuesta:** 2026-08-28 ·
**Índice:** [00_indice.md](00_indice.md)

El cálculo vive en `app/analytics/shot_quality.py` (paquete nuevo, sin Streamlit ni SQLAlchemy),
las consultas en `app/data/queries.py` (`league_shot_counts`, `team_shot_counts`,
`game_shot_counts`, `player_shot_counts`), el mapa "vs. liga" en
`app/components/court.py::zone_heatmap(..., mode="vs_league")`, el pintado compartido en
`app/components/shot_quality.py` y las herramientas `shot_quality_*` del asistente en
`app/assistant/tools/shot_quality.py`. Se ve en las tres pantallas: la fila de métricas por
partido en **Partidos anteriores → Tiros**, y la sección **Calidad de tiro (xPPS)** en *Estado del
equipo* y *Próximo rival*.

Se implementó tal como está descrita abajo, con tres ajustes:

- **Solo la v1 por zonas.** La v2 por posición (§4.2, rejilla/hexbin suavizado) no está hecha; con
  ella tampoco haría falta todavía la tabla `shot_quality_grid` de §6, porque la línea base por
  zonas agregada en SQL son unas decenas de filas y cabe de sobra en `@st.cache_data`.
- **Referencia con red de seguridad.** La separación por competición es la del documento, pero una
  celda (competición, zona) con menos de 200 tiros —Copa del Rey y Supercopa casi enteras— toma
  prestada la referencia agrupada de todas las competiciones en vez de juzgar a nadie con veinte
  tiros. Se marca (`is_pooled`).
- **La lectura defensiva cambia los verbos.** Una defensa no "genera" ni "saca" xPPS: lo *concede*
  y lo *encaja*, y quien acierta es el rival. Con los verbos de ataque la frase se lee justo al
  revés de lo que dice.

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28 (92.046 tiros localizados y clasificados
por zona, de 95.263 totales) y reproducidas por el código: la tabla de §3 es literalmente lo que
devuelve `shot_quality.league_zone_table` (`Pintura` 58,9% / 1,18; esquinas 39,1% / 1,17; media
distancia central 40,9% / 0,82).

## 1. El problema del entrenador

Después de una derrota hay dos conversaciones distintas y la estadística clásica las confunde:

- **"Tiramos bien y no entró"** → el proceso funciona, hay que insistir.
- **"Tiramos mal"** → el proceso falla, hay que cambiar la generación de tiro.

El %TC no distingue. Un 38% tirando siempre desde la esquina es un ataque sano con mala racha; un
38% tirando media distancia forzada es un ataque roto con suerte normal. Y en defensa es peor: el
%TC del rival depende tanto de su acierto que **un mal defensor con rivales fríos parece bueno
durante seis semanas**.

La solución estándar en la NBA es *shot quality*: valorar cada tiro por lo que vale **desde esa
posición para toda la liga**, no por si entró. Con 92.046 tiros localizados de una temporada
entera de ACB + Euroliga, se puede construir aquí.

## 2. Qué se ve

Tres lecturas de la misma métrica:

1. **Por partido (nuestro y del rival).** Dos números grandes: *puntos esperados por tiro*
   (**xPPS**, la calidad de lo que generamos) y *puntos reales por tiro* (**PPS**). La diferencia,
   con su signo, es el acierto. Un texto honesto debajo: "generamos 1,04 xPPS —nuestra media es
   1,01— y sacamos 0,91: el ataque funcionó, el tiro no cayó".
2. **Por jugador.** Ranking de la plantilla por xPPS (quién elige bien) y por PPS − xPPS (quién
   acierta por encima de lo que su tiro vale). Son dos habilidades distintas y hoy se mezclan en
   una sola columna de %.
3. **En defensa.** Lo mismo del lado contrario: xPPS **concedido** por partido y por zona. Es la
   métrica defensiva honesta, porque no premia que el rival falle tiros abiertos.

En el mapa de tiros existente (`app/components/court.py`), una capa nueva: en vez de pintar el %
de acierto por zona, pintar **la diferencia contra la media de la liga en esa zona** — que es lo
que responde "¿aquí somos buenos, o aquí tira bien todo el mundo?".

## 3. Datos: de dónde sale

`shots`: 95.263 filas, 737/737 partidos. Columnas: `game_id`, `player_id`, `zone_id`, `pos_x`,
`pos_y`, `made`, `located`.

- **93.467 localizados** (98,1%); 1.796 sin localizar y 1.421 sin `zone_id` — se excluyen del
  cálculo, nunca se imputan.
- El **valor del tiro (2 o 3) no está guardado**, pero es derivable sin ambigüedad del `zone_id`
  desde el reteselado de zonas (`tools/retile_court_zones.py`): las zonas de triple son
  `{4, 5, 6, 8, 9, 15, 17}` (esquinas, exterior, ala 3 y las dos "Ala (3)"), el resto son de dos.
  Las antiguas 2 y 3 ("Ala izq./der.") ya no reciben tiros: 0 filas.
- Para trabajar por coordenada en vez de por zona, la línea la da
  `packages/baskonia_core/court_geometry.py::three_point_ellipse` — **la misma** elipse con la
  que se clasificó en la ingesta y con la que se dibuja la pista, que es justo el motivo por el
  que ese módulo existe.

**Línea base real de la liga**, ya calculada (temporada 2025-2026, ACB + Euroliga):

| Zona | Valor | Tiros | FG% | PPS |
|------|-------|-------|-----|-----|
| Pintura | 2 | 39.978 | 58,9% | **1,18** |
| Triple esquina (izq./der.) | 3 | 5.645 | 39,1% | **1,17** |
| Ala 3 (der./izq.) | 3 | 21.291 | 35,4–36,0% | 1,06–1,08 |
| Triple exterior (frontal) | 3 | 8.336 | 34,5% | 1,03 |
| Triple ala (izq./der.) | 3 | 3.101 | 32,3–32,8% | 0,97 |
| Media distancia central | 2 | 2.510 | 40,9% | **0,82** |
| Ala 2 (der./izq.) | 2 | 5.248 | 39,0–39,2% | 0,78 |
| Fondo (izq./der.) | 2 | 5.841 | 37,2–37,6% | 0,75 |

Esta tabla ya es, por sí sola, un argumento de vestuario: **un triple de esquina vale
prácticamente lo mismo que un tiro de pintura, y un 50% más que una media distancia** — y sale de
los datos de esta liga, no de un artículo de la NBA.

## 4. Cálculo

**Modelo esperado.** Dos niveles, en este orden:

1. **Por zona** (v1, suficiente para lanzar): xPPS de un tiro = PPS medio de la liga en su zona,
   como en la tabla de arriba. Trivial, robusto y explicable a un entrenador en diez segundos.
2. **Por posición** (v2): rejilla o hexbin sobre `pos_x`/`pos_y` con suavizado (media ponderada
   por distancia, radio del orden de 40 unidades sobre las 500 de ancho de pista), usando la media
   de la zona como valor previo donde no llegan tiros suficientes. Da un mapa continuo en vez de
   trece escalones. **Nunca** una celda con menos de unos 30 tiros sin suavizar.

Separar los dos niveles por competición (`competition_id`): ACB y Euroliga no tienen el mismo
nivel de tiro, y mezclarlas contamina la referencia.

**Métricas derivadas**, por jugador / equipo / partido:

- `xPPS` = media de los puntos esperados por tiro → calidad de la selección.
- `PPS` = puntos reales por tiro → resultado.
- `PPS − xPPS` = acierto por encima de lo que valía la posición.
- Versión defensiva: lo mismo sobre los tiros **del rival** en los partidos del equipo.

**Regularización obligatoria.** Con pocos tiros, `PPS − xPPS` es ruido puro. Encoger hacia 0 con
un peso del tipo `n / (n + k)` (k del orden de 100 tiros) y **no mostrar el dato por debajo de
unos 50 tiros**; en su lugar, decir "muestra insuficiente". Es la diferencia entre una métrica que
un entrenador vuelve a mirar y una que le miente una vez y ya no vuelve.

## 5. Limitaciones conocidas

- **Los tiros no tienen tiempo ni reloj de posesión.** No hay xPPS "en los últimos 5 minutos" ni
  "con menos de 4 segundos de posesión". Es la limitación más importante de esta función.
- **No hay defensor ni distancia al defensor**, así que "calidad" aquí significa *posición*, no
  *apertura*. Un triple de esquina abierto y otro con la mano en la cara valen igual en este
  modelo. Hay que decirlo en la interfaz: es calidad de **localización**, no calidad de tiro
  completa.
- **Los tiros libres no están en `shots`** (viven en `player_game_stats.ftm`/`fta`), así que esto
  mide tiro de campo. Para eficiencia total hay que sumarlos aparte, y ojo:
  `app/assistant/capabilities.py` documenta que `ftm`/`fta` pueden estar vacíos en la base de
  datos que sirve la interfaz — sondear antes de prometerlos.
- **Zona 13 ("Línea de fondo")**: 96 tiros con 79,2% de acierto. Es una franja de 5 unidades
  pegada al fondo que casi con seguridad recoge tiros de aro mal situados. Volumen despreciable,
  pero conviene fundirla con "Pintura" en vez de enseñarla como zona propia.
- El modelo describe la liga de **esta** temporada. No extrapolar a otra sin recalcular.

## 6. Encaje en el código

- **Cálculo** en un módulo nuevo `app/analytics/shot_quality.py` (o
  `packages/baskonia_core/shot_quality.py` si la ingesta también va a precalcularlo). Solo pandas
  y numpy, sin dependencias nuevas.
- **Precálculo recomendado:** la línea base de liga es una agregación de 92.046 filas — cabe de
  sobra en `@st.cache_data`, pero si se hace la versión por posición conviene materializarla en
  una tabla `shot_quality_grid` al final de la ingesta, no recalcularla en cada arranque.
- **Consultas** en `app/data/queries.py`, junto a `game_shots` (línea 449) y `player_shots_season`
  (línea 656), que ya devuelven exactamente las filas que hacen falta.
- **Interfaz:** capa nueva en `app/components/court.py` (`zone_heatmap` ya pinta por zona: añadir
  modo "vs. liga"), y una fila de métricas en `app/pages/partidos_anteriores.py` y
  `app/pages/proximo_rival.py`.
- **Asistente:** herramienta `shot_quality` en `app/assistant/tools/` — responde a "¿por qué
  perdimos?" mejor que cualquier tabla.

## 7. Esfuerzo y orden

Bajo para la v1 por zonas (la tabla de §3 ya está calculada; falta interfaz y regularización),
medio para la v2 por posición. Sin dependencias nuevas ni cambios de esquema.

Va la segunda porque es **la métrica que más cambia una conversación** por unidad de trabajo, y
porque el mapa de tiros ya está construido y solo hay que darle una capa nueva.

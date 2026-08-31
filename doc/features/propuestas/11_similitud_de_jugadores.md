# 11. Similitud de jugadores

**Estado:** IMPLEMENTADA (2026-08-31) · **Fecha de la propuesta:** 2026-08-28 ·
**Índice:** [00_indice.md](00_indice.md)

El cálculo vive en `app/analytics/similarity.py` (lógica pura sobre `pandas`/`numpy`, sin
Streamlit ni SQLAlchemy — mismo criterio que `zone_matchup.py`/`win_thresholds.py`), las consultas
nuevas `queries.league_player_index`/`league_player_zone_volume`/`opponent_team_ids` y
`queries_assistant.league_player_percentiles`, el pintado en `app/components/similarity.py`, el
bloque compacto "Se parece a..." en `app/components/player_dialog.py` (uso 1: preparar al rival) y
el buscador completo en `app/pages/similitud.py` (uso 2: fichajes, con filtros y pesos ajustables).
La herramienta del asistente es `similar_players` (`app/assistant/tools/similarity.py`).

Se implementó tal como está descrita abajo, con matices:

- **10 dimensiones de producción/eficiencia, no 8-15 "a elegir"**: se reutilizan literalmente las
  columnas de percentil de la vista `player_percentiles` (rebote separado en ofensivo/defensivo, sin
  un "rebote total" aparte que lo contaría dos veces) más 5 de reparto de tiro por zona
  (`ZONE_GROUPS`: pintura, media distancia, triple de esquina, triple de ala, triple central) — 15 en
  total, dentro del rango que pide §4.
- **Percentiles por partido, no por 40 minutos**: la vista `player_percentiles` (única fuente que ya
  resuelve percentiles por competición, §6) los calcula sobre medias por partido, no normalizadas a
  40'. Es una simplificación consciente frente a la redacción de §3 — recalcular por 40 minutos
  habría exigido una vista nueva en vez de reutilizar la que ya existe.
- **Barras de percentil superpuestas, no radar** (de las dos opciones de §2, Altair no tiene un mark
  polar nativo y el resto de la interfaz ya es Altair — un radar habría exigido una librería nueva
  solo para esta pantalla).
- **El filtro de minutos (300/500) se aplica siempre a los CANDIDATOS, nunca al jugador de
  referencia** — alguien con pocos minutos puede seguir siendo la pregunta ("¿a quién se parece
  este suplente que nos preocupa?").
- Los avisos de §5 (sin altura/peso, edad casi vacía, posición vacía en casi la mitad de la base de
  datos, una sola temporada, sin ajuste de contexto de equipo) van siempre pegados al resultado
  (`similarity.SIMILARITY_CAVEAT`), no como nota al pie opcional.

Cifras verificadas contra `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

Dos usos distintos, el mismo cálculo:

1. **Preparar al rival.** "El escolta que nos va a matar el domingo, ¿a quién se parece de los que
   ya hemos defendido?" Traducir un jugador desconocido a uno que el equipo ya ha visto es la
   forma más rápida de explicar un plan defensivo en una reunión.
2. **Fichajes y sustituciones.** "Se lesiona nuestro cuatro: ¿quién juega como él en esta liga?"
   Con 968 jugadores en la base —**280 con 500 minutos o más** y 389 con 300 o más— hay un mercado
   entero de ACB y Euroliga sobre el que buscar.

Lo que no aporta valor es una lista de "jugadores parecidos" sin decir **en qué** se parecen. La
propuesta incluye siempre esa explicación.

## 2. Qué se ve

- **Buscador**: se elige un jugador (de cualquier equipo) y salen los 8-10 más parecidos, con
  equipo, competición, minutos y edad si se conoce.
- **En qué se parecen y en qué no**: al lado de cada resultado, las dos o tres dimensiones que más
  se acercan y la que más se aleja ("mismo perfil de tiro y de asistencia; rebotea bastante
  menos"). Es lo que convierte la lista en información.
- **Gráfico de radar comparado** o barras de percentil superpuestas, con las 8-10 dimensiones del
  perfil.
- **Filtros**: competición, minutos mínimos, y "solo jugadores que ya hemos enfrentado" — que es
  el filtro que hace útil el uso (1).

## 3. Datos: de dónde sale

El perfil se construye con lo que hay completo para los 968 jugadores:

| Bloque | Fuente |
|--------|--------|
| Producción por 40 minutos (puntos, rebote of./def., asistencias, robos, tapones, pérdidas, faltas) | `player_game_stats` |
| Eficiencia (eFG%, TS%, PIR) | `player_game_stats`, `player_stats_combined` |
| Reparto de tiro por zona (qué proporción de sus tiros sale de cada sitio) | `shots` + `court_zones` |
| Percentiles dentro de su competición | vista `player_percentiles` |

El reparto de tiro por zona es lo que separa esta propuesta de un comparador de medias
cualquiera: dos jugadores con 14 puntos por partido son cosas muy distintas si uno los mete desde
la esquina y el otro en el poste, y eso está en los 92.046 tiros clasificados.

## 4. Cálculo

- **Normalizar antes de comparar.** Cada dimensión, a percentil dentro de su competición (la vista
  `player_percentiles` ya hace exactamente eso para las principales). Comparar valores brutos
  entre ACB y Euroliga mezcla nivel con estilo.
- **Distancia**: euclídea sobre el vector de percentiles, o coseno si se quiere premiar la *forma*
  del perfil por encima del nivel. Recomendado ofrecer las dos con un interruptor "parecido en
  estilo" / "parecido en nivel", porque son las dos preguntas reales y dan listas distintas.
- **Pesos por dimensión**, ajustables: quien busca un sustituto para un tirador no quiere que el
  rebote pese lo mismo.
- **Filtro de minutos**: mínimo 300, recomendado 500 (280 jugadores). Por debajo, los percentiles
  de un jugador son ruido y aparecerá como "parecido" a cualquiera.
- **La explicación** sale gratis del propio cálculo: las dimensiones con menor y mayor diferencia
  absoluta entre los dos vectores.
- Nada de esto necesita `scikit-learn`: son 280×280 distancias sobre un vector de 10-15
  dimensiones, numpy sobra.

## 5. Limitaciones conocidas

Esta propuesta es la que peor servida está por los datos biográficos, y conviene saberlo antes de
empezar:

- **`height_cm` es NULL en los 968 jugadores.** La fuente que se scrapea no publica altura (está
  documentado en `doc/features/ingestor/01_estado.md`). Sin altura ni peso, la similitud es
  **puramente estadística**: puede emparejar a un base de 1,85 con un alero de 2,03 que produzca
  lo mismo. Para el uso (2), fichajes, eso es una limitación seria.
- **`birth_date` solo está en 12 jugadores** (la plantilla del Baskonia): no hay edad para filtrar
  ni para valorar proyección.
- **452 de los 968 jugadores tienen la posición vacía**, así que ni siquiera se puede filtrar por
  puesto de forma fiable. Paradójicamente, esto empuja a la solución correcta: **agrupar por rol
  estadístico en vez de por posición nominal**, que además es más informativo. Pero conviene
  decirlo en la interfaz en vez de dejar un filtro medio vacío.
- **Una temporada**: la similitud describe cómo ha jugado alguien estos meses, no lo que es.
- Un jugador con pocos minutos en un equipo dominante puede parecerse a uno con muchos minutos en
  un equipo malo: la similitud de percentiles no corrige el contexto de equipo.

**Recomendación derivada:** completar altura y fecha de nacimiento desde otra fuente (las fichas
de ACB y Euroliga las publican) es una mejora de ingesta pequeña que multiplica el valor de esta
función. Mientras no exista, presentarla enfocada al uso (1) —preparar al rival— y con reservas
explícitas para el uso (2).

## 6. Encaje en el código

- Cálculo en `app/analytics/similarity.py`; el vector de perfil, cacheado con `@st.cache_data` —
  se recalcula entero en menos de un segundo para 280 jugadores.
- Consultas: `queries_assistant.player_percentile_row` (línea 457) y `compare_entities`
  (línea 901) ya resuelven buena parte de la extracción del perfil.
- Interfaz: dentro de `app/components/player_dialog.py`, un bloque "se parece a" — es donde el
  usuario ya está mirando a un jugador — y un buscador propio si se quiere para fichajes.
- Asistente: herramienta `similar_players`, que encaja de forma muy natural en el chat.

## 7. Esfuerzo y orden

Bajo en código, y con la mayor parte del trabajo en el ajuste de pesos y en la presentación.

Va la última porque **hoy está limitada por los datos, no por el código**: sin altura, sin edad y
con la mitad de las posiciones vacías, resuelve bien el uso de scouting de rival y solo a medias el
de fichajes. Sube varios puestos el día que la ingesta complete la ficha biográfica.

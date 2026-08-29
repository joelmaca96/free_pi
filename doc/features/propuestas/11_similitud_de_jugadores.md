# 11. Similitud de jugadores

**Estado:** propuesta, sin implementar · **Fecha:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

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

# 04. Fatiga y calendario cruzado ACB + Euroliga

**Estado:** propuesta, sin implementar · **Fecha:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

Cifras verificadas contra `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

Un equipo de doble competición no juega 34 partidos: juega 78. El Baskonia disputó **78 partidos**
en la temporada 2025-2026 (37 ACB, 38 Euroliga, 3 Copa), y de los 77 intervalos entre partidos,
**36 fueron de dos días o menos** (dos de ellos, de un solo día). Hay **63 ventanas de 7 días con
tres partidos dentro**.

Ninguna web de estadística cruza eso, porque ninguna tiene las dos competiciones bajo la misma
identidad de club: la ACB publica la ACB y la Euroliga publica la Euroliga. Esta base de datos sí
las tiene juntas, con puente de identidad (`team_external_ids`), y eso permite dos cosas que hoy
el cuerpo técnico hace a ojo:

- **Hacia dentro:** ver la carga real acumulada de cada jugador y anticipar en qué semana hay que
  repartir minutos, antes de que aparezca la lesión.
- **Hacia el rival:** "el domingo recibimos a X, que juega el jueves en Belgrado, vuelve el
  viernes y su base lleva 34 minutos de media en tres partidos en seis días".

## 2. Qué se ve

**a) Vista de carga (nuestra), en `estado_equipo`.** La página ya tiene "Carga de minutos"
(`queries.minutes_load`); esto la convierte en algo accionable añadiendo el eje temporal real:

- Minutos acumulados por jugador en ventanas móviles de **7 y 14 días** (no "últimos N partidos":
  cinco partidos en nueve días y cinco en tres semanas no son la misma carga).
- **Días de descanso** antes de cada partido, pintados en el calendario.
- Marcas de aviso configurables: jugador por encima de X minutos en 7 días, o tercer partido en
  siete días con más de 30 minutos en los dos anteriores.

**b) Ficha de fatiga del rival, en `proximo_rival`.** Un bloque corto y muy leíble:

- Días de descanso con los que llega, y dónde jugó el partido anterior.
- Sus partidos en los últimos 7 y 14 días, con competición.
- Minutos de sus 5-6 jugadores principales en esa ventana, y quién está por encima de su media.
- Su rendimiento histórico **con ese descanso**: net rating con ≤2 días vs. ≥3 días de descanso.
  Ese es el dato que convierte la ficha en una decisión ("con dos días de descanso concede 6
  puntos más por 100 posesiones; hay que correr").

**c) Calendario de la temporada que viene.** `upcoming_matchups` ya tiene **72 partidos cargados
del 2026-09-24 al 2027-05-21**: se puede pintar de antemano el mapa de semanas duras, que es
material de planificación de pretemporada, no de scouting.

## 3. Datos: de dónde sale

| Fuente | Qué aporta | Cobertura |
|--------|------------|-----------|
| `games.game_date`, `competition_id` | Calendario real de las cuatro competiciones | 737 partidos, 2025-09-27 → 2026-06-20 |
| `player_game_stats.minutes` | Carga por jugador y partido | Completa |
| `games.arena` | Dónde se jugó (proxy de viaje) | 737/737 |
| `game_advanced_stats` | Rendimiento a cruzar con el descanso | Completa |
| `upcoming_matchups` | Calendario futuro | 72 partidos de 2026-2027 |

Todo el cálculo es aritmética de fechas sobre tablas que ya existen. **No hace falta ningún dato
nuevo** salvo el que se menciona en §5.

## 4. Cálculo

- **Descanso** = `game_date` menos la fecha del partido anterior *del mismo equipo, en cualquier
  competición* — el matiz es todo el valor de esta función.
- **Carga móvil** = suma de `minutes` de los partidos del jugador en los D días anteriores a una
  fecha. Con 7 y 14 días es suficiente; añadir "minutos por día disponible" ayuda a comparar entre
  jugadores con roles distintos.
- **Rendimiento por descanso** = agrupar `game_advanced_stats.net_rating` del equipo por tramos de
  descanso (`≤1`, `2`, `3-4`, `≥5`). Con 78 partidos por equipo de doble competición hay muestra
  para tres o cuatro cubos; **no** para cruzarlo además con local/visitante, y hay que resistirse.
- **Viaje**: sin coordenadas de pabellón no hay kilómetros. Un proxy honesto es "jugó fuera de
  España el partido anterior", derivable de `arena` con una tabla de pabellones, o simplemente
  marcar competición europea como desplazamiento largo. Es aproximado y debe presentarse como tal.

## 5. Limitaciones conocidas

- **El Barça está duplicado en la base de datos.** `barca` ("Barça") tiene 42 partidos de ACB + 2
  de Copa y `fcb` ("FC Barcelona") tiene 40 de Euroliga: **son el mismo club y el puente de
  identidad no los une**. Para el Barça, toda esta función daría descanso y carga equivocados
  (vería la mitad de sus partidos). De los cuatro clubes españoles de Euroliga, Real Madrid,
  Valencia y Baskonia sí están correctamente unificados — solo falla el Barça. **Arreglar
  `team_external_ids` para ese club es requisito previo** de esta propuesta, y conviene añadir una
  comprobación de integridad ("ningún club con dos identidades") a la ingesta; hay trabajo
  empezado en esa dirección en `tests/ingest/test_identity_collision.py`.
- **Solo hay una temporada cargada.** El "rendimiento con poco descanso" de un equipo se calcula
  sobre 78 partidos como mucho, y repartidos en cubos son pocos. Enseñar siempre el tamaño de
  muestra y evitar afirmaciones fuertes.
- **No hay minutos de entrenamiento, ni carga física, ni datos médicos**, que es lo que de verdad
  mide la fatiga. Esto es una aproximación desde el calendario y los minutos de partido; hay que
  llamarlo por su nombre y no venderlo como prevención de lesiones.
- `upcoming_matchups` cubre partidos del Baskonia, no calendarios completos de todos los rivales:
  la vista de "semanas duras" propia funciona; la del rival, solo hacia atrás.

## 6. Encaje en el código

- Nuevas funciones en `app/data/queries.py`, junto a `minutes_load` (línea 172):
  `rest_days(engine, team_id, season_id)`, `rolling_load(engine, team_id, season_id, days)`,
  `performance_by_rest(engine, team_id, season_id)`.
- Interfaz: ampliar el bloque "Carga de minutos" de `app/pages/estado_equipo.py` (línea 98) y
  añadir un bloque nuevo en `app/pages/proximo_rival.py`.
- Asistente: herramienta `fatigue_profile` — responde a "¿cómo llega el rival?" de una pieza.
- Sin dependencias nuevas ni cambios de esquema (salvo el arreglo de identidad del §5, que es de
  datos, no de esquema).

## 7. Esfuerzo y orden

Bajo. Es la propuesta más barata de la lista después de la 08: aritmética de fechas sobre tablas
existentes, sin modelo ni gráfico complicado. El bloqueo real es el duplicado del Barça.

Va la cuarta porque explota la **ventaja estructural** del proyecto (tener las dos competiciones
en la misma base de datos), y porque es información que el cuerpo técnico usa el mismo día que la
ve.

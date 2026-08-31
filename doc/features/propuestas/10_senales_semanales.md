# 10. Señales semanales

**Estado:** IMPLEMENTADA, incluida la señal de equipo por zona (2026-08-31) ·
**Fecha de la propuesta:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

El motor de detección (contrastes, corrección de Benjamini-Hochberg, tamaño de efecto mínimo,
ranking por relevancia y redacción de dos capas) vive en `app/analytics/signals.py`, puro sobre
`pandas`/`numpy`, sin dependencias nuevas. Las consultas nuevas están en `app/data/queries.py`
(`team_player_game_log`, `team_game_advanced_log`, `team_game_zone_counts`) y
`app/data/queries_assistant.py` (`pair_minutes_by_window`). La pantalla es el bloque superior de
`app/pages/estado_equipo.py` (no `app/Home.py`, que es solo el punto de entrada de navegación y no
pinta contenido propio — ver su docstring). Encaja con el asistente como herramienta
`weekly_signals` (`app/assistant/tools/signals.py`).

Se implementó tal como está descrita abajo, con dos matices de alcance v1:

- **Rotación** cubre parejas, no tríos — mismo dato (`lineup_stints`), extenderlo a tríos es
  mecánico si hiciera falta.
- **Carga** reutiliza el umbral fijo de `app/pages/estado_equipo.py` (140 min en 7 días) en vez de
  un contraste estadístico: es, por diseño, la única señal de calendario y no de hipótesis (§2).

El tercer matiz de la v1 original ya está cerrado: **Equipo** ahora cubre también el reparto de
tiro por zona (§2, "y reparto de tiro por zona"), no solo los cuatro factores + ritmo.
`queries.team_game_zone_counts` extiende `team_shot_counts` con `game_id`/`game_date` en el
`GROUP BY` (la misma consulta, no una tabla nueva), así que ya hay un desglose de zona
PARTIDO A PARTIDO para toda la temporada de un equipo — lo que faltaba para no tener que repetir
`zone_matchup.py` cada semana. `analytics.signals.detect_team_zone_signals` no compara contra la
liga (eso sigue siendo la propuesta 08): compara el equipo CONSIGO MISMO, últimos K partidos
contra el resto, con un `two_proportion_test` sobre qué parte del volumen de tiro sale de cada
zona — mismo test que ya usan los porcentajes de tiro de jugador y el peso de una pareja en la
rotación, aplicado aquí a "de todos los intentos, ¿qué parte salió de esta zona?".

## 1. El problema del entrenador

Nadie abre una herramienta de estadística todas las semanas por gusto. Se abre cuando hay una
pregunta concreta, y el resto del tiempo la información que había dentro no llega a nadie.

La vuelta a esto no es otra tabla: es que **la herramienta hable primero**. Una pantalla —o un
mensaje— que cada lunes diga cinco cosas y solo cinco: **lo que ha cambiado de verdad** desde la
semana pasada. No "Howard promedia 14,2 puntos" (eso ya se sabe), sino "el porcentaje de triples
de Howard ha subido de 31% a 44% en cinco partidos, y el volumen se mantiene: ya no es ruido".

La dificultad no es técnica, es estadística: **casi todo lo que parece un cambio en cinco partidos
de baloncesto es azar**, y una herramienta que grite cada semana deja de leerse en un mes. El
valor de esta propuesta está entero en decir *poco* y acertar.

## 2. Qué se ve

Una tarjeta por señal, ordenadas por importancia, con tres partes:

1. **Qué ha cambiado**, en una frase con los dos números (antes → ahora) y el periodo.
2. **Cuánta confianza hay**, en lenguaje llano: "sostenido en 6 partidos", "aún puede ser azar".
   Nunca un valor p en pantalla — se calcula, no se enseña.
3. **Un enlace a la vista que lo explica** (la ficha del jugador, el mapa de tiros, el timeline).

Máximo cinco señales. Si una semana no hay nada relevante, **la pantalla lo dice**: "sin cambios
significativos esta semana" es una respuesta correcta y es lo que hace creíble a las demás.

Tipos de señal que merece la pena vigilar:

- **Jugador**: minutos, tiro (con volumen, para no confundir racha con cambio de rol), pérdidas,
  faltas, rebote ofensivo.
- **Equipo**: los cuatro factores de [09_umbrales_de_victoria.md](09_umbrales_de_victoria.md),
  ritmo, y reparto de tiro por zona ([08](08_donde_castigar_al_rival.md)).
- **Rotación**: una pareja o un trío que ha ganado peso ([07](07_onoff_y_duplas.md)).
- **Carga**: un jugador entrando en zona de sobrecarga ([04](04_fatiga_y_calendario.md)).

## 3. Datos: de dónde sale

Nada nuevo: `player_game_stats` y `game_advanced_stats` con `games.game_date` como eje. La base
tiene 523 jugadores con 10 partidos o más, así que hay muestra para la vigilancia de rivales
además de la propia (la plantilla activa del Baskonia son 12 jugadores).

Ya existe `queries_assistant.player_form` (línea 858), que compara los últimos N partidos con la
temporada. Esta propuesta es esa idea **generalizada a todo el equipo, con test estadístico y con
un filtro de relevancia**.

## 4. Cálculo

**Comparación.** Últimos K partidos (K = 5 por defecto, configurable) frente al resto de la
temporada del mismo jugador o equipo. Siempre en **tasas** (por 40 minutos, o porcentajes con su
volumen), nunca en totales: un jugador que juega más minutos "mejora" en todo sin haber cambiado.

**Test.** Para porcentajes de tiro, comparación de dos proporciones; para tasas por minuto, un
test sobre las medias por partido. Con K = 5 el test es débil por definición, y eso hay que
asumirlo: solo pasan los cambios grandes.

**El problema serio: comparaciones múltiples.** Con ~15 jugadores × ~10 métricas se hacen 150
contrastes cada semana. A p < 0,05, eso son **entre 7 y 8 falsas alarmas por semana aunque no
haya pasado absolutamente nada**. Sin corrección, la pantalla es un generador de ruido con aspecto
de rigor. Dos medidas, ambas obligatorias:

1. Corregir por número de contrastes (Benjamini-Hochberg, ~20 líneas con numpy, sin dependencias
   nuevas), y
2. exigir además un **tamaño de efecto mínimo** en unidades de baloncesto (por ejemplo, 6 puntos
   de porcentaje de tiro con 20 intentos, o 4 minutos de cambio de rol). Un cambio
   estadísticamente detectable pero pequeño no le sirve a nadie.

**Ranking.** Ordenar por relevancia práctica (efecto × minutos del jugador), no por significación:
un cambio enorme en el duodécimo hombre importa menos que uno mediano en el base titular.

## 5. Limitaciones conocidas

- **Cinco partidos son cinco partidos.** Esta función va a detectar poco, y eso es correcto por
  diseño. Si al probarla aparecen ocho señales cada semana, está mal calibrada.
- **El calendario contamina**: cinco partidos pueden ser cinco rivales de Euroliga o cinco de la
  parte baja de la ACB. Lo honesto en la v1 es mencionarlo en la tarjeta ("contra rivales de
  Euroliga"); ajustar por calidad de rival es v2 y requiere un modelo.
- **Una sola temporada**: no hay línea base histórica del jugador. "Ha cambiado" significa
  "respecto a lo que llevaba esta temporada", nunca respecto a su carrera.
- **No hay datos de lesión ni de estado físico**, que son la explicación más frecuente de un
  cambio real. La señal describe, no diagnostica.
- Si se envía fuera de la aplicación (correo, mensaje), es información sensible del club: cualquier
  envío automático debería ser una decisión explícita, no un valor por defecto.

## 6. Encaje en el código

- Cálculo en `app/analytics/signals.py`, sin dependencias nuevas (numpy y pandas bastan).
- Pantalla en `app/Home.py` —es el sitio natural: lo primero que se ve al entrar— o como bloque
  superior de `app/pages/estado_equipo.py`.
- **Redacción de las frases**: reutilizar el patrón de dos capas de
  `app/reports/postgame_ppt.py` (reglas como suelo, LLM opcional por encima). Las señales las
  detecta el código, siempre; el LLM solo las redacta mejor si está configurado. Nunca al revés:
  un LLM buscando cambios en una tabla inventaría la mitad.
- Encaja con el asistente como herramienta `weekly_signals`, para poder preguntarle "¿qué ha
  cambiado esta semana?".

## 7. Esfuerzo y orden

Medio-bajo en código; el trabajo real es **calibrar los umbrales** para que la pantalla acierte, y
eso solo se hace probando contra la temporada ya cargada (correr la detección semana a semana
sobre los 737 partidos y mirar si lo que saca tiene sentido para el cuerpo técnico).

Va la décima porque su valor depende de que existan primero las vistas a las que enlazar: una
señal sin la pantalla que la explica es una alarma sin destino.

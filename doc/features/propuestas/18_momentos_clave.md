# 18. Momentos clave del partido (probabilidad de victoria)

**Estado:** IMPLEMENTADA (2026-09-28) · **Origen:** hoja de ruta, idea A4 (`14_hoja_de_ruta.md`) ·
**Índice:** [00_indice.md](00_indice.md)

Vive en la pestaña **Momentos clave** de `app/screens/partidos_anteriores.py`, con el cálculo en
`app/analytics/win_probability.py` (lógica pura, sin Streamlit ni SQL), las consultas en
`app/data/queries_win_probability.py`, la pantalla en `app/components/key_moments.py`, una
diapositiva en la PPT de post-partido (`app/reports/postgame_ppt.py`) y la herramienta
`game_key_moments` del asistente (`app/assistant/tools/key_moments.py`, familia `team`).
Tests: `tests/app/test_win_probability.py` y `tests/app/assistant/test_key_moments_tool.py`.

## 1. El problema del entrenador

La vista de parciales (propuesta [01](01_rotaciones_y_parciales.md)) ordena los tramos por el
tamaño del parcial. Eso engaña: **un 8-0 en el minuto 3 pesa mucho menos que un 5-0 en el 38**
con el partido igualado. Lo que el entrenador quiere saber después de perder de 3 no es dónde
hubo más puntos seguidos, sino **qué cinco jugadas decidieron el partido** y quién estaba en pista.

La moneda común para comparar un tramo del primer cuarto con uno del último es la
**probabilidad de ganar**: cuánto la movió cada tramo (WPA, *win probability added*).

## 2. Qué se ve

En la pestaña, de arriba abajo:

1. **Curva de probabilidad de victoria del Baskonia** (0-100%), en escalera, con la raya del 50%,
   fondo verde por encima y rojo por debajo, las líneas de cuarto y los **cinco momentos clave**
   sombreados y numerados. Un deslizador fija la duración máxima de un momento (2 min por
   defecto).
2. **Los momentos que decidieron el partido**: inicio y fin (cuarto + reloj, como el acta),
   parcial, cambio de probabilidad en puntos porcentuales, probabilidad antes y después,
   marcador y **el quinteto de cada equipo** que más segundos estuvo en pista en la ventana
   (`queries.window_lineup`, el mismo recorte que en Rotaciones).
3. **WPA por quinteto** del Baskonia (y, plegada, la del rival): cuánta probabilidad se ganó o
   perdió mientras cada cinco estuvo junto en pista, con minutos y +/-.
4. **Descargar lista de clips (CSV)**: la exportación de vídeo que el índice dejó aparcada. Sin
   conocer el formato del editor del club, se ofrece lo universal: orden, cuarto y reloj de
   inicio y fin (con 10 s de margen antes y 5 después), segundo absoluto y una descripción. No
   hay integración con el sistema de vídeo.

La **PPT para Paolo** añade, tras la portada, una diapositiva con la misma tabla de momentos
(solo si el partido tiene play-by-play; si algo falla, la PPT sale como antes).

## 3. El modelo

**Datos.** La escalera del marcador de cada partido es la MISMA de la pestaña Rotaciones
(`queries._score_steps`: 0-0 de salida y cierre con el marcador oficial de `games`). De cada
partido acabado de la temporada **con play-by-play tipado** —toda la liga, todas las
competiciones, no solo el Baskonia— se toma una foto del marcador **cada 30 s de juego**
(~80 por partido, ~59.000 en una temporada completa). Muestrear a intervalos fijos y no evento
a evento evita que un tramo con muchas faltas y rebotes pese más solo por estar más documentado.

**Logística** de "gana el local" (respuesta = resultado final del partido), ajustada por
Newton/IRLS escrito sobre `numpy` (sin scipy/sklearn), con `t` = segundos que quedan del
periodo final y `m` = margen del local:

| Variable | Qué recoge |
|---|---|
| constante | ventaja de campo que no depende del reloj (p.ej. empate a 0 s: la prórroga) |
| `m / sqrt(t + 20)` | el corazón: un margen vale más cuanto menos tiempo queda (paseo aleatorio) |
| `sqrt(t + 20) / sqrt(2400)` | ventaja de campo que aún queda por jugar |
| `m / sqrt(2400)` | término lineal que corrige lo que la raíz no explica |

Si con el término lineal la probabilidad dejara de crecer con el margen en algún punto del
partido, se descarta y se reajusta sin él: la monotonía en el margen no se negocia. Con el
partido acabado y sin empate, la probabilidad es exactamente 0 o 1 (no un valor del modelo),
así que la WPA de todos los quintetos de un equipo suma exactamente final − inicio.

**Prórrogas.** Un estado de la prórroga lleva como tiempo restante lo que queda DE ESA prórroga
(como mucho 5 min): un empate con 3 min de prórroga se parece a un empate con 3 min del último
cuarto. Un empate al final del tiempo reglamentario queda en la constante.

**Modelo de reserva.** Con menos de 60 partidos con play-by-play en la temporada, la logística
no tiene muestra; se usa la aproximación normal clásica (Stern): el margen que falta por jugar
es un paseo aleatorio con deriva = ventaja de campo (3 puntos por partido) y desviación
`σ·sqrt(t)` con σ = 12 puntos por partido. La pantalla dice qué modelo usa.

**Ventaja previa** (`pregame_edge`, opcional en `predict_home_wp`/`game_wp_curve`): puntos de
ventaja esperados del local sobre 40 min, además de la de campo, sumados como
`m + edge · t/2400`. Hoy la pantalla la deja a 0; es el enganche natural con la predicción de
partido (idea A2) cuando exista.

**Momentos clave.** Ventana deslizante sobre la curva (≤ 2 min por defecto), recorte de los
segundos planos de los extremos y descarte de ventanas solapadas — el mismo esqueleto que
`queries.detect_runs` — pero ordenando por |ΔWP| en vez de por puntos. Solo cuentan ventanas en
las que cambia el marcador (la probabilidad del que va delante también sube sola con el reloj,
y "no pasó nada en dos minutos" no es una jugada de vídeo).

**WPA por quinteto.** La probabilidad se evalúa en cada escalón del marcador y en cada cambio
de quinteto (con el marcador vigente), y cada trozo se apunta al tramo que estaba en pista.
Lo que no cae en ningún tramo va a una fila "(sin tramo de quinteto)" en vez de perderse.

## 4. Limitaciones conocidas

- **El modelo solo sabe margen, reloj y campo**: no sabe quién tiene la posesión ni la calidad
  de cada equipo. Es una probabilidad "de liga media"; la de un Madrid-colista a 0-0 no es la
  de un partido cualquiera (para eso, `pregame_edge`).
- **Los tiros no tienen reloj** (§5 de la propuesta 01): el salto del marcador se ve en el
  siguiente evento tipado, así que la canasta real cae un poco antes del inicio indicado. Por
  eso el clip arranca 10 s antes.
- **Los estados de un mismo partido no son independientes**: el ajuste da buenas
  probabilidades, pero sus errores estándar no valdrían y no se enseñan.
- **Coste**: el primer cálculo de la temporada recorre la escalera de cada partido (~5 s con
  ~740 partidos en una base de datos sintética equivalente); queda en caché una hora. Se
  reutiliza `queries._score_steps` a propósito en lugar de una consulta masiva paralela, para no
  tener dos definiciones del marcador.
- La WPA por quinteto dice **cuándo** se ganó o perdió el partido con quién en pista, no quién
  jugó mejor: un quinteto que sale con +15 a falta de 3 minutos acumula poca WPA haga lo que
  haga.
- No verificado contra `data/baskonia.db` real (no disponible en el entorno donde se
  implementó): conviene revisar los coeficientes ajustados y el % de victorias locales con la
  temporada real.

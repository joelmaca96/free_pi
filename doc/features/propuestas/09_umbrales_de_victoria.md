# 09. Umbrales de victoria

**Estado:** IMPLEMENTADA v1 + v2, con umbrales por competición y enganche al dossier
(2026-08-31) · **Fecha de la propuesta:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

El cálculo vive en `app/analytics/win_thresholds.py` (paquete puro sobre `pandas`/`numpy`, sin
Streamlit ni SQLAlchemy — mismo criterio que `shot_quality.py`/`zone_matchup.py`), la consulta
nueva `queries.game_factor_rows` en `app/data/queries.py` (una fila por equipo y partido, con la
del rival del mismo partido ya cruzada por self-join — trae `competition_id`, la columna que hace
posible acotar por competición), el pintado en `app/components/win_thresholds.py` y las secciones
**Objetivos del partido** en `app/pages/proximo_rival.py` (paneles a y c de §2) y la comprobación
por partido en `app/pages/partidos_anteriores.py`, pestaña Resumen (panel b de §2).

Los dos puntos que quedaron como trabajo futuro ya están cerrados:

- **Umbrales por competición (§5).** `proximo_rival.py` ofrece un `st.radio` ("ACB y Euroliga
  juntas" / una opción por competición) cuando esa competición sola tiene partidos de sobra para
  sostener el barrido (`2×MIN_SIDE_GAMES`); por debajo de eso, ni se ofrece la opción — no hay
  "umbral de competición" con cuatro partidos. El aviso permanente
  (`win_thresholds.objectives_caveat_text`) dice el ámbito real en cada caso, nunca "ACB y Euroliga
  juntas" cuando se ha acotado. La comprobación por partido de `partidos_anteriores.py` hace lo
  mismo automáticamente: compara cada partido contra el umbral de SU competición si hay muestra, y
  cae al combinado con una nota si no la hay.
- **Enganche con el dossier de scouting (§6).** `scouting_ppt.generate_scouting_ppt` calcula las
  tarjetas de objetivos ya ajustadas al rival (`rival_adjusted_card`) una sola vez y las mete en
  `ctx["win_threshold_cards"]`; tanto el fallback por reglas como el prompt del LLM de "claves del
  partido" las usan como candidatas ("Objetivo del partido: al menos 34,0% de rebote ofensivo
  (ajustado al perfil de X)"), exactamente como pedía el documento: calcularlo una vez, usarlo en
  los dos sitios.

Se implementó tal como está descrita abajo, con matices:

- **Las tarjetas muestran un número propio ("al menos 32% de rebote ofensivo"), no la diferencia
  frente al rival**, aunque el barrido de umbrales (v1) y el modelo (v2) trabajan siempre sobre la
  BATALLA (la propia cifra menos la del rival, §1) — es justo lo que pide el propio documento al
  avisar de que el porcentaje propio a secas engaña más que la diferencia. El número que se enseña
  se reconstruye sumando (o restando) el umbral de batalla a lo que concede/produce un rival MEDIO
  de la liga; ver el docstring de `_display_threshold`.
- **El ajuste por rival (§4) sale algebraicamente de esa misma reconstrucción**, no de un cálculo
  aparte: el umbral de liga ya es `concesión_media_liga ± umbral_de_batalla`, así que sustituir la
  concesión media de la liga por la de un rival concreto (su ORB% concedido, su TOV% forzado...
  sobre SU temporada completa, nunca sobre el cara a cara) da el número ajustado sin recalcular el
  barrido — ver `rival_adjusted_card`.
- **v2 (regresión logística) se enseña en un desplegable aparte** ("pesos relativos de cada
  batalla"), no sustituye al panel de v1: el barrido de umbrales sigue siendo la versión que se
  explica sola con una curva, que es la que se enseña sin pedir permiso (§4).

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28 (1.474 filas equipo-partido).

## 1. El problema del entrenador

En la charla previa hacen falta **tres objetivos numéricos**, no veinte estadísticas. "Si les
bajamos de 10 rebotes ofensivos, ganamos". El entrenador ya trabaja así; lo que no tiene es de
dónde sacar los números, así que se los inventa por experiencia.

Esta propuesta los calcula. Y de paso ordena el resto de propuestas de la carpeta: si algo no
mueve la probabilidad de ganar, no merece pantalla.

**Línea base ya calculada** sobre los 737 partidos (los cuatro factores clásicos, expresados como
"ganar esa batalla al rival"):

| Batalla | Correlación con ganar | Victorias cuando se gana |
|---------|----------------------|--------------------------|
| eFG% (acierto efectivo) | **+0,66** | **80,4%** |
| Tiros libres (FT rate) | +0,19 | 59,5% |
| Rebote ofensivo (ORB%) | +0,23 | 58,8% |
| Pérdidas (TOV%) | +0,21 | 56,3% |

Ya es un mensaje de vestuario por sí solo: **el 80% de los partidos los gana quien tira mejor**, y
las otras tres batallas juntas valen bastante menos de lo que se suele decir. Y en el otro
sentido: el porcentaje propio sin comparar con el rival (`eFG%` a secas, r = +0,46) engaña bastante
más que la diferencia.

## 2. Qué se ve

**a) Panel de objetivos del partido**, en `proximo_rival`: tres o cuatro tarjetas con un número
grande cada una — "menos de 12 pérdidas", "más del 28% de rebote ofensivo", "que no pasen de 46%
de eFG" — y, debajo en pequeño, el porcentaje de victorias históricas cuando se cumple, con el
número de partidos en que se basa.

**b) Cómo se sostuvo el objetivo, partido a partido**, en `partidos_anteriores`: para el partido
que se está revisando, cuáles de los objetivos se cumplieron y cuáles no. Convierte el panel en un
ciclo cerrado (se fija, se juega, se revisa) en vez de un adorno de prepartido.

**c) Perfil del rival aplicado.** Los mismos objetivos, ajustados a la debilidad del rival: si el
rival es el peor de la liga en rebote defensivo, ese objetivo sube; si es el que menos pierde el
balón, el objetivo de robos baja porque no va a pasar.

## 3. Datos: de dónde sale

Todo de `game_advanced_stats` (una fila por equipo y partido, cobertura completa) más el marcador
de `games`:

- Cuatro factores ya calculados en la tabla: `efg_pct`, `tov_pct`, `orb_pct`, `ft_rate`.
- Contexto: `ortg`, `drtg`, `net_rating`, `pace`.
- Caja larga en la misma tabla (`stl`, `blk`, `oreb`, `dreb`, `pf`, `pf_drawn`), por si algún
  objetivo se quiere en unidades naturales ("10 rebotes ofensivos") en vez de en porcentaje —
  que es lo que un jugador entiende.
- La fila del rival en el mismo partido, con un `JOIN` de `game_advanced_stats` consigo misma:
  es lo que permite expresar cada factor como diferencia, que es la forma que correlaciona.

1.474 filas equipo-partido, sin nulos en los cuatro factores.

## 4. Cálculo

**v1 — descriptivo, y probablemente suficiente.** Para cada variable candidata, barrer umbrales y
quedarse con el porcentaje de victorias por encima y por debajo. Se elige el umbral que mejor
separa (máxima diferencia de tasa de victoria entre los dos lados) **siempre que ambos lados
tengan al menos 30 partidos**. Sin modelo, sin dependencias, y explicable: la pantalla puede
enseñar la curva entera.

**v2 — modelo.** Regresión logística de la victoria sobre los cuatro factores en diferencias, para
dar pesos relativos y una probabilidad estimada. Requiere `scikit-learn` o `statsmodels`, que
**no** están en `app/requirements.txt` — o implementar el descenso de gradiente a mano con numpy,
que para cuatro variables es media página de código y evita añadir 100 MB a la imagen de la
interfaz. Recomendada esta segunda vía si se hace.

**Ajuste por rival.** Aquí está el peligro y hay que ser explícito: contra un rival concreto hay
**2, 3 o como mucho 4 partidos** en la temporada. Sacar un umbral de ahí es superstición con
formato de tabla. La forma sólida es en dos pasos:

1. calcular el umbral general sobre toda la liga (muestra grande),
2. **desplazarlo** según el perfil del rival medido en su temporada completa (su ORB% concedido,
   su ritmo, su TOV% forzado), que sí tiene 30-40 partidos detrás.

El resultado se presenta como "objetivo ajustado al rival", nunca como "histórico contra este
rival".

## 5. Limitaciones conocidas

- **Correlación, no causa** — y aquí la tentación es máxima. "Ganar el rebote ofensivo" y "ganar
  el partido" comparten causas (ir por delante cambia cómo se juega). El panel debe fijar
  objetivos **de proceso** (cosas que el equipo controla) y no leerse como una receta.
- **eFG% se lo come todo.** Con r = +0,66, cualquier modelo va a decir "tira mejor", que no es
  accionable. Por eso el panel debe forzar la inclusión de al menos dos objetivos **no de
  acierto** (pérdidas, rebote, faltas), aunque el modelo los ordene por debajo.
- **Una temporada, 737 partidos**: sobra para umbrales de liga, no para umbrales por equipo. Un
  equipo tiene entre 34 y 89 partidos; contra un rival concreto, 2-4.
- Los umbrales se calculan sobre ACB + Euroliga juntas por defecto; conviene poder separarlas,
  porque el ritmo y el arbitraje difieren (ver [05_perfil_arbitral.md](05_perfil_arbitral.md)).
- No hay datos de la temporada anterior para validar fuera de muestra. Mientras eso no exista,
  presentar los umbrales como **descriptivos de esta temporada**, no como predicción.

## 6. Encaje en el código

- Cálculo en `app/analytics/win_thresholds.py` (mismo módulo nuevo que
  [02_calidad_de_tiro.md](02_calidad_de_tiro.md)), con pandas y numpy.
- Consultas: `game_advanced_stats` ya se lee en `queries.game_advanced_stats` (línea 345) y en
  `queries_assistant.team_style_row`; falta una agregación de temporada con el `JOIN` del rival.
- Interfaz: tarjetas con `st.metric` en `app/pages/proximo_rival.py` y una fila de comprobación en
  `app/pages/partidos_anteriores.py`.
- Es también la fuente natural de la diapositiva "claves del partido" del dossier
  ([03](03_dossier_scouting_rival.md)): calcularlo una vez, usarlo en los dos sitios.

## 7. Esfuerzo y orden

Bajo en la v1 (barrido de umbrales, sin dependencias), medio en la v2. El trabajo real no es el
cálculo: es **elegir bien qué se enseña** para que el panel no acabe diciendo "tirad mejor".

Va la novena porque su valor depende de presentarla con mucha disciplina; mal contada, es la
propuesta con más riesgo de que el cuerpo técnico saque conclusiones falsas.

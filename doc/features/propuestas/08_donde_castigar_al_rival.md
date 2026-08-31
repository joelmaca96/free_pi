# 08. Dónde castigar al rival

**Estado:** IMPLEMENTADA (2026-08-30) · **Fecha de la propuesta:** 2026-08-28 ·
**Índice:** [00_indice.md](00_indice.md)

El cálculo vive en `app/analytics/zone_matchup.py` (paquete puro sobre `pandas`, sin Streamlit ni
SQLAlchemy — reutiliza `shot_value`/`ZONE_MERGES`/`POOLED_COMPETITION_ID` de
`app/analytics/shot_quality.py` en vez de duplicarlos), las consultas nuevas en `app/data/queries.py`
(`team_zone_profile(..., side=)`, `team_zone_profile_by_competition`, `league_zone_baseline_counts`,
`team_games_played`, junto a `court_zones`), el pintado en `app/components/zone_matchup.py`
(reutilizando `components/court.py::zone_heatmap(mode="vs_league")`, sin duplicar el mapa) y la
sección **Dónde castigar al rival** en `app/pages/proximo_rival.py`, encima del mapa de tiros.

Se implementó tal como está descrita abajo, con dos matices:

- **Las dos temporadas de scouting pueden diferir.** Si el rival ya jugó en la temporada
  seleccionada pero el Baskonia todavía no (o al revés), cada equipo se compara contra la línea
  base de liga de SU PROPIA temporada de scouting (mismo `queries.team_scouting_season` que ya usa
  el resto de la pantalla) en vez de forzar una temporada común.
- **Regularización un poco más agresiva que en la propuesta 02** (`SHRINK_K=200` y
  `MIN_SHOTS=100` frente a 100/50 en `shot_quality`): la muestra de EQUIPO por zona es más pequeña
  que la de la liga entera, así que hace falta encoger más para no confundir ruido con una
  debilidad real (§4).

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

El mapa de tiros del rival que ya enseña `proximo_rival` responde a "desde dónde tiran ellos". La
pregunta que se prepara en la pizarra es la contraria: **"¿desde dónde nos van a dejar tirar a
nosotros, y coincide eso con lo que nosotros metemos?"**.

Son dos perfiles cruzados —lo que el rival concede por zona y lo que nosotros producimos por
zona— y el resultado es una lista corta y accionable: las tres zonas donde su defensa está por
debajo de la liga y nosotros por encima. Eso es un plan de ataque, no una estadística.

Ejemplo real de esta temporada, calculado con los datos que ya están cargados — lo que concede el
Real Madrid respecto a la media de la liga:

| Zona | Tiros concedidos | Acierto concedido | Media liga | Diferencia |
|------|------------------|-------------------|------------|------------|
| Ala 2 der. | 195 | 46,7% | 39,2% | **+7,5** |
| Fondo izq. | 165 | 41,8% | 37,6% | +4,2 |
| Ala 3 der. | 633 | 38,2% | 36,0% | +2,2 |

## 2. Qué se ve

**a) Pista partida en dos.** A la izquierda, lo que concede el rival por zona (diferencia contra
la media de la liga, escala divergente). A la derecha, lo que producimos nosotros por zona con la
misma escala. Un entrenador ve la coincidencia sin leer un número.

**b) Las tres zonas a atacar.** Debajo, en texto plano y ordenadas por **puntos por partido que
se pueden ganar** (no por diferencia de porcentaje): zona, cuánto concede el rival de más, cuánto
metemos nosotros de más, y cuántos tiros por partido se lanzan ahí de media. Una zona donde el
rival concede +8% pero solo se tiran 3 tiros por partido vale menos que otra con +3% y 15 tiros;
ordenar por porcentaje induce al error.

**c) Y al revés: dónde nos van a castigar.** El mismo cruce con los papeles invertidos, que es la
mitad defensiva del plan.

**d) Reutilización directa en el dossier** ([03](03_dossier_scouting_rival.md)): esta pantalla es
literalmente dos de sus diapositivas.

## 3. Datos: de dónde sale

**La fuente correcta es `game_zone_stats`** (16.563 filas, `game_id` + `team_id` + `zone_id` +
`fg_pct` + `volume`), no la tabla `shots` directamente. Motivo importante: `shots` solo trae
`player_id`, y para saber de qué equipo era el tirador hay que pasar por `players.team_id`, que es
el equipo **actual** del jugador — un traspaso a mitad de temporada atribuiría sus tiros al equipo
equivocado en partidos anteriores. `game_zone_stats` ya trae el equipo resuelto partido a partido.

Se ha verificado que las dos vías dan el mismo resultado para los jugadores sin traspaso, así que
`game_zone_stats` no pierde nada.

- **Perfil ofensivo** de un equipo: sus filas de `game_zone_stats` en sus partidos.
- **Perfil defensivo**: las filas del **otro** equipo en esos mismos partidos.
- **Referencia de liga**: todas las filas de la temporada y competición, ponderadas por `volume`.

Detalle de formato que hay que respetar: `game_zone_stats.fg_pct` está en **puntos porcentuales**
(55,88), mientras que promediar `shots.made` da una fracción (0,5588). Mezclar las dos escalas es
el error tonto que arruina la pantalla.

## 4. Cálculo

- **Agregado ponderado por volumen**, nunca media de medias:
  `acierto = Σ(fg_pct · volume) / Σ(volume)` por equipo y zona.
- **Diferencia contra la liga**, calculada por competición separada (ACB y Euroliga no tienen el
  mismo nivel de tiro; ver [02_calidad_de_tiro.md](02_calidad_de_tiro.md)).
- **Valor de la zona en puntos**, que es lo que ordena la lista:
  `(acierto_rival_concedido − acierto_liga) × valor_del_tiro × tiros_por_partido_en_esa_zona`.
  Así el ranking habla en puntos por partido, que es la unidad en la que decide un entrenador.
- **Regularización imprescindible.** Las muestras por equipo y zona son pequeñas: en el ejemplo de
  §1, entre 165 y 633 tiros para las zonas relevantes, y bastante menos en las marginales. A 200
  tiros, el error típico del porcentaje ronda ±3,5 puntos — o sea, del orden de la señal que se
  busca. Encoger hacia la media de la liga con `n / (n + k)` (k del orden de 200) y **ocultar las
  zonas por debajo de 100 tiros**, que es exactamente el filtro que se usó al calcular la tabla
  del §1.

## 5. Limitaciones conocidas

- **Concedes lo que defiendes.** Un equipo que concede pocos tiros en pintura con buen porcentaje
  puede estar defendiendo bien (cede pocos) o mal (los que cede, entran). Por eso hay que enseñar
  **siempre volumen y acierto juntos**, no solo el porcentaje: son las dos mitades de la misma
  frase.
- **Sin defensor ni tipo de acción**, no se puede decir *cómo* atacar esa zona (bloqueo directo,
  puerta atrás, poste), solo *dónde*. La pizarra la sigue poniendo el entrenador.
- **Sin tiempo en los tiros**, no hay versión "dónde conceden en los últimos cinco minutos".
- Las zonas 14-17 vienen del reteselado (`tools/retile_court_zones.py`) y son asimétricas
  izquierda/derecha por diseño: eso es útil aquí (un equipo puede defender peor un lado), pero
  obliga a no fusionar lados al agregar.
- Una temporada: la diferencia entre "esta defensa concede la esquina" y "esta defensa concedió la
  esquina en 20 partidos" es real y conviene que el texto lo refleje.

## 6. Encaje en el código

- Consultas en `app/data/queries.py`, junto a `court_zones` (línea 468) y la familia de `shots`:
  `team_zone_profile(engine, team_id, season_id, side)` con `side` = ofensivo o defensivo, y
  `league_zone_baseline(engine, season_id, competition_id)`.
- Interfaz: `app/components/court.py::zone_heatmap` ya pinta una pista por zonas — aquí basta con
  pasarle la diferencia contra la liga en vez del porcentaje bruto, y pintar dos pistas juntas.
  Es la misma capa que necesita [02_calidad_de_tiro.md](02_calidad_de_tiro.md): conviene hacerla
  una sola vez.
- Bloque nuevo en `app/pages/proximo_rival.py`, encima del mapa de tiros actual (línea 431).

## 7. Esfuerzo y orden

**El más bajo de toda la lista.** Es una agregación sobre una tabla pequeña (16.563 filas) y un
componente de pista que ya existe. Puede estar en un día de trabajo, sin dependencias ni cambios
de esquema.

Está en el octavo puesto por impacto relativo, no por coste: si hace falta una victoria rápida
para enseñar al cuerpo técnico, **esta es la que antes se entrega**.

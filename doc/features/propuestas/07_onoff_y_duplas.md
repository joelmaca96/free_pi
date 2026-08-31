# 07. On/Off y duplas

**Estado:** IMPLEMENTADA (2026-08-31) · **Fecha de la propuesta:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

Pantalla nueva `app/pages/quintetos.py`, con las tres vistas de §2: On/Off por
jugador (a), matriz de parejas más lista de mejores/peores tríos (b) y
con-y-sin para dos jugadores concretos (c) — con selector de equipo, para que
sirva igual para el Baskonia y para el próximo rival (§2d). El cálculo amplía
`app/data/queries_assistant.py` con `player_on_off` y `player_combos`
(tamaño 2 y 3), reutilizando `_lineup_rows` y el recorte de `clutch_lineups`
tal como proponía §6; las herramientas equivalentes del asistente viven en
`app/assistant/tools/lineups.py`. Mínimos de muestra tal como pedía §4 (200
minutos para el On/Off individual, 100 para parejas y tríos) y encogido hacia
la media con `n/(n+k)` para ordenar, igual criterio que
[02_calidad_de_tiro.md](02_calidad_de_tiro.md). Tests en
`tests/app/assistant/test_tools.py`.

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

Las páginas de quintetos de la interfaz (`estado_equipo`, `proximo_rival`) enseñan hoy *quintetos
más utilizados* con minutos y diferencia. Es el punto de partida, pero tiene un problema de fondo:
**el quinteto de cinco no tiene muestra**. En toda la temporada el Baskonia usó **724
combinaciones distintas de cinco** en 3.111 minutos, y de ellas:

| Umbral | Combinaciones que lo alcanzan |
|--------|-------------------------------|
| ≥ 5 min | 164 |
| ≥ 10 min | 75 |
| ≥ 20 min | 23 |
| ≥ 50 min | **2** |
| ≥ 100 min | **0** |

Ordenar por diferencia un listado donde el mejor dato tiene 50 minutos es ordenar ruido. Un
entrenador que tome una decisión con eso, la toma con nada.

Donde **sí** hay muestra es un nivel por debajo:

| Unidad | Con muestra suficiente |
|--------|------------------------|
| Jugador individual | 13 de 21 con ≥500 minutos |
| Parejas | 89 de 180 con ≥100 minutos |
| Tríos | 92 de 704 con ≥100 minutos |

Esa es la propuesta: **bajar del quinteto a jugador, pareja y trío**, que es donde los números
empiezan a significar algo.

## 2. Qué se ve

**a) On/Off por jugador.** Tabla de la plantilla con: minutos en pista, diferencia por 40 con él,
diferencia por 40 sin él, y el **On/Off** (la resta). Ordenable, con la barra de error o al menos
el número de minutos siempre visible al lado.

**b) Duplas y tríos.** Matriz de parejas (jugadores en filas y columnas, color = diferencia por 40
juntos, celda vacía si no llegan al mínimo de minutos) y una lista de los mejores y peores tríos.
La matriz es la visualización correcta: se leen de un vistazo las combinaciones que funcionan y
las que no, que es la pregunta real ("¿puedo juntar a estos dos interiores?").

**c) Con y sin.** Para dos jugadores concretos, las cuatro situaciones —juntos, solo A, solo B,
ninguno—. **Esto ya está implementado**: `queries_assistant.player_pair_impact` (línea 701)
devuelve exactamente esas cuatro filas con minutos, diferencia y diferencia por 40. Hoy solo es
accesible desde el chat; falta llevarlo a una pantalla.

**d) Del rival, lo mismo.** `lineup_stints` guarda `team_id` de los dos equipos en cada partido,
así que todo lo anterior se calcula igual para el próximo rival: qué parejas suyas funcionan y
cuáles hay que provocar que coincidan.

## 3. Datos y qué existe ya

| Fuente | Filas | Nota |
|--------|-------|------|
| `lineups` + `lineup_players` | 27.900 | Quintetos agregados por partido, `team_id` no nulo en ninguno |
| `lineup_stints` + `lineup_stint_players` | 34.496 / 172.480 | Tramos sin agregar: permiten recortar por tiempo y marcador |
| `lineup_team` (vista) | — | Resuelve el equipo del quinteto, con `is_inferred` para los deducidos |

**Ya implementado en `app/data/queries_assistant.py`** (no rehacer):

- `team_lineups` (línea 662) y `_aggregate_lineups` — agregación por combinación exacta de cinco.
- `player_pair_impact` (línea 701) — las cuatro situaciones de dos jugadores.
- `clutch_lineups` (línea 741) — quintetos filtrados por ventana de tiempo y margen, con el
  recorte correcto del solape. Es el patrón a copiar para cualquier consulta por ventana.
- Herramientas del asistente equivalentes en `app/assistant/tools/lineups.py`.

**Lo que falta:** el On/Off de un jugador suelto, los tríos, la matriz de parejas, y —sobre
todo— que nada de esto tenga pantalla propia.

## 4. Cálculo

- **On/Off** = (diferencia por 40 en los tramos con el jugador) − (diferencia por 40 en los
  tramos sin él, del mismo equipo y temporada). Sobre `lineup_stints`, sumando `points_for` y
  `points_against`, no sobre `lineups`, para poder filtrar además por ventana si se quiere.
- **Parejas y tríos**: recorrer los tramos y acumular en cada subconjunto. Con 34.496 tramos y 5
  jugadores por tramo son 10 parejas y 10 tríos por tramo — perfectamente asumible en pandas, no
  hace falta SQL recursivo.
- **Mínimos de muestra**, no negociables: 100 minutos para parejas y tríos, 200 para el On/Off
  individual. Por debajo, la celda va vacía o marcada como insuficiente. Con los números de §1, un
  umbral bajo llenaría la pantalla de basura convincente.
- **Encoger hacia la media** con `n / (n + k)` para ordenar rankings (igual que en
  [02_calidad_de_tiro.md](02_calidad_de_tiro.md)), en vez de ordenar por el valor bruto.

## 5. Limitaciones conocidas

- **No hay posesiones por tramo.** `lineup_stints` guarda puntos, no posesiones, así que un
  *net rating* por 100 posesiones a nivel de quinteto hay que **estimarlo** (ritmo del partido ×
  minutos del tramo). La estimación es razonable de media y mala en tramos cortos; por eso la
  unidad recomendada aquí es **diferencia por 40 minutos**, que no requiere inventar nada. Si se
  quiere el rating por posesiones, hay que decir que es estimado.
- **Los tiros no tienen tiempo**, así que no se puede calcular el %TC de un quinteto ni su xPPS.
  El análisis de quintetos se queda en puntos, no llega a eficiencia de tiro. Es la carencia que
  más limita esta propuesta.
- **On/Off no es una medida de calidad del jugador**, es una medida de contexto: quien juega
  siempre con los buenos sale beneficiado y el suplente que comparte pista con suplentes, hundido.
  Presentarlo junto a los minutos y a con quién comparte pista, nunca solo.
- Los quintetos se **reconstruyen** del play-by-play (`ingest/common/lineups.py`); la vista
  `lineup_team` marca con `is_inferred` los que dedujo el equipo. Conviene mostrar ese matiz.

## 6. Encaje en el código

- Ampliar `app/data/queries_assistant.py` con `player_on_off(engine, team_id, season_id)` y
  `player_combos(engine, team_id, season_id, size)` reutilizando `_lineup_rows` y el recorte de
  `clutch_lineups`.
- **Pantalla nueva** —es la propuesta que más pide página propia—: `app/pages/quintetos.py`, con
  las tres vistas (On/Off, matriz, con-y-sin) y selector de equipo, para que sirva igual para el
  Baskonia y para el rival.
- La matriz, con Altair (`mark_rect` + escala divergente centrada en 0).
- El interruptor `lineup_stints` de `app/assistant/capabilities.py` ya condiciona estas
  funciones; respetarlo también en la página.

## 7. Esfuerzo y orden

Bajo-medio, y con ventaja de salida: media función ya está escrita y probada
(`tests/app/assistant/test_tools.py` cubre las herramientas de quintetos).

Va la séptima —y no antes— porque **el techo lo pone la muestra**, no el código: aunque se haga
perfecta, con una temporada las conclusiones serán tendencias, no certezas. Es más valiosa cuando
haya una segunda temporada cargada.

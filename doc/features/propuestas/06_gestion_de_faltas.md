# 06. Gestión de faltas (foul trouble)

**Estado:** propuesta, sin implementar · **Fecha:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28.

## 1. El problema del entrenador

La decisión de qué hacer con un jugador con dos faltas en el primer cuarto es de las que más
partidos deciden y de las que menos apoyo estadístico tienen. El boxscore dice "4 faltas": no dice
**cuándo**, y el cuándo es todo — cuatro faltas repartidas no cuestan nada, dos faltas en el
minuto 6 cuestan quince minutos de banquillo.

Con `play_events` el cuándo está, al segundo. En la temporada 2025-2026: **819 casos** de un
jugador con dos faltas dentro del primer cuarto y **403 casos** de tres faltas antes del descanso.
Eso es material suficiente para responder con datos a tres preguntas que hoy se responden por
intuición:

1. ¿Quién de los nuestros se carga pronto, y cuánto le cuesta en minutos?
2. ¿A quién del rival **no** hay que ponerle la mano?
3. ¿Sentar al jugador con dos faltas nos sale bien o mal?

## 2. Qué se ve

**a) Perfil de faltas de la plantilla** (en `estado_equipo` o en la ficha de jugador):

- Faltas por 40 minutos y **minuto medio de la 2.ª y 3.ª falta** de cada jugador.
- Cuántas veces se cargó pronto y cuántos minutos perdió en esos partidos frente a su media.
- Reparto por cuarto: quién acumula en el primero y quién en el último.

**b) A quién no ponerle la mano (rival), en `proximo_rival`:**

- Ranking de sus jugadores por **faltas provocadas por 40 minutos** (`pf_drawn`). Ejemplo real de
  esta temporada, entre los jugadores con 500+ minutos: Retin Obasohan 10,4 · Nadir Hifi 10,0 ·
  Luka Bozic 9,6 · Artem Pustovyi 9,3 · Ricky Rubio 9,2.
- Su relación con los tiros libres (`fta`, `ft_rate`): quién convierte esas faltas en puntos.
- Y al revés: cuáles de sus jugadores están un aviso de sentarse — los que más se cargan pronto.

**c) Línea temporal de faltas del partido**, integrada en la vista de rotaciones
([01](01_rotaciones_y_parciales.md)): marcas sobre el timeline con el momento de cada falta y de
cada jugador, y el bonus de equipo por cuarto. En un vistazo se ve "entramos en bonus en el minuto
4 del tercer cuarto" — que es una de las causas de parcial más habituales y hoy no se ve en
ningún sitio.

**d) Coste real del banquillo por faltas.** Cruzando con `lineup_stints`: qué diferencia hizo el
equipo en los minutos en que ese jugador estuvo sentado por faltas, comparado con su diferencia
habitual. Es la respuesta empírica a "¿compensa sentarlo?" — con las reservas del §5.

## 3. Datos: de dónde sale

| Fuente | Filas | Qué aporta |
|--------|-------|------------|
| `play_events` (`foul_personal`) | 29.933 | Momento exacto (`seconds`, `quarter`, `game_clock`) y autor |
| `play_events` (`foul_drawn`) | 31.612 | Quién la provoca |
| `player_game_stats.pf` / `pf_drawn` | Completo | Totales por partido, para tasas por 40 minutos |
| `game_team_quarter_stats.fouls_for` / `fouls_against` | 5.896 | Faltas de equipo por cuarto (bonus) |
| `lineup_stints` | 34.496 | Qué pasó mientras el jugador estaba sentado |

**Los 29.933 `foul_personal` y los 31.612 `foul_drawn` tienen `player_id`** — cero nulos, así que
no hace falta ningún criterio de imputación. Hay 13.615 pares jugador-partido con al menos una
falta, muestra de sobra para los agregados de plantilla.

## 4. Cálculo

- **Minuto de la n-ésima falta**: ordenar por `seconds` los `foul_personal` de cada
  (`game_id`, `player_id`) y quedarse con el n-ésimo. Directo, sin ambigüedad.
- **Carga temprana**: definir con dos parámetros de interfaz (por defecto: 2 faltas antes del
  minuto 10, o 3 antes del 20), no con constantes en el código.
- **Minutos perdidos**: minutos del jugador en ese partido frente a su media de la temporada, y
  además el hueco real en `lineup_stints` (desde la falta hasta su siguiente tramo en pista), que
  es la medida honesta.
- **Faltas provocadas por 40** = `40 · Σ pf_drawn / Σ minutes`, con mínimo de minutos (500 en el
  ejemplo de §2) para que no se cuele un jugador con 40 minutos jugados.
- **Bonus por cuarto**: acumulado de `foul_personal` por equipo y cuarto sobre `seconds`; el
  minuto en que se cruza la 4.ª falta de equipo. `game_team_quarter_stats` ya trae el total del
  cuarto, pero el **minuto** solo sale de los eventos.

## 5. Limitaciones conocidas

- **No se distingue el tipo de falta con fiabilidad.** `event_detail` solo guarda el código crudo
  de la fuente para `foul_personal` en ACB, con seis subtipos sin semántica distinguible (ver
  `schema.sql` y `doc/features/ingestor/02_plan_stats_completas.md`). No hay forma segura de
  separar falta en tiro, antideportiva o técnica. Cualquier análisis debe tratar todas las faltas
  como una sola cosa.
- **No hay defensor ni acción**, así que "a quién no ponerle la mano" es una tasa agregada, no un
  emparejamiento. No se puede decir *quién* de los nuestros le hace las faltas.
- **"¿Compensa sentarlo?" es una pregunta causal con datos observacionales.** Los minutos sin el
  jugador cargado no son comparables sin más con sus minutos normales (rival distinto, momento
  distinto, marcador distinto). Se puede enseñar la diferencia observada, pero **hay que
  etiquetarla como descriptiva**; venderla como recomendación sería el peor error de toda esta
  carpeta.
- `game_team_quarter_stats.fouls_for` es `NULL` (no 0) en partidos sin play-by-play; el
  `loader` es explícito en eso. Filtrar, no rellenar con ceros.

## 6. Encaje en el código

- Consultas en `app/data/queries_assistant.py`, junto a `game_play_events` (línea 323), que ya
  filtra `play_events` por tipo: `foul_timeline(engine, game_id)`,
  `foul_profile(engine, team_id, season_id)`, `foul_drawing_leaders(engine, team_id, season_id)`.
- Interfaz: bloque en `app/pages/proximo_rival.py`, ficha ampliada en
  `app/components/player_dialog.py`, y capa de marcas sobre el timeline de
  [01](01_rotaciones_y_parciales.md).
- El interruptor `play_events` de `app/assistant/capabilities.py` ya existe: úsese para apagar el
  bloque entero si la base de datos servida no los tiene.

## 7. Esfuerzo y orden

Bajo. Es casi todo SQL sobre una tabla bien indexada (`idx_play_events_type`), con una capa fina
de interfaz. La parte (d) es la única cara, y la que más cuidado requiere al presentarla.

Va la sexta porque es barata y porque su parte (b) —a quién no ponerle la mano— entra sola en el
dossier de prepartido del documento [03](03_dossier_scouting_rival.md).

# 13. Patrón de rotación del rival (y ventanas de ataque)

**Estado:** IMPLEMENTADA (2026-09-27) · **Índice:** [00_indice.md](00_indice.md)

Código: `app/analytics/rotation_patterns.py` (cálculo puro), `queries.team_stint_rows`,
`app/components/rotation_pattern.py` (sección en "Próximo rival") y la herramienta
`team_rotation_pattern` del asistente (`app/assistant/tools/rotations.py`). Tests:
`tests/app/test_rotation_patterns.py`, `tests/app/assistant/test_rotation_tools.py`.

## 1. El problema del entrenador

La vista de rotaciones de "Partidos anteriores" ([01](01_rotaciones_y_parciales.md)) explica
**un** partido. Preparando el siguiente, lo que se necesita es la **costumbre** del rival:

- ¿Con qué quinteto sale?
- ¿Cuándo sienta a su mejor jugador, y qué le pasa al equipo entonces?
- ¿Quién cierra los partidos apretados?
- ¿En qué minutos del partido se le cae el marcador?

Hoy eso lo saca un asistente viendo cinco vídeos. Los tramos (`lineup_stints`) ya lo tienen.

## 2. Qué se ve (en "Próximo rival", tras "Quintetos más utilizados")

1. **Resumen en frases**, listo para la pizarra: quinteto inicial más probable (en X de N
   partidos); para sus tres jugadores de más minutos, en qué minutos descansa habitualmente y
   cuánto hace el equipo sin él (On/Off, solo si hay muestra); quién cierra los finales
   apretados; su peor y mejor tramo del partido.
2. **Mapa de minutos**: jugador × minuto (1-40), color = en qué proporción de partidos estaba en
   pista. Las franjas claras dentro de una fila oscura son sus descansos.
3. **Quintetos iniciales** y **quién cierra** (proporción de los últimos 5 minutos en pista en
   partidos con ±8 o menos a falta de 5).
4. **Diferencia por tramo** (bloques de 4 minutos, +/- por 40).
5. Selector **toda la temporada / últimos 10 / últimos 5**: la rotación de octubre no es la de
   marzo.

## 3. Cálculo

- Proporción en pista por minuto = segundos en pista en ese minuto, sumados en todos los partidos,
  entre 60 × partidos. Un partido en que no jugó cuenta como 0 (la baja habitual también es
  información). Prórrogas fuera.
- Descanso habitual = tramo de ≥2 minutos seguidos con proporción < 0,35, **dentro** de la franja
  de juego del jugador (un suplente no "descansa" antes de entrar).
- Margen a falta de 5 minutos: interpolado dentro del tramo que cubre ese instante.
- Diferencia por bloque: los puntos de cada tramo se reparten por tiempo entre los bloques que
  toca (aproximación: no hay canasta a canasta en `lineup_stints`).

## 4. Limitaciones

- Minutos de reloj, no posesiones; y reparto proporcional de puntos dentro de cada tramo.
- Con 5 partidos, las proporciones son de 0,2 en 0,2: el selector "últimos 5" sirve para ver
  cambios de rol, no para medir tramos.
- El On/Off que acompaña a cada descanso es de temporada entera aunque el mapa sea de los últimos
  N partidos.

## 5. Siguiente paso natural

**Cruzar las dos propuestas**: "cuando el rival sienta a X (minutos 8-12), estos son nuestros
mejores quintetos disponibles para ese tramo" — ventanas de la 13 × constructor de la 12. Es lo
que convierte el scouting en un plan de rotación propio (ver [14](14_hoja_de_ruta.md)).

# Propuestas de nuevas funciones para el cuerpo técnico

Índice de las ideas propuestas el **2026-08-28**. Cada una tiene su propio documento, en el
orden en que se presentaron (impacto/esfuerzo descendente). **Son propuestas, no estado**: nada
de lo que hay aquí está implementado. El estado real del proyecto vive en
`doc/features/ingestor/01_estado.md` y en el código.

## El criterio

La base de datos no es "el Baskonia": son **737 partidos completos de la temporada 2025-2026**
(325 ACB, 402 Euroliga, 7 Copa del Rey, 3 Supercopa; 2025-09-27 → 2026-06-20), con 968
jugadores, 95.263 tiros con coordenadas, 172.762 eventos de play-by-play con reloj y marcador,
34.496 tramos de quinteto y la terna arbitral de 736 partidos.

Lo diferencial **no es tener más estadísticas**: las webs de estadística ya publican totales y
medias. Lo que ellas no tienen —y esta base de datos sí, a la vez— es **tiempo, posición y quién
estaba en pista**. Eso es lo que permite pasar de *qué pasó* a *por qué pasó*, que es la única
capa que un entrenador puede llevar al entrenamiento del día siguiente.

Segundo criterio, igual de importante: **el entrenador no vive en un dashboard, vive en la
reunión del día antes y en el vídeo**. Una función que no termina en algo proyectable o
accionable pesa menos que una peor pero exportable.

## Las propuestas

| # | Documento | En una línea | Datos que necesita |
|---|-----------|--------------|--------------------|
| 01 | [Rotaciones y parciales explicados](01_rotaciones_y_parciales.md) | Timeline de rotaciones con el margen detrás; clic en un parcial → qué quinteto había y qué pasó | Completos |
| 02 | [Calidad de tiro (xPPS)](02_calidad_de_tiro.md) | Separar la decisión (qué tiro se genera) del acierto (si entra), en ataque y en defensa | Completos |
| 03 | [Dossier de scouting del rival](03_dossier_scouting_rival.md) | Un botón → `.pptx` de prepartido con perfil, jugadores clave y claves del partido | Completos |
| 04 | [Fatiga y calendario ACB+Euroliga](04_fatiga_y_calendario.md) | Descanso real, semanas dobles y carga de minutos, nuestra y del rival | Completos |
| 05 | [Perfil arbitral](05_perfil_arbitral.md) | Qué pita cada árbitro: faltas, tiros libres, sesgo local, efecto sobre jugadores concretos | Completos |
| 06 | [Gestión de faltas](06_gestion_de_faltas.md) | Minuto exacto de cada falta: quién se carga pronto, a quién no ponerle mano | Completos |
| 07 | [On/Off y duplas](07_onoff_y_duplas.md) | Rendimiento del equipo con y sin cada jugador, y por parejas/tríos | Completos (parte ya existe en el asistente) |
| 08 | [Dónde castigar al rival](08_donde_castigar_al_rival.md) | Cruce de lo que el rival concede por zona con lo que nosotros metemos | Completos |
| 09 | [Umbrales de victoria](09_umbrales_de_victoria.md) | Qué números hay que alcanzar para ganar a ESE rival, no en general | Completos |
| 10 | [Señales semanales](10_senales_semanales.md) | Solo lo que ha cambiado de verdad, con test estadístico, no ruido de dos partidos | Completos |
| 11 | [Similitud de jugadores](11_similitud_de_jugadores.md) | Buscar entre los 968 jugadores quién se parece a X (fichajes y preparación del rival) | Completos |

## Descartada de momento

**Exportar lista de clips para vídeo.** Cada evento de `play_events` tiene `quarter` +
`game_clock`, así que generar un CSV/XML de timestamps a partir de cualquier filtro ("todas las
pérdidas del rival en los últimos 5 minutos") es técnicamente trivial y sería, probablemente, la
función más usada a diario. **Aparcada porque hoy no se puede enganchar con el sistema de vídeo
del club** — sin conocer el formato que traga su editor, exportar es adivinar. Queda escrita
aquí para no perderla: el día que se sepa el formato destino, es de las más baratas de la lista.

## Lo que falta en la ingesta para el siguiente escalón

Vale para toda la lista, conviene tenerlo presente antes de planificar. No hay en la base de
datos:

- **Tipo de jugada** (bloqueo directo, poste, transición, salida de tiempo muerto). Es la
  ausencia que más limita: sin ella no hay eficiencia en pick&roll ni análisis táctico fino.
- **Defensor / matchup**. Sin él, "defensa" solo se puede medir a nivel de equipo o de quinteto,
  nunca de emparejamiento individual.
- **Reloj de posesión** y **marca de tiempo en los tiros**: `shots` guarda `game_id`,
  `player_id`, `zone_id`, `pos_x`, `pos_y`, `made`, `located` — y nada de tiempo. Por eso ningún
  análisis puede cruzar *dónde* se tira con *cuándo* se tira (ver §5 del documento 02).
- **Posesiones por tramo**: `lineup_stints` guarda puntos, no posesiones, así que los ratings
  por 100 posesiones a nivel de quinteto hay que estimarlos (ver §5 del documento 07).

Todo lo propuesto en esta carpeta es factible **sin** ninguna de esas cuatro cosas. Si algún día
entran (o entra tracking), se abre otro nivel, sobre todo para 01, 02 y 08.

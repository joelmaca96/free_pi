# Propuestas de nuevas funciones para el cuerpo técnico

Índice de las ideas propuestas el **2026-08-28** (01-11), el **2026-09-27** (12-14) y el **2026-09-28** (15-18). Cada una tiene su propio documento, en el
orden en que se presentaron (impacto/esfuerzo descendente). **Son propuestas, no estado**, salvo
donde el propio documento diga otra cosa (la 01 ya está implementada). El estado real del
proyecto vive en `doc/features/ingestor/01_estado.md` y en el código.

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
| 01 ✅ | [Rotaciones y parciales explicados](01_rotaciones_y_parciales.md) | Timeline de rotaciones con el margen detrás; clic en un parcial → qué quinteto había y qué pasó | Completos |
| 02 ✅ | [Calidad de tiro (xPPS)](02_calidad_de_tiro.md) | Separar la decisión (qué tiro se genera) del acierto (si entra), en ataque y en defensa | Completos |
| 03 ✅ | [Dossier de scouting del rival](03_dossier_scouting_rival.md) | Un botón → `.pptx` de prepartido con perfil, jugadores clave y claves del partido | Completos |
| 04 | [Fatiga y calendario ACB+Euroliga](04_fatiga_y_calendario.md) | Descanso real, semanas dobles y carga de minutos, nuestra y del rival | Completos |
| 05 ✅ | [Perfil arbitral](05_perfil_arbitral.md) | Qué pita cada árbitro: faltas, tiros libres, sesgo local, efecto sobre jugadores concretos | Completos |
| 06 | [Gestión de faltas](06_gestion_de_faltas.md) | Minuto exacto de cada falta: quién se carga pronto, a quién no ponerle mano | Completos |
| 07 ✅ | [On/Off y duplas](07_onoff_y_duplas.md) | Rendimiento del equipo con y sin cada jugador, y por parejas/tríos | Completos |
| 08 ✅ | [Dónde castigar al rival](08_donde_castigar_al_rival.md) | Cruce de lo que el rival concede por zona con lo que nosotros metemos | Completos |
| 09 ✅ | [Umbrales de victoria](09_umbrales_de_victoria.md) | Qué números hay que alcanzar para ganar a ESE rival, no en general | Completos |
| 10 ✅ | [Señales semanales](10_senales_semanales.md) | Solo lo que ha cambiado de verdad, con test estadístico, no ruido de dos partidos | Completos |
| 11 ✅ | [Similitud de jugadores](11_similitud_de_jugadores.md) | Buscar entre los 968 jugadores quién se parece a X (fichajes y preparación del rival) | Completos |
| 12 ✅ | [Impacto ajustado y constructor de quintetos](12_impacto_ajustado_y_constructor.md) | RAPM (+/- descontando compañeros y rivales) y los mejores quintetos con los disponibles | Completos |
| 13 ✅ | [Patrón de rotación del rival](13_patron_de_rotacion.md) | Quién sale, cuándo descansan sus principales, quién cierra y en qué minutos sufre | Completos |
| 14 | [Hoja de ruta](14_hoja_de_ruta.md) | Siguientes funciones y mejoras; sus A1-A4 y A6 ya están implementadas (15-18 y 12 §6) | — |
| 15 ✅ | [Plan de rotación contra el rival](15_plan_de_rotacion.md) | Cuando el rival sienta a X o flojea, nuestros mejores quintetos contra SU quinteto habitual | Completos |
| 16 ✅ | [Predicción del partido](16_prediccion_del_partido.md) | Margen esperado y % de victoria, desglosados en nivel, campo y descanso, con prueba hacia atrás | Completos |
| 17 ✅ | [Planificador de minutos](17_planificador_de_minutos.md) | Reparto de los 200 minutos que maximiza la proyección con topes de carga y cobertura de posición | Completos |
| 18 ✅ | [Momentos clave del partido](18_momentos_clave.md) | Probabilidad de victoria en directo: las jugadas que decidieron el partido y lista de clips | Completos |

## Descartada de momento

**Exportar lista de clips para vídeo.** Cada evento de `play_events` tiene `quarter` +
`game_clock`, así que generar un CSV/XML de timestamps a partir de cualquier filtro ("todas las
pérdidas del rival en los últimos 5 minutos") es técnicamente trivial y sería, probablemente, la
función más usada a diario. **Aparcada porque hoy no se puede enganchar con el sistema de vídeo
del club** — sin conocer el formato que traga su editor, exportar es adivinar. Queda escrita
aquí para no perderla: el día que se sepa el formato destino, es de las más baratas de la lista.

## Hallazgos de datos al preparar estas propuestas

Dos cosas que aparecieron al verificar las cifras y que conviene arreglar al margen de lo que se
implemente:

- **El Barça estaba duplicado — arreglado (2026-08-29).** `barca` ("Barça", 42 partidos de ACB + 2
  de Copa) y `fcb` ("FC Barcelona", 40 de Euroliga) eran el mismo club bajo dos `team_id`, y el
  puente de identidad no los unía: bloqueaba la propuesta [04](04_fatiga_y_calendario.md) (que ya
  está implementada) y falseaba cualquier agregado por club de ese equipo. `_KNOWN_TEAM_ALIASES`
  (`packages/baskonia_core/names.py`) ya evita que una ingesta nueva vuelva a separarlos;
  `tools/fix_barca_identity.py` fusionó con retroactividad los que ya estaban cargados (equipo +
  14 jugadores partidos en dos roster distintos, fusionados por dorsal compartido). La comprobación
  de integridad que lo detecta corre ahora al final de cada ingesta
  (`ingest.common.identity.find_team_identity_collisions`, cableada en `ingest/run_all.py`).
  **Al arreglarlo apareció el mismo bug en otros ~23 pares de clubes** (Bilbao Basket/Surne Bilbao,
  Bayern/FC Bayern Múnich, varios Manresa/Burgos/Lleida/Granada con nombre de patrocinador...) —
  `_KNOWN_TEAM_ALIASES` ya los reconoce para ingestas futuras, pero los que YA están cargados no se
  han fusionado con retroactividad como el Barça: solo importa para un agregado por club de esos
  equipos concretos (ninguno juega ACB+Euroliga a la vez, así que no bloquean la 04), pero conviene
  planificar el mismo arreglo si se prepara scouting de alguno de ellos en profundidad.
  **Actualización 2026-09-28:** la causa de fondo está cerrada para ACB — la API trae un `clubId`
  estable entre temporadas y patrocinadores, que ahora se guarda en `teams.acb_club_id` y se usa
  antes que el nombre, así que un patrocinador nuevo ya no crea duplicado aunque no esté en
  `_KNOWN_TEAM_ALIASES` (que queda para cruzar ACB ↔ Euroliga). Los parecidos sin prueba salen
  como sugerencia al final de cada ingesta y nunca se fusionan solos. Los ~23 pares ya cargados
  se fusionan con copia previa con `tools/fix_team_identity.py --apply` o con
  `python -m ingest.run_all --season 2026 --merge-team-duplicates`; pasos exactos en
  [`ingestor/01_estado.md`](../ingestor/01_estado.md#identidad-de-club-sin-alias-a-mano-2026-09-28).
- **La ficha biográfica está casi vacía**: `height_cm` es NULL en los 968 jugadores, `birth_date`
  solo existe en 12 y 452 jugadores tienen la posición en blanco. Limita sobre todo a
  [11](11_similitud_de_jugadores.md).

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

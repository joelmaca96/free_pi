# 05. Perfil arbitral

**Estado:** propuesta, sin implementar · **Fecha:** 2026-08-28 · **Índice:** [00_indice.md](00_indice.md)

Cifras calculadas sobre `data/baskonia.db` el 2026-08-28 (736 partidos con terna).

## 1. El problema del entrenador

Todos los cuerpos técnicos preparan al árbitro, y todos lo hacen **de memoria y por anécdota**:
"este pita mucho", "con este no se puede protestar". Es la única parte de la preparación de un
partido donde nadie tiene números, en Europa, porque nadie los publica.

Aquí sí los hay: la terna está guardada en **736 de los 737 partidos**, lo que da **104 árbitros
distintos**, de los cuales **70 tienen 15 partidos o más** — muestra suficiente para hablar con
datos en vez de con impresiones.

Y hay señal de verdad, no ruido. Media de la liga: **43,6 faltas por partido** (desviación 5,7) y
42,4 tiros libres intentados. Entre los 70 árbitros con muestra:

| | Faltas/partido |
|---|---|
| Los que menos pitan | 39,5 – 39,6 |
| Los que más pitan | 47,8 – 49,5 |

**Diez faltas de diferencia** entre los extremos: la diferencia entre un partido en el que tu
pívot juega 30 minutos y uno en el que se sienta en el 24 con cuatro personales.

## 2. Qué se ve

**a) Ficha del árbitro** (una por cada uno de los tres de la terna del próximo partido):

- Faltas y tiros libres por partido, **ajustados por competición** (ver §4), con su percentil.
- Sesgo local/visitante: diferencia media de tiros libres entre local y visitante. La media de la
  liga es **+1,6 a favor del local**; los valores individuales verificados van de **−2,1 a +4,5**.
- Ritmo de los partidos que dirige (los partidos muy pitados son más lentos).
- Historial con nosotros y con el rival: balance, faltas recibidas y señaladas.

**b) Efecto sobre jugadores concretos.** La pregunta útil de verdad: "¿a nuestro 5 le pitan más
con este árbitro?". Faltas por 40 minutos de un jugador con ese árbitro frente a su media,
siempre con el aviso de muestra (serán 2-5 partidos: se enseña como indicio, no como hecho).

**c) Una línea en el dossier de prepartido** ([03](03_dossier_scouting_rival.md)): "Terna de
mucho contacto permitido (39,8 faltas/partido, p10 de la liga): el partido va a ser físico".

## 3. Datos: de dónde sale

`games.referees`, poblada en **736/737** partidos, con formato `"Nombre A · Nombre B · Nombre C"`
(separador `·`, U+00B7). Los 736 tienen **exactamente tres** nombres, así que el troceado es fiable.

Se cruza con:

- `game_advanced_stats.pf` / `fta` (faltas y tiros libres por equipo y partido, completo),
- `player_game_stats.pf` / `pf_drawn` / `minutes` para el efecto por jugador,
- `play_events` (`foul_personal` 29.933, `foul_drawn` 31.612) para el **momento** de las faltas,
  que es lo que conecta esta propuesta con [06_gestion_de_faltas.md](06_gestion_de_faltas.md),
- `games.home_team_id` para el sesgo local.

**Decisión de diseño importante:** el análisis va **por árbitro individual, nunca por terna**. Hay
711 combinaciones distintas de terna en 736 partidos: casi cada partido tiene un trío distinto, así
que la terna como unidad no tiene ninguna muestra. El nombre individual sí (70 con ≥15 partidos).

## 4. Cálculo

**Ajuste obligatorio por competición.** Las competiciones no se pitan igual, y sin corregirlo el
perfil de un árbitro mide sobre todo dónde le designan:

| Competición | Partidos | Faltas/partido | TLA/partido |
|---|---|---|---|
| ACB | 325 | 45,7 | 46,0 |
| Euroliga | 402 | 41,9 | 39,3 |
| Copa del Rey | 7 | 45,7 | 45,0 |

Casi **cuatro faltas** de diferencia entre ACB y Euroliga, frente a un rango total entre árbitros
de unas diez: ignorarlo se comería casi la mitad de la señal. Por eso la métrica que se muestra no
es la media bruta sino el **residuo**: faltas del partido menos la media de su competición,
promediado por árbitro. La media bruta se puede enseñar al lado, pero la que ordena el ranking es
la ajustada.

**Segundo ajuste, opcional:** los equipos también influyen (hay equipos que provocan más faltas).
Un modelo aditivo sencillo (residuo respecto a la suma de las medias de los dos equipos) lo
absorbe sin necesidad de regresión. Merece la pena solo si el ranking cambia; conviene medirlo
antes de complicarlo.

**Umbrales de presentación:** mínimo 15 partidos para aparecer en rankings; entre 8 y 14, se
muestra con aviso; por debajo de 8, no se muestra.

## 5. Limitaciones conocidas

- **Es una temporada.** 15-40 partidos por árbitro es muestra para tendencias gruesas (cuántas
  faltas pita), no para afirmaciones finas ("le pita a X más que a Y").
- **Correlación, no causa.** Un árbitro con muchas faltas puede ser un árbitro al que le designan
  partidos calientes. El ajuste por competición y por equipos mitiga, no elimina.
- **La designación no es aleatoria** y los partidos de fase final concentran a los mismos
  árbitros: cuidado con leer el sesgo local como intención.
- **Los nombres vienen sin normalizar** de cada fuente. Antes de agregar hay que pasar por
  `packages/baskonia_core/names.py::normalize_name` y revisar a mano la lista de 104 nombres:
  un mismo árbitro escrito de dos formas parte su muestra en dos.
- Este es material **interno del cuerpo técnico**. Conviene que la interfaz lo presente en tono
  descriptivo y sin juicios; es información para preparar, no un informe de arbitraje.

## 6. Encaje en el código

- **Normalizar en la ingesta, no en cada consulta.** Lo limpio es una tabla nueva
  `game_referees(game_id, referee_name, position)` poblada por `ingest/common/loader.py` al
  cargar el partido (una fila por árbitro), dejando `games.referees` intacta como dato crudo.
  Trocear la cadena en SQL en cada consulta es lento y frágil.
- Consultas en `app/data/queries_assistant.py` (donde ya viven las agregaciones de liga):
  `referee_profile(engine, referee_name, season_id)`, `referees_for_game(engine, game_id)`.
- Interfaz: bloque en `app/pages/proximo_rival.py` y ficha del árbitro accesible desde
  `app/pages/partidos_anteriores.py` (`queries.game_metadata` ya devuelve la terna, línea 434).
- `app/assistant/capabilities.py` ya tiene el interruptor `game_metadata` para saber si estos
  campos existen en la base de datos que sirve la interfaz: reutilizarlo para encender o apagar
  el bloque entero, en vez de asumir.

## 7. Esfuerzo y orden

Bajo-medio. Lo único que hay que construir de nuevo es la tabla `game_referees` y sus
agregaciones; el resto es interfaz. Sin dependencias nuevas.

Va la quinta porque, aunque no es la más importante para ganar partidos, **es probablemente la
más diferencial de toda la lista**: no existe en ninguna otra herramienta a la que un entrenador
de ACB o Euroliga tenga acceso.

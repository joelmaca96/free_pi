# Implementation: Competición real por partido (`Game.league`)

## Resumen

Implementados los 5 paquetes de trabajo del diseño sin desviaciones funcionales. `scraper/parser.py`
extrae ahora la competición real del `id` de cada tabla de calendario de BBR y la enhebra como clave
`"league"` de cada partido; `db/storage.py` corrige el bug colateral por el que `upsert_game()` nunca
actualizaba `.league` de un partido ya existente; `main.py` deja de forzar la liga fija del equipo de
origen en el bucle de box scores, propaga `row.league` al reconstruir partidos desde la BD, y añade
el flag `--fix-league` con `backfill_league()` / `_backup_database()` / `_league_counts()`. El
backfill se ejecutó **realmente** dos veces contra `data/baskonia.db`: de los 139 partidos jugados
quedan **101 `acb` / 38 `euroleague`**, 0 sin resolver, exactamente el número esperado por el diseño.

## Cambios por fichero

| Fichero | WP | Qué cambió |
|---|---|---|
| `scraper/parser.py` | WP-1 | Nuevos `_BBR_LEAGUE_MAP` (`SPA`→`acb`, `ELG`→`euroleague`) y `_table_competition(table_id)` (regex `-([A-Z]+)-(?:regular-season\|playoffs)$`, código desconocido → minúsculas, `None` si no aplica). |
| `scraper/parser.py` | WP-1 | `parse_schedule_games()` resuelve la liga **una vez por tabla** y la pasa a `_parse_schedule_table()`; docstring `Returns:` documenta la nueva clave. |
| `scraper/parser.py` | WP-1 | `_parse_schedule_table(schedule_table, league=None)`: nuevo parámetro y clave `"league": league` en cada dict de partido. |
| `db/storage.py` | WP-2 | `upsert_game()`, branch de actualización: nueva línea `game.league = league or game.league` (mismo patrón `nuevo or actual` que el resto de campos) + comentario del *por qué*. |
| `main.py` | WP-3 | Bucle de captura de box scores: `league=game.get("league") or source_team.league` (antes `source_team.league` a secas). |
| `main.py` | WP-3 | `_team_games_from_db()`: añade `"league": row.league` al dict reconstruido (evita la regresión a `'acb'` en ejecuciones sin `--refresh-teams`). |
| `main.py` | WP-4 | Nuevos `backfill_league()`, `_league_counts(session)` y `_backup_database()`; imports `shutil` y `from datetime import datetime`; flag CLI `--fix-league` con despacho `if args.fix_league: backfill_league() else: run(...)`; línea de uso en el docstring del módulo. |
| `data/baskonia.db` | WP-5 | Dato, no código: 38 filas de `games` pasaron de `league='acb'` a `league='euroleague'`. Copias de seguridad en `data/baskonia.db.bak-*` (ver Evidencia). |

## Delegaciones

| WP | Especialista | Resultado |
|---|---|---|
| WP-1 … WP-5 | ninguno (roster de especialistas vacío en `workflow.config.md`) | Implementados directamente por el feature-developer. Oleada 1 = WP-1 + WP-2 (independientes), oleada 2 = WP-3 + WP-4 (mismo `main.py`), luego WP-5 (ejecución). Build por oleada + build de integración final. |

## Desviaciones del diseño

Ninguna desviación funcional. Dos añadidos menores, ambos documentación dentro de los ficheros ya en
alcance y trazables al WP correspondiente:

1. `parse_schedule_games()`: se amplió la sección `Returns:` del docstring para documentar la nueva
   clave `"league"` (el diseño mostraba solo el código). Justificación: el contrato público de la
   función cambia; las normas del proyecto piden docstrings Google en español en firmas públicas.
2. Docstring de módulo de `main.py`: se añadió `python main.py --fix-league` a la sección `Uso:`,
   donde ya estaban documentados `python main.py` y `--refresh-teams`. Justificación: consistencia
   con la convención ya presente; el flag nuevo quedaría invisible ahí.

## Build

Comandos del `workflow.config.md`, ejecutados tras la oleada 2 (build de integración, con todos los
cambios de los WP-1 a WP-4 ya aplicados):

```
$ .venv/Scripts/python.exe -m py_compile scraper/parser.py db/storage.py main.py
py_compile: OK (rc=0)

$ .venv/Scripts/python.exe -c "import app, main, stats, insights, report, config"
2026-08-18 11:49:33.807 WARNING streamlit.runtime.scriptrunner_utils.script_run_context: Thread
'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
smoke import: OK (rc=0)
```

El warning de `ScriptRunContext` es **preexistente** y ajeno a esta feature: lo emite Streamlit
siempre que `app.py` se importa fuera de `streamlit run`. Sin warnings nuevos.

Build de oleada 1 (`py_compile scraper/parser.py db/storage.py`): OK, más comprobación unitaria de
`_table_competition()` con los ids reales documentados en el diseño §1:

```
'vitoria-ELG-regular-season'      -> 'euroleague'
'vitoria-SPA-regular-season'      -> 'acb'
'vitoria-SPA-playoffs'            -> 'acb'
'bilbao-SPA-regular-season'       -> 'acb'
'bilbao-SPA-playoffs'             -> 'acb'
'gran-canaria-SPA-regular-season' -> 'acb'
'games' / 'schedule' / ''         -> None      (fallback a la liga de reserva, como hoy)
'vitoria-XYZ-playoffs'            -> 'xyz'     (código desconocido, no falla)
```

`--fix-league` aparece correctamente registrado en `python main.py --help`.

## Evidencia de los criterios de aceptación

Estado de partida medido antes de tocar nada (`data/baskonia.db`, 143360 bytes):
220 `games` (139 jugados **todos `acb`**, 81 pendientes: 42 `acb` + 38 `euroleague` + 1 `supercopa`),
665 `boxscores`, 56 `team_game_stats`, 56 `players`, 45 `teams`; 3 equipos con roster propio
(`vitoria` 24, `bilbao` 15, `gran-canaria` 17).

### Criterio 1 — Backup antes de la primera escritura: CUMPLIDO

Pre-flight **antes de ejecutar nada que escriba** (invocación directa de `main._backup_database()`,
para no fiarse de que el diseño lo especifique):

```
db size antes      = 143360     db sha256 antes = c411bca6069f7a7f...
backup devuelto    = data/baskonia.db.bak-20260818115031
backup existe      = True       backup size = 143360    backup sha256 = c411bca6069f7a7f...
MISMO TAMANO = True             CONTENIDO IDENTICO = True    db intacta = True
```

Backup creado por el propio backfill (1ª ejecución), primera línea del log tras el rótulo y **antes**
de cualquier `session.commit()`:

```
11:51:12,896 === Backfill de Game.league ===
11:51:12,922 Copia de seguridad creada en data/baskonia.db.bak-20260818115112
```

Verificación en disco: `data/baskonia.db.bak-20260818115112` = **143360 bytes**, exactamente el
tamaño de `data/baskonia.db` medido justo antes de lanzar la ejecución (143360), y sha256 idéntico al
del estado pre-backfill capturado en el pre-flight.

### Criterio 2 — Idempotencia: CUMPLIDO

| Métrica | Tras 1ª ejecución | Tras 2ª ejecución |
|---|---|---|
| `games` (total) | 220 | 220 |
| `boxscores` | 665 | 665 |
| `team_game_stats` | 56 | 56 |
| `players` | 56 | 56 |
| `teams` | 45 | 45 |
| jugados `league='acb'` | 101 | 101 |
| jugados `league='euroleague'` | 38 | 38 |
| pendientes (`acb`/`euroleague`/`supercopa`) | 42 / 38 / 1 | 42 / 38 / 1 |
| `league` NULL o vacía | 0 | 0 |
| tamaño de `data/baskonia.db` | 143360 | 143360 |

`diff` de los dos snapshots completos: **sin diferencias**. Log de la 2ª ejecución (ya arranca del
estado corregido y lo deja igual):

```
11:54:21,822 Distribución ANTES (partidos jugados): {'acb': 101, 'euroleague': 38}
11:56:05,127 Distribución DESPUÉS (partidos jugados): {'acb': 101, 'euroleague': 38}
```

Backup de la 2ª ejecución: `data/baskonia.db.bak-20260818115421` (143360 bytes).

### Criterio 3 — Resultado numérico esperado (101/38, 0 sin resolver): CUMPLIDO

Log de la 1ª ejecución:

```
11:51:13,059 Distribución ANTES (partidos jugados): {'acb': 139}
11:52:54,967 Distribución DESPUÉS (partidos jugados): {'acb': 101, 'euroleague': 38}
```

Consulta real contra `data/baskonia.db` (módulo `sqlite3`; no hay `sqlite3` CLI en este entorno):
`SELECT league, count(*) FROM games WHERE home_score IS NOT NULL GROUP BY league` →
**`acb`: 101, `euroleague`: 38** (total 139). `SELECT count(*) FROM games WHERE home_score IS NOT
NULL AND (league IS NULL OR league='')` → **0**. Coincide exactamente con el diseño (§1 y criterio 7),
así que el HTML de BBR no ha cambiado desde la verificación del arquitecto.

Verificación **semántica** adicional (que el reparto sea correcto, no solo que sume 38): los 38
partidos de Euroliga son 19 rivales × exactamente 2 encuentros (ida/vuelta), formato de liga regular
de Euroliga: `anadolu-efes`, `barcelona`, `bayern-muenchen`, `dubai`, `hapoel-tel-aviv`,
`maccabi-tel-aviv`, `milano`, `monaco`, `olympiakos`, `panathinaikos`, `paris-basket`, `partizan`,
`real-madrid`, `red-star`, `ulker-fenerbahce`, `valencia`, `villeurbanne`, `virtus-bologna`,
`zalgiris`. Los únicos rivales con partidos jugados en **ambas** competiciones son exactamente los
tres que el diseño anticipaba: `real-madrid` (acb=4, euroleague=2), `barcelona` (acb=4, euroleague=2)
y `valencia` (acb=5, euroleague=2) — es decir, 16 rivales solo-Euroliga × 2 = 32, más 3 × 2 = 6, total
38.

### Criterio 4 — Build: CUMPLIDO

Ver sección "Build": `py_compile` de los 3 ficheros sin error y smoke import de los 6 módulos de
borde sin excepción.

### Criterio 5 — No regresión tras ejecución normal: CUMPLIDO

Ejecutado `python main.py` (sin flags) después del backfill, precedido de una copia de seguridad
manual con la misma función (`data/baskonia.db.bak-20260818115653`, el pipeline normal no hace backup
por sí mismo, por diseño). Terminó con `Pipeline completado. Datos guardados en
sqlite:///data/baskonia.db` (exit 0). Estado tras esa ejecución:

```
games=220  boxscores=665  team_game_stats=56  players=56  teams=45
jugados:     'acb': 101   'euroleague': 38
pendientes:  'acb': 42    'euroleague': 38    'supercopa': 1
liga NULL o vacia: 0
```

`diff` contra el snapshot posterior al backfill: **sin diferencias**. La distribución de partidos
jugados sigue siendo 101/38 y **no** vuelve a `acb` fijo, lo que confirma que
`_team_games_from_db()` propaga `row.league` y que el bucle de box scores lo respeta (este era el
riesgo de regresión que motivaba el WP-3(b)).

### Criterio 8 del diseño — Sin efecto en pendientes: CUMPLIDO

Los 81 partidos pendientes mantienen 42 `acb` + 38 `euroleague` + 1 `supercopa` en los tres
snapshots (tras 1ª y 2ª ejecución del backfill, y tras la ejecución normal).

## Notas para el reviewer

1. **Copias de seguridad acumuladas en `data/`** (4 ficheros de 143360 bytes cada uno, ~560 KB en
   total): `.bak-20260818115031` (pre-flight de `_backup_database()`), `.bak-20260818115112` (1ª
   ejecución del backfill), `.bak-20260818115421` (2ª ejecución), `.bak-20260818115653` (previa a la
   ejecución normal de regresión). Las cuatro contienen datos válidos; las tres primeras son el
   estado pre-backfill (139 `acb`). Se dejan en disco a propósito como evidencia; el usuario puede
   borrarlas cuando dé el resultado por bueno. No se añadió ninguna lógica de rotación/limpieza
   (no estaba en el diseño).
2. **Precisión del docstring de `_backup_database()`**: dice "Nunca sobrescribe una copia anterior:
   cada llamada genera un fichero nuevo". Eso es cierto con la granularidad de segundo del sufijo
   `%Y%m%d%H%M%S` salvo que dos llamadas caigan en el **mismo segundo**, caso en el que `shutil.copy2`
   sobrescribiría. Se implementó literalmente como especifica el diseño (no se cambió el formato de
   timestamp); se señala por transparencia, no es un problema práctico para una operación manual.
3. **`upsert_game()` cambia de comportamiento, no de firma** (WP-2). Efecto colateral deseado: ahora
   cualquier llamada con una liga distinta a la guardada la corrige. Los llamadores existentes
   (`persist_schedule`, bucle de box scores) pasan `game.get("league") or team.league`, así que un
   `None` de la fuente nunca borra una liga ya correcta (`league or game.league`).
4. **Riesgo 2 del diseño confirmado en los datos**: la tabla `SPA` de BBR agrupa más que liga
   regular ACB (Real Madrid y Barcelona aparecen con 4 partidos `acb` jugados, Valencia con 5 —
   liga regular + playoffs/Copa fusionados bajo el mismo código). No bloqueante para esta feature
   (el objetivo era distinguir ACB-familia vs Euroliga), pero es material para el Documenter como
   limitación conocida.
5. **Coste de red real del backfill**: 6 peticiones (2 por equipo × 3 equipos con roster), ~1 min 42 s
   por ejecución con `REQUEST_DELAY=20`, por debajo de la estimación de ~2 min del diseño.
6. `README.md` **no** se ha tocado (es trabajo de la etapa 4, Documenter, según la tabla de módulos
   del diseño).

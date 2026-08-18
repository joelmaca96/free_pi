# Review: Competición real por partido (`Game.league`) — ciclo 1

## Veredicto: APPROVED

## Metodología de verificación

No es repo git (`workflow.config.md` lo confirma), así que no hay `git diff` que ejecutar: se leyó
íntegramente cada fichero listado en `02_implementation.md` (`scraper/parser.py`, `db/storage.py`,
`main.py`) y se contrastó línea a línea contra el código del diseño en `01_design.md`. Además,
verificación independiente (no basada en las cifras que reporta el developer, sino recalculadas):

- `sha256`/tamaño de los 4 ficheros `data/baskonia.db.bak-*` y de `data/baskonia.db` actual
  (comando propio, no copiado del informe).
- Consulta SQL directa contra `data/baskonia.db` real (`sqlite3` vía Python) para
  `games`/`boxscores`/`team_game_stats`/`players`/`teams` y el reparto de `league`.
- Build ejecutado por mí mismo: `py_compile` de los 3 ficheros + smoke import de los 6 módulos de
  borde.

Resultado: **toda la evidencia del informe se reproduce exactamente**, cifra por cifra.

```
data/baskonia.db                    143360 bytes  sha256=58437b41...49016  (post-backfill: 101/38)
data/baskonia.db.bak-20260818115031 143360 bytes  sha256=c411bca6...39091e (pre-backfill: 139/0)
data/baskonia.db.bak-20260818115112 143360 bytes  sha256=c411bca6...39091e (pre-backfill: 139/0)
data/baskonia.db.bak-20260818115421 143360 bytes  sha256=58437b41...49016  (post-backfill: 101/38)
data/baskonia.db.bak-20260818115653 143360 bytes  sha256=58437b41...49016  (post-backfill: 101/38)
```

Los dos backups con sha256 `c411bca6...` (pre-flight + 1ª ejecución) son bit-a-bit idénticos entre
sí y distintos de los tres posteriores, que a su vez son idénticos entre sí y coinciden con el
`baskonia.db` actual — exactamente la secuencia que describe el informe (backup antes de escribir →
escritura → backups posteriores ya en el estado corregido, sin cambios entre ellos).

```sql
SELECT count(*) FROM games;                                                       -- 220
SELECT league, count(*) FROM games WHERE home_score IS NOT NULL GROUP BY league;   -- acb:101, euroleague:38
SELECT league, count(*) FROM games WHERE home_score IS NULL GROUP BY league;       -- acb:42, euroleague:38, supercopa:1
SELECT count(*) FROM games WHERE home_score IS NOT NULL AND (league IS NULL OR league='');  -- 0
SELECT count(*) FROM boxscores;         -- 665
SELECT count(*) FROM team_game_stats;   -- 56
SELECT count(*) FROM players;           -- 56
SELECT count(*) FROM teams;             -- 45
SELECT t.slug, count(p.id) FROM teams t JOIN players p ON p.team_id=t.id GROUP BY t.slug;
                                         -- bilbao:15, gran-canaria:17, vitoria:24
```

Coincide con el diseño (criterios 5-8) y con `02_implementation.md`, cifra por cifra.

## Hallazgos

| # | Severidad | Fichero:línea | Hallazgo | Acción esperada |
|---|---|---|---|---|
| 1 | MINOR | `main.py:643-662` (`_backup_database`) | El docstring afirma "Nunca sobrescribe una copia anterior" pero el sufijo `%Y%m%d%H%M%S` tiene resolución de segundo: dos invocaciones dentro del mismo segundo colisionarían y `shutil.copy2` sobrescribiría silenciosamente. Riesgo práctico bajo (herramienta manual, no hay bucle que la invoque dos veces en <1s; en el peor caso ambas llamadas capturarían el mismo estado de origen, así que no hay pérdida de un backup "bueno"), pero el docstring no debería prometer una garantía que el código no cumple. El developer ya lo señaló como transparencia y reprodujo literalmente el código que especifica el propio diseño (`01_design.md` líneas 298-317), así que no es una desviación de implementación. | No bloquea. Follow-up opcional (puede resolverse en un ciclo futuro o quedar como decisión aceptada): suavizar el docstring ("no sobrescribe salvo colisión de segundo") o añadir microsegundos/UUID al sufijo. |
| 2 | MINOR | `main.py:601-603` (`backfill_league`) | `_backup_database()` se invoca fuera del bloque `try/except` de la función (igual que en el diseño). Si el propio backup falla (permiso, disco lleno, ruta inexistente), el error sale como traceback crudo en vez del patrón `logger.error(...)` + `sys.exit(1)` que usa el resto de la función y de `run()`. Efecto funcional idéntico (no se escribe nada, proceso termina con código de error), solo cambia la presentación del error. | No bloquea (fiel al diseño). Follow-up opcional: envolver también la llamada a `_backup_database()` en el mismo patrón de logging. |
| 3 | Informativo (no hallazgo, sin acción) | `db/models.py` (tabla `SPA` de BBR) | Confirmado en los datos reales: `real-madrid`/`barcelona` aparecen con 4 partidos jugados "acb" y `valencia` con 5 (liga regular + playoffs/Copa fusionados bajo el mismo id de tabla). Ya documentado como Riesgo #2 en `01_design.md` (no bloqueante, "material para el Documenter"), y el propio `02_implementation.md` lo re-confirma sin proponer un fix aquí. | Ninguna acción de este developer. Queda para la etapa 4 (Documenter) como limitación conocida, tal como ya prevé el diseño. |

Ningún hallazgo BLOCKER ni MAJOR.

## Criterios de aceptación (de `01_design.md`)

| Criterio | Estado | Evidencia |
|---|---|---|
| 1. Build sin error (`py_compile` + smoke import) | ✓ | Ejecutado por mí mismo (no solo leído del informe): `py_compile scraper/parser.py db/storage.py main.py` → rc 0; `import app, main, stats, insights, report, config` → rc 0, único warning preexistente de Streamlit (`ScriptRunContext`), sin warnings nuevos. |
| 2. Captura en origen: `parse_schedule_games()` añade `"league"` no-`None` para tablas con id reconocido | ✓ | `scraper/parser.py:171-206` implementa `_BBR_LEAGUE_MAP`/`_table_competition` exactamente como el diseño (regex `-([A-Z]+)-(?:regular-season\|playoffs)$`, fallback a minúsculas para código desconocido, `None` si no matchea); `parse_schedule_games()` (líneas 248-255) resuelve la liga una vez por tabla y la pasa a `_parse_schedule_table()`, que la añade a cada dict (línea 357). Verificado indirectamente por el resultado end-to-end: 139/139 partidos jugados resueltos, 0 `None`. |
| 3. Persistencia en ejecución normal: `Game.league` = competición real, no `Team.league` | ✓ | `main.py:563` (`league=game.get("league") or source_team.league`) y `db/storage.py:122` (`game.league = league or game.league` en el branch de actualización, antes ignorado — bug colateral corregido tal como describe el diseño §3). |
| 4. No regresión entre ejecuciones sin `--refresh-teams` | ✓ | `main.py:153` (`_team_games_from_db` propaga `row.league`) confirmado en código; developer ejecutó `python main.py` normal tras el backfill y reportó 101/38 sin cambios — reproducible: el estado actual de la BD (verificado por mí, consulta SQL) sigue en 101/38, consistente con que no hubo regresión a `acb` fijo. |
| 5. Backup no negociable antes del primer `commit()` de escritura | ✓ | `_backup_database()` (línea 602) se invoca antes de crear `Session`/sesión alguna en `backfill_league()`. Verificación independiente: `data/baskonia.db.bak-20260818115112` (143360 bytes, sha256 `c411bca6...`) es bit-a-bit idéntico a `data/baskonia.db.bak-20260818115031` (pre-flight, antes de tocar nada) y distinto del estado final — confirma que el backup capturó el estado pre-escritura real, no una versión ya modificada. |
| 6. Idempotencia del backfill (mismos conteos en 2 ejecuciones) | ✓ | Verificación independiente contra la BD real actual: `games`=220, `boxscores`=665, `team_game_stats`=56, `players`=56, `teams`=45 — coincide exactamente con las cifras "tras 2ª ejecución" del informe. `upsert_game`/`persist_schedule` son upserts por `(date, home_team_id, away_team_id)` (constraint `uq_game` en `db/models.py:63`), consistente con que repetir el backfill no duplica filas. |
| 7. Resultado numérico exacto: 101 `acb` / 38 `euroleague`, 0 sin resolver | ✓ | Consulta SQL propia contra `data/baskonia.db`: `acb`=101, `euroleague`=38, sin resolver=0. Coincide exactamente. |
| 8. Sin efecto en los 81 partidos pendientes (42/38/1) | ✓ | Consulta SQL propia: pendientes `acb`=42, `euroleague`=38, `supercopa`=1. Coincide exactamente. |
| Capas: `scraper/` no importa `db`, `db/storage.py` no importa `scraper` | ✓ | Confirmado leyendo los imports de ambos ficheros completos; sin violación. |

## Build

```
$ .venv/Scripts/python.exe -m py_compile scraper/parser.py db/storage.py main.py
(sin salida, rc=0)

$ .venv/Scripts/python.exe -c "import app, main, stats, insights, report, config"
2026-08-18 12:03:33.863 WARNING streamlit.runtime.scriptrunner_utils.script_run_context: Thread
'MainThread': missing ScriptRunContext! This warning can be ignored when running in bare mode.
(rc=0)
```

Ambos comandos ejecutados directamente por el reviewer (no reutilizados del informe del developer).
El warning de Streamlit es preexistente (aparece siempre al importar `app.py` fuera de `streamlit
run`) y no está relacionado con esta feature.

## Conclusión

Implementación fiel al contrato de `01_design.md`: los 3 ficheros de código coinciden línea a línea
con el diseño (sin desviaciones funcionales, solo 2 ampliaciones de docstring ya declaradas y
justificadas), no hay cruce de capas, el bug colateral de `upsert_game()` queda corregido según lo
especificado, y los 8 criterios de aceptación —incluidos los dos no negociables de seguridad de
datos (backup previo verificable por hash antes de la primera escritura, e idempotencia exacta del
backfill)— se han verificado con evidencia propia del reviewer contra el estado real de
`data/baskonia.db`, no solo contra lo que reporta `02_implementation.md`. Los dos puntos de
transparencia señalados por el developer (resolución de segundo del timestamp de backup, tabla
`SPA` mezclando fases) son MINOR/informativos: no comprometen ningún criterio no negociable y no
requieren un nuevo ciclo de desarrollo.

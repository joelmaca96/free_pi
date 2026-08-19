# Status: 001-analisis-diferencial-7-3
stage: done
docs: yes
tests: no
review_cycles: 1
design_cycles: 4
blocked_on: (ninguno) — DOCUMENTACIÓN COMPLETA (etapa 4, Feature Documenter). Ejecutado WP-8, que el
  developer dejó explícitamente pendiente para esta etapa (ver `02_implementation.md` →
  "Desviaciones del diseño" #1): en `README.md` — (a) sección 7.3, los 6 checkboxes `[ ]`→`[x]` +
  cabecera/intro actualizada de "(propuestas, sin implementar)" a "(implementadas)"; (b) "Estado
  actual", nuevo bullet con las 6 ideas + los dos selectores globales (temporada/competición),
  mencionando los umbrales de racha/narrativa como constantes ajustables en `insights.py`; (c)
  ampliada (no duplicada) la limitación ya documentada por la feature 002 sobre la tabla `SPA` de
  BBR (mezcla liga regular con playoffs/Copa), señalando que el nuevo selector de Competición hereda
  esa misma limitación; (d) sección 6 (GUI), párrafo de cabecera + ítems 1 y 3 de la lista de
  pestañas, documentando los selectores nuevos y las subsecciones nuevas visibles en la UI. No se
  tocó el párrafo de `format_date_es()` ya presente en esa sección (añadido por una ejecución
  anterior, no relacionado con esta feature). No se tocó ningún fichero `.py` ni `data/baskonia.db`.
  Verificado que las ~20 funciones/constantes públicas nuevas o extendidas ya tienen docstring
  español estilo Google, consistente con el resto del código — sin huecos de documentación de
  código detectados (ver `04_docs.md` → "Huecos detectados"). `tests: no` → no se encadena etapa 5;
  esta era la última etapa activa de la feature. Ver `04_docs.md` para el detalle completo.
  ------ (registro previo, etapa 3 / reviewer, ciclo 1) ------
  (ninguno) — REVIEW CICLO 1: APPROVED (0 BLOCKER, 0 MAJOR, 2 MINOR/INFO no bloqueantes).
  Ver `03_review.md`. Los dos hallazgos que el developer ya había señalado en `02_implementation.md`
  se verificaron de forma independiente por el reviewer (código real + consultas propias contra
  `data/baskonia.db`) y no bloquean: (1) las 7 funciones cuyo cuerpo remitía a un "ciclo 1" ya
  borrado del diseño están implementadas de forma coherente con el contrato fijado y el estilo del
  resto del código — verificado línea a línea, sin incoherencias; (2) los umbrales de racha ±1.0 no
  se disparan con `recent_n=5` (rango real de `z_score_pts`: -0.615..0.125) es una consecuencia
  matemática fiel a la fórmula ya fijada por el diseño, no un bug — queda como decisión de producto
  pendiente para el usuario (ajustar constantes o normalizar por `√N`), no para el pipeline de
  desarrollo. Build reproducido por el reviewer (`py_compile` + smoke import limpios) más una
  verificación adicional con `streamlit.testing.v1.AppTest` (0 excepciones en 3 combinaciones
  temporada×competición). Siguiente etapa: **documentación** (`docs: yes`) — el Documenter debe
  ejecutar WP-8 (pendiente, deliberadamente no hecho por el developer): checkboxes `[ ]`→`[x]` de la
  sección 7.3 de `README.md` + nota en "Estado actual" mencionando los dos selectores y la
  limitación de cobertura de datos de Euroliga (ver supuesto #13 de `01_design.md` y desviación #1
  de `02_implementation.md`). `tests: no` → no se encadena etapa 5.
  ------ (registro previo, etapa 2 / developer) ------
  (ninguno) — DESARROLLO COMPLETO, LISTO PARA REVIEW. Implementados 9 de los 10 WPs
  (WP-0a, WP-0b, WP-1..WP-6, WP-7) en `insights.py`, `stats.py` y `app.py`; ver
  `02_implementation.md`. **WP-8 (checkboxes de 7.3 + nota en "Estado actual" de `README.md`)
  deliberadamente NO ejecutado**: lo asume la etapa 4 (Feature Documenter) por indicación explícita
  del invocador, para no solaparse — el Documenter debe hacerlo (supuesto #8 del diseño ya preveía la
  coordinación). Build limpio: `py_compile stats.py insights.py app.py` y
  `import app, main, stats, insights, report, config` sin error ni warning nuevo. Verificación ad-hoc
  contra `data/baskonia.db` real de las 6 ideas + Ampliación A + Ampliación B, con los 3 casos de
  degradado limpio (temporada 2026-27 vacía, 2025-26×Supercopa = 0 partidos, equipo sin
  `team_game_stats`) documentados con resultados literales; todos los números coinciden con los
  criterios de aceptación del diseño (incluida la matriz temporada×competición fila por fila).
  Además `app.py` se ejecutó headless de verdad con `streamlit.testing.v1.AppTest`: **0 excepciones**
  en las 8 combinaciones temporada×competición y en el camino "rival ya scouteado"
  (proyección 77.3/88.5/82.9), y `streamlit run app.py` arranca sin traceback (healthz HTTP 200).
  6 desviaciones menores documentadas con justificación en `02_implementation.md` (ninguna toca
  contratos ni comportamiento especificado). **Hallazgo para el reviewer**: el diseño remite los
  cuerpos de 7 funciones al "ciclo 1", cuyo texto ya no existe en `01_design.md` (firmas y contratos
  sí estaban fijados; los detalles no especificados se implementaron mínimos y están señalados uno a
  uno). **Segundo hallazgo, no bloqueante**: con `recent_n=5` ningún jugador alcanza el umbral de
  racha ±1.0 (rango real de `z_score_pts`: -0.615 .. 0.125), consecuencia matemática de la fórmula
  fijada por el diseño; ajustar umbral o normalizar por √N es decisión de producto y NO se ha tocado.
  Pendiente solo verificación *visual* de aspecto (anchos de columnas/cabecera), explicitada en
  `02_implementation.md`.
  ------ (registro previo, etapa 1 / ciclo 4 de diseño) ------
  (ninguno) — DISEÑO DEL CICLO 4 COMPLETO, SIN BLOQUEO REAL. `01_design.md` ya integra la
  Ampliación B (selector de competición) sobre el diseño ya aprobado (6 ideas de 7.3 + Ampliación A),
  editado en sitio como delta, sin rehacer el documento. Verificado de nuevo, de forma independiente,
  contra `data/baskonia.db` real: 101 partidos jugados `acb` / 38 `euroleague` (139 total, 0 sin
  resolver), coincide exactamente con lo reportado por `local/features/002-competicion-real-por-partido/`
  (`stage: done`, `APPROVED`). Selector de competición diseñado junto al de temporada en la cabecera
  de `main()` (ortogonal, combinación por AND), enhebrado por el mismo conjunto de funciones que ya
  respetan `season` (las 6 funciones nuevas de 7.3 salvo `player_load`/`games_in_window`, excepción
  documentada; `player_recent_form`, `team_advanced_summary`, `past_games`; las 4 funciones de la
  Ampliación A vía `_team_games`/`head_to_head_games` ya centralizados). Único hallazgo relevante, no
  bloqueante: hoy el 100% de `team_game_stats`/`boxscores` ya capturados (56/665 filas, cualquier
  equipo) son de liga `'acb'`, 0 `'euroleague'` — limitación de cobertura de datos ya conocida (fuera
  de las capas de esta feature), cubierta por el contrato `Optional[...]`/`None` existente sin código
  nuevo (ver `01_design.md` → "Diseño → Ciclo 4 → Hallazgo de cobertura de datos"). Clase de
  complejidad: sigue **complejo** (sin cambio de umbral). WPs: siguen siendo 10 (se amplió el alcance
  de WP-0a/WP-0b/WP-1/WP-3/WP-4/WP-6/WP-7/WP-8 en vez de crear WPs nuevos, ver "Paquetes de trabajo").
  **Listo para encadenar directamente a `feature-developer`**: desarrollo completo de las 6 ideas de
  7.3 + Ampliación A + Ampliación B, de una sola vez (nada de esta feature se ha implementado todavía
  — 0 líneas de código tocadas hasta ahora), seguido de review y docs. No se ha necesitado
  `AskUserQuestion` en este ciclo: todas las decisiones de bajo riesgo encontradas (default "Todas"
  en el selector, filtrado de candidatos por liga en `schedule_difficulty`, exclusión de
  `player_load`, no filtrar `upcoming_games` por liga, renombrado de higiene en `render_player_card`)
  se tomaron con evidencia y quedaron documentadas en "Supuestos y riesgos" (#11-#15), no como
  preguntas abiertas.
  ------ (registro previo del bloqueo, ciclo 3, mantenido como evidencia — ya resuelto en ciclo 4) ------
  Ciclo 3 completado con las dos ampliaciones pedidas por el gate del ciclo 2, resultado dispar:

  **(A) Filtro de temporada extendido a toda la app — COMPLETO, sin bloqueo.** `01_design.md` ya
  refleja el delta: `season` se enhebra también en `recent_games_df`, `team_summary_df`,
  `head_to_head_summary_df` y `head_to_head_games` (esta última encontrada en el barrido pedido por
  el gate, no prevista en ciclo 1/2 — hermana "detallada" de `head_to_head_summary_df`, dejarla
  fuera habría sido la misma inconsistencia que el gate pidió cerrar). Implementación centralizada
  en `_team_games(session, team, season=None)`, único punto de acceso de las tres primeras. Únicas
  excepciones que sobreviven, cada una con motivo documentado: `upcoming_games` (calendario
  pendiente, confirmada por el usuario), `validate_data` (calidad de datos transversal),
  `player_load`/`games_in_window` (ventana de días, no cruza temporadas), `boxscore_df`/
  `current_roster` (no son agregaciones sobre partidos, no aplica el concepto). Ver "Diseño → Ciclo
  3 → Ampliación A" y criterios de aceptación con números concretos contra `data/baskonia.db` real
  (incluye el caso 0 partidos: `head_to_head_summary_df(vitoria, season=2026)` → `[]`).

  **(B) Filtro de competición — BLOQUEADO, no implementado.** Se investigó contra
  `data/baskonia.db` real antes de diseñar nada, tal como pedía el gate: `Game.league` tiene 3
  valores limpios (`acb`=181, `euroleague`=38, `supercopa`=1, 0 nulos, sin inconsistencias de
  formato) pero **no es fiable para el dato que importa**: el 100% de los 139 partidos ya jugados
  tiene `league='acb'` — 0 con `'euroleague'`/`'supercopa'` — porque `main.py:466-468` asigna la
  liga de forma fija al equipo de origen ("asumimos liga ACB para el caso de uso", comentario
  propio del código) y `main.py:556` copia esa misma liga fija a cada partido, sin mirar la
  competición real. Solo los 39 partidos pendientes (calendario oficial baskonia.com) llevan una
  liga fiable. Implementar el selector mostraría, p.ej., "Euroliga" + temporada 2025-26 → 0
  partidos — no porque no se jugaran (el Baskonia es club de Euroliga y sí los jugó), sino porque
  están mal etiquetados: un resultado silenciosamente incorrecto, no un degradado limpio legítimo.
  Arreglarlo de raíz requiere tocar `scraper/parser.py` (capturar la competición por tabla al
  parsear el calendario BBR — la información existe en el HTML, hoy se descarta),
  `db/storage.py`/`main.py` (persistirla por partido) y un backfill de los 139 partidos ya
  guardados (probablemente re-scraping) — las tres cosas fuera de las capas que esta feature toca.
  Ver evidencia completa en `01_design.md` → "Preguntas abiertas para el usuario".

  **Pregunta concreta para el usuario**: ¿se prioriza una feature aparte (scraper/db, con backfill)
  para que `Game.league` refleje la competición real por partido, o se renuncia al selector de
  competición y se cierra 7.3 solo con la Ampliación A? El Feature Lead debe devolver esta pregunta
  al usuario antes de invocar `feature-developer` — no encadenar automáticamente a etapa 2 mientras
  esta decisión esté pendiente. Si el usuario decide "renunciar al selector", el desarrollo puede
  arrancar directamente con `01_design.md` tal cual está (Ampliación A completa, Ampliación B fuera
  de alcance documentada) sin necesidad de un cuarto ciclo de diseño.

  Decisiones de bajo riesgo #1 (TS% sobre eFG%) y #2 (temporada por defecto = más reciente con
  partidos jugados) del ciclo 2 quedan confirmadas tal cual por el usuario, sin cambios en el ciclo 3.
  ------ (registro previo, ciclo 2) ------
  01_design.md (ciclo 2) listo para nuevo gate humano. Resuelto: "temporada"
  se deriva de `Game.date` en tiempo de consulta (regla de corte mes >= 7), sin columna nueva en
  `db/models.py` ni migración/backfill — verificado sin ambigüedad contra `data/baskonia.db` real
  (220 partidos, 0 sin parsear, nunca hay partidos en jul-ago). Se confirmó además que la BD ya
  tiene dos temporadas simultáneas hoy (2025-26 jugada, 79 partidos/75 jugados; 2026-27 con
  calendario cargado y 0 partidos jugados — el escenario real de "pretemporada" que preocupaba al
  usuario, reproducible ahora mismo, sin fechas simuladas). Aplicadas las 3 respuestas ya dadas por
  el usuario (doble z-score PTS+TS% en rachas, umbrales de narrativa confirmados, ventana de
  carga/fatiga a 14 días). No quedan preguntas abiertas que bloqueen: las dos dudas estructurales
  del gate 1 (derivar vs. persistir `season`; tratamiento de pretemporada) se resolvieron con
  evidencia real sin trade-off genuino pendiente de decisión humana (ver 01_design.md, sección
  "Preguntas abiertas para el usuario" — vacía salvo decisiones de bajo riesgo documentadas para
  posible corrección en el gate). Clase de complejidad recalculada: pasa de `normal` a `complejo`
  (el filtro de temporada obliga a extender el contrato de `player_recent_form`/
  `team_advanced_summary` y a enhebrar `season` por casi toda la superficie de `app.py`, no solo en
  las 6 funciones nuevas; sigue sin tocar `db/models.py` ni requerir migración). WPs: 10 (antes 8).
  Pendiente: gate humano sobre este diseño revisado antes de reanudar la etapa de desarrollo.
updated: 2026-08-19 por feature-documenter

## Nota operativa de sesión
Esta sesión no puede lanzar diálogos interactivos desde subagentes (sin AskUserQuestion). Ningún
agente del pipeline debe bloquear esperando input humano: ante una decisión que requiera al
usuario (incluido el gate de aprobación de diseño), el agente devuelve el control a quien lo invocó
con un resumen y la pregunta concreta, en vez de asumir una respuesta.

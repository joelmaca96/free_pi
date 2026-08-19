# Migración: de la app Streamlit monolítica a pipeline + API + SPA

Documento hermano de [01_design.md](01_design.md), que define la arquitectura destino. Aquí se
define **cómo llegar** desde el estado actual sin romper nada por el camino.

## Contexto y objetivo

Restricción no negociable del encargo (requisito 6): *el pipeline no debe cambiar de comportamiento
y la migración debe ser incremental y no bloqueante*. Traducido a una regla operativa:

> **Al final de cada fase, `python main.py` y `streamlit run app.py` deben seguir funcionando
> exactamente igual que hoy** — hasta la fase F7, que es la única que retira Streamlit, y solo
> después de haber demostrado paridad.

Esto descarta un *big bang* (reescribir y sustituir) y obliga a un patrón **strangler fig**: la
nueva arquitectura crece al lado de la vieja, absorbe responsabilidades una a una, y la vieja se
apaga al final.

Se apoya en dos hechos verificados del código actual:

1. La lógica de negocio (`stats.py`, `insights.py`) ya está aislada, testeada y no la toca ninguna
   fase. **Mover no es reescribir.**
2. La lógica atrapada en `app.py` es acotada y localizable: 9 helpers de acceso a BD
   (líneas 117-427) y 2 generadores de informes (líneas 897-1135). El resto de `app.py` es
   renderizado, que se sustituye, no se migra.

## Alcance

**Entra:** las 7 fases, sus gates de entrada/salida, el arnés de paridad, la estrategia de
*rollback* y los criterios de aceptación por fase y globales.

**Fuera de alcance:** el diseño (ver [01_design.md](01_design.md)) y el despliegue (ver
[03_deplyment_design.md](03_deplyment_design.md)). Tampoco entra la planificación temporal: las
fases se ordenan por dependencia, no por fecha.

## Principios de la migración

| # | Principio | Consecuencia práctica |
|---|---|---|
| 1 | **Una fase = un PR revertible** | Cada fase se puede deshacer con `git revert` sin arrastrar a la siguiente |
| 2 | **Mover antes que reescribir** | F1, F2 y F4 no cambian ni una línea de lógica; el diff es de rutas de import |
| 3 | **Los módulos puente son temporales y explícitos** | Cada puente lleva un comentario `# PUENTE DE MIGRACIÓN — eliminar en F7` |
| 4 | **La paridad se demuestra con números, no con la vista** | El arnés compara valores, no capturas de pantalla |
| 5 | **Ninguna fase introduce peticiones de red en los tests** | La suite sigue siendo offline al 100% |
| 6 | **Sin cambios de esquema** | Ninguna fase toca las 5 tablas. Riesgo de pérdida de datos: nulo |

---

## Fase F0 — Línea base y arnés de paridad

**Objetivo:** congelar el comportamiento actual como oráculo objetivo, **antes** de mover nada.

**Trabajo:**

1. `tools/parity_dump.py`: para una combinación fija `(team, season, league, last_n)`, invoca las
   funciones de datos actuales de `app.py` y vuelca su salida a JSON canónico (claves ordenadas,
   flotantes redondeados a 4 decimales):

   ```
   past_games · upcoming_games · team_summary_df · recent_games_df ·
   recent_form_df · streaks_df · schedule_difficulty_df · player_load_df ·
   head_to_head_summary_df · boxscore_df ·
   insights.team_advanced_summary · project_next_matchup · scouting_narrative
   ```

2. Se ejecuta contra `data/baskonia.db` real para **al menos 4 combinaciones**, elegidas para
   cubrir los casos límite ya conocidos del proyecto:

   | Combinación | Qué caso límite cubre |
   |---|---|
   | `vitoria`, temporada jugada, `league=None`, `last_n=5` | camino feliz, todos los paneles con datos |
   | `vitoria`, temporada jugada, `league=euroleague`, `last_n=5` | filtro de competición combinado por AND |
   | `vitoria`, temporada **sin partidos jugados** (calendario cargado), `last_n=5` | rachas vacías, narrativa `None`, proyección `None` |
   | `bilbao` o `gran-canaria`, temporada jugada, `last_n=3` | equipo con menos cobertura de box scores |

3. Los volcados se guardan en `tests/parity/baseline/*.json`, versionados en el repo.

**Gate de salida:** los ficheros de línea base existen y `tools/parity_dump.py` es determinista
(dos ejecuciones seguidas producen ficheros idénticos, comprobado con hash).

**Riesgo cubierto:** sin F0, "no hemos roto nada" es una opinión. Con F0, es un `diff`.

---

### ✅ F0 completada (2026-08-19)

**Estado:** ✅ **COMPLETADA** — gate de salida verificado.

**Entregables:**
- `tools/parity_dump.py` — arnés de paridad (CLI `--team --season --league --last-n
  --reference-date --out`). Vuelca las 13 salidas a JSON canónico (claves ordenadas,
  flotantes a 4 decimales, `NaN`→`null`, timestamps a ISO). `deterministic` refleja si
  se inyectó `--reference-date`.
- `tools/__init__.py` — paquete para importar el arnés desde los tests.
- `tests/parity/baseline/*.json` — 4 combinaciones versionadas:
  - `vitoria-2025-all-last5.json` (camino feliz)
  - `vitoria-2025-euroleague-last5.json` (filtro AND por competición)
  - `vitoria-2026-all-last5.json` (temporada sin partidos jugados)
  - `gran-canaria-2025-all-last3.json` (menor cobertura de box scores)
- `tests/test_parity_dump.py` — 4 tests (determinismo por hash, presencia de las 13
  claves, redondeo a 4 decimales, existencia/validez de la línea base).

**Gate de salida verificado:**
- Línea base presente y válida (JSON parseable).
- Determinismo: dos ejecuciones consecutivas con `--reference-date` producen ficheros
  byte a byte idénticos (SHA256 coincidente).
- Suite completa verde: `python -m pytest` → **100 passed**.

**Desviaciones declaradas respecto al diseño** (detalle en `02_implementation.md`):
- `player_load_df` no acepta `reference_date` → reimplementado en el arnés
  (`_player_load_df`) usando `app.games_in_window(..., reference_date)` +
  `insights.player_load`, respetando la regla de solo-lectura sobre `app.py`.
- `upcoming_games` no acepta fecha de referencia → `_filter_upcoming_by_reference()`
  reimplementa su filtro de fechas.
- `generated_at` (timestamp) se excluye del volcado canónico para no romper el
  determinismo (no forma parte del contrato de paridad).

**Próxima fase:** F1 (ver tabla de fases más abajo).

> Nota: `player_load_df` depende de la fecha actual (ventana de N días) y `upcoming_games` filtra
> por `datetime.now()`. El arnés debe aceptar una fecha de referencia inyectada para ser
> reproducible; si no, esas dos salidas se comparan con tolerancia y no como igualdad estricta.

---

## Fase F1 — Extraer el dominio compartido (`packages/baskonia_core`)

**Objetivo:** que exista un único paquete de dominio importable por las dos aplicaciones futuras,
sin que nada del código actual se entere.

**Trabajo:**

1. `git mv` de `db/`, `stats.py`, `insights.py`, `config.py` a `packages/baskonia_core/`.
   Usar `git mv` (no copiar/borrar) para conservar el historial y que el diff sea legible.
2. Dejar en la raíz **módulos puente** de una línea:

   ```python
   # config.py — PUENTE DE MIGRACIÓN, eliminar en F7
   from packages.baskonia_core.config import *          # noqa: F401,F403
   ```

   Uno por cada módulo movido (`config.py`, `stats.py`, `insights.py`, `db/__init__.py`,
   `db/models.py`, `db/storage.py`). `main.py`, `app.py`, `report.py` y los 5 ficheros de test
   siguen importando exactamente igual que hoy.

   > Cuidado: `from X import *` no reexporta nombres con guion bajo inicial. `tests/test_main.py`
   > prueba `_normalize_team_name` y `_select_boxscores`, pero esos viven en `main.py`, que no se
   > mueve en F1. Si algún test importara un nombre privado de un módulo movido, el puente debe
   > reexportarlo de forma explícita.

3. `pytest.ini`: añadir `packages/` al `pythonpath`.
4. Añadir `tests/test_architecture.py` con la primera regla: `packages/baskonia_core/*` no importa
   nada de la raíz ni de `apps/`.

**Gate de salida:**
- `python -m pytest` verde, **sin tocar ningún test** más allá de `pytest.ini`.
- `python main.py --help` y `streamlit run app.py` funcionan.
- `tools/parity_dump.py` produce ficheros **idénticos byte a byte** a la línea base de F0.

**Rollback:** `git revert` del PR. Ninguna otra fase depende de artefactos generados.

---

## Fase F2 — Extraer `core/services/` desde `app.py`

**Objetivo:** sacar de la capa de UI la lógica de negocio que hoy vive ahí. Es la fase que más
valor arquitectónico aporta y la que hace posible que API y Streamlit compartan comportamiento en
vez de duplicarlo.

**Trabajo:**

1. Mover a `packages/baskonia_core/services/`, **sin cambiar el cuerpo de las funciones**:

   | Origen en `app.py` | Destino |
   |---|---|
   | `_team_games`, `past_games`, `upcoming_games`, `games_in_window`, `_result_label`, `_rival_of` | `services/calendar.py` |
   | `current_roster`, `_player_stats_row` | `services/roster.py` |
   | `head_to_head_games` | `services/matchup.py` |
   | `_team_stats_for_game`, la consulta de box score de `boxscore_df` | `services/boxscore.py` |
   | `parse_bbr_date` | `core/dates.py` (lo necesitan servicios y API) |

2. `app.py` los importa desde `core.services` y **borra** sus copias locales. Las funciones `*_df()`
   se quedan en `app.py` reducidas a lo que realmente son: formato sobre el resultado del servicio.
3. `format_date_es`, `_fmt`, `_fmt_pct`, `_streak_label` y los diccionarios `_WEEKDAYS_ES` /
   `_MONTHS_ES` **se quedan en `app.py`**: son presentación y morirán con Streamlit. Su equivalente
   se reescribe en `apps/web/src/lib/format.ts` en F5.
4. Nuevo `tests/test_services.py` con tests directos sobre lo extraído, reutilizando las fixtures
   de `tests/conftest.py`. Esto cubre por primera vez código que hasta ahora **no tenía ningún
   test** (`app.py` no está en la matriz de cobertura del README §6.2).

**Gate de salida:**
- Suite verde, incluida la nueva `test_services.py`.
- **Paridad estricta**: `tools/parity_dump.py` idéntico a la línea base de F0.
- `app.py` ya no contiene ninguna llamada a `session.query(...)`. Verificable con un `grep` en CI.

**Rollback:** `git revert`. No hay estado externo afectado.

---

## Fase F3 — Backend: `apps/api` (FastAPI)

**Objetivo:** que exista una API completa y testeada. **Streamlit sigue siendo la UI y no se toca
en esta fase.** Ambas conviven leyendo la misma BD a través del mismo dominio.

**Trabajo:**

1. Esqueleto: `main.py` (`create_app`), `settings.py`, `deps.py`, `errors.py`, `middleware.py`.
2. Los 19 endpoints del §5.1 de [01_design.md](01_design.md), con sus schemas Pydantic y
   `mappers.py` (fecha BBR → ISO, resultado → `W`/`L`, nulos reales).
3. Alembic + `db/session.py` con WAL, retirando `models._add_missing_columns()`. La primera
   revisión de Alembic se genera con `--autogenerate` contra el esquema actual y se marca como
   aplicada (`alembic stamp head`) en la BD existente: **no se recrean tablas**.
4. `tests/api/` con `TestClient` y `dependency_overrides` sobre SQLite en memoria, sembrada con las
   fixtures ya existentes de `tests/conftest.py`.
5. Congelar el contrato: `openapi.json` versionado en el repo + comprobación en CI de que el
   generado coincide.

**Gate de salida:**
- Los 19 endpoints responden `200` sobre `data/baskonia.db` real.
- `tests/api/` verde; suite completa verde.
- **Paridad numérica API ↔ Streamlit**: para las 4 combinaciones de F0, un segundo script
  `tools/parity_api.py` llama a la API y compara contra la línea base. Diferencias admitidas y
  esperadas **solo** en representación (fecha ISO vs. española, `null` vs. `"-"`, `"W"` vs.
  `"88-79"`); cualquier diferencia **numérica** es un fallo del gate.
- Ninguna respuesta ≥400 fuera del formato `problem+json`.
- La app Streamlit sigue funcionando sin un solo cambio en esta fase.

**Rollback:** `apps/api` es aditivo. Revertir es borrar el directorio; nada del código existente
depende de él. Única excepción: Alembic toca la tabla `alembic_version` de la BD real — hacer
backup previo (`main.py:_backup_database()` ya existe) y documentar la reversión
(`DROP TABLE alembic_version` + restaurar `_add_missing_columns`).

---

## Fase F4 — Separar el pipeline: `apps/ingest`

**Objetivo:** el pipeline de captura pasa a ser una aplicación autónoma con su propio Dockerfile y
sus propias dependencias. Es el requisito explícito del encargo ("separa el pipeline como si fuera
una aplicación aparte").

**Trabajo:**

1. `git mv scraper/ apps/ingest/scraper/`; `git mv main.py apps/ingest/pipeline.py`;
   `git mv report.py apps/ingest/report.py`.
2. Partir `pipeline.py`: el `argparse` y el `if __name__ == "__main__"` salen a
   `apps/ingest/cli.py`; `run()`, `fetch_opponent_scouting()`, `backfill_league()` y sus
   auxiliares se quedan en `pipeline.py` **sin cambios de lógica**.
3. `main.py` en la raíz se convierte en un puente de 3 líneas
   (`from apps.ingest.cli import main; main()`) para que la documentación, el `cron` y la memoria
   muscular sigan funcionando durante la transición.
4. `apps/ingest/requirements.txt` y `apps/api/requirements.txt` separados; el de la raíz queda
   como agregador para desarrollo.
5. Ampliar `tests/test_architecture.py`: `apps/api` no puede importar `requests`,
   `beautifulsoup4` ni `apps.ingest`.
6. `tests/test_main.py` actualiza sus imports a `apps.ingest.pipeline`.

**Gate de salida:**
- `python -m apps.ingest.cli --help` muestra los mismos flags que hoy (`--refresh-teams`,
  `--fix-league`).
- `python main.py --help` (puente) sigue funcionando.
- Suite verde.
- **Prueba de no-regresión del pipeline**: ejecutar el pipeline dos veces seguidas sobre una copia
  de la BD real deja los mismos recuentos de filas en las 5 tablas y la misma distribución de
  `SELECT league, count(*) FROM games GROUP BY league` — el mismo criterio de idempotencia que ya
  usó la feature 002.

**Rollback:** `git revert`. El pipeline es idempotente por diseño, así que una ejecución con código
revertido no corrompe datos.

---

## Fase F5 — Frontend: `apps/web`, pantalla a pantalla

**Objetivo:** construir la SPA. **Streamlit sigue viva y sirviendo a los usuarios durante toda la
fase.**

**Trabajo:**

1. Andamiaje Vite + React + TS + Tailwind; cliente generado desde `openapi.json`; TanStack Query;
   layout con los filtros globales en la query string.
2. Las 4 pantallas **una a una, en este orden**, cada una con su propio gate de paridad:

   | Orden | Pantalla | Por qué en este orden |
   |---|---|---|
   | 1 | Plantilla | La más simple; valida el andamiaje completo (tabla + logos + fotos) con riesgo mínimo |
   | 2 | Partidos anteriores | Tabla + detalle de box score; valida navegación y estado en URL |
   | 3 | Resumen | La más densa (6 endpoints, gráficos, narrativa); ya sobre andamiaje probado |
   | 4 | Próximos enfrentamientos | Depende de proyección, dificultad y h2h — los payloads con más `null` |

3. Componentes reutilizables (`StatTable`, `StatCard`, `TeamLogo`, `SeasonPicker`, `charts/`) en
   vez de repetir construcción de tablas como hace hoy `app.py`.
4. Tests con Vitest + MSW por pantalla.
5. Los tres estados obligatorios por panel: cargando, error (con `request_id`) y **"sin datos
   suficientes"** — este último no es un error y debe verse distinto.

**Gate de salida por pantalla** (no se empieza la siguiente sin cerrar la anterior):
- Los valores mostrados coinciden con los de la pestaña equivalente de Streamlit para las 4
  combinaciones de F0, revisado por una persona con las dos pantallas abiertas en paralelo.
- Recargar en frío la URL con filtros reconstruye la vista completa.
- Tests de la pantalla verdes.

---

## Fase F6 — Informes exportables al backend

**Objetivo:** PDF y PPTX dejan de generarse en la UI y pasan a ser endpoints.

**Trabajo:**

1. Mover `build_pdf_report`, `_pdf_table`, `_pdf_safe`, `build_roster_pptx`, `_fetch_image_bytes` y
   las constantes `_PPT_*` a `apps/api/exports/`.
2. Cambiarles la **fuente de datos**: dejan de recibir `DataFrame` ya formateados y pasan a recibir
   los mismos objetos de dominio que alimentan los endpoints JSON. Es el único cambio de lógica
   real de toda la migración, y es el que impide que el informe y la pantalla diverjan.
3. Exponerlos como endpoints 17 y 18 con `Content-Disposition`.
4. Los botones de descarga de Streamlit pasan a apuntar a la API (`st.link_button`), de modo que
   ambas UIs sirven exactamente el mismo fichero.

**Gate de salida:**
- El PDF y el PPTX generados por la API son equivalentes en contenido a los actuales para las 4
  combinaciones de F0 (comparación de las cadenas de texto extraídas, no del binario: fpdf2 y
  python-pptx incrustan metadatos con marca de tiempo).
- `_fetch_image_bytes` hace peticiones HTTP (fotos de jugadores). Al vivir ahora en `apps/api`, hay
  que **excepcionar explícitamente** esa dependencia en `tests/test_architecture.py` o, mejor,
  cachear las fotos en disco durante la ingesta. Recomendación: cachear en la ingesta y dejar la
  API sin red saliente, cumpliendo la frontera de §2 del diseño sin excepciones.

---

## Fase F7 — Retirada de Streamlit y limpieza

**Objetivo:** eliminar la duplicidad. Solo se ejecuta cuando el usuario confirma que la SPA cubre
su trabajo diario (ver pregunta abierta 3 de [01_design.md](01_design.md)).

**Trabajo:**

1. `git mv app.py legacy/app_streamlit.py`, con una nota de "no mantenido" en cabecera. Se borra
   definitivamente tras una temporada de uso real.
2. Borrar los módulos puente de la raíz (`config.py`, `stats.py`, `insights.py`, `db/`) y el
   `main.py` puente; actualizar `cron`/`systemd` a `apps/ingest/cli.py`.
3. Sacar `streamlit`, `fpdf2`, `python-pptx` y `Pillow` de las dependencias de la raíz (fpdf2,
   python-pptx y Pillow pasan a `apps/api/requirements.txt`).
4. Actualizar README: secciones 2 (Arquitectura), 6 (Cómo ejecutar), 6.1 (Despliegue) y 6.2 (Tests).

**Gate de salida:**
- `grep -r "import streamlit" --include="*.py"` no devuelve nada fuera de `legacy/`.
- Suite verde; los 4 contenedores levantan; el `cron` sigue produciendo datos frescos.

---

## Arnés de paridad (herramienta transversal)

Es el mecanismo que sostiene la promesa de "no romper nada". Dos scripts:

| Script | Qué hace | Se usa en |
|---|---|---|
| `tools/parity_dump.py` | Vuelca a JSON canónico la salida de las funciones de datos actuales | F0 (crear línea base), F1, F2 (comprobar igualdad estricta) |
| `tools/parity_api.py` | Llama a los endpoints equivalentes y compara contra la línea base | F3 en adelante |

**Reglas de comparación en `parity_api.py`** (lo que se admite como diferencia y lo que no):

| Eje | Admitido | Fallo del gate |
|---|---|---|
| Fecha | `"2026-01-15"` vs `"jueves, 15 de enero de 2026"` | fecha distinta |
| Nulo | `null` vs `"-"` / `"n/d"` | `null` donde la línea base tiene un número, o al revés |
| Resultado | `{"team_score":88,"opponent_score":79,"result":"W"}` vs `"88-79"` | marcador distinto |
| Porcentaje | `0.5312` vs `"53,1 %"` | diferencia > 0,0001 tras redondeo |
| Orden de listas | ninguno: el orden debe coincidir | cualquier reordenación (el orden es parte del contrato: `player_recent_form` ordena por `avg_pts` desc, `player_form_zscore` por `z_score_pts` desc, `player_load` por `total_minutes` desc) |
| Nº de elementos | ninguno | cualquier diferencia de cardinalidad |

Ambos scripts corren contra `data/baskonia.db` **en modo solo lectura** y nunca hacen peticiones de
red. Viven en `tools/`, no en `tests/`, porque dependen de la BD real (los tests siguen siendo
herméticos).

## Estado de las dos UIs por fase

| Fase | `python main.py` | `streamlit run app.py` | API | SPA |
|---|---|---|---|---|
| F0 | ✅ sin cambios | ✅ sin cambios | — | — |
| F1 | ✅ vía puente | ✅ vía puente | — | — |
| F2 | ✅ | ✅ (usa `core/services`) | — | — |
| F3 | ✅ | ✅ sin cambios | ✅ en paralelo | — |
| F4 | ✅ vía puente | ✅ | ✅ | — |
| F5 | ✅ | ✅ (aún es la UI oficial) | ✅ | 🔨 en construcción |
| F6 | ✅ | ✅ (descargas ya vía API) | ✅ | ✅ |
| F7 | ✅ ruta nueva | ⛔ retirada | ✅ | ✅ única UI |

**Punto de no retorno: ninguno antes de F7.** Hasta esa fase, abandonar la migración deja el
proyecto en un estado mejor que el actual (dominio limpio + API), nunca peor.

## Criterios de aceptación globales

Además de los gates por fase:

1. **Comportamiento del pipeline idéntico.** Antes de F1 y después de F7, ejecutar el pipeline
   sobre una copia de la BD produce los mismos recuentos por tabla y la misma distribución de
   `Game.league`.
2. **La suite existente nunca se debilita.** Ningún test de los 5 ficheros actuales se borra,
   se marca `xfail` ni se salta. Solo se permiten cambios de rutas de import.
3. **Cobertura neta creciente.** Al terminar F2, existe cobertura de tests sobre código que hoy no
   la tiene (los helpers de `app.py`). Al terminar F3, sobre los 19 endpoints.
4. **Sin red en tests.** Ninguna fase introduce una petición de red en la suite.
5. **Paridad demostrada, no asumida.** Cada gate de paridad queda registrado (fichero de diff
   adjunto al PR de la fase).
6. **Reversibilidad.** Cualquier fase se revierte con `git revert` del PR; la única con efecto
   sobre datos es F3 (Alembic) y tiene su procedimiento de reversión documentado.
7. **Un solo dominio.** Al terminar F7, ninguna regla de negocio existe por duplicado: `grep` de
   los umbrales de racha, del cálculo de resultado W/L y de la etiqueta de temporada devuelve una
   única definición en `packages/baskonia_core`.

## Supuestos y riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| 1 | Los módulos puente se quedan para siempre (deuda permanente) | Comentario obligatorio `# PUENTE DE MIGRACIÓN — eliminar en F7` + test en F7 que falla si queda alguno |
| 2 | F5 (frontend) es la fase más larga y puede quedarse a medias | Streamlit sigue siendo la UI oficial durante toda F5. Un abandono en F5 no deja al usuario sin herramienta |
| 3 | La paridad "a ojo" en F5 es subjetiva | Se apoya en el arnés de F0/F3, que ya garantiza que los **números** de la API son correctos; la revisión humana solo valida la presentación |
| 4 | Alembic aplicado sobre la BD real podría intentar recrear tablas | `alembic stamp head` sobre la BD existente antes de cualquier `upgrade`; backup previo con `_backup_database()` |
| 5 | `from X import *` en los puentes no reexporta nombres privados | Revisar en F1 qué nombres con `_` importan `app.py`/`main.py`/tests desde módulos movidos, y reexportarlos explícitamente |
| 6 | `_fetch_image_bytes` mete red en `apps/api` (F6) | Cachear las fotos de jugadores durante la ingesta; la API queda sin red saliente |
| 7 | Deriva del contrato entre backend y frontend durante F5 | `openapi.json` versionado + cliente TS generado: un cambio incompatible rompe el build del frontend, no la ejecución |
| 8 | La ventana temporal de `player_load` y `upcoming_games` hace el arnés no determinista | Fecha de referencia inyectable en el arnés; si no, comparar esas dos salidas con tolerancia explícita |
| 9 | El `cron` del despliegue apunta a `main.py` y F4 lo mueve | El puente de la raíz mantiene la ruta viva; el `cron` se actualiza en F7, no antes |

## Preguntas abiertas para el usuario

1. **¿Se despliega la API (F3) antes de tener frontend?** Recomendación: **sí**. Levantar el
   contenedor `api` junto al de Streamlit permite validar el contrato con datos reales y con uso
   real (p.ej. desde Postman o un notebook) meses antes de que exista la SPA, con riesgo nulo
   porque es solo lectura.
2. **Orden de las pantallas en F5.** El propuesto minimiza riesgo técnico (de la más simple a la
   más compleja). Si el valor de negocio manda otro orden (p.ej. "Próximos" primero, por ser lo
   que más se usa para preparar partidos), es un cambio sin coste arquitectónico — solo hay que
   asumir que la pantalla más difícil se construye sobre el andamiaje menos rodado.
3. **¿Se conserva `report.py`** (CLI de consulta) tras la migración? No estorba y es útil para
   diagnóstico sin levantar nada. Recomendación: conservarlo en `apps/ingest/`.

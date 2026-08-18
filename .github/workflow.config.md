# Workflow Config — baskonia-pipeline

Adaptador de proyecto para el pipeline agéntico ([AGENTIC_WORKFLOW.md](AGENTIC_WORKFLOW.md)).
**Único fichero que hay que reescribir al portar el pipeline a otro proyecto.**

## Identidad del proyecto

- Producto: PoC de herramienta de scouting para asistentes de entrenador del Baskonia. Pipeline
  de scraping (Basketball-Reference + web oficial baskonia.com) que persiste equipos/jugadores/
  partidos/box scores/estadísticas avanzadas en SQLite, expuestos vía app Streamlit con
  navegación por partidos anteriores, próximos enfrentamientos, plantilla e informes exportables.
- Plataforma / runtime: Python 3 (venv local en `.venv/`), sin compilación — interpretado. UI con
  Streamlit; persistencia con SQLAlchemy sobre SQLite (`data/baskonia.db`).
- Comunicación / interfaces clave: HTTP contra `basketball-reference.com` (scraping HTML,
  `requests`+`BeautifulSoup`) y contra la API pública JSON de `cms.deportivoalaves.com` (CMS
  Strapi que usa baskonia.com). Sin API propia expuesta; todo el consumo es interno (CLI `main.py`
  y la app Streamlit `app.py` leen directamente de la BD vía SQLAlchemy).

## Workspace (estructura de repos / carpetas)

| Repo / carpeta | Rol |
|---|---|
| `scraper/` | Acceso a red: `client.py` (HTTP+rate-limit), `bbr.py` (fetch BBR), `parser.py` (parseo HTML BBR), `baskonia_official.py` (API oficial: próximos partidos + plantilla actual) |
| `db/` | `models.py` (esquema SQLAlchemy: Team, Player, Game, BoxScore, TeamGameStats + `init_db`/migración ligera), `storage.py` (funciones `upsert_*`, idempotentes) |
| `stats.py` | Cálculo puro de estadísticas avanzadas (eFG%, TS%, posesiones, pace, ORtg/DRtg, Net Rating) — sin red ni sesión de BD, solo funciones sobre dicts/valores |
| `insights.py` | Agregados sobre datos ya persistidos vía sesión SQLAlchemy (`player_recent_form`, `team_advanced_summary`, `validate_data`) — sin red |
| `main.py` | Orquestador CLI del pipeline de scraping (`python main.py [--refresh-teams]`) |
| `app.py` | App Streamlit: `get_session()`, funciones de preparación de datos (`*_df`, devuelven `pandas.DataFrame`) y funciones `render_*_tab()` que dibujan cada pestaña; también genera PDF (`build_pdf_report`) y PPTX (`build_roster_pptx`) |
| `report.py` | Export a texto plano por CLI (`--export`), alternativa ligera al PDF de `app.py` |
| `config.py` | Configuración centralizada vía `.env` (rutas, rate-limit, temporada, equipos) |
| `download_logos.py` | Utilidad puntual de descarga de assets, no forma parte del pipeline principal |
| `data/` | `baskonia.db` (SQLite, con datos reales ya cargados) |
| `assets/` | Logos/imágenes estáticas usadas por `app.py` |

No hay submódulos ni monorepo: un solo paquete Python plano en la raíz. **No es un repositorio
git** (no hay `.git/`); no hay convención de commits que seguir, y el pipeline de features no debe
asumir operaciones git salvo que el usuario las pida explícitamente.

## Capas (regla de dependencia, si aplica)

```
scraper/  →  db/  →  stats.py / insights.py  →  app.py (UI Streamlit) / report.py / main.py
```

- `scraper/` no importa nada de `db/`, `stats.py`, `insights.py` ni `app.py` — solo hace HTTP y
  devuelve estructuras (dicts/listas) planas. Es la única capa con red.
- `db/` (modelos + upserts) no depende de `scraper/`; recibe dicts ya parseados. No contiene
  lógica de cálculo.
- `stats.py` es cálculo puro (sin red, sin sesión de BD): recibe valores/dicts, devuelve valores.
  Reusable desde `main.py` (al persistir) o desde `app.py`/`insights.py` (al mostrar).
- `insights.py` agrega sobre datos ya persistidos vía sesión SQLAlchemy (`db.models`); no hace
  peticiones de red y no debe importar `scraper/`.
- `app.py`, `report.py` y `main.py` son las capas "de borde": orquestan sesión + scraper + stats +
  insights para servir CLI, export o UI. Las nuevas funcionalidades de análisis van en
  `stats.py`/`insights.py` (cálculo) y se exponen en `app.py` (presentación), nunca al revés.
- Dominios/capas de trabajo a efectos de paquetes de trabajo (WP) del Architect, ya que este
  proyecto no tiene especialistas de hardware: **scraping** (`scraper/`), **modelo de datos**
  (`db/`), **analítica** (`stats.py`/`insights.py`), **UI Streamlit** (`app.py`), **docs**
  (`README.md`).
- Contrato de retorno / manejo de errores: funciones de cálculo devuelven `None` (u `Optional[...]`)
  ante datos insuficientes en vez de lanzar excepción (ver `stats.py`/`insights.py` — patrón
  `if not fga: return None`). El scraping de la fuente no oficial (`baskonia_official.py`) se
  envuelve en `try/except` amplio en el punto de llamada (`main.py`/`app.py`) para que un fallo de
  esa fuente no tumbe el resto de la app — un cambio de forma en la API de baskonia.com no debe
  romper scouting basado en BBR. No hay un tipo de excepción propio del dominio; se usan las
  excepciones estándar de `requests`/SQLAlchemy tal cual.

## Normas de código

- Fichero normativo: no hay guía de estilo explícita ni linter configurado; la referencia son las
  convenciones ya presentes en el código (`stats.py`, `insights.py`, `db/models.py`,
  `scraper/baskonia_official.py`): type hints en firmas públicas, docstrings en **español** estilo
  Google (`Args:`/`Returns:`/`Raises:`), funciones puras devuelven `Optional[...]`/`None` en vez de
  lanzar, comentarios explicando *por qué* (no *qué*) cuando hay una decisión no obvia (p.ej. bugs
  de BBR ya conocidos, columnas ausentes en la fuente).
- Formato / linters: ninguno instalado (no hay `ruff`/`black`/`flake8` en `requirements.txt`). No
  inventar un linter nuevo; mantener consistencia visual con el código adyacente (4 espacios,
  líneas ~100-110 col, imports agrupados stdlib/terceros/locales).

## Build

```bash
# No hay build binario (proyecto Python interpretado). Verificación mínima tras cualquier cambio:
.venv/Scripts/python.exe -m py_compile <ficheros tocados>
.venv/Scripts/python.exe -c "import app, main, stats, insights, report, config"   # smoke import de todos los módulos de borde
```

Condición de salida: los imports y `py_compile` no lanzan excepción/`SyntaxError`. Para cambios en
`app.py`, verificación adicional manual recomendada (no automatizable sin navegador):
`.venv/Scripts/python.exe -m streamlit run app.py` y comprobar visualmente la pestaña afectada.

## Tests

- Framework: ninguno instalado actualmente (`pytest` no está en `requirements.txt` ni en `.venv`;
  no existe carpeta `tests/`).
- Ubicaciones existentes: n/a — no hay tests previos.
- Patrón: si una feature pide tests (etapa 5), el Tester añade `pytest` a `requirements.txt`, crea
  `tests/` en la raíz y prioriza tests unitarios de las funciones puras de `stats.py`/`insights.py`
  (no requieren red; para las que sí requieren sesión SQLAlchemy, usar SQLite en memoria
  `sqlite:///:memory:` con `db.models.init_db`-style setup, nunca contra `data/baskonia.db` real).
  No inventar un runner distinto de pytest.

## Documentación a mantener

| Qué cambia | Documento a actualizar |
|---|---|
| Cualquier funcionalidad de análisis nueva (sección 7 del README) | `README.md` — mover el checkbox `[ ]`→`[x]` en la subsección correspondiente de la sección 7 y añadir una entrada breve en "Estado actual" si cambia el comportamiento visible de la app |
| Cambios de esquema en `db/models.py` | `README.md` sección 1/7 si se documentan tablas, y comentario docstring del propio modelo |
| Nuevas variables de `.env` | `README.md` (si documenta configuración) y `.env.example` |

## Conocimiento del proyecto (leer antes de diseñar)

1. `README.md` — visión del PoC, estado actual (sección "Estado actual"), decisiones de diseño y
   bugs ya corregidos (evitar reintroducirlos), y sección 7 (roadmap) incluida la 7.3 (alcance de
   esta feature).
2. `stats.py` y `insights.py` — capa analítica existente: estilo docstring, funciones puras vs.
   funciones con sesión, fórmulas ya implementadas (pace/ORtg/DRtg/eFG%/TS%) que las nuevas ideas
   de 7.3 deben reutilizar en vez de reimplementar.
3. `db/models.py` — esquema real disponible (`Team`, `Player`, `Game`, `BoxScore`,
   `TeamGameStats`) antes de proponer cualquier columna o tabla nueva.
4. `app.py` — convenciones de la UI: `get_session()`, funciones `*_df()` que devuelven
   `pandas.DataFrame` y funciones `render_*_tab()` que las pintan; patrón de pestañas
   (`st.tabs`) y de botones de exportación (PDF/PPTX).
5. `scraper/baskonia_official.py` — `fetch_upcoming_games()`, necesaria para la idea de
   "dificultad del próximo tramo de calendario" de 7.3; no se toca su lógica de red, solo se
   consume su salida ya persistida por `main.py` (`persist_schedule`).

## Roster de especialistas (subagentes vía `agent/runSubagent`)

Vacío: este proyecto no tiene agentes de dominio locales en `.github/agents/` (no aplica un roster
tipo HAL/STM32 — es un pipeline Python plano de un solo paquete). El Feature Developer implementa
directamente en las capas relevantes (**scraping**, **modelo de datos**, **analítica**,
**UI Streamlit**, **docs** — ver sección "Capas" arriba), apoyándose en la skill
`karpathy-guidelines` al generar código, sin delegar en subagentes de dominio salvo que el
proyecto crezca y se registren agentes locales.

## Pipelines

- Directorio de features: `local/features/<NNN>-<slug>/`
- Directorio de auditorías: `local/audits/<NNN>-<slug>/`
- Directorio de troubleshooting: `local/troubleshoot/<NNN>-<slug>/`
- Numeración: secuencial de 3 dígitos por directorio (mirar el mayor existente y sumar 1).

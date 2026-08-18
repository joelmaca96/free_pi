# Design: Análisis diferencial 7.3 — 6 ideas de scouting sobre datos ya persistidos

## Contexto y objetivo

El README (sección 7.3, líneas 695-731) lista 6 ideas de análisis pendientes, todas calculables
sobre datos ya persistidos (`boxscores`, `team_game_stats`, calendario en `games`) sin scraping
nuevo. El usuario ya resolvió el alcance: implementar las 6, no un subconjunto (ver
`00_request.md`). Este diseño reparte cada idea en cambios concretos de `stats.py`/`insights.py`
(cálculo) y `app.py` (presentación), reutilizando el 100% del esquema y los patrones ya existentes
(`Optional[...]`/`None`, funciones `*_df()`, `render_*_tab()`).

**Ciclo 2 (esta revisión)**: el gate humano aprobó el diseño del ciclo 1 con un cambio de alcance
explícito: las 6 funcionalidades nuevas deben quedar acotadas a una temporada concreta, no agregar
todo el histórico de la BD (comportamiento que sí seguían teniendo, sin cambios, `player_recent_form`
y `team_advanced_summary`). Se investigó contra `data/baskonia.db` real si "temporada" es derivable
de los datos ya persistidos o requiere esquema nuevo (ver "Diseño → Modelo de temporada"): **es
derivable sin ningún cambio de esquema**, con evidencia concreta de que la BD ya contiene **dos
temporadas simultáneas hoy mismo** (2025-26 jugada y 2026-27 con calendario cargado y 0 partidos
jugados — el escenario real de "pretemporada" que preocupaba al usuario). También se resolvieron
las 3 preguntas abiertas del ciclo 1 (racha con doble z-score PTS+TS%, umbrales de narrativa
confirmados, ventana de carga/fatiga a 14 días) — ver "Diseño" por idea.

Verificado contra `data/baskonia.db` real (no contra el snapshot desactualizado del README, que
dice "8 partidos"): el equipo `vitoria` (Baskonia) tiene 152 partidos guardados, jugadores con hasta
12-13 partidos con box score, y 77 partidos pendientes con rivales ya parcialmente scouteados — hay
datos reales de sobra para ejercitar las 6 ideas de forma significativa. Fecha actual del sistema:
2026-08-18.

**Ciclo 3 (esta revisión) — delta de alcance**: el gate del ciclo 2 aprobó el diseño en general pero
señaló dos ampliaciones puntuales, ninguna cambia una decisión ya cerrada:

- **Ampliación A**: el filtro de temporada debe cubrir *toda* la app, no solo las 4 funciones que el
  gate del ciclo 1 había señalado explícitamente. Se enhebra `season` también en `recent_games_df`,
  `team_summary_df` y `head_to_head_summary_df` (las 3 que quedaron fuera "por omisión, no por
  diseño" en el ciclo 2), y — tras el barrido pedido por el gate sobre el resto de `app.py` — también
  en `head_to_head_games` (función hermana de `head_to_head_summary_df`, hasta ahora sin filtrar,
  ver "Diseño → Ciclo 3"). `upcoming_games` y `validate_data` se mantienen como las únicas
  excepciones, ambas ya razonadas y confirmadas por el usuario.
- **Ampliación B**: se investigó contra `data/baskonia.db` real si `Game.league` es una base fiable
  para un selector de competición. **Resultado: no lo es** para el dato que importa (partidos
  jugados) — ver evidencia completa en "Diseño → Ciclo 3 → Ampliación B" y en "Preguntas abiertas
  para el usuario". No se implementa un selector de competición en este ciclo; se documenta como
  bloqueo real para decisión del usuario, no como una feature descartada por conveniencia.

**Ciclo 4 (esta revisión) — feature retomada, Ampliación B ya diseñada**: la feature
`local/features/002-competicion-real-por-partido/` terminó `stage: done` con veredicto `APPROVED`
(ver `local/features/002-competicion-real-por-partido/03_review.md`) y corrigió la causa raíz que
bloqueaba la Ampliación B en el ciclo 3: `Game.league` ya refleja la competición real de cada
partido, no la liga fija del equipo de origen. Verificado de nuevo en este ciclo, de forma
independiente (no copiado del informe de la 002), contra `data/baskonia.db` real (220 partidos):
de los 139 partidos ya jugados, **101 tienen `league='acb'` y 38 `league='euroleague'`** (antes del
backfill de la 002: 139/0) — coincide exactamente con lo reportado por esa feature. Con el dato ya
fiable, este ciclo diseña el selector de competición que el gate del ciclo 2 pidió y el ciclo 3
dejó bloqueado (ver "Diseño → Ciclo 4"): un segundo selector global, ortogonal al de temporada,
enhebrado por el mismo conjunto de funciones que ya respetan `season` tras la Ampliación A —
incluye una limitación real de datos encontrada durante la investigación de este ciclo (no
introducida por este diseño, preexistente): hoy el 100% de los `team_game_stats`/`boxscores` ya
capturados en la BD (56 y 665 filas respectivamente, de cualquier equipo) son de liga `'acb'`, 0 de
`'euroleague'` — ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos" para el detalle y su
impacto en los criterios de aceptación.

## Alcance

**Dentro:**
- Las 6 ideas de 7.3 completas: rachas (1), perfil de tiro (2), dificultad de calendario (3),
  proyección de partido (4), scouting narrativo (5), carga/fatiga (6) — todas acotadas a una
  temporada seleccionable.
- Capa de "temporada" nueva (derivada, sin columna nueva): `insights.season_start_year`,
  `season_label`, `list_seasons`, `current_season`.
- Selector de temporada en `app.py` (cabecera global, ver "Diseño"), enhebrado a través de las
  funciones existentes que se decide que respeten el filtro (`player_recent_form`,
  `team_advanced_summary`, `past_games` — ver justificación) y de las 6 funciones nuevas.
- Nuevas funciones en `stats.py` (cálculo puro) e `insights.py` (agregación sobre sesión), y su
  exposición en `app.py` dentro de las pestañas ya existentes.
- Actualización mecánica de `README.md` (checkboxes de 7.3 + nota en "Estado actual").
- **(Ciclo 3)** Enhebrar `season` también en `recent_games_df`, `team_summary_df`,
  `head_to_head_summary_df` y `head_to_head_games` (ver "Diseño → Ciclo 3 → Ampliación A"), cerrando
  el hueco que dejó el ciclo 2 en el resto de `app.py`.
- **(Ciclo 4) Selector de competición (Ampliación B, ya no bloqueado)**: segundo selector global en
  la cabecera de `main()`, junto al de temporada — competiciones disponibles (derivadas de
  `Game.league` ya fiable tras la feature 002) + opción "Todas". Se enhebra `league` por el mismo
  conjunto de funciones que ya reciben `season`: las 6 funciones nuevas de 7.3 (con una excepción
  documentada, `player_load`/`games_in_window`, ver "Diseño → Ciclo 4"), `player_recent_form`,
  `team_advanced_summary`, `past_games`, y las 4 funciones de la Ampliación A
  (`_team_games`/`recent_games_df`, `team_summary_df`, `head_to_head_summary_df`,
  `head_to_head_games`). Ver "Diseño → Ciclo 4" para el detalle completo, incluida la extensión de
  `_team_games`/`head_to_head_games` con un segundo parámetro `league` en el mismo punto ya
  centralizado por el ciclo 3.

**Fuera (explícito):**
- PER y cualquier métrica basada en +/- (ya descartadas en el propio README por falta de datos en
  la fuente BBR internacional).
- Cambios de esquema en `db/models.py`: **sigue sin necesitarse ninguna columna ni tabla nueva** —
  "temporada" se deriva de `Game.date` en tiempo de consulta (ver "Diseño"), no se persiste.
- Migración/backfill de datos: no aplica, es consecuencia directa del punto anterior.
- Nueva pestaña de Streamlit: las 6 ideas se integran como subsecciones dentro de las pestañas ya
  existentes, no se crea una pestaña "Análisis" nueva.
- Tocar `config.SEASON` o el flujo de scraping de `main.py`/`scraper/bbr.py`: `config.SEASON` es un
  entero de uso exclusivo para construir URLs de BBR (año de finalización de temporada) y no se
  toca; la "temporada" de esta feature es un concepto de la capa analítica, independiente y sin
  relación de código con `config.SEASON` (ver "Diseño" para la equivalencia conceptual, solo
  documental).
- **Selector de competición — resuelto en ciclo 4, ya no está "fuera"** (histórico: bloqueado en el
  ciclo 3 porque `Game.league` no era fiable — el 100% de los 139 partidos jugados guardaba
  `league='acb'` por asignación fija del equipo de origen; corregido por la feature
  `002-competicion-real-por-partido`, `APPROVED`, backfill idempotente verificado. Ver "Diseño →
  Ciclo 4"). Sigue "fuera" de esta feature, sin cambios respecto al ciclo 3, lo siguiente:
  - Corregir la severidad residual ya documentada por la 002 (riesgo #2 de su diseño): la tabla
    `SPA` de BBR mezcla liga regular ACB con playoffs/Copa bajo el mismo código, así que
    `league='acb'` no distingue sub-fases dentro de ACB — el selector de esta feature distingue
    ACB vs Euroliga vs Supercopa (lo que pedía el usuario), no sub-fases dentro de ACB.
  - Ampliar la captura de `team_game_stats`/`boxscores` para que existan filas reales de liga
    `'euroleague'` (hoy 0 en toda la BD, ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos") —
    es un cambio de alcance de scraping/captura (`main.py`/`config.LAST_N_GAMES` o scouting bajo
    demanda), no de la capa analítica/UI que toca esta feature.
- **(Ciclo 2, actualizado en ciclo 4)** "Temporada" y "competición" son dos selectores **ortogonales
  e independientes**: "temporada" sigue siendo puramente un bucket temporal (fecha del partido, ver
  "Modelo de temporada"), no una distinción por competición — eso es lo que aporta el selector de
  competición del ciclo 4, como filtro adicional aplicado en paralelo, no fusionado dentro del
  concepto de temporada. La razón original de ciclo 2/3 para no fusionarlos ("el dato de origen no
  lo permite hoy de forma fiable") ya no aplica tras la feature 002, pero la decisión de diseño de
  mantenerlos como dos ejes independientes (en vez de una sola "temporada por competición") se
  mantiene por claridad: cada uno tiene su propio selector, su propio `Optional[...]`, y ambos se
  combinan por intersección (AND) en cada función que los recibe.
- Nuevas dependencias de `requirements.txt`: ninguna.

## Módulos y capas afectados

| Fichero | Tipo de cambio | Resumen |
|---|---|---|
| `stats.py` | modificado | + `project_matchup()` (idea 4, función pura, sin cambios por el ciclo 2) |
| `insights.py` | modificado | + capa de temporada (`season_start_year`, `season_label`, `list_seasons`, `current_season`); `player_recent_form()`/`team_advanced_summary()` extendidas con `season: Optional[int] = None` (retrocompatible) + `fg3a_rate`/`ft_rate` (idea 2); + `player_form_zscore()` (idea 1, doble z-score PTS+TS%, `season` requerido); + `schedule_difficulty()` (idea 3, `season` requerido); + `project_next_matchup()` (idea 4, `season` requerido); + `scouting_narrative()` (idea 5, `season` requerido); + `player_load()` (idea 6, sin `season`, ver justificación). **(Ciclo 4)** + capa de competición (`list_leagues`, `league_label`); `player_recent_form()`/`team_advanced_summary()` extendidas además con `league: Optional[str] = None`; `player_form_zscore()`/`project_next_matchup()`/`scouting_narrative()` extendidas con `league: Optional[str] = None`; `schedule_difficulty()` extendida con `league: Optional[str] = None` (filtra los partidos candidatos por competición **antes** de cortar `next_n`, no solo las estadísticas del rival); `player_load()` sin cambios (excepción documentada, ver "Diseño → Ciclo 4") |
| `app.py` | modificado | + selector "Temporada" en cabecera de `main()`; `season` enhebrado en `render_team_tab`, `render_past_games_tab`, `render_upcoming_tab`, `render_roster_tab`, `render_player_card`, `_player_stats_row`, `recent_form_df`, `past_games` (nuevo filtro), `build_pdf_report`, `build_roster_pptx`; `upcoming_games` sin cambios; + `streaks_df()`/`render_streaks_section()` (1, columnas dobles PTS+TS%); columnas `"3PA%"`/`"FTr"` en `recent_form_df` + 2 métricas en `render_player_card` (2); `schedule_difficulty_df()`/`render_schedule_difficulty_section()` (3); `render_matchup_projection_section()` (4); `render_narrative_section()` (5); `games_in_window()` (ventana default 14 días)/`player_load_df()`/`render_player_load_section()` (6). **(Ciclo 3)** `season` enhebrado además en `_team_games`, `recent_games_df`, `team_summary_df`, `head_to_head_summary_df`, `head_to_head_games`, `render_head_to_head_tab` (ver "Diseño → Ciclo 3 → Ampliación A"). **(Ciclo 4)** + selector "Competición" en cabecera de `main()` (junto al de temporada); `league` enhebrado exactamente por los mismos puntos que `season` salvo `player_load`/`games_in_window` (excepción propia, ver "Diseño → Ciclo 4"); `_team_games`/`head_to_head_games` extendidas con `league` (filtro SQL directo sobre `Game.league`, distinto del filtro Python de `season` por ser columna real); renombrada la variable local `season` de `render_player_card` a `season_stats` para evitar sombra de nombre con el parámetro `season` (higiene, ver "Diseño → Ciclo 4") |
| `README.md` | modificado | checkboxes `[ ]`→`[x]` de 7.3 + nota en "Estado actual" (incluye la mención del selector de temporada y, **ciclo 4**, del selector de competición y su limitación de cobertura de datos actual) |

Ningún cambio toca `scraper/`, `db/models.py`, `db/storage.py`, `main.py` ni `report.py`. No hay
`common/` en este proyecto (repo único), así que no hay impacto en código compartido entre repos.

## Diseño

### Decisión estructural: subsecciones vía funciones `render_*_section()`, no una pestaña nueva

`render_team_tab()` ya se reutiliza tal cual para el equipo propio (pestaña "Resumen") y para el
rival scouteado (dentro de "Próximos enfrentamientos", línea `render_team_tab(session, rival,
last_n)`, ahora `render_team_tab(session, rival, last_n, season)`). Añadir las ideas 1, 5 y 6 ahí
las aplica automáticamente a ambos contextos sin duplicar código. Cada idea nueva (salvo la 2, que
es una extensión quirúrgica de una función ya existente) se implementa como una función
`render_*_section(session, team, ...)` propia, con una sola línea de llamada insertada en el
`render_*_tab` anfitrión — mismo patrón que ya usa el código (`render_head_to_head_tab` se llama
igual desde dentro de `render_upcoming_tab`, línea 479).

### Modelo de temporada (decisión estructural del ciclo 2)

**Pregunta de investigación**: ¿"temporada" es derivable de los datos ya persistidos, o hace falta
esquema nuevo? Se investigó `db/models.py` (`Game` no tiene columna `season`; solo `date` en
formato BBR `'%a, %b %d, %Y'` y `league` de texto libre), `config.py` (`SEASON` es un entero — año
de finalización de temporada — usado solo por `scraper/bbr.py` para construir URLs de BBR, sin
relación con la capa analítica) y `scraper/baskonia_official.py` (no distingue "pretemporada" como
concepto propio; solo mapea el nombre de competición de la API a `games.league`, con fallback a
texto libre para lo no mapeado — hoy en la BD real solo aparecen `acb`, `euroleague`, `supercopa`,
ningún valor de tipo "amistoso"/"pretemporada").

**Evidencia contra `data/baskonia.db` real** (220 partidos totales, 152 de `vitoria`):
- Todas las fechas de `Game.date` parsean sin error con `'%a, %b %d, %Y'` (0 filas sin parsear de
  220), incluidas las generadas por `baskonia_official._to_bbr_style_date` (formato con día
  zero-padded, p.ej. `"Sun, Oct 04, 2026"`) y las nativas de BBR (sin zero-pad, p.ej. `"Sun, Nov 23,
  2025"`) — ambas ya las parsea hoy `app.parse_bbr_date` sin distinción.
- **Nunca hay partidos en julio ni agosto** (el hueco real de descanso entre temporadas): los meses
  presentes en toda la tabla son sep-jun. Esto hace que la regla de corte "mes ≥ 7 → la temporada
  empieza ese año natural; mes < 7 → empezó el año natural anterior" sea **inequívoca** — no hay
  ningún partido real cuyo bucket de temporada dependa de dónde se ponga exactamente el corte
  dentro de jul-ago.
- **La BD ya tiene dos temporadas simultáneas hoy**: bucket 2025 ("2025-26", vitoria) = 79 partidos,
  75 jugados, liga `acb` únicamente; bucket 2026 ("2026-27", vitoria) = 73 partidos, **0 jugados**,
  ligas `acb`/`euroleague`/`supercopa` — calendario ya cargado desde
  `baskonia_official.fetch_upcoming_games()` (BBR no publica el calendario de la temporada
  siguiente hasta que empieza, ver docstring del propio scraper). El 100% de las filas con datos
  reales hoy (665 `boxscores`, 56 `team_game_stats`) caen en el bucket 2025 — confirma que el
  escenario "temporada actual con 0 partidos jugados" (la preocupación del usuario, "estamos en
  pretemporada") es **reproducible ahora mismo** contra la BD real, sin necesidad de fechas
  simuladas.

**Opciones comparadas:**

| Opción | Coste | Riesgo | ¿Migración? |
|---|---|---|---|
| (a) Derivar en código a partir de `Game.date` + regla de corte (mes ≥ 7) | 1 función pura (`season_start_year`) + 3 funciones de apoyo (`season_label`, `list_seasons`, `current_season`) | Ninguno detectado: regla de corte validada sin ambigüedad contra el 100% de los datos reales; mismo parseo que ya usa `app.parse_bbr_date` | No |
| (b) Persistir columna `season` en `Game`, con backfill de los partidos ya guardados | Migración de esquema (`_add_missing_columns` ya soporta `ALTER TABLE ADD COLUMN`, pero además requiere un script de backfill que recalcule `season` para las 220 filas existentes) + mantener la columna sincronizada en cada nuevo `upsert_game` de `main.py`/`db/storage.py` (fuera del alcance de esta feature, que no toca `scraper/`/`db/`) | Riesgo de desincronización si un futuro `upsert_game` olvida rellenar `season`; ningún beneficio de rendimiento apreciable a esta escala (220 filas, SQLite) | Sí — y además obligaría a tocar `db/storage.py`/`main.py`, fuera del alcance actual de esta feature |

**Decisión: opción (a), derivar en código.** No hay trade-off real que requiera decisión del
usuario: la opción (b) no aporta ninguna ventaja a esta escala y sí introduce coste/riesgo (fuera de
capas ya no tocadas, sincronización manual) sin beneficio medible. Se documenta como decisión
cerrada, no como pregunta abierta.

**Dónde vive la derivación de temporada (nota sobre capas)**: se añade a `insights.py`, no a
`app.py`. El diseño del ciclo 1 estableció que `insights.py` no debe parsear fechas de
*presentación* (ordenación/filtrado de listas para pintar en la UI, responsabilidad de `app.py`);
pero derivar la temporada es distinto: es una clave de agrupación **estructural** que casi todas las
funciones de `insights.py` necesitan resolver internamente sobre sus propias consultas
SQLAlchemy (`player_recent_form`, `team_advanced_summary`, `player_form_zscore`, etc., cada una hace
su propio `session.query(...)`, no reciben listas ya filtradas de `app.py`). Exigir que `app.py`
pre-filtrase por temporada y pasara listas a cada función de `insights.py` multiplicaría el
acoplamiento entre capas mucho más que la alternativa: una función pura de una línea
(`datetime.strptime` + comparación de mes) duplicada deliberadamente respecto a
`app.parse_bbr_date` — mismo criterio que ya se aplicó en el ciclo 1 a `_rival_of` (WP-3 original:
"duplicado aquí en una línea porque `insights.py` no puede importar de `app.py`").

```python
# insights.py — nuevos imports: from datetime import datetime

def season_start_year(date_str: Optional[str]) -> Optional[int]:
    """Deriva el año de inicio de la temporada europea a partir de la fecha
    de un partido en formato BBR ('%a, %b %d, %Y').

    Una temporada europea cruza el año natural (p.ej. 2025-26 va de
    septiembre de 2025 a junio de 2026); el año de la fecha del partido NO
    equivale a "temporada" si el partido es de enero-junio. Regla de corte:
    mes >= 7 -> la temporada empieza ese año natural; mes < 7 -> empezó el
    año natural anterior. Verificado sin ambigüedad contra
    `data/baskonia.db` real: nunca hay partidos en julio/agosto (descanso
    real entre temporadas), así que ningún partido depende de dónde se
    ponga el corte dentro de ese hueco.

    Duplica deliberadamente el parseo de `app.parse_bbr_date` (mismo
    formato): `insights.py` no puede importar de `app.py` (regla de capas)
    y esta derivación es estructural para el filtrado por temporada de las
    funciones de agregación, no una cuestión de presentación.

    Returns:
        Año de inicio de temporada (p.ej. 2025 para "2025-26"), o `None`
        si `date_str` no se puede parsear.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%a, %b %d, %Y")
    except (ValueError, TypeError):
        return None
    return dt.year if dt.month >= 7 else dt.year - 1


def season_label(season: Optional[int]) -> str:
    """Formatea un año de inicio de temporada como 'AAAA-AA' (2025 -> '2025-26'), o '-' si `None`."""
    if season is None:
        return "-"
    return f"{season}-{str(season + 1)[-2:]}"


def list_seasons(session, team: "models.Team") -> List[int]:
    """Temporadas con al menos un partido guardado (jugado o pendiente) de `team`.

    Returns:
        Años de inicio de temporada, ordenados descendente (más reciente/
        futura primero). Vacía si el equipo no tiene ningún partido.
    """
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    seasons = {season_start_year(g.date) for g in games}
    seasons.discard(None)
    return sorted(seasons, reverse=True)


def current_season(session, team: "models.Team", reference_date: Optional[datetime] = None) -> Optional[int]:
    """Temporada a preseleccionar por defecto en la UI.

    Prioriza la temporada de `reference_date` (por defecto ahora) SI ya
    tiene algún partido jugado (algún `TeamGameStats` de `team` dentro de
    ese bucket); si no (descanso de temporada real, caso 2026-08-18: la
    temporada 2026-27 ya tiene calendario pero 0 partidos jugados), cae a
    la temporada más reciente con al menos un partido jugado, para no abrir
    la app con paneles vacíos por defecto. El usuario puede elegir
    explícitamente la temporada vacía desde el selector si quiere verla.

    Returns:
        Año de inicio de temporada, o `None` si el equipo no tiene ningún
        partido guardado en absoluto (BD recién inicializada).
    """
    seasons = list_seasons(session, team)
    if not seasons:
        return None
    reference_date = reference_date or datetime.now()
    ref_season = season_start_year(reference_date.strftime("%a, %b %d, %Y"))
    stats_rows = (
        session.query(models.TeamGameStats)
        .join(models.Game, models.TeamGameStats.game_id == models.Game.id)
        .filter(models.TeamGameStats.team_id == team.id)
        .all()
    )
    played_seasons = {season_start_year(r.game.date) for r in stats_rows}
    played_seasons.discard(None)
    if ref_season in played_seasons:
        return ref_season
    if played_seasons:
        return max(played_seasons)
    return ref_season if ref_season in seasons else seasons[0]
```

**Extensión retrocompatible de `player_recent_form`/`team_advanced_summary`** (mismo fichero,
añadir `season: Optional[int] = None` — `None` preserva el comportamiento exacto de hoy,
"todo el histórico", así que ningún consumidor existente que no pase `season` se rompe):

```python
def player_recent_form(session, team: "models.Team", last_n: int = 5, season: Optional[int] = None) -> List[Dict[str, object]]:
    rows = (
        session.query(models.BoxScore)
        .join(models.Game, models.BoxScore.game_id == models.Game.id)
        .filter(models.BoxScore.team_id == team.id)
        .order_by(models.Game.id.desc())
        .all()
    )
    if season is not None:
        rows = [r for r in rows if season_start_year(r.game.date) == season]
    # ... resto sin cambios (agrupar por jugador, slice [:last_n] YA DENTRO de la temporada)
```

```python
def team_advanced_summary(session, team: "models.Team", season: Optional[int] = None) -> Dict[str, Optional[float]]:
    stats_rows = session.query(models.TeamGameStats).join(models.Game, models.TeamGameStats.game_id == models.Game.id).filter(models.TeamGameStats.team_id == team.id).all()
    box_rows = session.query(models.BoxScore).join(models.Game, models.BoxScore.game_id == models.Game.id).filter(models.BoxScore.team_id == team.id).all()
    if season is not None:
        stats_rows = [r for r in stats_rows if season_start_year(r.game.date) == season]
        box_rows = [r for r in box_rows if season_start_year(r.game.date) == season]
    # ... resto sin cambios
```

Efecto colateral positivo: "Forma reciente (últimos N partidos jugados)" ya NO puede mezclar
temporadas cuando se le pasa `season` (antes sí podía, con `last_n` grande cerca del cambio de
temporada — el riesgo que el usuario señaló). También permite **eliminar el hack** de
`render_player_card` (`_player_stats_row(..., last_n=1000)` para aproximar "toda la temporada"): con
`season` explícito, `last_n=1000` sigue siendo un límite práctico "sin techo real" pero ahora
correctamente acotado a una sola temporada.

**Funciones existentes que se decide NO acotar por temporada** (documentado, no un olvido; lista
revisada en el ciclo 3 — ver más abajo qué entró y qué se mantiene fuera):
- `upcoming_games` (`app.py`): es el calendario pendiente — su propósito es "qué toca jugar a
  continuación", independiente de qué temporada esté seleccionada para ver estadísticas
  retrospectivas. Acotarla al selector global haría que, al elegir una temporada ya cerrada, la
  pestaña "Próximos enfrentamientos" se quedara sin nada que mostrar — friction sin beneficio.
  Confirmado por el usuario en el gate del ciclo 2, sigue vigente sin cambios en el ciclo 3.
- `validate_data`: es una herramienta de calidad de datos transversal a toda la BD, no una vista de
  scouting — se deja sin acotar por diseño.
- `player_load`/`games_in_window` (idea 6): ver razonamiento en la sección de la idea 6 — no
  necesitan `season` porque una ventana de días (máx. 30) nunca puede cruzar el hueco real de ~3
  meses entre temporadas.
- `boxscore_df`: no es una agregación sobre varios partidos — pinta el box score de **un** partido
  ya elegido por el usuario (vía `past_games`/`upcoming_games`/`head_to_head_games`, cuya lista de
  elección ya respeta o excluye `season` según corresponda). No hay nada que acotar en la propia
  función.
- `current_roster`: plantilla actual de jugadores (foto, dorsal, posición) — no es una vista de
  rendimiento sobre partidos, es "quién está en el equipo ahora"; no aplica un concepto de
  temporada.

**(Ciclo 3)** `recent_games_df`/`_team_games`, `team_summary_df`, `head_to_head_summary_df` **salen
de esta lista de excepciones**: el gate de este ciclo no acepta dejarlas fuera solo porque el gate
del ciclo 1 no las nombrara explícitamente. Se enhebra `season` en las tres (más `head_to_head_games`,
encontrada en el barrido pedido por el gate — ver "Ciclo 3 → Ampliación A" más abajo). Las únicas
excepciones que sobreviven al ciclo 3 son las cinco de la lista de arriba, cada una con un motivo
distinto a "no estaba en la lista original".

**Selector de temporada en `app.py`** (cabecera global de `main()`, no por pestaña — mismo criterio
que `last_n`, que ya es global): se añade una columna nueva junto a `header_n_col` (líneas 820-826
actuales) con
`st.selectbox("Temporada", options=insights.list_seasons(session, baskonia), format_func=insights.season_label, index=<posición de current_season>, key="season_selector")`.
Si `list_seasons` devuelve `[]` (BD sin ningún partido, caso solo posible en una BD recién
inicializada), se muestra `st.info("Sin temporadas con partidos registrados. Ejecuta `python
main.py`.")` y se omiten las subsecciones dependientes de temporada (guard equivalente al ya
existente `if baskonia is None`). El valor `season` resultante se pasa como parámetro nuevo a:
`render_team_tab`, `render_past_games_tab`, `render_upcoming_tab`, `render_roster_tab` (y de ahí a
`render_player_card`/`_player_stats_row`), y a `build_pdf_report`/`build_roster_pptx` allí donde ya
se invocan dentro de esas funciones. **Interacción con los widgets ya diseñados**: ninguna — `next_n`
(idea 3, calendario futuro) y `load_window` (idea 6, ventana de días) operan sobre datos que por
construcción no pueden cruzar el límite de una temporada real (calendario futuro / ventana ≤ 30
días vs. hueco real de ~3 meses entre temporadas), así que coexisten sin lógica de interacción
adicional.

### Idea 1 — Detector de rachas (hot/cold streaks)

**Resuelto por el usuario**: doble z-score — volumen (PTS) **y** eficiencia. Verificado contra
`boxscores` de `vitoria`: `efg_pct` y `ts_pct` tienen exactamente la misma cobertura por jugador
(128/141 filas no nulas cada una, idéntica fila a fila — ambas se calculan juntas en `stats.py` a
partir de las mismas columnas fg/fg3/ft). Sin diferencia de cobertura real, se prioriza **TS%**
como métrica de eficiencia: es la fórmula más completa (pondera tiros libres además de tiro de
campo, a diferencia de eFG%), y el z-score de "racha" debe reflejar eficiencia real de anotación,
no solo selección de tiro. Resultado: **dos columnas de z-score**, no una.

**`insights.py`** — constantes + función (`season` requerido, no `Optional`: el objetivo explícito
de esta idea es acotar la racha a la temporada seleccionada, un default silencioso "todo el
histórico" reintroduciría justo el problema que el gate señaló):

```python
ZSCORE_HOT_THRESHOLD = 1.0
ZSCORE_COLD_THRESHOLD = -1.0


def player_form_zscore(
    session, team: "models.Team", season: int, recent_n: int = 5, min_season_games: int = 6
) -> List[Dict[str, object]]:
    """Detecta rachas dentro de una temporada: z-score de los últimos
    `recent_n` partidos jugados de cada jugador frente a la media/
    desviación de todos sus partidos con minutos registrados **de esa
    temporada** (no de todo el histórico) — dos métricas independientes:
    volumen anotador (PTS) y eficiencia de tiro (TS%).

    Sigue el mismo criterio de recencia que `player_recent_form` (orden por
    `Game.id` descendente entre los partidos de la temporada indicada).

    Un jugador se omite del todo si tiene menos de `min_season_games`
    partidos con minutos registrados en la temporada. El z-score de TS%
    (`z_score_ts`) se calcula de forma independiente al de PTS
    (`z_score_pts`) y puede ser `None` para un jugador aunque `z_score_pts`
    no lo sea (si tiene menos de `min_season_games` partidos con `ts_pct`
    no nulo, o su desviación de TS% en temporada es 0).

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo cuyos jugadores se analizan.
        season: año de inicio de temporada (ver `season_start_year`); los
            partidos fuera de esta temporada no se consideran ni para la
            racha ni para la media/desviación base.
        recent_n: nº de partidos recientes de la "racha" (3-5 recomendado).
        min_season_games: mínimo de partidos para una desviación de
            temporada mínimamente estable (aplicado por separado a PTS y a
            TS%, ya que la cobertura de `ts_pct` puede ser algo menor que
            la de partidos con minutos).

    Returns:
        Lista de dicts con player_name, games_season, recent_avg_pts,
        season_avg_pts, season_std_pts, z_score_pts, recent_avg_ts_pct,
        season_avg_ts_pct, season_std_ts_pct, z_score_ts — ordenada por
        z_score_pts descendente (métrica principal de "racha"). Vacía si
        nadie cumple el mínimo de partidos en esa temporada (incluida una
        temporada sin ningún partido jugado todavía).
    """
```

Implementación: mismo patrón que la versión del ciclo 1, con dos cambios: (1) filtrar
`player_rows` a `season_start_year(row.game.date) == season` antes de agrupar/cortar; (2) calcular
`z_score_pts` (sobre `points`, minutos válidos vía `parse_minutes`) y `z_score_ts` (sobre `ts_pct`,
filtrando además `ts_pct is not None`) de forma independiente, cada uno con su propio
`mean`/`pstdev` manual (`sum()/len()`, sin `statistics`, mismo estilo que el resto del fichero).

**`app.py`**:

```python
def streaks_df(session, team: models.Team, season: int, recent_n: int) -> pd.DataFrame: ...

def render_streaks_section(session, team: models.Team, season: int, recent_n: int) -> None:
    """Subsección 'Rachas (hot/cold)': tabla con doble z-score (PTS y TS%) por jugador."""
```

`streaks_df` mapea cada fila a `Jugador`, `PJ temporada`, `PTS últimos N`, `PTS temporada`,
`z-score PTS`, `Racha PTS`, `TS% últimos N`, `TS% temporada`, `z-score TS%`, `Racha TS%` — las
columnas `Racha *` usan `"🔥 en racha"`/`"❄️ bajo forma"`/`"➖"` según el z-score correspondiente
frente a `insights.ZSCORE_HOT_THRESHOLD`/`COLD_THRESHOLD` (mismo umbral para ambas métricas, no se
pidió uno distinto por métrica). Se mantienen dos columnas de racha separadas (PTS y TS%) en vez de
fusionarlas en una sola etiqueta combinada, para no inventar una regla de combinación no pedida.
`render_streaks_section` pinta `st.subheader("Rachas (hot/cold)")`, llama a `streaks_df` y usa
`st.dataframe(...)`; si vacío, `st.info(f"Sin jugadores con partidos suficientes en la temporada
{insights.season_label(season)} para calcular racha todavía.")`.

**Inserción**: `render_streaks_section(session, team, season, last_n)` dentro de `render_team_tab`,
justo después del bloque de "Forma reciente" (referencia actual: después de la línea 334; verificar
tras aplicar WP-0b, que solo alarga líneas existentes sin añadir/quitar, así que el punto de
inserción no debería desplazarse).

### Idea 2 — Perfil / selección de tiro

**`insights.py`** — extensión de `player_recent_form` (misma firma pública ya extendida con
`season` en la sección anterior; se añaden 2 claves más al dict de cada jugador, sin romper a los
consumidores existentes):

```python
fg3a_total = sum(row.fg3_attempted or 0 for row, _ in played)
fga_total = sum(row.fg_attempted or 0 for row, _ in played)
fta_total = sum(row.ft_attempted or 0 for row, _ in played)
fg3a_rate = fg3a_total / fga_total if fga_total else None   # % de intentos que son de 3
ft_rate = fta_total / fga_total if fga_total else None      # tasa de tiros libres (FTA/FGA, no % de acierto)
```

Añadir `"fg3a_rate": fg3a_rate, "ft_rate": ft_rate` al dict que ya construye la función (sobre el
conjunto `played` ya acotado por `season` si se pasó), y actualizar el docstring `Returns:`. Los
ratios se calculan sobre la **suma** de intentos (criterio estándar de "shot profile", evita
división por cero en partidos individuales sin tiros de campo).

**`app.py`**:
- `recent_form_df()` (ahora con parámetro `season`): añadir columnas `"3PA%"`
  (=`fg3a_rate * 100` redondeado, o `None`) y `"FTr"` (=`ft_rate` redondeado a 2 decimales, o
  `None`).
- `render_player_card()`: en las dos filas de métricas ya existentes (`recent` y `season`), pasar de
  5 a 7 columnas y añadir `m6.metric("3PA%", _fmt_pct(recent["fg3a_rate"]))` y `m7.metric("FTr",
  _fmt(recent["ft_rate"]))` (mismo patrón para la fila `season`, que ahora usa
  `_player_stats_row(session, team, player, last_n=1000, season=season)` en vez del hack de solo
  `last_n=1000` — ver "Modelo de temporada").

No requiere ninguna sección nueva ni widget nuevo: se integra en tablas/métricas ya existentes.

### Idea 3 — Dificultad del próximo tramo de calendario

**`insights.py`** (`season` requerido: es la temporada cuyo Net Rating se usa para valorar la forma
actual de cada rival — la misma temporada seleccionada globalmente, no una temporada distinta "del
rival"; si el usuario quiere ver la dificultad basada en la temporada anterior de los rivales,
cambia el selector global, igual que para el resto de la app):

```python
def schedule_difficulty(
    session, team: "models.Team", upcoming_games: List["models.Game"], season: int, next_n: int = 5
) -> Dict[str, object]:
    """Dificultad del próximo tramo de calendario: media del Net Rating de
    los próximos `next_n` rivales, calculado sobre la temporada `season` de
    cada rival (ver `team_advanced_summary(session, opponent, season=season)`).

    Recibe `upcoming_games` ya resuelto por el llamador (ver
    `app.upcoming_games`, que NO se acota por temporada — es calendario
    pendiente, ver "Modelo de temporada") en vez de reimplementar el
    parseo/ordenación de fechas de BBR aquí (regla de capas: `insights.py`
    no depende de `app.py`).

    Args:
        session: sesión SQLAlchemy activa.
        team: equipo de referencia (para resolver quién es "el rival" en
            cada partido de `upcoming_games`).
        upcoming_games: partidos pendientes de `team`, mismo orden que
            devuelve `app.upcoming_games`.
        season: temporada cuyo Net Rating de cada rival se usa como proxy
            de forma actual (normalmente la temporada más reciente con
            datos, resuelta por `current_season`).
        next_n: nº de próximos rivales a considerar.

    Returns:
        Dict (nunca `None`) con games_considered, opponents_scouted,
        avg_opponent_net_rating (`None` si ningún rival tiene Net Rating en
        `season`), opponents (lista de {opponent_name, date, net_rating}).
    """
```

Implementación: igual que el ciclo 1 (`next_games = upcoming_games[:next_n]`; rival = `game.away_team
if game.home_team_id == team.id else game.home_team`), cambiando `team_advanced_summary(session,
opponent)` por `team_advanced_summary(session, opponent, season=season)`.

**`app.py`**: `schedule_difficulty_df(session, team, season, next_n)` /
`render_schedule_difficulty_section(session, team, season, upcoming)` — mismo diseño que el ciclo 1
(widget `next_n` sin cambios, sin interacción con el selector de temporada más allá de pasarlo como
argumento). Inserción sin cambios: en `render_upcoming_tab`, después del guard `if not games` (línea
~436) y antes del `selectbox` de rival.

### Idea 4 — Proyección simple del próximo partido

**`stats.py`** — sin cambios respecto al ciclo 1 (función pura, recibe los 6 valores ya resueltos,
no conoce el concepto de temporada):

```python
def project_matchup(
    team_pace, team_off_rating, team_def_rating, opp_pace, opp_off_rating, opp_def_rating,
) -> Optional[Dict[str, float]]:
    """Proyecta posesiones y marcador esperado combinando pace y ORtg/DRtg
    medios de temporada de cada equipo. Devuelve None si falta cualquiera
    de los 6 valores."""
    values = (team_pace, team_off_rating, team_def_rating, opp_pace, opp_off_rating, opp_def_rating)
    if any(v is None for v in values):
        return None
    projected_possessions = (team_pace + opp_pace) / 2
    team_rating = (team_off_rating + opp_def_rating) / 2
    opp_rating = (opp_off_rating + team_def_rating) / 2
    return {
        "projected_possessions": projected_possessions,
        "team_projected_rating": team_rating,
        "opp_projected_rating": opp_rating,
        "team_projected_score": team_rating * projected_possessions / 100,
        "opp_projected_score": opp_rating * projected_possessions / 100,
    }
```

**`insights.py`** (`season` requerido, aplicado a ambos equipos):

```python
def project_next_matchup(
    session, team: "models.Team", opponent: "models.Team", season: int
) -> Optional[Dict[str, float]]:
    """Proyecta el marcador esperado entre `team` y `opponent` combinando
    sus medias de la temporada `season` (`team_advanced_summary(...,
    season=season)`) vía `stats.project_matchup`.

    Returns:
        Igual que `stats.project_matchup`, o `None` si a alguno de los dos
        equipos le falta pace/ORtg/DRtg en esa temporada (incluye el caso
        de una temporada sin ningún partido jugado todavía).
    """
    team_summary = team_advanced_summary(session, team, season=season)
    opp_summary = team_advanced_summary(session, opponent, season=season)
    return project_matchup(
        team_summary["avg_pace"], team_summary["avg_off_rating"], team_summary["avg_def_rating"],
        opp_summary["avg_pace"], opp_summary["avg_off_rating"], opp_summary["avg_def_rating"],
    )
```

**`app.py`**: `render_matchup_projection_section(session, team, rival, season)` — mismo diseño que
el ciclo 1 (3 `st.metric`, o `st.info` si `None`). Inserción sin cambios: después del guard
`if not has_roster` (línea ~473) y antes de `st.subheader(f"Scouting: {rival.name}")`.

### Idea 5 — Scouting narrativo automático

**`insights.py`** (`season` requerido; combina resultados de las ideas 1 y 2, todas ya acotadas a
`season`):

```python
_NARRATIVE_PACE_FAST = 75.0
_NARRATIVE_PACE_SLOW = 68.0
_NARRATIVE_FG3A_RATE_HIGH = 0.40
_NARRATIVE_FG3A_RATE_LOW = 0.25
```

Umbrales confirmados sin cambios por el usuario ("aceptados tal cual"). Igualmente se conserva
`ZSCORE_HOT_THRESHOLD`/`COLD_THRESHOLD` (±1.0) para la frase de racha.

```python
def scouting_narrative(session, team: "models.Team", season: int, recent_n: int = 5) -> Optional[str]:
    """Genera un resumen narrativo en español sobre el estilo de un equipo
    en una temporada concreta, combinando pace/ratings, perfil de tiro y
    forma de temporada (`player_recent_form(..., season=season)`) y rachas
    (`player_form_zscore(..., season=season)`).

    Returns:
        Párrafo de 3-5 frases, o `None` si el equipo no tiene ningún
        partido con estadísticas avanzadas guardado **en esa temporada**
        (nada que resumir — incluye el caso de temporada sin jugar
        todavía).
    """
```

Lógica (sin cambios de estructura respecto al ciclo 1, salvo enhebrar `season` en cada llamada):
1. `summary = team_advanced_summary(session, team, season=season)`; si `avg_pace is None`: `return
   None`.
2. Frase de ritmo (umbrales `_NARRATIVE_PACE_*`).
3. Frase de balance (`avg_net_rating`).
4. `form = player_recent_form(session, team, last_n=1000, season=season)`; frase de perfil de tiro
   con la media de `fg3a_rate` (umbrales `_NARRATIVE_FG3A_RATE_*`).
5. Máximo anotador de `form`.
6. `streaks = player_form_zscore(session, team, season=season, recent_n=recent_n)`; frase de racha
   usando **`z_score_pts`** como señal principal (volumen; la tabla detallada de rachas ya muestra
   ambas métricas, la narrativa usa una sola frase con la más directa de interpretar).
7. Unir las frases no vacías.

**`app.py`**: `render_narrative_section(session, team, season, recent_n)` — sin cambios de diseño
respecto al ciclo 1. Inserción sin cambios: después del bloque `logo_col/title_col/pdf_col` (línea
~292) y antes de "Estadísticas avanzadas (medias)".

### Idea 6 — Gestión de carga/fatiga (minutos por ventana de días)

**Resuelto por el usuario**: ventana por defecto **14 días** (no 7), widget ajustable 1-30 sin
cambios respecto al ciclo 1.

**Por qué esta idea NO necesita `season`**: la ventana es un rango de **días reales**, acotado por
el propio widget a 1-30. El hueco real entre temporadas en la BD es de ~3 meses (jun→sep, ver
"Modelo de temporada"), muy superior al máximo de 30 días del widget — una ventana de carga no
puede cruzar dos temporadas con datos reales de este proyecto. Añadir `season` aquí sería
complejidad sin beneficio observable; se documenta como decisión explícita, no como omisión.

**`app.py`** — sin cambios respecto al ciclo 1 salvo el valor por defecto:

```python
def games_in_window(
    session, team: models.Team, window_days: int, reference_date: "datetime | None" = None
) -> list:
    """Partidos ya JUGADOS de `team` dentro de los últimos `window_days`
    días respecto a `reference_date` (por defecto, ahora)."""
    reference_date = reference_date or datetime.now()
    cutoff = reference_date - timedelta(days=window_days)
    return [
        g for g in past_games(session, team)
        if cutoff <= (parse_bbr_date(g.date) or datetime.min) <= reference_date
    ]
```

Nota: `past_games(session, team)` dentro de `games_in_window` se llama **sin** `season` (todo el
histórico), a propósito: la ventana de días ya es su propio filtro temporal más preciso: acotarla
además por temporada sería redundante y podría introducir un borde falso si `window_days` se
ampliara en el futuro más allá de 30. Requiere `timedelta` añadido al `from datetime import
datetime` ya existente en `app.py` (línea 20).

**`insights.py`** — sin cambios respecto al ciclo 1 (`player_load`, agregación pura sobre la lista
ya filtrada, sin `season`, sin parseo de fechas).

**`app.py`** (continuación): `player_load_df(session, team, window_days)` /
`render_player_load_section(session, team)` — `st.number_input("Ventana de días", min_value=1,
max_value=30, value=14, key=f"load_window_{team.id}")` (default cambiado de 7 a 14). Resto sin
cambios. Inserción sin cambios: al final de `render_team_tab`.

### Ciclo 3 — Ampliación A: filtro de temporada en el resto de `app.py`

**Motivo**: el gate de este ciclo no acepta que `recent_games_df`, `team_summary_df` y
`head_to_head_summary_df` sigan fuera del filtro solo porque el gate del ciclo 1 no las nombrara
explícitamente. Se enhebra `season` en las tres, y el barrido pedido sobre el resto de `app.py`
encuentra una cuarta función en la misma situación no detectada en el ciclo 2:
`head_to_head_games` (usada por `render_head_to_head_tab` — subsección "últimos N enfrentamientos
directos" dentro de `render_upcoming_tab` — y directamente por `build_pdf_report` para el bloque
"Enfrentamientos directos" del PDF, con el histórico completo sin límite de N). Es la hermana
"detallada" (partido a partido, con box score) de `head_to_head_summary_df` (agregada, una fila por
partido); dejar una acotada por temporada y la otra no sería una inconsistencia dentro de la propia
app — exactamente el tipo de hueco que el gate pidió cerrar. El resto del barrido (`boxscore_df`,
`current_roster`, `validate_data`, `player_load`/`games_in_window`) no cambia — ver motivos
actualizados en "Funciones existentes que se decide NO acotar por temporada", arriba.

**Decisión de implementación — centralizar el filtro en `_team_games`**: las tres funciones
(`recent_games_df`, `team_summary_df`, `head_to_head_summary_df`) son los únicos tres consumidores
de `_team_games` en todo `app.py` (verificado, no hay otros call sites). En vez de repetir el
filtro de temporada en cada una, se añade `season` directamente a `_team_games`, que ya es el punto
único de acceso a "todos los partidos de un equipo":

```python
def _team_games(session, team: models.Team, season: "int | None" = None):
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    if season is not None:
        games = [g for g in games if insights.season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games
```

Requiere importar `season_start_year` desde `insights` en `app.py` (ya se añade `season_label`/
`list_seasons`/`current_season` a ese mismo `import` en WP-0a/WP-0b; se añade `season_start_year`
al mismo `from insights import ...`). `season=None` preserva exactamente el comportamiento actual
(todo el histórico), igual que en `player_recent_form`/`team_advanced_summary` — ningún consumidor
que no pase `season` se rompe.

Con `_team_games` ya filtrando, las tres funciones solo necesitan propagar el parámetro:

```python
def team_summary_df(session, team: models.Team, season: "int | None" = None) -> pd.DataFrame:
    """Tabla de todos los partidos guardados de un equipo (jugados y pendientes) en `season`
    (todo el histórico si `season` es `None`)."""
    return _games_to_df(session, _team_games(session, team, season), team)


def recent_games_df(session, team: models.Team, last_n: int, season: "int | None" = None) -> pd.DataFrame:
    """Tabla de los últimos `last_n` partidos JUGADOS de un equipo dentro de `season`."""
    played = [g for g in _team_games(session, team, season) if g.home_score is not None]
    return _games_to_df(session, played[-last_n:], team)


def head_to_head_summary_df(session, team: models.Team, season: "int | None" = None) -> pd.DataFrame:
    """Tabla de los partidos jugados contra los otros equipos de `config.TEAMS`, dentro de `season`."""
    rival_slugs = {slug for slug in config.TEAMS if slug != team.slug}
    played = [g for g in _team_games(session, team, season) if g.home_score is not None]
    games = [g for g in played if _rival_of(g, team).slug in rival_slugs]
    return _games_to_df(session, games, team)
```

Nota sobre `team_summary_df`: al no distinguir jugado/pendiente, un partido pendiente cuya fecha cae
dentro de la temporada seleccionada (p.ej. un aplazado real de esa temporada, o un partido de la
propia temporada aún no llegado si se selecciona la temporada en curso) **sigue apareciendo** — el
filtro es por bucket temporal (fecha), no por si ya se jugó; esto es intencional y coherente con
"temporada" como concepto puramente temporal (ver "Modelo de temporada"), y no entra en conflicto
con la excepción de `upcoming_games` (que es una vista distinta, con propósito de navegación al
próximo partido, no una tabla de resumen).

**`head_to_head_games`** (nueva pieza detectada en el barrido, no prevista en ciclo 1/2):

```python
def head_to_head_games(session, team_a: models.Team, team_b: models.Team, season: "int | None" = None):
    games = (
        session.query(models.Game)
        .filter(
            ((models.Game.home_team_id == team_a.id) & (models.Game.away_team_id == team_b.id))
            | ((models.Game.home_team_id == team_b.id) & (models.Game.away_team_id == team_a.id))
        )
        .all()
    )
    if season is not None:
        games = [g for g in games if insights.season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games
```

`render_head_to_head_tab(session, team_a, team_b, last_n=None, season=None)` pasa `season` a
`head_to_head_games` sin más cambios de lógica interna. Llamadores actualizados:
- `render_upcoming_tab`: `render_head_to_head_tab(session, team, rival, last_n=H2H_LAST_N,
  season=season)`.
- `build_pdf_report`: `games = head_to_head_games(session, team_a, team_b, season)` (sustituye la
  llamada sin argumento de temporada).

Nota de comportamiento pre-existente, no introducida por este cambio: si la temporada seleccionada
tiene enfrentamientos directos *pendientes* (p.ej. `season=2026` entre vitoria/bilbao tiene 2
partidos ya programados sin jugar), `render_head_to_head_tab` los pinta con `None` en el marcador
(`game.home_score`/`game.away_score`) y un box score vacío — comportamiento ya existente hoy (la
función nunca filtró por jugado/pendiente, con o sin `season`); acotar por temporada no lo empeora,
de hecho reduce cuántos de estos casos se ven a la vez. No se corrige en este ciclo (fuera del pedido
del gate); se deja anotado en "Supuestos y riesgos".

**`build_pdf_report`/`render_team_tab`**: los call sites ya previstos en el ciclo 2 (que reciben
`season` como parámetro nuevo) pasan a invocar las versiones con `season` de las tres funciones:
`recent_games_df(session, team, last_n, season)`, `head_to_head_summary_df(session, team, season)`
dentro de `render_team_tab`; `team_summary_df(session, team, season)` dentro de `build_pdf_report`
(sección "Partidos guardados" del informe).

### Ciclo 3 — Ampliación B: filtro de competición (bloqueado en su momento — ver Ciclo 4 para la implementación ya resuelta)

**Investigación previa al diseño, contra `data/baskonia.db` real (220 partidos):**

| Comprobación | Resultado |
|---|---|
| Columna que identifica competición | `games.league` (`String`, `nullable=False`). Se rellena en `main.py:556` (`league=source_team.league`) para partidos vía BBR, y en `scraper/baskonia_official._to_league()` para partidos vía calendario oficial. |
| Valores distintos en los 220 partidos | Exactamente 3: `acb` (181), `euroleague` (38), `supercopa` (1). |
| Nulos | 0. |
| Inconsistencias de formato (mayúsculas/espacios/duplicados) | Ninguna — los 3 valores están limpios. |
| **Fiabilidad real (el problema)** | De los 139 partidos **ya jugados**, el 100% tiene `league='acb'` — 0 tienen `'euroleague'` o `'supercopa'`, aunque el Baskonia es un club de Euroliga y ha jugado partidos reales de Euroliga en la temporada 2025-26. Los 39 partidos con `league` distinto de `'acb'` (38 `euroleague` + 1 `supercopa`) son, sin excepción, partidos **pendientes** (`home_score IS NULL`), procedentes del calendario oficial de baskonia.com. |

**Causa raíz** (verificada en el código, no es una suposición): `main.py:466-468` asigna la liga del
equipo de forma fija al crearlo (`team = upsert_team(session, team_slug, team_name, "acb")`, con el
comentario propio del código "asumimos liga ACB para el caso de uso"), y `main.py:556` copia esa
misma liga fija a **cada partido** de ese equipo (`league=source_team.league`), sin distinguir la
competición real de cada partido concreto. La página de calendario de BBR que se scrapea sí separa
los partidos por competición (`scraper/parser.py:174-178`, docstring: "una tabla por
competición/fase, p.ej. 'EuroLeague - Regular Season', 'Liga ACB - Regular Season'..."), pero
`_parse_schedule_table` no propaga ese `table_id`/competición al diccionario de cada partido
devuelto, así que la información existe en el HTML ya descargado pero se descarta antes de llegar a
`main.py`. README.md (líneas 176-178 y 653-654) ya documenta como limitación menor conocida que la
liga asignada al rival/al calendario es "una suposición basada en el equipo de origen" — este ciclo
es el primero que intenta *usar* ese dato como filtro, lo que expone la severidad real del problema.

**Por qué no se implementa el selector con este dato**: un selector de competición sobre partidos
jugados con la temporada 2025-26 seleccionada mostraría, p.ej., "Euroliga" → 0 partidos — no porque
el Baskonia no jugara Euroliga esa temporada (sí lo hizo), sino porque todos esos partidos están mal
etiquetados como `'acb'`. Esto **no es** el mismo tipo de "0 resultados" que el degradado limpio ya
usado para `season=2026` (ahí 0 resultados es la verdad: 0 partidos jugados todavía); aquí 0
resultados sería un dato falso presentado como si fuera correcto — silenciosamente engañoso, peor
que no tener la funcionalidad.

**Por qué arreglarlo está fuera de alcance de esta feature**: requeriría (a) `scraper/parser.py` —
capturar la competición por tabla/fila en `parse_schedule_games`/`_parse_schedule_table`; (b)
`db/storage.py`/`main.py` — persistir esa competición por partido en vez de la liga fija del equipo
de origen; (c) un backfill de los 139 partidos ya jugados, que probablemente requiere volver a
scrapear BBR (la competición original de cada tabla nunca se persistió en ningún sitio recuperable,
solo el valor final ya incorrecto). Las tres cosas tocan capas (`scraper/`, `db/storage.py`,
`main.py`) que esta feature tiene explícitamente fuera de su alcance (ver "Alcance → Fuera" y
`.github/workflow.config.md`). No se implementa "a ciegas": ver "Preguntas abiertas para el
usuario" para la recomendación formal.

### Ciclo 4 — Ampliación B: selector de competición (implementado)

**Precondición ya cumplida**: `local/features/002-competicion-real-por-partido/` está `stage: done`,
veredicto `APPROVED` (0 BLOCKER/MAJOR). Verificado de nuevo en este ciclo, de forma independiente
(consulta SQL propia contra `data/baskonia.db` real, no copiada de `02_implementation.md`/
`03_review.md` de la 002):

```sql
SELECT league, count(*) FROM games WHERE home_score IS NOT NULL GROUP BY league;
-- acb: 101   euroleague: 38   (total 139, 0 sin resolver)
SELECT league, count(*) FROM games WHERE home_score IS NULL GROUP BY league;
-- acb: 42    euroleague: 38   supercopa: 1   (total 81)
```

Coincide exactamente con lo reportado por la 002. `Game.league` ya es una base fiable para el
selector que el gate del ciclo 2 pidió.

#### Evidencia temporada × competición contra `data/baskonia.db` real (vitoria = Baskonia, 152 partidos)

Consulta real (`season_start_year` sobre `Game.date`, cruzado con `Game.league` y
`Game.home_score`):

| Temporada | Liga | Total | Jugados | Pendientes |
|---|---|---|---|---|
| 2025-26 | acb | 41 | 37 | 4 |
| 2025-26 | euroleague | 38 | 38 | 0 |
| 2025-26 | supercopa | 0 | 0 | 0 |
| 2026-27 | acb | 34 | 0 | 34 |
| 2026-27 | euroleague | 38 | 0 | 38 |
| 2026-27 | supercopa | 1 | 0 | 1 |

Suma: 79 partidos en 2025-26 (75 jugados: 37+38, coincide con la cifra ya usada en "Criterios de
aceptación" antes de este ciclo) + 73 en 2026-27 (0 jugados) = 152, coincide con el total de
`vitoria` ya verificado en ciclos anteriores. Los 4 "pendientes" de 2025-26/acb son partidos con
fecha ya pasada respecto a hoy (2026-08-18) pero sin resultado registrado (aplazados reales de BBR,
ver supuesto/riesgo #9 — comportamiento preexistente, no introducido por este ciclo); no los
devuelve `upcoming_games` (que excluye fechas pasadas), pero sí `team_summary_df`/`_team_games`
(que no distinguen jugado/pendiente, por diseño desde el ciclo 3).

Esta tabla fija los ejemplos concretos de "Criterios de aceptación" de este ciclo: **la combinación
"2025-26 × supercopa" tiene 0 partidos, jugados o pendientes** — es el caso de "0 resultados"
legítimo elegido para verificar el degradado limpio de la Ampliación B (paralelo al ya usado para
"season=2026" en la Ampliación A), porque no es un error de etiquetado: el Baskonia simplemente no
tiene ningún partido de Supercopa programado ni jugado en esa temporada con los datos actuales.

#### Decisión: derivar competiciones disponibles de los datos, no de `config.LEAGUES`

`config.py` ya tiene una constante `LEAGUES = ["acb", "euroleague"]` (usada solo por
`main.py:450` para iterar qué ligas scrapear), pero **no sirve** como fuente de "competiciones
disponibles" para el selector: (a) es un concepto de la capa de scraping, no de la analítica —
mismo criterio ya aplicado a `config.SEASON` en "Modelo de temporada" ("independiente, sin relación
de código"); (b) no incluye `"supercopa"`, que sí existe como valor real en `Game.league` (llega vía
`scraper/baskonia_official.py`, no vía el bucle de `config.LEAGUES`); (c) un valor de `config.LEAGUES`
sin ningún partido real asociado haría aparecer una opción vacía en el selector sin ningún beneficio.
Se deriva en su lugar de los datos ya persistidos, mismo patrón que `insights.list_seasons`:

```python
def list_leagues(session, team: "models.Team") -> List[str]:
    """Competiciones con al menos un partido guardado (jugado o pendiente) de `team`.

    A diferencia de `list_seasons`, no depende de `config.LEAGUES` (concepto de la capa de
    scraping, usado solo para decidir qué páginas de BBR visitar) — se deriva de `Game.league`
    ya persistido, que tras la feature `002-competicion-real-por-partido` refleja la competición
    real de cada partido, no la liga fija del equipo de origen.

    Returns:
        Valores distintos de `Game.league` (no vacíos), ordenados alfabéticamente. Vacía si el
        equipo no tiene ningún partido guardado en absoluto.
    """
    games = (
        session.query(models.Game)
        .filter((models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id))
        .all()
    )
    leagues = {g.league for g in games if g.league}
    return sorted(leagues)


_LEAGUE_LABELS = {"acb": "ACB", "euroleague": "Euroliga", "supercopa": "Supercopa"}


def league_label(league: Optional[str]) -> str:
    """Formatea un código de competición para la UI ('acb' -> 'ACB'), o 'Todas' si `league` es
    `None` (sin filtro, comportamiento por defecto). Un código no listado en `_LEAGUE_LABELS`
    (ninguno visto hoy en los datos reales, pero robustez ante uno nuevo) se muestra con
    `.capitalize()` en vez de fallar — mismo criterio que el fallback de
    `scraper.parser._table_competition` para códigos de liga desconocidos."""
    if league is None:
        return "Todas"
    return _LEAGUE_LABELS.get(league, league.capitalize())
```

Para `vitoria`: `list_leagues(session, vitoria)` devuelve `["acb", "euroleague", "supercopa"]`
(verificado). No hace falta un guard propio de "lista vacía": el único caso en que estaría vacía
(equipo sin ningún partido guardado) ya está cubierto por el guard existente de `list_seasons` en la
cabecera de `main()` — ambas listas derivan del mismo universo de partidos del equipo, así que si
`list_seasons` está vacía, `list_leagues` también lo está, y el flujo ya retorna antes de llegar al
selector de competición.

#### Selector en la cabecera global de `main()` (junto al de temporada)

Se añade una columna más a la fila de cabecera ya extendida por el ciclo 2/3 (que agregó una
columna para "Temporada" junto a `header_n_col`), en vez de crear una fila o sección aparte —
mismo criterio de "widget global, no por pestaña" ya aplicado a `last_n` y a `season`:

```python
header_logo_col, header_title_col, header_n_col, header_season_col, header_league_col = st.columns(
    [1, 5, 2, 2, 2]
)
...
with header_league_col:
    leagues = insights.list_leagues(session, baskonia)
    league = st.selectbox(
        "Competición",
        options=[None] + leagues,
        format_func=insights.league_label,
        index=0,   # "Todas" por defecto — ver justificación abajo
        key="league_selector",
    )
```

**Por qué el valor por defecto es "Todas" (`None`, índice 0) y no una competición concreta** —a
diferencia de `season`, cuyo valor por defecto es `current_season` (la temporada más reciente con
partidos jugados), para no abrir la app con paneles vacíos—: no hay una competición "más relevante"
por defecto sin más contexto que justifique una que no sea "todas" (el comportamiento de hoy, antes
de este ciclo); elegir por ejemplo "acb" por defecto ocultaría de entrada los partidos de Euroliga
sin que el usuario lo pidiera. Decisión de bajo riesgo, reversible con un cambio de una línea
(`index=`) si el uso real muestra lo contrario.

`league=None` (valor por defecto de todos los parámetros nuevos, exactamente igual que `season`)
preserva el comportamiento de hoy en el 100% de los casos: ningún consumidor que no pase `league`
se rompe.

#### Enhebrado: mismo conjunto de funciones que ya reciben `season`, con dos filtros combinados por AND

Regla operativa (aplicada de forma mecánica a cada función ya extendida con `season` en ciclos
2/3, salvo la excepción documentada de `player_load`/`games_in_window` más abajo): se añade
`league: Optional[str] = None` a la misma firma, con el mismo contrato `None` = "sin filtro". La
combinación de ambos filtros es una intersección (AND): un partido debe pertenecer a la temporada
seleccionada **y** a la competición seleccionada para contar, cuando ambos son distintos de `None`.

**Diferencia clave respecto a `season`**: `Game.league` es una columna real (no derivada, a
diferencia de `season_start_year`), así que el filtro de `league` se aplica **en la propia consulta
SQL** (`.filter(models.Game.league == league)`) en vez de en una lista por comprensión en Python
tras `.all()` — más simple, y evita traer de la BD filas que se descartarían de todos modos.

**`app.py` — `_team_games` (punto único ya centralizado por el ciclo 3) y `head_to_head_games`**,
exactamente como sugiere el encargo de este ciclo (extender el punto único en vez de enhebrar dos
filtros por separado en cada una de las 4 funciones que lo consumen):

```python
def _team_games(session, team: models.Team, season: "int | None" = None, league: "str | None" = None):
    query = session.query(models.Game).filter(
        (models.Game.home_team_id == team.id) | (models.Game.away_team_id == team.id)
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    games = query.all()
    if season is not None:
        games = [g for g in games if insights.season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games


def head_to_head_games(
    session, team_a: models.Team, team_b: models.Team,
    season: "int | None" = None, league: "str | None" = None,
):
    query = session.query(models.Game).filter(
        ((models.Game.home_team_id == team_a.id) & (models.Game.away_team_id == team_b.id))
        | ((models.Game.home_team_id == team_b.id) & (models.Game.away_team_id == team_a.id))
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    games = query.all()
    if season is not None:
        games = [g for g in games if insights.season_start_year(g.date) == season]
    games.sort(key=lambda g: parse_bbr_date(g.date) or datetime.min)
    return games
```

`recent_games_df`, `team_summary_df`, `head_to_head_summary_df` solo necesitan propagar `league` a
`_team_games` (igual que ya propagan `season`, sin lógica propia adicional):

```python
def team_summary_df(session, team: models.Team, season: "int | None" = None, league: "str | None" = None) -> pd.DataFrame:
    return _games_to_df(session, _team_games(session, team, season, league), team)

def recent_games_df(session, team: models.Team, last_n: int, season: "int | None" = None, league: "str | None" = None) -> pd.DataFrame:
    played = [g for g in _team_games(session, team, season, league) if g.home_score is not None]
    return _games_to_df(session, played[-last_n:], team)

def head_to_head_summary_df(session, team: models.Team, season: "int | None" = None, league: "str | None" = None) -> pd.DataFrame:
    rival_slugs = {slug for slug in config.TEAMS if slug != team.slug}
    played = [g for g in _team_games(session, team, season, league) if g.home_score is not None]
    games = [g for g in played if _rival_of(g, team).slug in rival_slugs]
    return _games_to_df(session, games, team)
```

`past_games(session, team, season=None, league=None)`: mismo patrón (`.filter(models.Game.league
== league)` sobre la query ya existente, antes del filtro Python de `season`).

**`insights.py` — `player_recent_form`/`team_advanced_summary`** (ya extendidas con `season` en
ciclo 2; ambas ya hacen `.join(models.Game, ...)` para poder ordenar/derivar temporada, así que
añadir el filtro de `league` es una línea de `.filter()` condicional sobre la query ya construida,
antes de `.all()`):

```python
def player_recent_form(
    session, team: "models.Team", last_n: int = 5,
    season: Optional[int] = None, league: Optional[str] = None,
) -> List[Dict[str, object]]:
    query = (
        session.query(models.BoxScore)
        .join(models.Game, models.BoxScore.game_id == models.Game.id)
        .filter(models.BoxScore.team_id == team.id)
    )
    if league is not None:
        query = query.filter(models.Game.league == league)
    rows = query.order_by(models.Game.id.desc()).all()
    if season is not None:
        rows = [r for r in rows if season_start_year(r.game.date) == season]
    # ... resto sin cambios (agrupar por jugador, slice [:last_n] ya dentro de temporada+competición)


def team_advanced_summary(
    session, team: "models.Team", season: Optional[int] = None, league: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    stats_query = session.query(models.TeamGameStats).join(models.Game, models.TeamGameStats.game_id == models.Game.id).filter(models.TeamGameStats.team_id == team.id)
    box_query = session.query(models.BoxScore).join(models.Game, models.BoxScore.game_id == models.Game.id).filter(models.BoxScore.team_id == team.id)
    if league is not None:
        stats_query = stats_query.filter(models.Game.league == league)
        box_query = box_query.filter(models.Game.league == league)
    stats_rows = stats_query.all()
    box_rows = box_query.all()
    if season is not None:
        stats_rows = [r for r in stats_rows if season_start_year(r.game.date) == season]
        box_rows = [r for r in box_rows if season_start_year(r.game.date) == season]
    # ... resto sin cambios
```

**`player_form_zscore` (idea 1)**: añade `league: Optional[str] = None` con el mismo patrón (filtro
SQL de `league` sobre la query de box scores, antes del filtro Python de `season` ya existente
desde el ciclo 2); `games_season` de cada jugador pasa a reflejar el recuento dentro de
temporada+competición.

**`project_next_matchup` (idea 4)**: pasa `league` a ambas llamadas a `team_advanced_summary`:

```python
def project_next_matchup(
    session, team: "models.Team", opponent: "models.Team", season: int, league: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    team_summary = team_advanced_summary(session, team, season=season, league=league)
    opp_summary = team_advanced_summary(session, opponent, season=season, league=league)
    return project_matchup(...)
```

**`scouting_narrative` (idea 5)**: pasa `league` a las tres llamadas internas
(`team_advanced_summary`, `player_recent_form`, `player_form_zscore`) — sin cambios de estructura
más allá de enhebrar el parámetro nuevo.

**`schedule_difficulty` (idea 3) — única función con lógica nueva, no solo enhebrado**: `league`
filtra los partidos **candidatos** antes de cortar `next_n`, no solo las estadísticas del rival —
decisión de diseño explícita, no un enhebrado mecánico:

```python
def schedule_difficulty(
    session, team: "models.Team", upcoming_games: List["models.Game"], season: int,
    next_n: int = 5, league: Optional[str] = None,
) -> Dict[str, object]:
    """Dificultad del próximo tramo de calendario, opcionalmente acotado a una competición.

    Si `league` no es `None`, se descartan del calendario pendiente los partidos de otras
    competiciones **antes** de tomar los próximos `next_n` — es decir, "los próximos 5 partidos
    de Euroliga", no "de los próximos 5 partidos (cualquier competición), cuántos son de
    Euroliga". Se considera la interpretación más útil para un cuerpo técnico que quiere preparar
    específicamente el próximo tramo de una competición dada; la alternativa (filtrar solo las
    estadísticas del rival, no el propio calendario) mezclaría rivales de competiciones distintas
    en la misma cuenta de "próximos N", lo que no tiene una lectura clara para el usuario.

    Returns:
        Igual que antes, más el propio `league` recibido, para que el llamador pueda etiquetar
        la sección ("dificultad de los próximos N de <competición>"). `games_considered` puede ser
        menor que `next_n` si `league` filtra tantos partidos que no quedan suficientes candidatos.
    """
    candidates = [g for g in upcoming_games if league is None or g.league == league]
    next_games = candidates[:next_n]
    # ... resto sin cambios (rival = away/home según corresponda;
    # team_advanced_summary(session, opponent, season=season, league=league) por rival)
```

Verificado contra `data/baskonia.db` real (`season=2025`, `next_n=5`, hoy 2026-08-18): con
`league='acb'`, los próximos 5 partidos ACB son burgos/girona/joventut/canarias/valencia,
`opponents_scouted=4` (girona/joventut/canarias/valencia ya tienen `team_game_stats` en
2025-26+acb; burgos no); con `league='euroleague'`, los próximos 5 son
olympiakos/valencia/olimpiamilano/besiktasistanbul/dubai, `opponents_scouted=0` (ninguno tiene
`team_game_stats` etiquetado `'euroleague'` hoy — ver "Hallazgo de cobertura de datos" abajo,
`avg_opponent_net_rating=None`, degradado limpio); con `league='supercopa'`, solo hay 1 partido de
Supercopa en todo el calendario pendiente (rival: Joventut), `games_considered=1` (`< next_n`),
`opponents_scouted=0` (Joventut tiene 3 filas de `team_game_stats`, las 3 etiquetadas `'acb'`, 0
`'supercopa'`).

#### Excepción documentada: `player_load`/`games_in_window` (idea 6) NO recibe `league`

A diferencia de las demás funciones nuevas de 7.3, `player_load`/`games_in_window` **no** se
extiende con `league`, por un motivo propio (distinto del que ya la excluía de `season`):

- La exclusión de `season` (ciclo 2) se basaba en que una ventana de ≤30 días nunca cruza el hueco
  real de ~3 meses entre temporadas — ese argumento **no** aplica igual a `league`: una ventana de
  14 días sí puede cruzar dos competiciones distintas en el calendario real (p.ej. un partido ACB
  el martes y uno de Euroliga el jueves de la misma semana, ver evidencia de próximos partidos
  arriba).
- La razón para excluirla de `league` es otra, y positiva, no solo "no aplica el mismo argumento":
  la fatiga/carga física de un jugador es transversal a la competición — filtrar los minutos
  acumulados a una sola competición **subestimaría** la carga real del jugador en esa ventana de
  días (un jugador que disputó un partido de ACB y otro de Euroliga en la misma semana está
  igualmente cansado, independientemente de qué competición esté seleccionada en el filtro
  global). Añadir `league` aquí sería, en el mejor caso, ruido; en el peor, una cifra de "carga"
  engañosamente baja.

Se documenta como decisión explícita (mismo criterio que la exclusión de `season` en el ciclo 2),
no como un olvido del barrido de este ciclo.

#### Interacción con `upcoming_games` y el `selectbox` de "próximo enfrentamiento" (decisión de bajo riesgo, con precedente)

`upcoming_games` (`app.py`) se mantiene sin `season` **ni ahora `league`**, por el mismo motivo ya
confirmado por el usuario en el ciclo 2 para `season` ("calendario pendiente, independiente de qué
selector esté activo para ver estadísticas retrospectivas") extendido por simetría a `league`: el
`selectbox` de "Próximo enfrentamiento" (`render_upcoming_tab`) sigue mostrando el calendario
completo (todas las competiciones) aunque el selector global de competición tenga una liga
concreta seleccionada. Elegir un rival de una competición distinta a la seleccionada en el filtro
global es válido (p.ej. filtro global "Euroliga" + rival elegido de un partido ACB): el scouting
del rival elegido (`render_team_tab(session, rival, last_n, season, league)`,
`render_head_to_head_tab(..., season, league)`) se sigue mostrando filtrado por el selector global,
que puede legítimamente no coincidir con la competición real del partido elegido en el desplegable
— mismo comportamiento ya aceptado para `season` (elegir una temporada cerrada no vacía "Próximos
enfrentamientos"), no una inconsistencia nueva introducida por este ciclo.

#### Nota de higiene: renombrar la variable local `season` de `render_player_card`

El código ya existente (pre-feature) de `render_player_card` usa `season` como nombre de variable
local para la fila de estadísticas "de la temporada" (`season = _player_stats_row(session, team,
player, last_n=1000)`), que el ciclo 2/3 ya iba a reescribir a `_player_stats_row(..., last_n=1000,
season=season)` — funcionalmente correcto (Python evalúa el lado derecho antes de reasignar el
nombre local), pero confuso: el mismo identificador nombra primero el parámetro entero (temporada
seleccionada) y después queda reescrito a un `dict` de estadísticas. Al añadir también `league`
como parámetro en este ciclo, se aprovecha para renombrar la variable local a `season_stats`
(cambio de una palabra, sin efecto funcional, mejora de claridad):

```python
st.subheader("Estadísticas de la temporada")
season_stats = _player_stats_row(session, team, player, last_n=1000, season=season, league=league)
if season_stats is None:
    st.info("Sin partidos registrados esta temporada todavía.")
else:
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("Partidos", season_stats["games"])
    ...
```

#### Hallazgo de cobertura de datos (real, verificado, no introducido por este ciclo)

Investigación adicional, no pedida explícitamente pero necesaria para fijar ejemplos correctos en
"Criterios de aceptación": se comprobó contra `data/baskonia.db` real qué liga tienen las filas de
`team_game_stats`/`boxscores` ya capturadas (no solo `games`, que ya se sabía fiable tras la 002):

```sql
SELECT g.league, count(*) FROM team_game_stats t JOIN games g ON t.game_id = g.id GROUP BY g.league;
-- acb: 56   (0 euroleague, 0 supercopa)
SELECT g.league, count(*) FROM boxscores b JOIN games g ON b.game_id = g.id GROUP BY g.league;
-- acb: 665  (0 euroleague, 0 supercopa)
```

**El 100% de las estadísticas avanzadas/box scores ya capturadas en toda la BD (cualquier equipo,
no solo `vitoria`) son de liga `'acb'`**. No es un bug de esta feature ni de la 002 (que corrigió
`Game.league`, una columna distinta de qué partidos tienen box score descargado): es una
consecuencia de qué partidos se han capturado hasta ahora (`LAST_N_GAMES=10` por defecto, o
scouting bajo demanda de un rival concreto) — los partidos más recientes con box score capturado
resultan ser, por casualidad del calendario real, todos de ACB (playoffs/tramo final de la
2025-26). **Implicación directa para "Criterios de aceptación" de este ciclo**: seleccionar
`league='euroleague'` hoy deja en blanco/`None`/vacío **todas** las subsecciones que dependen de
`team_game_stats`/`boxscores` (estadísticas avanzadas, rachas, perfil de tiro, narrativa,
proyección, dificultad de calendario del lado del rival) para **cualquier** equipo, no solo para
Baskonia — mientras que las tablas de calendario (`recent_games_df`, `team_summary_df`,
`head_to_head_summary_df`, `head_to_head_games`) sí muestran los partidos reales de Euroliga (fecha,
rival, resultado), con las columnas de Pace/ORtg/DRtg/Net a `None` porque no hay
`TeamGameStats` para esas filas — dos tipos distintos de "degradado limpio" que conviene no
confundir: (a) 0 filas = no hay partidos en esa combinación (p.ej. 2025-26 × Supercopa); (b) N
filas con columnas de stats a `None` = hay partidos pero sin box score/estadísticas avanzadas
capturadas todavía para ellos (p.ej. 2025-26 × Euroliga en `recent_games_df`). Ninguna de las dos
lanza excepción, ambas ya están cubiertas por el contrato `Optional[...]`/`None` existente; no se
introduce código nuevo para distinguirlas, solo se documentan para que el developer/reviewer no las
confunda con un bug del filtro al verificar manualmente. Ampliar la captura de datos de Euroliga
(más partidos con box score) es un cambio de scraping/captura fuera del alcance de esta feature
(ver "Alcance → Fuera").

## Paquetes de trabajo

Convención de `depende_de`: se marca dependencia cuando un WP consume una función/constante que
crea otro WP (contrato real), o cuando ambos editan las mismas líneas de una función ya existente.
En este proyecto no hay especialistas de dominio (roster vacío); el valor de `depende_de` es para
que el Developer sepa en qué orden **debe** escribir el código.

| WP | Descripción | Ficheros | Especialista | depende_de |
|---|---|---|---|---|
| WP-0a | Capa de temporada: `insights.season_start_year`/`season_label`/`list_seasons`/`current_season`; extender `player_recent_form`/`team_advanced_summary` con `season: Optional[int] = None` retrocompatible. **(Ciclo 4, alcance ampliado)** + capa de competición en el mismo WP (mismas funciones ya tocadas): `insights.list_leagues`/`league_label`; extender `player_recent_form`/`team_advanced_summary` con `league: Optional[str] = None` (filtro SQL sobre `Game.league`, ver "Diseño → Ciclo 4") | `insights.py` | ninguno | - |
| WP-0b | Superficie de temporada en la UI: selector "Temporada" en `main()`; enhebrar `season` en `render_team_tab`, `render_past_games_tab`, `render_upcoming_tab`, `render_roster_tab`, `render_player_card`, `_player_stats_row`, `recent_form_df`, `past_games` (nuevo filtro), `build_pdf_report`, `build_roster_pptx`; `upcoming_games` sin cambios (documentado). **(Ciclo 3, alcance ampliado)** + `season` en `_team_games` (centralizado, único punto de acceso), `recent_games_df`, `team_summary_df`, `head_to_head_summary_df`, `head_to_head_games`, `render_head_to_head_tab` — ver "Diseño → Ciclo 3 → Ampliación A". **(Ciclo 4, alcance ampliado)** + selector "Competición" en `main()` (nueva columna en la cabecera, junto al de temporada); enhebrar `league` exactamente por los mismos puntos que `season` en este WP (incluidos `_team_games`/`head_to_head_games` con el filtro SQL sobre `Game.league`), salvo `player_load`/`games_in_window` (excepción de WP-5); renombrar la variable local `season`→`season_stats` en `render_player_card` (higiene, ver "Diseño → Ciclo 4") | `app.py` | ninguno | WP-0a |
| WP-1 | Idea 1 — Rachas (doble z-score PTS+TS%): `insights.player_form_zscore` + constantes `ZSCORE_*` + `app.streaks_df`/`render_streaks_section` (columnas dobles) + inserción en `render_team_tab`. **(Ciclo 4)** + `league: Optional[str] = None` en `player_form_zscore` (mismo filtro SQL que WP-0a) | `insights.py`, `app.py` | ninguno | WP-0a, WP-0b |
| WP-2 | Idea 2 — Perfil de tiro: extender `insights.player_recent_form` (+`fg3a_rate`/`ft_rate`, misma función ya tocada en WP-0a) + columnas nuevas en `app.recent_form_df` + métricas nuevas en `app.render_player_card` (sustituye el hack `last_n=1000` por `season=season, league=league`) | `insights.py`, `app.py` | ninguno | WP-0a |
| WP-3 | Idea 3 — Dificultad de calendario: `insights.schedule_difficulty` (`season` requerido) + `app.schedule_difficulty_df`/`render_schedule_difficulty_section` + inserción en `render_upcoming_tab`. **(Ciclo 4)** + `league: Optional[str] = None` en `schedule_difficulty` — lógica propia, no solo enhebrado: filtra los partidos candidatos por competición **antes** de cortar `next_n` (ver "Diseño → Ciclo 4") | `insights.py`, `app.py` | ninguno | WP-0a, WP-0b |
| WP-4 | Idea 4 — Proyección de partido: `stats.project_matchup` (sin cambios) + `insights.project_next_matchup` (`season` requerido) + `app.render_matchup_projection_section` + inserción en `render_upcoming_tab`. **(Ciclo 4)** + `league: Optional[str] = None` en `project_next_matchup` (passthrough a ambas llamadas de `team_advanced_summary`) | `stats.py`, `insights.py`, `app.py` | ninguno | WP-0a, WP-0b |
| WP-5 | Idea 6 — Carga/fatiga: `app.games_in_window` (default 14 días) + `insights.player_load` (sin `season`, justificado) + `app.player_load_df`/`render_player_load_section` + inserción en `render_team_tab`. **(Ciclo 4: sin cambios, excepción documentada)** no recibe `league` — la fatiga es transversal a la competición, filtrar por liga subestimaría la carga real (ver "Diseño → Ciclo 4") | `insights.py`, `app.py` | ninguno | - |
| WP-6 | Idea 5 — Narrativa: `insights.scouting_narrative` (`season` requerido; usa `z_score_pts` de WP-1 y `fg3a_rate` de WP-2) + `app.render_narrative_section` + inserción en `render_team_tab`. **(Ciclo 4)** + `league: Optional[str] = None` en `scouting_narrative` (passthrough a `team_advanced_summary`/`player_recent_form`/`player_form_zscore`) | `insights.py`, `app.py` | ninguno | WP-0a, WP-1, WP-2 |
| WP-7 | Integración final: orden de subsecciones insertadas, unicidad de `key=` de widgets (incluidos `season_selector` y `league_selector`), `py_compile` + smoke import (ver comando en `workflow.config.md`), verificación manual de las 4 pestañas **con las dos temporadas reales** (2025-26 con datos, 2026-27 vacía). **(Ciclo 3)** incluye verificar explícitamente `recent_games_df`/`team_summary_df`/`head_to_head_summary_df`/`head_to_head_games` bajo ambas temporadas. **(Ciclo 4, alcance ampliado)** + verificar las combinaciones temporada×competición de "Criterios de aceptación" (incluida la combinación "0 partidos" 2025-26×Supercopa, y el caso "N partidos con stats a `None`" de 2025-26×Euroliga, ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos") | `app.py` (revisión), `stats.py`/`insights.py` (solo smoke) | ninguno | WP-0a, WP-0b, WP-1..WP-6 |
| WP-8 | Documentación mecánica: checkboxes `[ ]`→`[x]` de 7.3 en `README.md` + nota en "Estado actual" (incluye la mención del selector de temporada). **(Ciclo 4)** + mención del selector de competición y de la limitación de cobertura de datos de Euroliga (ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos") | `README.md` | ninguno | WP-7 |

Total: **10 WPs** — sin cambios respecto al ciclo 2/3. El delta del ciclo 4 (Ampliación B, ya
resuelta) tampoco añade WPs nuevos: se absorbe ampliando el alcance de WP-0a/WP-0b (capa y UI de
competición, mismo criterio que ya usó el ciclo 3 para no añadir WPs con la Ampliación A) y de
WP-1/WP-3/WP-4/WP-6/WP-7/WP-8 (cada uno enhebra `league` en las funciones que ya construye o
verifica). Se valoró explícitamente crear un WP-0c/WP-0d dedicado a competición (como sugería el
encargo de este ciclo, "probablemente un WP nuevo"), pero se descarta: el trabajo de competición es
mecánicamente idéntico al de temporada ya repartido en WP-0a/WP-0b (misma función, un parámetro
más), y crear WPs nuevos solo para esto subiría el total a 12, por encima del límite ya señalado en
el ciclo 2 ("~10 sin fasear"), sin ninguna ventaja de paralelización real (WP-0a/WP-0b ya son
paquetes secuenciales por construcción). WP-5 (idea 6) sigue siendo el único WP totalmente
independiente (`depende_de: -`), sin cambios de este ciclo. Nota WP-8: igual que en ciclos previos,
si el Documenter (etapa 4, `docs: yes`) amplía más allá de los checkboxes, no colisiona porque WP-8
solo toca esas líneas.

## Clase de complejidad

**Complejo** (cambia respecto al ciclo 1, que era "normal"). Justificación:

- El requisito de temporada no se resuelve solo con las 6 funciones nuevas: obliga a extender el
  **contrato** de dos funciones ya existentes y ampliamente consumidas (`player_recent_form`,
  `team_advanced_summary`) y a enhebrar el nuevo parámetro `season` a través de prácticamente toda
  la superficie de `app.py` (selector nuevo + 8 funciones existentes que pasan a recibir `season`:
  `render_team_tab`, `render_past_games_tab`, `render_upcoming_tab`, `render_roster_tab`,
  `render_player_card`, `_player_stats_row`, `recent_form_df`, `past_games`, más
  `build_pdf_report`/`build_roster_pptx`), no solo en las funciones nuevas de 7.3.
- Se introduce un concepto transversal nuevo (capa de "temporada": derivación + listado + resolución
  de "actual") consumido por las 3 capas de análisis del proyecto (`insights.py` directamente,
  `stats.py` indirectamente vía `insights.project_next_matchup`, `app.py` en la UI) — encaja en el
  criterio "toca contratos ya existentes / introduce una noción nueva cross-cutting", aunque sin
  cruzar capas del proyecto (`scraper/`→`db/`→`stats/insights`→`app` sigue respetándose) ni tocar
  `db/models.py`.
- Sigue **sin** requerir migración de esquema ni backfill (lo que mantendría el criterio en
  "normal" si solo fuera eso) — el salto a "complejo" es por la superficie de cambio en contratos ya
  existentes, no por riesgo de esquema/datos.
- WPs totales: 10 (en el límite superior antes de requerir fasear).

Esto implica (regla transversal 4 del pipeline): máximo 3 ciclos developer↔reviewer, sin fast-path
de gate informativo (ya aplicaba en el ciclo 1 al ser "normal"; con "complejo" sigue sin aplicar).

**Revisión ciclo 3 (con evidencia, no de memoria)**: se mantiene **complejo**. La Ampliación A
amplía aún más la superficie de contrato ya extendida en el ciclo 2 (4 funciones más de `app.py`
reciben `season`: `_team_games`, `recent_games_df`, `team_summary_df`, `head_to_head_summary_df`,
más `head_to_head_games`/`render_head_to_head_tab` encontradas en el barrido — 6 puntos de cambio
adicionales), pero no introduce un módulo nuevo ni cruza capas, así que no cambia la clase, solo
refuerza la misma justificación ya dada. La Ampliación B no añade complejidad de implementación
porque **no se implementa**: su contribución a esta revisión es puramente de investigación
(consulta a `data/baskonia.db` real + lectura de `main.py`/`scraper/parser.py`) y termina en un
bloqueo documentado, no en código. WPs totales: siguen siendo 10 (sin cambio).

**Revisión ciclo 4 (con evidencia, no de memoria)**: se mantiene **complejo**, y es el ciclo con
más superficie de cambio acumulada hasta ahora — pero no cruza el umbral hacia "fasear" ni cambia
los WPs totales (siguen siendo 10, ver "Paquetes de trabajo"). Justificación de por qué el alcance
total ahora es mayor sin cambiar de clase:

- Cada función que ya recibía `season` (10+ puntos de la app: `player_recent_form`,
  `team_advanced_summary`, `past_games`, `_team_games`/`recent_games_df`/`team_summary_df`/
  `head_to_head_summary_df`/`head_to_head_games`, más las 5 funciones nuevas de 7.3 salvo
  `player_load`) recibe ahora también `league` — duplica, en la práctica, el número de parámetros
  nuevos enhebrados por la app, aunque no el número de funciones tocadas (son las mismas).
  `schedule_difficulty` es la única con lógica genuinamente nueva (filtrado de candidatos antes de
  `next_n`), no solo un parámetro más.
- Sigue **sin** cruzar capas (`scraper/`→`db/`→`stats/insights`→`app` se respeta igual que en
  ciclos anteriores) ni tocar `db/models.py` ni requerir migración/backfill (el backfill de
  `Game.league` ya se hizo en la feature 002, fuera de esta feature).
- No se crea ningún módulo ni WP nuevo (ver "Paquetes de trabajo" para la justificación explícita
  de por qué no se opta por WP-0c/WP-0d dedicados).
- El criterio "toca contratos ya existentes / introduce una noción nueva cross-cutting" ya
  aplicaba desde el ciclo 2 (motivo original del salto a "complejo"); el ciclo 4 refuerza ese mismo
  motivo (dos nociones cross-cutting combinadas por AND, en vez de una) sin añadir un motivo nuevo
  de la lista del config (no hay cruce de capas ni código compartido nuevo que evaluar).

No se propone fasear: aunque el volumen de cambio es mayor, sigue repartido en los mismos 10 WPs
ya existentes, ninguno de los cuales crece hasta un tamaño que justifique dividirlo en un WP
propio.

## Criterios de aceptación

Generales (todas las ideas):
- `.venv/Scripts/python.exe -m py_compile stats.py insights.py app.py` sin error.
- `.venv/Scripts/python.exe -c "import app, main, stats, insights, report, config"` sin excepción.
- `streamlit run app.py`: las 4 pestañas cargan sin traceback visible con **ambas** temporadas
  reales seleccionadas desde el nuevo selector (2025-26 y 2026-27); las nuevas subsecciones
  aparecen en "Resumen" (rachas, narrativa, carga) y "Próximos enfrentamientos" (dificultad de
  calendario, proyección) tanto para Baskonia como para un rival scouteado.

Capa de temporada (nuevo, verificación contra `data/baskonia.db` real, sistema en 2026-08-18):
- `insights.list_seasons(session, vitoria_team)` devuelve `[2026, 2025]` (descendente).
- `insights.current_season(session, vitoria_team)` devuelve `2025` (la temporada 2026-27 tiene
  calendario pero 0 partidos jugados; cae a la más reciente con datos jugados) — el selector de la
  UI se abre con "2025-26" preseleccionado, no vacío.
- Con `season=2026` seleccionado explícitamente, todas las subsecciones de temporada degradan
  limpiamente (listas vacías / `None` / `st.info`), sin excepción ni traceback — este es el
  escenario real de "pretemporada" verificable hoy sin fechas simuladas.

Por idea (verificación manual, con `season=2025` salvo que se indique lo contrario):
1. **Rachas**: `insights.player_form_zscore(session, vitoria_team, season=2025)` devuelve lista no
   vacía (p.ej. "Matteo Spagnolo" con 12 partidos en esa temporada); cada entrada tiene `z_score_pts`
   numérico y `z_score_ts` numérico o `None` según cobertura de `ts_pct`; con `season=2026` devuelve
   `[]` sin excepción.
2. **Perfil de tiro**: `player_recent_form(..., season=2025)[i]["fg3a_rate"]`/`["ft_rate"]` son
   `float` en `[0, ~2]` para jugadores con `fg_attempted` sumado > 0 en esa temporada, `None` si no
   tiraron; columnas "3PA%"/"FTr" visibles en la tabla y en la ficha de jugador.
3. **Dificultad de calendario**: `schedule_difficulty(session, vitoria_team,
   upcoming_games(session, vitoria_team), season=2025, next_n=5)` devuelve `games_considered == 5`,
   `opponents_scouted` entre 0 y 5 (con los datos actuales, joventut y valencia ya tienen
   `team_game_stats` en la temporada 2025-26 y son 2 de los primeros 5 rivales del calendario real)
   y `avg_opponent_net_rating` no `None`; con `season=2026`, `opponents_scouted == 0` (ningún rival
   tiene `team_game_stats` en esa temporada todavía) y `avg_opponent_net_rating is None`, la UI
   muestra el estado sin datos en vez de fallar.
4. **Proyección**: `project_next_matchup(session, vitoria_team, un_rival_scouteado, season=2025)`
   devuelve dict con `team_projected_score`/`opp_projected_score` numéricos; con `season=2026`
   devuelve `None` (ninguno de los dos equipos tiene pace/ORtg/DRtg en esa temporada) y la UI
   muestra el mensaje de datos insuficientes.
5. **Narrativa**: `scouting_narrative(session, vitoria_team, season=2025)` devuelve un `str` no
   vacío; con `season=2026` devuelve `None` (sin `team_game_stats` en esa temporada) y la UI no
   pinta la subsección, sin excepción.
6. **Carga/fatiga**: `games_in_window(session, team, 14, reference_date=datetime(2026, 5, 1))`
   devuelve partidos reales de esa ventana (temporada 2025-26 en curso en esa fecha);
   `player_load(...)` sobre esos partidos devuelve minutos agregados > 0. Con la fecha real del
   sistema (2026-08-18, descanso de temporada) la ventana de 14 días da lista vacía y la UI muestra
   el `st.info` correspondiente — comportamiento correcto, no un fallo, e **independiente** de qué
   temporada esté seleccionada en el selector global (esta idea no lee `season`).
- **`past_games` con `season`**: `past_games(session, vitoria_team, season=2025)` devuelve 75
  partidos jugados (idéntico al comportamiento de hoy, porque el 100% de los partidos jugados caen
  en esa temporada); `past_games(session, vitoria_team, season=2026)` devuelve `[]` y la pestaña
  "Partidos anteriores" muestra su `st.info` existente en vez de un `selectbox` vacío.
- **`upcoming_games` sin `season`**: seleccionar `season=2025` (temporada cerrada) en el selector
  global NO vacía la pestaña "Próximos enfrentamientos" — sigue mostrando los 73 partidos pendientes
  de la temporada 2026-27, confirmando que esta función queda deliberadamente fuera del filtro.

**Ciclo 3 — Ampliación A, verificación contra `data/baskonia.db` real** (mismos equipos/temporadas
que arriba; `vitoria` = Baskonia, `bilbao` = único rival de `config.TEAMS` con enfrentamientos
directos jugados):
- **`recent_games_df`**: con `season=2025`, devuelve filas solo entre los 75 partidos jugados de esa
  temporada (mismo resultado que hoy, porque el 100% de lo jugado cae en 2025-26); con `season=2026`,
  devuelve un DataFrame vacío y la UI muestra `st.info("Sin partidos guardados todavía.")` (guard ya
  existente en `render_team_tab`), sin excepción.
- **`team_summary_df`**: con `season=2025` devuelve 79 filas (75 jugados + 4 pendientes/aplazados
  reales cuya fecha cae dentro de esa temporada); con `season=2026` devuelve 73 filas (0 jugados + 73
  pendientes: 34 ACB + 38 Euroliga + 1 Supercopa) — combinación temporada×competición con 0 partidos
  jugados pero no vacía del todo, verificando que el filtro es por fecha y no oculta el calendario
  pendiente de la propia temporada.
- **`head_to_head_summary_df`**: con `season=2025`, `vitoria` vs `bilbao` devuelve 2 filas (los dos
  enfrentamientos ya jugados esa temporada: 23/11/2025 y 15/02/2026 — ambos caen en el bucket 2025
  por la regla de corte de mes); con `season=2026` devuelve 0 filas (los dos enfrentamientos de esa
  temporada, 22/11/2026 y 30/01/2027, están pendientes) y la UI muestra el `st.info` ya existente
  ("Sin enfrentamientos directos jugados todavía.") — **esta es la combinación temporada×competición
  con 0 partidos** que verifica el degradado limpio pedido para la Ampliación A.
- **`head_to_head_games`**: con `season=2025` devuelve los mismos 2 partidos jugados que
  `head_to_head_summary_df`; con `season=2026` devuelve 2 partidos (los pendientes, sin resultado) —
  no vacío, porque esta función no filtra por jugado/pendiente (nunca lo hizo, con o sin `season`);
  `render_head_to_head_tab` los pinta sin excepción (comportamiento pre-existente con marcador
  `None`, ver "Diseño → Ciclo 3 → Ampliación A" y "Supuestos y riesgos").

**Ciclo 4 — Ampliación B, verificación contra `data/baskonia.db` real** (post-002: 101 `acb`/38
`euroleague` jugados, ver "Diseño → Ciclo 4"; `vitoria` = Baskonia, hoy 2026-08-18):

- **Capa de competición**: `insights.list_leagues(session, vitoria_team)` devuelve `["acb",
  "euroleague", "supercopa"]` (alfabético); `insights.league_label("euroleague")` devuelve
  `"Euroliga"`, `insights.league_label(None)` devuelve `"Todas"`. El selector "Competición" de la
  cabecera se abre con "Todas" preseleccionado (índice 0, `league=None`) — a diferencia del
  selector de temporada, que preselecciona una temporada concreta.
- **`team_summary_df`**: `season=2025, league='acb'` → 41 filas (37 jugados + 4 pendientes/
  aplazados); `season=2025, league='euroleague'` → 38 filas (38 jugados, 0 pendientes);
  `season=2025, league='supercopa'` → **0 filas** (combinación real sin ningún partido, jugado ni
  pendiente — el caso de "0 resultados" elegido para este ciclo, análogo a `season=2026` en la
  Ampliación A); `season=2026, league='supercopa'` → 1 fila (pendiente, Baskonia-Joventut,
  19/09/2026).
- **`recent_games_df`**: `last_n=5, season=2025, league='acb'` → 5 filas con columnas Pace/ORtg/
  DRtg/Net numéricas (dentro de los 12 partidos con `team_game_stats` ya capturados, todos `acb`);
  `last_n=5, season=2025, league='euroleague'` → 5 filas (partidos reales de Euroliga) **con las 4
  columnas de stats a `None`** — degradado limpio del tipo (b) descrito en "Hallazgo de cobertura
  de datos" (hay partido, no hay `team_game_stats` capturado para él todavía), sin excepción.
- **`head_to_head_summary_df`**: con el rival por defecto (`bilbao`, único en `config.TEAMS`),
  `season=2025, league='euroleague'` → **0 filas**, y **también con `season=2026`** → 0 filas —
  Bilbao Basket nunca ha jugado Euroliga contra el Baskonia en ningún partido guardado
  (jugado o pendiente), así que este "0" es independiente de qué temporada esté seleccionada,
  caso de degradado limpio distinto al de "temporada sin jugar todavía".
- **`head_to_head_games`** (rival genérico, no solo `config.TEAMS`): `vitoria` vs `real-madrid`,
  `season=2025, league='euroleague'` → 2 filas, ambas jugadas (11/12/2025 94-87, 03/04/2026 98-96);
  `season=2025, league='acb'` → 3 filas (2 jugadas + 1 pendiente/aplazada del 24/05/2026, ver
  supuesto/riesgo #9, comportamiento preexistente no relacionado con este ciclo).
- **`player_form_zscore`**: `season=2025, league='acb'` → no vacía (12 de 17 jugadores con ≥6
  partidos en esa combinación, incluye "Matteo Spagnolo" con 12 PJ, mismo resultado que sin filtro
  de liga porque el 100% de los box scores capturados hoy son `acb`); `season=2025,
  league='euroleague'` → **`[]`** (0 jugadores — ver "Hallazgo de cobertura de datos": 0 filas de
  `boxscores` etiquetadas `euroleague` en toda la BD hoy, no es un fallo del filtro).
- **`team_advanced_summary`**: `season=2025, league='euroleague'` para `vitoria` → dict con las 6
  medias a `None` (mismo motivo). `scouting_narrative(session, vitoria, season=2025,
  league='euroleague')` → `None` (por la misma razón, `avg_pace is None`); la UI no pinta la
  subsección, sin excepción. `project_next_matchup(session, vitoria, real_madrid, season=2025,
  league='euroleague')` → `None` (el resumen de `vitoria` en esa combinación ya es `None`).
- **`schedule_difficulty`**: `season=2025, next_n=5, league='acb'` → `games_considered=5`
  (Burgos/Girona/Joventut/Canarias/Valencia, próximos 5 ACB reales desde hoy), `opponents_scouted=4`
  (todos salvo Burgos), `avg_opponent_net_rating` no `None`; `league='euroleague'` →
  `games_considered=5` (Olympiacos/Valencia/Olimpia Milano/Besiktas/Dubái), `opponents_scouted=0`,
  `avg_opponent_net_rating=None`, la UI muestra el estado sin datos; `league='supercopa'` →
  `games_considered=1` (`< next_n`, solo hay un partido de Supercopa en todo el calendario
  pendiente), `opponents_scouted=0`.
- **Combinación de ambos filtros (AND)**: cualquier ejemplo de arriba con `season` y `league`
  simultáneos distintos de `None` filtra por la intersección exacta — no hay ningún caso, con los
  datos reales de hoy, en que combinar ambos produzca una excepción o un resultado distinto de
  vacío/`None`/lista vacía cuando la combinación no tiene partidos.

## Supuestos y riesgos

| # | Supuesto/riesgo | Validación |
|---|---|---|
| 1 | Métrica de racha (idea 1) = doble z-score PTS (volumen) + TS% (eficiencia) — decisión ya tomada por el usuario. Se prioriza TS% sobre eFG% por ser la fórmula más completa (incluye tiros libres); cobertura idéntica entre ambas para los jugadores de `vitoria` (128/141 filas cada una), así que no hay diferencia de datos que decante la elección. | Enseñar la tabla "Rachas" (ambas columnas) al cuerpo técnico tras implementar; si prefieren eFG% en vez de TS%, es cambiar una columna dentro de `player_form_zscore` sin tocar la firma pública. |
| 2 | Umbrales heurísticos de narrativa (pace 75/68, %3PA 0.40/0.25) y de racha (±1.0 desviación) — confirmados por el usuario "tal cual" en el gate del ciclo 1. Siguen sin calcularse contra una media de liga real (muestra insuficiente en la BD). | Revisar 2-3 párrafos narrativos generados contra rivales reales tras implementar; ajustar constantes si conviene (no requiere cambiar contratos). |
| 3 | `min_season_games=6` (idea 1) y `window_days` por defecto = 14 (idea 6, confirmado por el usuario) son valores de diseño. Verificado que `min_season_games=6` sigue dejando pasar jugadores habituales dentro de la temporada 2025-26 real (12-13 partidos jugados). | Ajustables vía parámetro sin tocar la firma pública. |
| 4 | **Resuelto (ciclo 2)**: "temporada" se deriva de `Game.date` en tiempo de consulta (regla de corte mes ≥ 7), sin columna nueva en `db/models.py`. Verificado sin ambigüedad contra el 100% de los datos reales (nunca hay partidos en jul-ago). Ya no es un riesgo pendiente, es una decisión cerrada con evidencia — ver "Diseño → Modelo de temporada". | Si en el futuro BBR/la web oficial empezaran a programar partidos en julio/agosto (pretemporada real con fecha en el hueco), la regla de corte debería revisarse; no hay evidencia de que esto ocurra hoy. |
| 5 | **(Resuelto en ciclo 4)** "Temporada" y "competición" son dos ejes ortogonales, cada uno con su propio selector — ya no es una limitación de alcance. En el ciclo 2/3 `Game.league` no era fiable (100% de los 139 partidos jugados en `'acb'` por asignación fija del equipo de origen); la feature `002-competicion-real-por-partido` (`APPROVED`) corrigió la causa raíz con backfill idempotente y con backup. Verificado de nuevo en este ciclo, de forma independiente: 101 `acb`/38 `euroleague` en los 139 jugados, 0 sin resolver. Limitación residual conocida (documentada por la propia 002, no nueva de este ciclo): la tabla `SPA` de BBR mezcla liga regular con playoffs/Copa bajo el mismo código, así que `league='acb'` no distingue sub-fases — el selector de esta feature sí distingue ACB vs Euroliga vs Supercopa, que es lo que pedía el usuario. | Ya validado con evidencia real (consulta SQL propia contra `data/baskonia.db`, no solo copiada del informe de la 002). Si en el futuro se quiere distinguir sub-fases dentro de ACB, es una feature aparte que toque `scraper/parser.py` de nuevo (fuera de las capas de esta feature). |
| 6 | El selector de temporada por defecto (`current_season`) prioriza la temporada más reciente **con partidos jugados** sobre la temporada calendario-actual si esta está vacía (ahora mismo: preselecciona 2025-26, no 2026-27) — decisión de UX propia, no pedida explícitamente, para no abrir la app con paneles vacíos. | Verificar con el usuario en el primer uso real de la app tras esta feature si esta preselección es la esperada; cambiarla es una línea en `current_season`. |
| 7 | **(Actualizado ciclo 3)** Se enhebra `season` en `past_games`, `player_recent_form`, `team_advanced_summary`, las 6 funciones nuevas, y — desde este ciclo — también en `recent_games_df`, `team_summary_df`, `head_to_head_summary_df` y `head_to_head_games` (encontrada en el barrido del ciclo 3, ver "Diseño → Ciclo 3 → Ampliación A"). Las únicas funciones que quedan deliberadamente sin acotar por temporada son `upcoming_games` (calendario pendiente), `validate_data` (calidad de datos transversal), `player_load`/`games_in_window` (ventana de días, no puede cruzar temporadas) y `boxscore_df`/`current_roster` (no son agregaciones sobre partidos) — cada una con su motivo documentado en "Diseño → Modelo de temporada". | Si en el futuro se detecta alguna otra función agregada sobre partidos que quedó fuera de este barrido, es una línea de filtro adicional sobre el mismo patrón (`_team_games`/`insights.season_start_year`), no un rediseño. |
| 8 | WP-8 (checkboxes README) y la etapa 4 opcional (Feature Documenter) podrían pisarse si ambos tocan `README.md` sin coordinación. | El Feature Lead debe indicar al Documenter que los checkboxes de 7.3 ya los deja hechos el Developer en WP-8. |
| 9 | **(Nuevo ciclo 3)** `head_to_head_games` nunca ha filtrado por partido jugado/pendiente (con o sin `season`): si la temporada seleccionada tiene enfrentamientos directos ya programados pero no jugados, `render_head_to_head_tab` los pinta con marcador `None-None` y box score vacío en vez de omitirlos. Es un comportamiento pre-existente (no introducido por este ciclo) que la Ampliación A no agrava — de hecho reduce cuántos casos así se ven a la vez al acotar por temporada. | No se corrige en esta feature (no estaba en el pedido del gate); si se quiere pulir, es añadir un filtro `home_score is not None` a `head_to_head_games` o a su punto de renderizado — cambio de una línea, sin impacto en contratos. |
| 10 | **(Resuelto en ciclo 4)** El selector de competición (`Game.league`) ya no está bloqueado (ver supuesto #5); diseñado en este ciclo, ver "Diseño → Ciclo 4". | Pendiente de implementación (etapa 2, `feature-developer`) siguiendo el diseño de este ciclo. |
| 11 | **(Nuevo ciclo 4)** `schedule_difficulty` filtra los partidos **candidatos** del calendario pendiente por `league` antes de cortar `next_n` (no solo las estadísticas del rival) — decisión de diseño explícita, no la única interpretación posible (la alternativa, filtrar solo las stats del rival dejando el calendario mixto, mezclaría competiciones en la misma cuenta de "próximos N" sin una lectura clara). | Mostrar al cuerpo técnico "próximos 5 de Euroliga" vs "próximos 5 (todas)" tras implementar; si prefieren la alternativa, es un cambio de una línea (mover el filtro de `league` a solo la llamada de `team_advanced_summary` del rival), sin tocar la firma pública. |
| 12 | **(Nuevo ciclo 4)** `player_load`/`games_in_window` (idea 6) no recibe `league`, a diferencia de las demás funciones nuevas: la fatiga es transversal a la competición, filtrarla subestimaría la carga real de un jugador que compite en varias competiciones la misma semana. | Decisión de diseño documentada, no un olvido del barrido. Si en el futuro se quisiera "carga solo en partidos de una competición" como métrica aparte (no como sustituto de la carga total), sería una función nueva, no una extensión de esta. |
| 13 | **(Nuevo ciclo 4, hallazgo real de datos)** Hoy el 100% de `team_game_stats` (56 filas) y `boxscores` (665 filas) de toda la BD —cualquier equipo, no solo Baskonia— están etiquetados `league='acb'`; 0 filas `'euroleague'`/`'supercopa'`. No es un bug de esta feature ni de la 002 (que corrigió `Game.league`, columna distinta de qué partidos tienen box score descargado): es consecuencia de qué partidos se han capturado hasta ahora. Efecto: seleccionar `league='euroleague'` hoy deja en blanco/`None` toda subsección basada en estadísticas avanzadas (rachas, perfil de tiro, narrativa, proyección, lado del rival en dificultad de calendario), para cualquier equipo, mientras que las tablas de calendario (`Game`) sí muestran partidos reales de Euroliga. | Verificado con dos consultas SQL propias contra `data/baskonia.db` real (ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos"). No requiere cambio de diseño: el contrato `Optional[...]`/`None` ya cubre este caso sin excepción. Si se quiere demostrar el selector con datos reales de Euroliga en el propio Baskonia, haría falta una recaptura dirigida de box scores de un partido de Euroliga ya jugado — cambio de scraping/captura fuera del alcance de esta feature. |
| 14 | **(Nuevo ciclo 4)** `upcoming_games` y el `selectbox` de "Próximo enfrentamiento" siguen sin filtrar por `league` (igual que ya no filtran por `season`, decisión ya confirmada por el usuario en el ciclo 2): se puede tener el filtro global de competición en "Euroliga" y elegir en el desplegable un rival de un partido ACB; el scouting de ese rival se muestra igualmente filtrado por el selector global, que puede no coincidir con la competición real del partido elegido. | Mismo precedente ya aceptado para `season`; no se considera una inconsistencia nueva. Si el usuario prefiere que el desplegable también se filtre por el selector global de competición, es un cambio de una línea (`upcoming_games` recibiría `league` y filtraría antes del `selectbox`) sin tocar contratos. |
| 15 | **(Nuevo ciclo 4)** Se renombra la variable local `season` de `render_player_card` a `season_stats` para evitar la sombra de nombre con el parámetro `season` del propio ciclo 2/3 (funcionalmente no era un bug — Python evalúa el lado derecho antes de reasignar — pero confuso, y se agrava al añadir también `league`). | Cambio de una palabra, sin efecto funcional; verificable con `py_compile`/smoke import igual que el resto. |

## Preguntas abiertas para el usuario

Ninguna pregunta bloqueante en este ciclo (ciclo 4). La única pregunta que estaba pendiente
(selector de competición) ya se resolvió — ver "Cierre del ciclo 4" abajo. El resto de decisiones
tomadas en este ciclo (default "Todas" en el selector de competición, filtrado de candidatos por
liga en `schedule_difficulty`, exclusión de `player_load`/`games_in_window`, no filtrar
`upcoming_games` por `league`) son decisiones de bajo riesgo con validación propuesta, documentadas
en "Supuestos y riesgos" (#11-#15), no preguntas que requieran respuesta del usuario antes de
desarrollar.

### Ciclo 3 → Ciclo 4 — histórico del bloqueo, ya resuelto

**Resumen breve (trazabilidad, no bloquea)**: en el ciclo 3 se investigó `Game.league` contra
`data/baskonia.db` real y resultó no fiable para un selector de competición — el 100% de los 139
partidos ya jugados tenía `league='acb'` por una asignación fija del equipo de origen en
`main.py`, no la competición real de cada partido. El detalle completo de esa investigación (causa
raíz con líneas de código, verificación numérica) se conserva en `STATUS.md` (registro del ciclo 3)
y en `local/features/002-competicion-real-por-partido/01_design.md` §1 (la investigación que lo
resolvió). El usuario decidió no renunciar al selector y priorizar primero una feature aparte que
arreglara el dato en origen.

### Cierre del ciclo 4 (Ampliación B ya diseñada)

`local/features/002-competicion-real-por-partido/` terminó `stage: done`, veredicto `APPROVED`,
con `Game.league` ya fiable (101 `acb`/38 `euroleague` en los 139 partidos jugados, verificado de
nuevo de forma independiente en este ciclo). Este ciclo diseña la Ampliación B completa (selector
de competición + enhebrado de `league` por el mismo conjunto de funciones que ya reciben `season`,
ver "Diseño → Ciclo 4"), sin encontrar ningún bloqueo estructural nuevo. El único hallazgo
relevante — que hoy el 100% de `team_game_stats`/`boxscores` ya capturados son de liga `'acb'`
(0 `'euroleague'`), ver "Diseño → Ciclo 4 → Hallazgo de cobertura de datos" — no es un bloqueo de
diseño: es una limitación de cobertura de datos ya conocida (fuera de las capas de esta feature),
con un contrato de degradado limpio (`Optional[...]`/`None`/lista vacía) que ya cubre el caso sin
necesidad de código nuevo. **Este diseño queda listo para encadenar directamente a `feature-developer`**
(desarrollo completo de las 6 ideas de 7.3 + Ampliación A + Ampliación B, de una sola vez, tal como
ya estaba previsto — ver `STATUS.md`).

---

### Ciclo 2 (ya resuelto, contexto histórico — sin cambios en el ciclo 3)

Las dos dudas estructurales planteadas por el gate del
ciclo 1 (derivar vs. persistir `season`; tratamiento de pretemporada) se resolvieron con evidencia
concreta contra `data/baskonia.db` real sin que quedara un trade-off genuino que solo el usuario
pudiera zanjar:

- **Derivar vs. persistir**: la opción de derivar en código no tiene ninguna desventaja medible
  frente a persistir una columna nueva a esta escala de datos (220 partidos, SQLite), y persistir sí
  tiene coste real (migración + sincronización futura en `db/storage.py`/`main.py`, fuera del
  alcance actual). No hay trade-off que decidir, ver "Diseño → Modelo de temporada".
- **Pretemporada**: no existen en la BD partidos de tipo "amistoso"/pretemporada real (solo
  `acb`/`euroleague`/`supercopa`); el escenario que preocupaba al usuario ("estamos en
  pretemporada") es, con los datos reales, el caso "temporada actual con 0 partidos jugados", que
  ya está cubierto explícitamente en los criterios de aceptación con la temporada 2026-27 real.

Quedan documentadas como **decisiones de diseño de bajo riesgo, no como preguntas abiertas** (por
si el usuario quiere corregirlas en el gate, pero no bloquean el desarrollo):
- Prioridad TS% sobre eFG% para el z-score de eficiencia (supuesto #1). Confirmada por el usuario en
  el gate del ciclo 2, sin cambios en el ciclo 3.
- Preselección de "temporada más reciente con datos jugados" en vez de "temporada calendario-actual"
  cuando esta última está vacía (supuesto #6). Confirmada por el usuario en el gate del ciclo 2, sin
  cambios en el ciclo 3.
- **(Resuelto en ciclo 3, ya no es una limitación de alcance)** El filtro de temporada cubre ahora
  toda la app: `recent_games_df`, `team_summary_df`, `head_to_head_summary_df` y
  `head_to_head_games` se enhebran junto con las funciones ya cubiertas en el ciclo 2 (supuesto #7).
  Las únicas excepciones que quedan son `upcoming_games`, `validate_data`, `player_load`/
  `games_in_window` y `boxscore_df`/`current_roster`, cada una con motivo propio documentado.
- No distinguir por competición dentro de una temporada (supuesto #5) — en el ciclo 3 se investigó
  explícitamente y resultó ser un bloqueo real por datos no fiables; **resuelto en el ciclo 4** tras
  la feature 002 (`Game.league` ya fiable): ver "Diseño → Ciclo 4" para el selector de competición
  ya diseñado, y "Cierre del ciclo 4" arriba.

## Estrategia de validación

Sin tests automatizados en este proyecto (`STATUS.md`: `tests: no`). Validación en dos niveles:

1. **Build/smoke** (obligatorio, WP-7): `py_compile` de los 3 ficheros tocados + smoke import de
   los 6 módulos de borde — condición de salida del config.
2. **Manual dirigido** (obligatorio, WP-7): `streamlit run app.py`, recorrer las 4 pestañas **con
   las dos temporadas reales** disponibles en el selector (2025-26 con datos completos, 2026-27
   vacía) — verificando explícitamente que cambiar de temporada no revienta ninguna subsección y
   que "Próximos enfrentamientos" sigue mostrando calendario aunque se seleccione una temporada
   cerrada. **(Ciclo 4)** + recorrer las 4 pestañas con el selector de competición en cada valor
   (`Todas`, `ACB`, `Euroliga`, `Supercopa`), verificando en particular que `Euroliga` no lanza
   excepción en ninguna subsección aunque hoy no tenga estadísticas avanzadas capturadas (ver
   "Diseño → Ciclo 4 → Hallazgo de cobertura de datos") y que combinar temporada+competición no
   produce ningún estado distinto de los ya documentados en "Criterios de aceptación".
3. **Ad-hoc por consola** (recomendado, uno por WP): un `python -c` corto por función nueva/
   extendida contra `data/baskonia.db` real, con los ejemplos concretos de "Criterios de
   aceptación" (incluye probar explícitamente `season=2026` para la rama de degradación, no solo
   `season=2025`). **(Ciclo 3)** incluye explícitamente `recent_games_df`/`team_summary_df`/
   `head_to_head_summary_df`/`head_to_head_games` bajo ambas temporadas, con la combinación
   `head_to_head_summary_df(session, vitoria, season=2026)` como caso concreto de "0 partidos" para
   verificar el degradado limpio de la Ampliación A. **(Ciclo 4)** + probar explícitamente
   `league='euroleague'` en `player_form_zscore`/`team_advanced_summary`/`scouting_narrative`/
   `project_next_matchup` (deben devolver `[]`/`None` sin excepción, caso real de hoy) y
   `league='supercopa'` en `season=2025` (0 partidos) vs `season=2026` (1 partido pendiente) como
   los dos casos concretos de "Criterios de aceptación" de la Ampliación B.

Si en el futuro se activan tests (etapa 5): `insights.season_start_year`/`season_label` son
triviales de testear sin sesión (funciones puras); `list_seasons`/`current_season`/`list_leagues`/
`league_label` y las 6 funciones nuevas son candidatas naturales a SQLite en memoria con un par de
partidos sintéticos a caballo de un cambio de temporada y de dos competiciones distintas, para
fijar la regla de corte y el filtro de liga con un test de regresión explícito.

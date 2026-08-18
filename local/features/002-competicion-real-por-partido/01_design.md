# Design: Competición real por partido (`Game.league`)

## Contexto y objetivo

`Game.league` se asigna hoy con la liga fija del equipo de origen (`main.py:466-468` crea cada
`Team` con `"acb"` a fuego, y `main.py:556` copia esa liga fija a cada partido), no con la
competición real de ese partido concreto. Resultado verificado contra `data/baskonia.db` real: de
139 partidos ya jugados, el 100% tiene `league='acb'`, aunque el Baskonia ha jugado 38 partidos
reales de Euroliga esta temporada. El objetivo es que `Game.league` refleje la competición real de
cada partido, capturándola en origen (BBR ya la separa por tabla) y corrigiendo retroactivamente
los 139 partidos ya jugados, sin perder ni corromper datos.

## Alcance

**Entra:**
1. Captura de la competición real por fila de calendario en `scraper/parser.py` (el dato vive en
   el `id` de cada tabla de BBR, hoy descartado).
2. Dejar de sobreescribir esa competición con la liga fija del equipo en `main.py` y `db/storage.py`.
3. Backfill idempotente y con copia de seguridad previa de los 139 partidos ya jugados.
4. Verificación numérica del resultado contra la base de datos real.

**Fuera de alcance** (explícito):
- Selector de competición en `app.py` (Ampliación B de la feature 001, se retoma después).
- Corregir la semántica de `Team.league` (liga "fija" del equipo, no del partido): no se lee en
  ningún otro sitio del código (confirmado por grep en todo el repo: solo `main.py` la usa, nunca
  `app.py`/`insights.py`/`stats.py`), y tras este fix solo actúa como valor de última reserva
  cuando no se puede extraer la competición real de una tabla. Cambiar su semántica añadiría
  alcance sin beneficio observable hoy.
- Distinguir fase dentro de una misma competición (regular season vs. playoffs de ACB): ambas se
  guardan bajo el mismo valor de liga, igual que hoy.
- Los 81 partidos **pendientes** de la temporada 2026-27 (42 `acb` + 38 `euroleague` + 1
  `supercopa`): su calendario BBR vive en una página de temporada distinta (`season=2027`, no
  `2026`) que este backfill no re-descarga; se autocorregirán cuando el pipeline normal
  (`--refresh-teams` o scouting bajo demanda) visite esa página ya con el código corregido.

## Módulos y capas afectados

| Fichero | Tipo de cambio | Resumen |
|---|---|---|
| `scraper/parser.py` | modificado | Extrae la competición real del `id` de cada tabla de calendario BBR y la añade como clave `"league"` en cada partido devuelto por `parse_schedule_games()`. |
| `db/storage.py` | modificado | `upsert_game()` actualiza `.league` también cuando el partido ya existe (hoy el branch de actualización lo ignora silenciosamente). |
| `main.py` | modificado | (a) el bucle de captura de box scores deja de forzar `league=source_team.league`; (b) `_team_games_from_db()` propaga la liga ya persistida al reconstruir partidos sin red; (c) nuevo flag `--fix-league` + `backfill_league()` (con copia de seguridad previa). |
| `data/baskonia.db` | dato, no código | El backfill escribe sobre él; se crea antes `data/baskonia.db.bak-<timestamp>`. |
| `README.md` | doc (etapa 4, Documenter) | Reflejar el fix y actualizar la limitación conocida descrita en líneas ~176-178 y el punto ya marcado `[x]` de líneas ~653-654 (la liga del rival ya no es "una suposición basada en el equipo de origen"). |

No se cruza la regla de capas (`scraper/ → db/ → .../ main.py`): `parser.py` sigue sin importar
`db`; `storage.py` sigue sin importar `scraper`; `main.py` sigue siendo la capa de borde que ya
importaba ambos. No hay `common/` en este proyecto (paquete plano); el impacto de este cambio se
limita a este único repo.

## Diseño

### 1. Evidencia que sustenta el diseño (verificada contra BBR real y `data/baskonia.db` real)

Se descargó una vez (sin bucles, respetando el espíritu de `REQUEST_DELAY`) el HTML real de
calendario de los 3 equipos que hoy tienen roster propio en la BD (`vitoria`, `bilbao`,
`gran-canaria` — los únicos con `fetch_team()` ya ejecutado alguna vez, confirmado por
`SELECT slug, count(players) ... HAVING count>0`) y se inspeccionaron sus tablas:

```
vitoria:      id="vitoria-ELG-regular-season"       (38 filas)
              id="vitoria-SPA-regular-season"       (38 filas)
              id="vitoria-SPA-playoffs"              (3 filas)
bilbao:       id="bilbao-SPA-regular-season"        (38 filas)
              id="bilbao-SPA-playoffs"                (? filas)
gran-canaria: id="gran-canaria-SPA-regular-season"  (38 filas)
```

El `id` de cada tabla de calendario de BBR internacional sigue el patrón
`<slug-equipo>-<CÓDIGO>-<fase>` donde `<fase>` es `regular-season` o `playoffs` (ya detectado por
el parser actual, que solo mira el sufijo) y `<CÓDIGO>` es un acrónimo en mayúsculas que identifica
la competición real de **todas** las filas de esa tabla: `SPA` = Liga ACB (familia "España"),
`ELG` = EuroLeague. No hace falta conocer el slug del equipo para extraerlo: basta una regex sobre
el final del `id` (`-([A-Z]+)-(?:regular-season|playoffs)$`), robusta incluso para slugs con
guiones propios (`gran-canaria`, `real-madrid`).

**Por qué no basta una heurística sin red (offline) para el 100% de los casos:** `Team.league` no
sirve como señal (confirmado: los 45 equipos de la BD tienen `league='acb'` sin excepción, también
clubes claramente extranjeros como Mónaco o Anadolu Efes, por el mismo bug de raíz). La identidad
del rival tampoco basta por sí sola: Real Madrid, Barcelona **y Valencia Basket** (verificado con
datos reales, no de memoria — Valencia sí juega Euroliga esta temporada según el HTML real de BBR)
juegan contra el Baskonia tanto en ACB como en Euroliga, así que un partido "Baskonia vs Valencia"
no es clasificable por rival. Contando encuentros jugados por pareja de equipos en la BD real: 59
parejas tienen exactamente 2 encuentros (ida/vuelta de una sola competición, sin ambigüedad), pero
4 parejas tienen 4 encuentros (`real-madrid`/`barcelona`/`valencia` vs `vitoria`) y 1 pareja tiene 5
(`joventut` vs `vitoria`, 2 liga regular + 3 playoffs). Sin visitar la tabla real de BBR no hay
forma fiable de saber cuáles de esos encuentros extra son Euroliga.

**Verificación end-to-end (la que debe reproducir la implementación):** parseando las 3 páginas
reales con la lógica descrita arriba y cruzando cada fila (fecha + rival + local/visitante) contra
los 139 partidos jugados de la BD real, **los 139 quedan resueltos sin ambigüedad, cero casos sin
resolver**. Reparto resultante: **101 `acb` / 38 `euroleague`** (antes: 139 `acb` / 0 `euroleague`).
Los 38 `euroleague` corresponden exactamente a: 16 rivales exclusivamente extranjeros de Euroliga ×
2 encuentros (32) + 1 encuentro de ida y 1 de vuelta de Euroliga contra cada uno de
`real-madrid`/`barcelona`/`valencia` (3 × 2 = 6) = 38.

### 2. `scraper/parser.py` — captura de la competición real

```python
# Código de competición embebido en el id de la tabla de calendario de BBR
# internacional (p.ej. "vitoria-ELG-regular-season", "vitoria-SPA-playoffs") ->
# valor de `games.league` a guardar. Verificado contra HTML real de BBR (tablas
# de vitoria/bilbao/gran-canaria, temporada 2026): "SPA" (Liga ACB) y "ELG"
# (EuroLeague) son los únicos códigos vistos. Un código no reconocido se guarda
# tal cual en minúsculas (mismo patrón que `_to_league` en
# scraper/baskonia_official.py) en vez de fallar.
_BBR_LEAGUE_MAP: Dict[str, str] = {
    "SPA": "acb",
    "ELG": "euroleague",
}


def _table_competition(table_id: str) -> Optional[str]:
    """Extrae la competición real codificada en el id de una tabla de calendario BBR.

    El código va siempre justo antes del sufijo de fase (`-regular-season` o
    `-playoffs`), independientemente de cuántos guiones tenga el slug del
    equipo (p.ej. "gran-canaria-SPA-regular-season"), así que no hace falta
    recibir el slug como parámetro.

    Args:
        table_id: Atributo `id` de la tabla (`<table id="...">`).

    Returns:
        Valor de liga a guardar en `games.league`, o `None` si el id no sigue
        el patrón conocido (tablas genéricas `games`/`schedule`, o el
        fallback por cabecera Date/Result de `parse_schedule_games`): en ese
        caso el llamador debe seguir usando su liga de reserva actual
        (`team.league`), igual que hoy.
    """
    match = re.search(r"-([A-Z]+)-(?:regular-season|playoffs)$", table_id)
    if not match:
        return None
    comp_code = match.group(1)
    return _BBR_LEAGUE_MAP.get(comp_code, comp_code.lower())
```

Cambios en `parse_schedule_games()` (resolver la liga **una vez por tabla**, no por fila — todas
las filas de una tabla comparten competición) y en `_parse_schedule_table()` (nuevo parámetro
`league: Optional[str] = None`, se añade `"league": league` a cada dict de partido devuelto):

```python
def parse_schedule_games(html: str) -> List[Dict[str, object]]:
    ...
    games: List[Dict[str, object]] = []
    for schedule_table in schedule_tables:
        league = _table_competition(schedule_table.get("id", ""))
        games.extend(_parse_schedule_table(schedule_table, league))
    return games


def _parse_schedule_table(schedule_table, league: Optional[str] = None) -> List[Dict[str, object]]:
    ...
    games.append(
        {
            "date": date,
            "opponent": opp,
            "opponent_slug": opponent_slug,
            "boxscore_url": boxscore_url,
            "is_home": is_home,
            "points": pts,
            "opp_points": opp_pts,
            "notes": notes,
            "league": league,   # NUEVO: None si no se pudo determinar (ver _table_competition)
        }
    )
    ...
```

Esta forma (`"league"` como clave del dict de partido) replica exactamente el contrato que ya usa
`scraper/baskonia_official.py::fetch_upcoming_games()` — el patrón que pedía la solicitud — y que
`main.py::persist_schedule()` **ya consume correctamente** hoy (`league=game.get("league") or
team.league`, línea 228): no hace falta tocar `persist_schedule()`.

### 3. `db/storage.py` — `upsert_game()` debe poder corregir un partido ya existente

Bug colateral encontrado durante el diseño: en el branch de actualización (partido que ya existe),
`upsert_game()` **nunca toca `.league`**, a diferencia de `home_score`/`away_score`/`boxscore_url`/
`notes`, que sí siguen el patrón `nuevo or actual`. Sin arreglar esto, ni el backfill ni ninguna
ejecución futura del pipeline podrían corregir la liga de un partido ya persistido (solo la fijarían
bien en partidos nuevos). Fix mínimo, mismo patrón que el resto de la función:

```python
    else:
        game.league = league or game.league
        game.home_score = home_score or game.home_score
        game.away_score = away_score or game.away_score
        game.boxscore_url = boxscore_url or game.boxscore_url
        game.notes = notes or game.notes
```

Contrato de `upsert_game()` no cambia de firma, solo de comportamiento (más correcto). Es
retrocompatible: cualquier llamador que ya pasara la liga correcta sigue funcionando igual; el único
efecto nuevo es que una llamada con una liga distinta a la ya guardada **ahora sí la corrige**.

### 4. `main.py` — dejar de forzar la liga fija del equipo

**(a) Bucle de captura de box scores** (hoy ignora `game.get("league")`, línea ~556):

```python
            game_obj = upsert_game(
                session,
                date=str(game.get("date", "")),
                league=game.get("league") or source_team.league,   # antes: source_team.league
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                boxscore_url=game.get("boxscore_url"),
                notes=game.get("notes") or None,
            )
```

**(b) `_team_games_from_db()`** (hoy no incluye la liga al reconstruir partidos desde la BD sin
red; sin este cambio, en cada ejecución normal sin `--refresh-teams` el paso (a) recibiría
`game.get("league") is None` y volvería a caer en `source_team.league`, **regresando** partidos ya
corregidos a `'acb'` en la siguiente ejecución del pipeline):

```python
        games.append(
            {
                "date": row.date,
                "opponent": opponent_team.name,
                "opponent_slug": opponent_team.slug,
                "boxscore_url": row.boxscore_url,
                "is_home": is_home,
                "points": points,
                "opp_points": opp_points,
                "notes": row.notes,
                "league": row.league,   # NUEVO: propaga la liga ya persistida (evita regresión)
                "team_slug": team_slug,
            }
        )
```

**(c) Backfill: nuevo flag `--fix-league`**

```python
def backfill_league() -> None:
    """Corrige `Game.league` de partidos ya persistidos con la competición real de BBR.

    Reutiliza el calendario de los equipos que ya tienen roster propio guardado
    (hoy: vitoria, bilbao, gran-canaria — los únicos con `fetch_team()` ya
    ejecutado alguna vez): son los únicos cuya página de calendario de BBR se
    puede re-visitar para leer la competición real de cada fila (ver
    `scraper.parser._table_competition`). No re-descarga equipos sin roster
    propio (su calendario solo se conoce por reconstrucción desde la BD u otra
    fuente, sin id de tabla de BBR que consultar).

    Hace copia de seguridad de la base de datos (`_backup_database()`) antes de
    escribir nada, y es idempotente: `upsert_game`/`persist_schedule` ya son
    upserts por `(date, home_team_id, away_team_id)`, así que ejecutarlo varias
    veces no duplica filas; solo puede volver a fijar `.league` al mismo valor
    correcto.
    """
    logger.info("=== Backfill de Game.league ===")
    backup_path = _backup_database()
    logger.info("Copia de seguridad creada en %s", backup_path)

    client = BBRClient()
    Session = models.init_db()
    session = Session()
    try:
        logger.info("Distribución ANTES (partidos jugados): %s", _league_counts(session))

        teams_with_roster = session.query(models.Team).join(models.Team.players).distinct().all()
        logger.info("Equipos con roster propio a re-consultar: %s", [t.slug for t in teams_with_roster])

        for team in teams_with_roster:
            logger.info("--- Re-descargando calendario de %s ---", team.slug)
            try:
                team_data = fetch_team(client, team.slug, config.SEASON)
            except Exception as exc:  # noqa: BLE001
                logger.warning("No se pudo re-descargar el calendario de %s: %s", team.slug, exc)
                continue
            games = parse_schedule_games(team_data.get("html", ""))
            persist_schedule(session, team, games)
            session.commit()

        logger.info("Distribución DESPUÉS (partidos jugados): %s", _league_counts(session))
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Error en el backfill de liga: %s", exc)
        sys.exit(1)
    finally:
        session.close()


def _league_counts(session) -> Dict[str, int]:
    """Cuenta partidos jugados por liga (para el log antes/después del backfill)."""
    rows = session.query(models.Game.league).filter(models.Game.home_score.isnot(None)).all()
    counts: Dict[str, int] = {}
    for (league,) in rows:
        counts[league] = counts.get(league, 0) + 1
    return counts


def _backup_database() -> str:
    """Copia el fichero sqlite de `config.DATABASE_URL` con un sufijo de timestamp.

    Solo soporta `DATABASE_URL` de tipo `sqlite:///<ruta>` (único backend usado
    en este PoC). Nunca sobrescribe una copia anterior: cada llamada genera un
    fichero nuevo.

    Returns:
        Ruta del fichero de copia de seguridad creado.

    Raises:
        RuntimeError: si `DATABASE_URL` no es sqlite (no hay fichero que copiar).
    """
    prefix = "sqlite:///"
    if not config.DATABASE_URL.startswith(prefix):
        raise RuntimeError(f"Backup no soportado para DATABASE_URL='{config.DATABASE_URL}' (solo sqlite:///)")
    db_path = config.DATABASE_URL[len(prefix):]
    backup_path = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    return backup_path
```

Nuevos imports en `main.py`: `shutil`, `from datetime import datetime`. Nuevo argumento CLI:

```python
    arg_parser.add_argument(
        "--fix-league",
        action="store_true",
        help=(
            "Backfill de Game.league: hace copia de seguridad de la BD, vuelve a descargar el "
            "calendario de los equipos con roster propio (vitoria/bilbao/gran-canaria hoy) y "
            "corrige la competición real de cada partido ya persistido. No ejecuta el resto del "
            "pipeline."
        ),
    )
    args = arg_parser.parse_args()
    if args.fix_league:
        backfill_league()
    else:
        run(refresh_teams=args.refresh_teams)
```

`--fix-league` es una acción independiente de `run()` (no descarga box scores ni plantilla oficial):
su único propósito es corregir `games.league` de lo ya persistido, con el mismo espíritu acotado que
`--refresh-teams`.

### 5. Coste de red del backfill

3 peticiones (`fetch_team()` hace 2 peticiones por equipo — página de equipo + calendario — × 3
equipos con roster propio = 6 peticiones; podría reducirse a 3 con una función `fetch_schedule_only`
dedicada, pero no se justifica el nuevo símbolo solo para ahorrar 3 peticiones de una operación que
se ejecuta una vez). Con `REQUEST_DELAY=20s` (default), el backfill tarda **≈ 2 minutos** (6 × 20s
+ tiempo de descarga). Muy por debajo de cualquier riesgo de bloqueo por BBR mencionado en el
README/`config.py`.

## Paquetes de trabajo

| WP | Descripción | Ficheros | Especialista | depende_de |
|---|---|---|---|---|
| WP-1 | `_BBR_LEAGUE_MAP` + `_table_competition()`; enhebrar `league` por tabla en `parse_schedule_games()`/`_parse_schedule_table()` | `scraper/parser.py` | ninguno | - |
| WP-2 | `upsert_game()`: actualizar `.league` también cuando el partido ya existe | `db/storage.py` | ninguno | - |
| WP-3 | Bucle de captura de box scores usa `game.get("league") or source_team.league`; `_team_games_from_db()` propaga `row.league` | `main.py` | ninguno | WP-1, WP-2 |
| WP-4 | Flag `--fix-league` + `backfill_league()` + `_backup_database()` + `_league_counts()` | `main.py` | ninguno | WP-3 |
| WP-5 | Ejecutar `python main.py --fix-league` contra `data/baskonia.db` real; verificar cifras (ver Criterios de aceptación) y registrar el resultado en `02_implementation.md` | — (operación, no código) | ninguno | WP-4 |

WP-1 y WP-2 son independientes entre sí (ficheros distintos, sin contrato compartido) y
paralelizables. WP-3 depende de ambos porque consume la clave `"league"` que crea WP-1 y necesita
que `upsert_game()` (WP-2) sepa aplicarla a partidos ya existentes. WP-4 comparte fichero con WP-3
(mismo `main.py`) y se apoya en el pipeline ya corregido. WP-5 es la ejecución/verificación real,
no paralelizable con nada anterior.

## Clase de complejidad

**normal**: varios ficheros (`parser.py`, `storage.py`, `main.py` ×2 cambios + 1 función nueva),
contratos ya conocidos (mismo patrón `"league"` que `baskonia_official.py`, mismo patrón de flag
CLI que `--refresh-teams`), sin nuevo módulo, sin cruce de capas, sin tocar código compartido entre
repos (este proyecto no tiene `common/`).

## Criterios de aceptación

1. **Build**: `python -m py_compile scraper/parser.py db/storage.py main.py` sin error; smoke
   import (`python -c "import app, main, stats, insights, report, config"`) sin excepción.
2. **Captura en origen**: para un equipo con tabla de calendario reconocida (id que termina en
   `-regular-season` o `-playoffs`), cada partido devuelto por `parse_schedule_games()` incluye
   `"league"` con el valor derivado de `_table_competition()` (no `None`) cuando el id de tabla
   sigue el patrón conocido.
3. **Persistencia**: tras `python main.py` (ejecución normal, sin flags) sobre un equipo
   recién refrescado, los partidos nuevos/actualizados de ese equipo quedan con `Game.league` igual
   a la competición real de la tabla de origen, no a `Team.league`.
4. **No regresión entre ejecuciones**: ejecutar `python main.py` dos veces seguidas (sin
   `--refresh-teams`) deja la misma distribución de `Game.league` ambas veces (verifica que
   `_team_games_from_db()` propaga correctamente lo ya corregido).
5. **Backup no negociable**: al ejecutar `python main.py --fix-league`, existe un fichero
   `data/baskonia.db.bak-<timestamp>` con tamaño igual al de `data/baskonia.db` **antes** de que se
   ejecute el primer `session.commit()` de escritura.
6. **Idempotencia del backfill**: ejecutar `python main.py --fix-league` dos veces consecutivas
   deja exactamente los mismos resultados de `SELECT league, count(*) FROM games WHERE
   home_score IS NOT NULL GROUP BY league` y el mismo `SELECT count(*) FROM games` (220) ambas
   veces; ninguna tabla (`games`, `boxscores`, `team_game_stats`, `players`) cambia de tamaño entre
   la 1ª y la 2ª ejecución.
7. **Resultado numérico exacto** (verificado en este diseño contra BBR real, ver sección
   "Diseño" §1 — la implementación debe reproducir estos números exactos sobre la BD actual, salvo
   que BBR haya cambiado su HTML entre el diseño y la implementación): de los 139 partidos ya
   jugados, **101 quedan con `league='acb'` y 38 con `league='euroleague'`** (antes: 139/0). 0
   partidos jugados quedan sin resolver (todos los 139 deben tener un valor de liga tras el
   backfill, nunca `None`/vacío).
8. **Sin efecto en pendientes fuera de alcance**: los 81 partidos pendientes (42 `acb` + 38
   `euroleague` + 1 `supercopa`) no cambian de valor con este backfill (pertenecen a la temporada
   2026-27, calendario no re-descargado aquí).

## Supuestos y riesgos

| # | Supuesto/riesgo | Validación |
|---|---|---|
| 1 | Los únicos códigos de competición en los `id` de tabla de BBR para este dataset son `SPA` y `ELG` (verificado contra el HTML real de `vitoria`/`bilbao`/`gran-canaria`, temporada 2026). | Ya verificado en el diseño. Riesgo residual: un rival scouteado en el futuro que juegue otra competición (p.ej. Basketball Champions League) tendría un código nuevo; `_table_competition()` no falla ante uno desconocido (lo guarda en minúsculas, mismo patrón que `_to_league`), así que no hay riesgo de excepción, solo de una etiqueta nueva no listada en `README.md` hasta que el Documenter la documente. |
| 2 | La tabla `<slug>-SPA-regular-season` de BBR puede incluir encuentros que no son estrictamente liga regular (se detectaron recuentos >2 frente a algunos rivales, p.ej. 3 veces Real Madrid dentro de esa misma tabla — probablemente Copa del Rey fusionada bajo el mismo id de competición por BBR). | No bloqueante para esta feature: el objetivo es distinguir ACB-familia vs Euroliga vs Supercopa, no sub-fases dentro de ACB. Documentar como limitación conocida (Documenter). |
| 3 | Colisión futura improbable: si BBR llegara a publicar un partido que `baskonia_official.py` ya etiquetó correctamente (p.ej. Supercopa) bajo una tabla sin id reconocible, el fallback a `team.league` podría sobrescribirlo con `'acb'`. | Hoy no ocurre (evidencia: los 39 partidos no-`acb` pendientes vienen exclusivamente de `baskonia_official`, BBR no publica Supercopa). Vigilar si cambia tras futuras ejecuciones de `--refresh-teams`. |
| 4 | `Team.league` se deja sin tocar (sigue "acb" fijo para todos los equipos). | Confirmado sin uso fuera de `main.py` (grep en todo el repo). Si una feature futura necesita el "país/liga doméstica real" de un equipo como dato propio (no como fallback de partido), habrá que revisar esta decisión entonces. |
| 5 | Los 3 equipos con roster propio hoy (`vitoria`, `bilbao`, `gran-canaria`) cubren el 100% de los 139 partidos jugados (cada partido jugado tiene al menos uno de estos 3 como local o visitante). | Ya verificado por consulta SQL contra la BD real en este diseño (0 partidos jugados sin ninguno de los 3). Si entre el diseño y la implementación se scoutea un equipo nuevo con partidos jugados propios no capturados por estos 3, `backfill_league()` lo incluye automáticamente (itera sobre "equipos con roster", no una lista fija). |

## Preguntas abiertas para el usuario

Ninguna bloqueante: la investigación (re-descarga real de las 3 páginas de calendario relevantes +
cruce contra los 139 partidos jugados de la BD real) dio una respuesta completa y verificada —
cobertura del 100% sin ambigüedad, coste de red trivial (6 peticiones, ~2 minutos) — así que no hay
un trade-off genuino que requiera decisión humana. Se documentan como transparencia (no como
bloqueo) las decisiones de alcance ya tomadas y justificadas arriba: no tocar `Team.league`, no
tocar los partidos pendientes de 2026-27, no distinguir Copa del Rey de Liga ACB dentro de `'acb'`.

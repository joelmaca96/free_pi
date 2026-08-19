"""Arnés de paridad — Fase F0 de la migración.

Vuelca a JSON canónico la salida de las funciones de datos actuales de
`app.py`/`insights.py` para una combinación fija `(team, season, league,
last_n)`. Sirve de **oráculo objetivo**: congela el comportamiento actual
antes de mover nada, de modo que las fases posteriores de la migración
puedan demostrar paridad con un `diff` en vez de con una opinión.

Uso:
    python tools/parity_dump.py --team vitoria --season 2025 --last-n 5 \
        --reference-date 2026-08-19 --out tests/parity/baseline/x.json

Salida canónica:
    - Claves ordenadas alfabéticamente.
    - Flotantes redondeados a 4 decimales.
    - `None`/`NaN`/`pd.NA` → `null`.

Determinismo:
    `player_load_df` (ventana de N días) y `upcoming_games` (filtro por
    `datetime.now()`) dependen de la fecha actual. El arnés acepta
    `--reference-date` para inyectar una fecha fija y ser reproducible:
    - `player_load_df` recibe la fecha vía `games_in_window(reference_date=...)`.
    - `upcoming_games` no acepta fecha; el arnés reimplementa el filtro de
      fecha sobre su salida (equivalente al comportamiento actual), sin tocar
      `app.py` (principio de solo lectura en F0).

Solo lectura: el arnés abre `data/baskonia.db` en modo lectura y nunca hace
peticiones de red.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Añade la raíz del proyecto al sys.path para poder importar app/insights/db.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import insights  # noqa: E402
from db import models  # noqa: E402


def _round(value, ndigits=4):
    """Redondea un flotante a `ndigits` decimales; deja el resto tal cual."""
    if isinstance(value, float):
        return round(value, ndigits)
    return value


def _clean(value):
    """Normaliza un valor para JSON canónico: NaN/NA → None, flotantes redondeados."""
    if value is None:
        return None
    # pd.NA / numpy NaN / float('nan')
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return _round(value)


def _df_to_records(df):
    """Convierte un DataFrame a lista de dicts canónicos (NaN → null, flotantes redondeados)."""
    if df is None or df.empty:
        return []
    records = df.to_dict(orient="records")
    return [{k: _clean(v) for k, v in row.items()} for row in records]


def _player_load_df(session, team, window_days, reference_date):
    """Reimplementa `app.player_load_df` con una fecha de referencia inyectada.

    `app.player_load_df` no acepta fecha de referencia (usa `datetime.now()`
    internamente vía `games_in_window`). Para que el volcado sea reproducible
    se replica su lógica exacta — `games_in_window(..., reference_date)` +
    `insights.player_load` — sin modificar `app.py` (solo lectura en F0).
    """
    games = app.games_in_window(session, team, window_days, reference_date=reference_date)
    rows = [
        {
            "Jugador": r["player_name"],
            "PJ ventana": r["games"],
            "MIN totales": round(r["total_minutes"], 1),
            "MIN/partido": round(r["avg_minutes"], 1),
        }
        for r in insights.player_load(session, team, games)
    ]
    return pd.DataFrame(rows)


def _game_to_dict(game):
    """Serializa un partido (jugado o pendiente) a dict canónico."""
    return {
        "id": game.id,
        "date": game.date,
        "league": game.league,
        "home_team": game.home_team.name if game.home_team else None,
        "away_team": game.away_team.name if game.away_team else None,
        "home_score": _clean(game.home_score),
        "away_score": _clean(game.away_score),
    }


def _filter_upcoming_by_reference(games, reference_date):
    """Reimplementa el filtro de fecha de `app.upcoming_games` con una fecha fija.

    `app.upcoming_games` filtra por `datetime.now()`; como no acepta fecha de
    referencia, se aplica aquí el mismo criterio (fecha >= reference_date)
    sobre su salida para que el volcado sea reproducible.
    """
    return [g for g in games if (app.parse_bbr_date(g.date) or datetime.max) >= reference_date]


def build_dump(session, team, season, league, last_n, reference_date, deterministic=True):
    """Construye el dict canónico con las 13 salidas de paridad.

    Args:
        session: sesión SQLAlchemy activa (solo lectura).
        team: objeto `models.Team` de referencia.
        season: año de inicio de temporada o `None`.
        league: código de competición o `None`.
        last_n: nº de partidos recientes para las tablas de forma.
        reference_date: `datetime` de referencia para las salidas dependientes de fecha.
        deterministic: `True` si `reference_date` fue inyectada por el usuario (volcado
            reproducible); `False` si se usó `datetime.now()` (las salidas dependientes
            de fecha no son estables entre ejecuciones).

    Returns:
        Dict canónico con las claves de paridad.
    """
    # --- Salidas dependientes de fecha (reproducibles con reference_date) ---
    past = app.past_games(session, team, season, league)
    upcoming_raw = app.upcoming_games(session, team)
    upcoming = _filter_upcoming_by_reference(upcoming_raw, reference_date)

    # --- DataFrames ---
    team_summary = app.team_summary_df(session, team, season, league)
    recent_games = app.recent_games_df(session, team, last_n, season, league)
    recent_form = app.recent_form_df(session, team, last_n, season, league)
    streaks = app.streaks_df(session, team, season, last_n, league)
    # player_load_df no acepta fecha de referencia; se reimplementa su lógica
    # (games_in_window + insights.player_load) con la fecha inyectada, sin tocar
    # app.py (principio de solo lectura en F0). Equivalente al comportamiento actual.
    player_load = _player_load_df(session, team, window_days=14, reference_date=reference_date)
    head_to_head = app.head_to_head_summary_df(session, team, season, league)

    # schedule_difficulty: necesita la salida de insights.schedule_difficulty
    difficulty = insights.schedule_difficulty(
        session, team, upcoming, season=season, next_n=last_n, league=league
    )
    schedule_diff = app.schedule_difficulty_df(difficulty)

    # boxscore_df: sobre el partido jugado más reciente (el primero de `past`)
    if past:
        boxscore = app.boxscore_df(session, past[0], team)
    else:
        boxscore = pd.DataFrame()

    # --- insights ---
    advanced = insights.team_advanced_summary(session, team, season=season, league=league)

    # project_next_matchup: contra el rival del primer partido pendiente
    projection = None
    if upcoming:
        first = upcoming[0]
        rival = first.away_team if first.home_team_id == team.id else first.home_team
        projection = insights.project_next_matchup(
            session, team, rival, season=season, league=league
        )

    narrative = insights.scouting_narrative(
        session, team, season=season, recent_n=last_n, league=league
    )

    return {
        "meta": {
            "team": team.slug,
            "season": season,
            "league": league,
            "last_n": last_n,
            "reference_date": reference_date.date().isoformat(),
            "deterministic": deterministic,
        },
        "past_games": [_game_to_dict(g) for g in past],
        "upcoming_games": [_game_to_dict(g) for g in upcoming],
        "team_summary_df": _df_to_records(team_summary),
        "recent_games_df": _df_to_records(recent_games),
        "recent_form_df": _df_to_records(recent_form),
        "streaks_df": _df_to_records(streaks),
        "schedule_difficulty_df": _df_to_records(schedule_diff),
        "player_load_df": _df_to_records(player_load),
        "head_to_head_summary_df": _df_to_records(head_to_head),
        "boxscore_df": _df_to_records(boxscore),
        "team_advanced_summary": {k: _clean(v) for k, v in advanced.items()},
        "project_next_matchup": (
            {k: _clean(v) for k, v in projection.items()} if projection else None
        ),
        "scouting_narrative": narrative,
    }


def _canonical_json(dump):
    """Serializa el dict a JSON canónico: claves ordenadas, indent 2, ensure_ascii=False."""
    return json.dumps(dump, sort_keys=True, indent=2, ensure_ascii=False)


def main(argv=None):
    """Punto de entrada CLI del arnés de paridad."""
    parser = argparse.ArgumentParser(description="Arnés de paridad — Fase F0")
    parser.add_argument("--team", required=True, help="Slug del equipo (p.ej. vitoria)")
    parser.add_argument("--season", type=int, default=None, help="Año de inicio de temporada")
    parser.add_argument("--league", default=None, help="Código de competición (acb/euroleague)")
    parser.add_argument("--last-n", type=int, default=5, help="Nº de partidos recientes")
    parser.add_argument(
        "--reference-date",
        default=None,
        help="Fecha de referencia YYYY-MM-DD para reproducibilidad (por defecto: hoy)",
    )
    parser.add_argument("--out", default=None, help="Ruta de salida (por defecto: stdout)")
    args = parser.parse_args(argv)

    # `deterministic` refleja si la fecha fue inyectada por el usuario: si se omite
    # `--reference-date`, se usa `datetime.now()` y el volcado no es reproducible.
    deterministic = args.reference_date is not None
    reference_date = (
        datetime.strptime(args.reference_date, "%Y-%m-%d") if args.reference_date else datetime.now()
    )

    session = app.get_session()
    try:
        team = session.query(models.Team).filter_by(slug=args.team).first()
        if team is None:
            print(f"ERROR: equipo '{args.team}' no encontrado en la BD.", file=sys.stderr)
            return 2

        dump = build_dump(
            session,
            team,
            season=args.season,
            league=args.league,
            last_n=args.last_n,
            reference_date=reference_date,
            deterministic=deterministic,
        )
        output = _canonical_json(dump)

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output, encoding="utf-8")
            print(f"Volcado escrito en {out_path}")
        else:
            print(output)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())

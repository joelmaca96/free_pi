"""Carga idempotente de un `NormalizedGame` en el esquema de scouting.

Estrategia de idempotencia (re-ejecutar el pipeline no duplica filas):
- Tablas con clave natural real (`games`, `game_advanced_stats`,
  `game_team_quarter_stats`, `player_game_stats`, `game_zone_stats`):
  `INSERT ... ON CONFLICT DO UPDATE`.
- Tablas "detalle" sin clave natural (`lineups`+`lineup_players`,
  `lineup_stints`+`lineup_stint_players`, `shots`, `key_events`): se borran
  las filas de ese `game_id` y se reinsertan enteras.
  Es más simple y igual de idempotente que intentar casar cada fila.

`games.id` no necesita tabla puente de identidad (a diferencia de equipos y
jugadores): cada partido pertenece a una sola fuente/competición, así que el
propio `NormalizedGame.id` ya viene construido por el parser como
`f"{source}-{external_game_id}"`, determinista y único.
"""
import logging
from typing import Dict, List, Set

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from .schema_types import NormalizedGame

logger = logging.getLogger(__name__)


def list_existing_external_ids(engine: Engine, source: str, season_start_year: int) -> Set[str]:
    """Ids externos (sin prefijo) ya presentes en `games` para una fuente/temporada.

    Lo usa `discover_missing_games` de cada pipeline para saber qué partidos del
    calendario de la fuente ya están cargados. `games.id` siempre se construye
    como `f"{source}-{external_id}"` (ver `raw_game.parse_and_resolve`), así que
    basta un LIKE por prefijo + join a `seasons` por `label`
    (`f"{season_start_year}-{season_start_year + 1}"`, mismo formato que
    `identity.get_or_create_season`). No es parte del camino de escritura; vive
    aquí porque este es el otro módulo que ya conoce el formato de `games.id`.

    Args:
        engine: engine SQLAlchemy sobre el esquema de scouting.
        source: prefijo de fuente (`"acb"`, `"euroleague"`).
        season_start_year: año de inicio de temporada (`2025` = "2025-2026").

    Returns:
        Conjunto de ids externos (sin el prefijo `"<source>-"`).
    """
    label = f"{season_start_year}-{season_start_year + 1}"
    prefix = f"{source}-"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT games.id FROM games JOIN seasons ON seasons.id = games.season_id "
                "WHERE seasons.label = :label AND games.id LIKE :like_prefix"
            ),
            {"label": label, "like_prefix": f"{prefix}%"},
        ).all()
    return {row[0][len(prefix):] for row in rows}


def load_game(conn: Connection, game: NormalizedGame) -> None:
    """Vuelca un partido completo (y sus tablas hijas) de forma idempotente."""
    _upsert_game(conn, game)
    for advanced in game.advanced:
        _upsert_game_advanced_stats(conn, game.id, advanced)
    for quarter_stat in game.quarter_stats:
        _upsert_quarter_stats(conn, game.id, quarter_stat)
    for stat in game.boxscore:
        _upsert_player_game_stats(conn, game.id, stat)
    _replace_lineups(conn, game.id, game.lineups)
    _replace_stints(conn, game.id, game.stints)
    _replace_shots(conn, game.id, game.shots)
    _replace_zone_stats_from_shots(conn, game.id, game.shots)
    _replace_key_events(conn, game.id, game.key_events)
    _replace_score_progression(conn, game.id, game.score_progression)
    logger.info(
        "game %s cargado (%d boxscore, %d lineups, %d tramos, %d shots, %d eventos)",
        game.id, len(game.boxscore), len(game.lineups), len(game.stints),
        len(game.shots), len(game.key_events),
    )


def _upsert_game(conn: Connection, game: NormalizedGame) -> None:
    conn.execute(
        text(
            """
            INSERT INTO games
                (id, season_id, competition_id, home_team_id, away_team_id,
                 game_date, home_score, away_score, pace, narrative)
            VALUES
                (:id, :season_id, :competition_id, :home_team_id, :away_team_id,
                 :game_date, :home_score, :away_score, :pace, :narrative)
            ON CONFLICT (id) DO UPDATE SET
                season_id = excluded.season_id,
                competition_id = excluded.competition_id,
                home_team_id = excluded.home_team_id,
                away_team_id = excluded.away_team_id,
                game_date = excluded.game_date,
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                pace = excluded.pace,
                narrative = excluded.narrative
            """
        ),
        {
            "id": game.id,
            "season_id": game.season_id,
            "competition_id": game.competition_id,
            "home_team_id": game.home_team_id,
            "away_team_id": game.away_team_id,
            "game_date": game.game_date,
            "home_score": game.home_score,
            "away_score": game.away_score,
            "pace": game.pace,
            "narrative": game.narrative,
        },
    )


def _upsert_game_advanced_stats(conn: Connection, game_id: str, advanced) -> None:
    conn.execute(
        text(
            """
            INSERT INTO game_advanced_stats
                (game_id, team_id, ortg, drtg, net_rating, efg_pct, ts_pct, tov_pct, orb_pct,
                 ast_pct, stl_pct, blk_pct, ft_rate, ast_to_ratio, ftm, fta)
            VALUES
                (:game_id, :team_id, :ortg, :drtg, :net_rating, :efg_pct, :ts_pct, :tov_pct, :orb_pct,
                 :ast_pct, :stl_pct, :blk_pct, :ft_rate, :ast_to_ratio, :ftm, :fta)
            ON CONFLICT (game_id, team_id) DO UPDATE SET
                ortg = excluded.ortg,
                drtg = excluded.drtg,
                net_rating = excluded.net_rating,
                efg_pct = excluded.efg_pct,
                ts_pct = excluded.ts_pct,
                tov_pct = excluded.tov_pct,
                orb_pct = excluded.orb_pct,
                ast_pct = excluded.ast_pct,
                stl_pct = excluded.stl_pct,
                blk_pct = excluded.blk_pct,
                ft_rate = excluded.ft_rate,
                ast_to_ratio = excluded.ast_to_ratio,
                ftm = excluded.ftm,
                fta = excluded.fta
            """
        ),
        {
            "game_id": game_id,
            "team_id": advanced.team_id,
            "ortg": advanced.ortg,
            "drtg": advanced.drtg,
            "net_rating": advanced.net_rating,
            "efg_pct": advanced.efg_pct,
            "ts_pct": advanced.ts_pct,
            "tov_pct": advanced.tov_pct,
            "orb_pct": advanced.orb_pct,
            "ast_pct": advanced.ast_pct,
            "stl_pct": advanced.stl_pct,
            "blk_pct": advanced.blk_pct,
            "ft_rate": advanced.ft_rate,
            "ast_to_ratio": advanced.ast_to_ratio,
            "ftm": advanced.ftm,
            "fta": advanced.fta,
        },
    )


def _upsert_quarter_stats(conn: Connection, game_id: str, quarter_stat) -> None:
    conn.execute(
        text(
            """
            INSERT INTO game_team_quarter_stats (game_id, team_id, quarter, points_for, points_against)
            VALUES (:game_id, :team_id, :quarter, :points_for, :points_against)
            ON CONFLICT (game_id, team_id, quarter) DO UPDATE SET
                points_for = excluded.points_for,
                points_against = excluded.points_against
            """
        ),
        {
            "game_id": game_id,
            "team_id": quarter_stat.team_id,
            "quarter": quarter_stat.quarter,
            "points_for": quarter_stat.points_for,
            "points_against": quarter_stat.points_against,
        },
    )


def _upsert_player_game_stats(conn: Connection, game_id: str, stat) -> None:
    conn.execute(
        text(
            """
            INSERT INTO player_game_stats
                (game_id, player_id, minutes, pts, reb, ast, efg_pct, ftm, fta)
            VALUES (:game_id, :player_id, :minutes, :pts, :reb, :ast, :efg_pct, :ftm, :fta)
            ON CONFLICT (game_id, player_id) DO UPDATE SET
                minutes = excluded.minutes,
                pts = excluded.pts,
                reb = excluded.reb,
                ast = excluded.ast,
                efg_pct = excluded.efg_pct,
                ftm = excluded.ftm,
                fta = excluded.fta
            """
        ),
        {
            "game_id": game_id,
            "player_id": stat.player_id,
            "minutes": stat.minutes,
            "pts": stat.pts,
            "reb": stat.reb,
            "ast": stat.ast,
            "efg_pct": stat.efg_pct,
            "ftm": stat.ftm,
            "fta": stat.fta,
        },
    )


def _replace_lineups(conn: Connection, game_id: str, lineups: List) -> None:
    lineup_ids = [
        row[0]
        for row in conn.execute(text("SELECT id FROM lineups WHERE game_id = :g"), {"g": game_id}).all()
    ]
    if lineup_ids:
        conn.execute(
            text("DELETE FROM lineup_players WHERE lineup_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": lineup_ids},
        )
        conn.execute(text("DELETE FROM lineups WHERE game_id = :g"), {"g": game_id})

    for lineup in lineups:
        result = conn.execute(
            text(
                "INSERT INTO lineups (game_id, minutes, plus_minus, team_id)"
                " VALUES (:g, :m, :pm, :team_id)"
            ),
            {
                "g": game_id,
                "m": lineup.minutes,
                "pm": lineup.plus_minus,
                # `None` si la fuente no lo dio: la columna es nullable y la
                # vista `lineup_team` lo infiere, avisando de que lo hace.
                "team_id": getattr(lineup, "team_id", None),
            },
        )
        lineup_id = result.lastrowid
        for player_id in lineup.player_ids:
            conn.execute(
                text("INSERT INTO lineup_players (lineup_id, player_id) VALUES (:l, :p)"),
                {"l": lineup_id, "p": player_id},
            )


def _replace_stints(conn: Connection, game_id: str, stints: List) -> None:
    """Reescribe los tramos de un partido (`lineup_stints` + `lineup_stint_players`).

    Mismo patrón de borrar-y-reinsertar por `game_id` que `_replace_lineups`,
    y por el mismo motivo: son tablas de detalle sin clave natural, así que
    casar fila a fila costaría más y sería igual de idempotente.
    """
    stint_ids = [
        row[0]
        for row in conn.execute(text("SELECT id FROM lineup_stints WHERE game_id = :g"), {"g": game_id}).all()
    ]
    if stint_ids:
        conn.execute(
            text("DELETE FROM lineup_stint_players WHERE stint_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": stint_ids},
        )
        conn.execute(text("DELETE FROM lineup_stints WHERE game_id = :g"), {"g": game_id})

    for stint in stints:
        result = conn.execute(
            text(
                "INSERT INTO lineup_stints"
                " (game_id, team_id, start_seconds, end_seconds, points_for, points_against, margin_start)"
                " VALUES (:g, :team_id, :start, :end, :pf, :pa, :margin)"
            ),
            {
                "g": game_id,
                "team_id": stint.team_id,
                "start": stint.start_seconds,
                "end": stint.end_seconds,
                "pf": stint.points_for,
                "pa": stint.points_against,
                "margin": stint.margin_start,
            },
        )
        stint_id = result.lastrowid
        for player_id in stint.player_ids:
            conn.execute(
                text("INSERT INTO lineup_stint_players (stint_id, player_id) VALUES (:s, :p)"),
                {"s": stint_id, "p": player_id},
            )


def _replace_shots(conn: Connection, game_id: str, shots: List) -> None:
    conn.execute(text("DELETE FROM shots WHERE game_id = :g"), {"g": game_id})
    for shot in shots:
        conn.execute(
            text(
                "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made, located)"
                " VALUES (:g, :p, :z, :x, :y, :made, :located)"
            ),
            {
                "g": game_id,
                "p": shot.player_id,
                "z": shot.zone_id,
                "x": shot.pos_x,
                "y": shot.pos_y,
                "made": int(shot.made),
                "located": int(shot.located),
            },
        )


def _replace_zone_stats_from_shots(conn: Connection, game_id: str, shots: List) -> None:
    """Agrega `shots` por (equipo, zona) y vuelca el resultado en `game_zone_stats`."""
    conn.execute(text("DELETE FROM game_zone_stats WHERE game_id = :g"), {"g": game_id})

    buckets: Dict[tuple, Dict[str, int]] = {}
    for shot in shots:
        if shot.zone_id is None:
            continue
        key = (shot.team_id, shot.zone_id)
        bucket = buckets.setdefault(key, {"made": 0, "volume": 0})
        bucket["volume"] += 1
        bucket["made"] += 1 if shot.made else 0

    for (team_id, zone_id), bucket in buckets.items():
        fg_pct = round(100.0 * bucket["made"] / bucket["volume"], 1) if bucket["volume"] else 0.0
        conn.execute(
            text(
                """
                INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume)
                VALUES (:g, :team_id, :zone_id, :fg_pct, :volume)
                ON CONFLICT (game_id, team_id, zone_id) DO UPDATE SET
                    fg_pct = excluded.fg_pct,
                    volume = excluded.volume
                """
            ),
            {"g": game_id, "team_id": team_id, "zone_id": zone_id, "fg_pct": fg_pct, "volume": bucket["volume"]},
        )


def _replace_key_events(conn: Connection, game_id: str, events: List) -> None:
    conn.execute(text("DELETE FROM key_events WHERE game_id = :g"), {"g": game_id})
    for event in events:
        conn.execute(
            text(
                "INSERT INTO key_events (game_id, team_id, quarter, game_clock, label)"
                " VALUES (:g, :team_id, :quarter, :clock, :label)"
            ),
            {
                "g": game_id,
                "team_id": event.team_id,
                "quarter": event.quarter,
                "clock": event.game_clock,
                "label": event.label,
            },
        )


def _replace_score_progression(conn: Connection, game_id: str, steps: List) -> None:
    conn.execute(text("DELETE FROM score_progression WHERE game_id = :g"), {"g": game_id})
    for step in steps:
        conn.execute(
            text(
                "INSERT INTO score_progression (game_id, step_index, home_score, away_score)"
                " VALUES (:g, :idx, :home, :away)"
            ),
            {"g": game_id, "idx": step.step_index, "home": step.home_score, "away": step.away_score},
        )

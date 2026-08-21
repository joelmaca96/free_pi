"""Carga idempotente de un `NormalizedGame` en el esquema de scouting.

Estrategia de idempotencia (re-ejecutar el pipeline no duplica filas):
- Tablas con clave natural real (`games`, `game_advanced_stats`,
  `player_game_stats`, `game_zone_stats`): `INSERT ... ON CONFLICT DO UPDATE`.
- Tablas "detalle" sin clave natural (`lineups`+`lineup_players`, `shots`,
  `key_events`): se borran las filas de ese `game_id` y se reinsertan enteras.
  Es más simple y igual de idempotente que intentar casar cada fila.

`games.id` no necesita tabla puente de identidad (a diferencia de equipos y
jugadores): cada partido pertenece a una sola fuente/competición, así que el
propio `NormalizedGame.id` ya viene construido por el parser como
`f"{source}-{external_game_id}"`, determinista y único.
"""
import logging
from typing import Dict, List

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

from .schema_types import NormalizedGame

logger = logging.getLogger(__name__)


def load_game(conn: Connection, game: NormalizedGame) -> None:
    """Vuelca un partido completo (y sus tablas hijas) de forma idempotente."""
    _upsert_game(conn, game)
    for advanced in game.advanced:
        _upsert_game_advanced_stats(conn, game.id, advanced)
    for stat in game.boxscore:
        _upsert_player_game_stats(conn, game.id, stat)
    _replace_lineups(conn, game.id, game.lineups)
    _replace_shots(conn, game.id, game.shots)
    _replace_zone_stats_from_shots(conn, game.id, game.shots)
    _replace_key_events(conn, game.id, game.key_events)
    _replace_score_progression(conn, game.id, game.score_progression)
    logger.info(
        "game %s cargado (%d boxscore, %d lineups, %d shots, %d eventos)",
        game.id, len(game.boxscore), len(game.lineups), len(game.shots), len(game.key_events),
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
                (game_id, team_id, ortg, drtg, net_rating, efg_pct, ts_pct, tov_pct, orb_pct)
            VALUES
                (:game_id, :team_id, :ortg, :drtg, :net_rating, :efg_pct, :ts_pct, :tov_pct, :orb_pct)
            ON CONFLICT (game_id, team_id) DO UPDATE SET
                ortg = excluded.ortg,
                drtg = excluded.drtg,
                net_rating = excluded.net_rating,
                efg_pct = excluded.efg_pct,
                ts_pct = excluded.ts_pct,
                tov_pct = excluded.tov_pct,
                orb_pct = excluded.orb_pct
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
        },
    )


def _upsert_player_game_stats(conn: Connection, game_id: str, stat) -> None:
    conn.execute(
        text(
            """
            INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct)
            VALUES (:game_id, :player_id, :minutes, :pts, :reb, :ast, :efg_pct)
            ON CONFLICT (game_id, player_id) DO UPDATE SET
                minutes = excluded.minutes,
                pts = excluded.pts,
                reb = excluded.reb,
                ast = excluded.ast,
                efg_pct = excluded.efg_pct
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
            text("INSERT INTO lineups (game_id, minutes, plus_minus) VALUES (:g, :m, :pm)"),
            {"g": game_id, "m": lineup.minutes, "pm": lineup.plus_minus},
        )
        lineup_id = result.lastrowid
        for player_id in lineup.player_ids:
            conn.execute(
                text("INSERT INTO lineup_players (lineup_id, player_id) VALUES (:l, :p)"),
                {"l": lineup_id, "p": player_id},
            )


def _replace_shots(conn: Connection, game_id: str, shots: List) -> None:
    conn.execute(text("DELETE FROM shots WHERE game_id = :g"), {"g": game_id})
    for shot in shots:
        conn.execute(
            text(
                "INSERT INTO shots (game_id, player_id, zone_id, pos_x, pos_y, made)"
                " VALUES (:g, :p, :z, :x, :y, :made)"
            ),
            {
                "g": game_id,
                "p": shot.player_id,
                "z": shot.zone_id,
                "x": shot.pos_x,
                "y": shot.pos_y,
                "made": int(shot.made),
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

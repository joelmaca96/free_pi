"""Capa de acceso a datos (repositorio) para la base de datos de scouting.

Cada método implementa una de las consultas que necesita la UI, descritas
como comentarios de ejemplo al final de `schema.sql`. Devuelve estructuras
de datos simples (dicts/listas) en vez de filas de SQLAlchemy, para no
acoplar a quien las consuma a este módulo.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import text
from sqlalchemy.engine import Engine

DateLike = Union[str, date]


def _as_iso_date(value: DateLike) -> str:
    """Normaliza un `date` o un str ISO a str ISO (`YYYY-MM-DD`)."""
    return value.isoformat() if isinstance(value, date) else value


class ScoutingRepository:
    """Repositorio de consultas de lectura sobre la base de datos de scouting."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def get_roster(self, season_label: str) -> List[Dict[str, Any]]:
        """Plantilla completa con medias de temporada (todas las competiciones), por dorsal.

        Las medias vienen de la vista `player_stats_combined` (agregada sobre
        `player_game_stats`/`games`); z-scores y "streak" ya no se almacenan,
        se calculan en la capa de aplicación a partir de estos promedios.
        """
        sql = text(
            """
            SELECT p.id, p.name, p.number, p.position, p.team_id, p.active,
                   p.photo_url, p.height_cm, p.birth_date, p.nationality,
                   v.gp, v.min_avg, v.pts_avg, v.reb_avg, v.ast_avg, v.efg_pct
            FROM players p
            JOIN player_stats_combined v ON v.player_id = p.id
            JOIN seasons s ON s.id = v.season_id
            WHERE s.label = :season_label
            ORDER BY p.number
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"season_label": season_label}).mappings().all()
        return [dict(row) for row in rows]

    def get_game_season_label(self, game_id: str) -> Optional[str]:
        """`seasons.label` del partido (`'2025-2026'`), o `None` si no existe.

        Consulta mínima (una fila) para quien solo necesita saber si el partido
        está cargado y de qué temporada es, sin pagar el detalle completo de
        `get_game_detail` (que dispara varias consultas de tablas hijas).
        """
        with self._engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT s.label FROM games g JOIN seasons s ON s.id = g.season_id"
                    " WHERE g.id = :game_id"
                ),
                {"game_id": game_id},
            ).scalar_one_or_none()

    def get_game_detail(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Detalle de un partido: resultado, stats avanzadas, lineups, zonas y eventos clave.

        `games` es neutral (equipo local/visitante, no necesariamente el
        Baskonia): junto al resultado tal cual, se incluye un bloque
        `baskonia` de conveniencia (`opponent`, `is_home`, `score_for`,
        `score_against`) que es `None` si el Baskonia no jugó ese partido.
        """
        with self._engine.connect() as conn:
            game_row = conn.execute(
                text(
                    """
                    SELECT g.*, c.name AS competition_name,
                           ht.name AS home_team_name, at.name AS away_team_name
                    FROM games g
                    JOIN teams ht ON ht.id = g.home_team_id
                    JOIN teams at ON at.id = g.away_team_id
                    JOIN competitions c ON c.id = g.competition_id
                    WHERE g.id = :game_id
                    """
                ),
                {"game_id": game_id},
            ).mappings().first()
            if game_row is None:
                return None

            own_team_row = conn.execute(
                text("SELECT id FROM teams WHERE is_own_team = 1 LIMIT 1")
            ).first()
            own_team_id = own_team_row[0] if own_team_row is not None else None

            advanced_rows = conn.execute(
                text(
                    """
                    SELECT gas.*, t.name AS team_name
                    FROM game_advanced_stats gas
                    JOIN teams t ON t.id = gas.team_id
                    WHERE gas.game_id = :game_id
                    ORDER BY gas.team_id
                    """
                ),
                {"game_id": game_id},
            ).mappings().all()

            lineup_rows = conn.execute(
                text(
                    "SELECT id, minutes, plus_minus FROM lineups"
                    " WHERE game_id = :game_id ORDER BY minutes DESC"
                ),
                {"game_id": game_id},
            ).mappings().all()

            lineup_player_rows = conn.execute(
                text(
                    """
                    SELECT lp.lineup_id, p.id AS player_id, p.name
                    FROM lineup_players lp
                    JOIN players p ON p.id = lp.player_id
                    JOIN lineups l ON l.id = lp.lineup_id
                    WHERE l.game_id = :game_id
                    ORDER BY lp.lineup_id, p.id
                    """
                ),
                {"game_id": game_id},
            ).mappings().all()

            zone_rows = conn.execute(
                text(
                    """
                    SELECT gz.team_id, t.name AS team_name, gz.zone_id, cz.label,
                           gz.fg_pct, gz.volume
                    FROM game_zone_stats gz
                    JOIN court_zones cz ON cz.id = gz.zone_id
                    JOIN teams t ON t.id = gz.team_id
                    WHERE gz.game_id = :game_id
                    ORDER BY gz.team_id, cz.id
                    """
                ),
                {"game_id": game_id},
            ).mappings().all()

            event_rows = conn.execute(
                text(
                    """
                    SELECT ke.team_id, t.name AS team_name, ke.quarter, ke.game_clock, ke.label
                    FROM key_events ke
                    JOIN teams t ON t.id = ke.team_id
                    WHERE ke.game_id = :game_id
                    ORDER BY ke.id
                    """
                ),
                {"game_id": game_id},
            ).mappings().all()

        players_by_lineup: Dict[int, List[Dict[str, Any]]] = {}
        for row in lineup_player_rows:
            players_by_lineup.setdefault(row["lineup_id"], []).append(
                {"id": row["player_id"], "name": row["name"]}
            )

        game = dict(game_row)
        if own_team_id == game["home_team_id"]:
            baskonia = {
                "is_home": True,
                "opponent_id": game["away_team_id"],
                "opponent_name": game["away_team_name"],
                "score_for": game["home_score"],
                "score_against": game["away_score"],
            }
        elif own_team_id == game["away_team_id"]:
            baskonia = {
                "is_home": False,
                "opponent_id": game["home_team_id"],
                "opponent_name": game["home_team_name"],
                "score_for": game["away_score"],
                "score_against": game["home_score"],
            }
        else:
            baskonia = None  # partido entre dos equipos rivales

        return {
            **game,
            "baskonia": baskonia,
            "advanced": [dict(row) for row in advanced_rows],
            "lineups": [
                {
                    "id": row["id"],
                    "minutes": row["minutes"],
                    "plus_minus": row["plus_minus"],
                    "players": players_by_lineup.get(row["id"], []),
                }
                for row in lineup_rows
            ],
            "zone_stats": [dict(row) for row in zone_rows],
            "key_events": [dict(row) for row in event_rows],
        }

    def get_game_boxscore(self, game_id: str) -> List[Dict[str, Any]]:
        """Boxscore de un partido (una fila por jugador), ordenado por puntos.

        Cada fila incluye el `team_id` del jugador (vía `players.team_id`) para que
        el consumidor pueda agrupar el boxscore por equipo.
        """
        sql = text(
            """
            SELECT pgs.game_id, pgs.player_id, p.name, p.team_id, pgs.minutes,
                   pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct
            FROM player_game_stats pgs
            JOIN players p ON p.id = pgs.player_id
            WHERE pgs.game_id = :game_id
            ORDER BY pgs.pts DESC, p.name
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"game_id": game_id}).mappings().all()
        return [dict(row) for row in rows]

    def get_player_recent_form(self, player_id: str, last_n: int = 5) -> List[Dict[str, Any]]:
        """Forma reciente de un jugador: últimos N partidos ordenados por fecha desc."""
        sql = text(
            """
            SELECT g.id AS game_id, g.game_date, pgs.pts, pgs.reb, pgs.ast, pgs.efg_pct
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            WHERE pgs.player_id = :player_id
            ORDER BY g.game_date DESC
            LIMIT :last_n
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"player_id": player_id, "last_n": last_n}).mappings().all()
        return [dict(row) for row in rows]

    def get_player_minutes_load(
        self,
        player_id: str,
        days: int = 14,
        as_of: Optional[DateLike] = None,
    ) -> float:
        """Suma de minutos jugados por un jugador en los últimos `days` días.

        Args:
            player_id: id del jugador.
            days: tamaño de la ventana, en días.
            as_of: fecha de referencia (por defecto hoy); se acepta explícita
                para que el resultado sea determinista en tests/CLIs.
        """
        reference = _as_iso_date(as_of) if as_of is not None else date.today().isoformat()
        cutoff = (date.fromisoformat(reference) - timedelta(days=days)).isoformat()
        sql = text(
            """
            SELECT COALESCE(SUM(pgs.minutes), 0) AS total_minutes
            FROM player_game_stats pgs
            JOIN games g ON g.id = pgs.game_id
            WHERE pgs.player_id = :player_id
              AND g.game_date >= :cutoff
              AND g.game_date <= :reference
            """
        )
        with self._engine.connect() as conn:
            total = conn.execute(
                sql, {"player_id": player_id, "cutoff": cutoff, "reference": reference}
            ).scalar_one()
        return float(total)

    def get_rating_trend(self, team_id: str = "bas", last_n: int = 8) -> List[Dict[str, Any]]:
        """Tendencia ORtg/DRtg de un equipo (por defecto el Baskonia) en sus últimos N partidos."""
        sql = text(
            """
            SELECT g.id AS game_id, g.game_date, gas.ortg, gas.drtg
            FROM games g
            JOIN game_advanced_stats gas
              ON gas.game_id = g.id AND gas.team_id = :team_id
            ORDER BY g.game_date DESC
            LIMIT :last_n
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"team_id": team_id, "last_n": last_n}).mappings().all()
        return [dict(row) for row in rows]

    def get_games_for_team(
        self, team_id: str, season_label: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Calendario/resultados de un equipo cualquiera (propio o rival), por fecha.

        Permite listar los partidos ya almacenados de un futuro rival (no solo
        los del Baskonia), para poder scoutear su forma reciente.
        """
        params: Dict[str, Any] = {"team_id": team_id}
        season_filter = ""
        if season_label is not None:
            season_filter = "AND s.label = :season_label"
            params["season_label"] = season_label
        sql = text(
            f"""
            SELECT g.id AS game_id, g.game_date, c.name AS competition_name,
                   g.home_team_id, ht.name AS home_team_name,
                   g.away_team_id, at.name AS away_team_name,
                   g.home_score, g.away_score, g.pace
            FROM games g
            JOIN teams ht ON ht.id = g.home_team_id
            JOIN teams at ON at.id = g.away_team_id
            JOIN competitions c ON c.id = g.competition_id
            JOIN seasons s ON s.id = g.season_id
            WHERE (g.home_team_id = :team_id OR g.away_team_id = :team_id)
            {season_filter}
            ORDER BY g.game_date
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def get_upcoming_matchups(self) -> List[Dict[str, Any]]:
        """Próximos rivales, ordenados por fecha de partido."""
        sql = text(
            """
            SELECT um.*, t.name AS opponent_name, c.name AS competition_name
            FROM upcoming_matchups um
            JOIN teams t ON t.id = um.opponent_team_id
            JOIN competitions c ON c.id = um.competition_id
            ORDER BY um.match_date
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
        return [dict(row) for row in rows]

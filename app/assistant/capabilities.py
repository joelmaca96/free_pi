"""Sondeo de la base de datos real: qué puede prometer el asistente, hoy, aquí.

Esto evita el peor modo de fallo del proyecto (§7.3): la base de datos de
producción va por detrás de las migraciones aditivas de `schema.sql`, así que
mirar el DDL para decidir qué se puede contestar es mirar la promesa en vez
del hecho. El caso real que lo motivó es `player_game_stats.ftm`/`fta`:
existen en `schema.sql` desde el 2026-08-24 y **no** en la base de datos que
sirve la interfaz. Sin sondeo, el asistente prometería tiros libres que no
puede servir; con sondeo, dice que no los tiene — y el día que se reingiera,
la capacidad se enciende sola sin tocar código.

Se comprueba siempre **columna presente Y con algún dato**, no solo la
columna: una columna añadida por `_apply_additive_migrations` existe llena de
`NULL` hasta que se recarga la temporada, y prometer sobre ella es el mismo
error una capa más abajo.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


@dataclass
class Capabilities:
    """Qué hay de verdad en esta base de datos.

    Los booleanos apagan herramientas enteras (`clutch_lineups` no se registra
    si no hay tramos, §4.4) o secciones de una respuesta; las listas y el
    rango de fechas viajan al prompt para que el modelo no ofrezca temporadas
    que no existen.
    """

    free_throws: bool = False
    key_events: bool = False
    narratives: bool = False
    lineup_team: bool = False
    lineup_stints: bool = False
    league_percentiles: bool = False
    seasons: List[Dict[str, object]] = field(default_factory=list)
    competitions: List[str] = field(default_factory=list)
    date_range: Optional[List[str]] = None
    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "free_throws": self.free_throws,
            "key_events": self.key_events,
            "narratives": self.narratives,
            "lineup_team": self.lineup_team,
            "lineup_stints": self.lineup_stints,
            "league_percentiles": self.league_percentiles,
            "seasons": self.seasons,
            "competitions": self.competitions,
            "date_range": self.date_range,
            "counts": self.counts,
        }

    def missing_summary(self) -> List[str]:
        """Frases cortas de lo que NO se puede contestar, para el prompt (§7.1).

        Redactadas como límites concretos y no como banderas técnicas: el
        modelo tiene que poder copiarlas casi tal cual al explicar por qué no
        responde algo.
        """
        gaps = []
        if not self.free_throws:
            gaps.append("No hay tiros libres (ftm/fta) en esta base de datos: no se puede hablar del juego desde la línea.")
        if not self.key_events:
            gaps.append("No hay eventos clave registrados: el relato de un partido se construye desde parciales por cuarto y boxscore.")
        if not self.narratives:
            gaps.append("No hay crónicas de partido que citar.")
        if not self.lineup_stints:
            gaps.append(
                "Los quintetos están agregados por partido, sin tramos de tiempo ni marcador: "
                "no se puede aislar 'los últimos minutos' ni el juego apretado."
            )
        if not self.lineup_team:
            gaps.append(
                "Los quintetos no guardan su equipo: se infiere por el equipo ACTUAL de sus jugadores, "
                "así que un traspaso ensucia los datos históricos."
            )
        if not self.league_percentiles:
            gaps.append(
                "No hay vistas de percentiles de liga en esta base de datos: los perfiles de equipo "
                "van sin contexto de liga (hay que reingerir para crearlas)."
            )
        return gaps


def _has_column(inspector, table: str, column: str) -> bool:
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def _scalar(engine: Engine, sql: str, default=0):
    try:
        with engine.connect() as conn:
            value = conn.execute(text(sql)).scalar()
    except Exception:
        # Una vista/tabla que no existe en ESTA base de datos no es un error:
        # es precisamente lo que este módulo está averiguando.
        return default
    return default if value is None else value


def probe(engine: Engine) -> Capabilities:
    """Inspecciona la base de datos y devuelve qué se puede prometer.

    Barato (un puñado de `COUNT` acotados con `LIMIT 1` donde se puede) y sin
    escribir nada. Se llama una vez por sesión desde la página del chat.
    """
    inspector = inspect(engine)

    free_throws = _has_column(inspector, "player_game_stats", "fta") and bool(
        _scalar(engine, "SELECT 1 FROM player_game_stats WHERE fta IS NOT NULL LIMIT 1")
    )
    lineup_team = _has_column(inspector, "lineups", "team_id") and bool(
        _scalar(engine, "SELECT 1 FROM lineups WHERE team_id IS NOT NULL LIMIT 1")
    )
    lineup_stints = inspector.has_table("lineup_stints") and bool(
        _scalar(engine, "SELECT 1 FROM lineup_stints LIMIT 1")
    )
    # `Inspector.has_view` no existe en SQLAlchemy 2.0.x (sí `has_table`, que
    # no ve las vistas en SQLite), así que se mira el catálogo de vistas.
    # Y existir no basta: una vista sobre `team_stats_by_competition` con
    # `gp >= 5` puede estar vacía en una base de datos recién sembrada, y una
    # vista vacía promete tanto como una que no está.
    league_percentiles = "team_style_percentiles" in set(inspector.get_view_names()) and bool(
        _scalar(engine, "SELECT 1 FROM team_style_percentiles LIMIT 1")
    )

    with engine.connect() as conn:
        seasons = [
            {"id": row[0], "label": row[1]}
            for row in conn.execute(text("SELECT id, label FROM seasons ORDER BY id DESC"))
        ]
        competitions = [row[0] for row in conn.execute(text("SELECT name FROM competitions ORDER BY id"))]
        date_row = conn.execute(text("SELECT MIN(game_date), MAX(game_date) FROM games")).fetchone()

    date_range = [str(date_row[0]), str(date_row[1])] if date_row and date_row[0] else None

    return Capabilities(
        free_throws=free_throws,
        key_events=bool(_scalar(engine, "SELECT 1 FROM key_events LIMIT 1")),
        narratives=bool(
            _scalar(engine, "SELECT 1 FROM games WHERE narrative IS NOT NULL AND TRIM(narrative) <> '' LIMIT 1")
        ),
        lineup_team=lineup_team,
        lineup_stints=lineup_stints,
        league_percentiles=league_percentiles,
        seasons=seasons,
        competitions=competitions,
        date_range=date_range,
        counts={
            "games": _scalar(engine, "SELECT COUNT(*) FROM games"),
            "players": _scalar(engine, "SELECT COUNT(*) FROM players"),
            "teams": _scalar(engine, "SELECT COUNT(*) FROM teams"),
            "shots": _scalar(engine, "SELECT COUNT(*) FROM shots"),
        },
    )

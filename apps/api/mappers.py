"""Mappers repositorio de scouting → contrato API.

Centraliza la conversión de los dicts que devuelve `ScoutingRepository`
(`packages/baskonia_core/db/scouting/repository.py`) a los schemas Pydantic
del contrato. Aquí viven las reglas de representación del §"Contratos de
datos" de `local/features/012-api-nuevo-modelo-datos/01_design.md`:

- Identidad `team_id`/`game_id` TEXT (esquema de scouting).
- Resultado `"W"` / `"L"` / `null` desde el punto de vista de un equipo.
- Números como números (sin formatear); `null` para ausencia de dato.

La API no formatea: estos mappers solo normalizan la representación, nunca
redondean ni convierten a cadenas de presentación.
"""
from .schemas import games as games_schemas
from .schemas import matchups as matchups_schemas
from .schemas import players as players_schemas
from .schemas import teams as teams_schemas


def team_ref(team_id: str, name: str) -> teams_schemas.TeamRef:
    """Convierte un id TEXT + nombre a `TeamRef`."""
    return teams_schemas.TeamRef(id=team_id, name=name)


def _result_label(
    team_id: str,
    home_team_id: str,
    away_team_id: str,
    home_score: int | None,
    away_score: int | None,
) -> str | None:
    """Resultado del partido desde el punto de vista de `team_id`: W/L/null."""
    is_home = home_team_id == team_id
    team_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score
    if team_score is None or opp_score is None:
        return None
    return "W" if team_score > opp_score else "L"


def game_item(row: dict, team_id: str) -> games_schemas.GameItem:
    """Convierte una fila de `get_games_for_team` a `GameItem` (vista de `team_id`)."""
    is_home = row["home_team_id"] == team_id
    opponent_id = row["away_team_id"] if is_home else row["home_team_id"]
    opponent_name = row["away_team_name"] if is_home else row["home_team_name"]
    team_score = row["home_score"] if is_home else row["away_score"]
    opp_score = row["away_score"] if is_home else row["home_score"]
    return games_schemas.GameItem(
        id=row["game_id"],
        date=row["game_date"],
        competition_name=row["competition_name"],
        is_home=is_home,
        opponent=team_ref(opponent_id, opponent_name),
        team_score=team_score,
        opponent_score=opp_score,
        result=_result_label(
            team_id, row["home_team_id"], row["away_team_id"],
            row["home_score"], row["away_score"],
        ),
        pace=row.get("pace"),
        advanced=None,
    )


def boxscore_row(row: dict) -> games_schemas.BoxScoreRow:
    """Convierte una fila de `get_game_boxscore` a `BoxScoreRow`."""
    return games_schemas.BoxScoreRow(
        game_id=row["game_id"],
        player_id=row["player_id"],
        name=row["name"],
        team_id=row["team_id"],
        minutes=row["minutes"],
        pts=row["pts"],
        reb=row["reb"],
        ast=row["ast"],
        efg_pct=row["efg_pct"],
    )


def game_advanced(row: dict) -> games_schemas.GameAdvanced:
    """Convierte una fila de `game_advanced_stats` a `GameAdvanced`."""
    return games_schemas.GameAdvanced(
        ortg=row.get("ortg"),
        drtg=row.get("drtg"),
        net_rating=row.get("net_rating"),
        efg_pct=row.get("efg_pct"),
        ts_pct=row.get("ts_pct"),
        tov_pct=row.get("tov_pct"),
        orb_pct=row.get("orb_pct"),
        ast_pct=row.get("ast_pct"),
        stl_pct=row.get("stl_pct"),
        blk_pct=row.get("blk_pct"),
        ft_rate=row.get("ft_rate"),
        ast_to_ratio=row.get("ast_to_ratio"),
    )


def lineup(row: dict) -> games_schemas.Lineup:
    """Convierte una fila de lineups (con `players`) a `Lineup`."""
    return games_schemas.Lineup(
        id=row["id"],
        minutes=row.get("minutes"),
        plus_minus=row.get("plus_minus"),
        players=[team_ref(p["id"], p["name"]) for p in row.get("players", [])],
    )


def zone_stat(row: dict) -> games_schemas.ZoneStat:
    """Convierte una fila de `game_zone_stats` a `ZoneStat`."""
    return games_schemas.ZoneStat(
        team_id=row["team_id"],
        team_name=row["team_name"],
        zone_id=row["zone_id"],
        label=row["label"],
        fg_pct=row.get("fg_pct"),
        volume=row.get("volume"),
    )


def key_event(row: dict) -> games_schemas.KeyEvent:
    """Convierte una fila de `key_events` a `KeyEvent`."""
    return games_schemas.KeyEvent(
        team_id=row["team_id"],
        team_name=row["team_name"],
        quarter=row.get("quarter"),
        game_clock=row.get("game_clock"),
        label=row.get("label"),
    )


def roster_player(row: dict) -> players_schemas.RosterPlayer:
    """Convierte una fila de `get_roster` a `RosterPlayer`."""
    return players_schemas.RosterPlayer(
        id=row["id"],
        name=row["name"],
        number=row.get("number"),
        position=row.get("position"),
        team_id=row["team_id"],
        active=bool(row.get("active", 1)),
        photo_url=row.get("photo_url"),
        height_cm=row.get("height_cm"),
        birth_date=row.get("birth_date"),
        nationality=row.get("nationality"),
        gp=row.get("gp"),
        min_avg=row.get("min_avg"),
        pts_avg=row.get("pts_avg"),
        reb_avg=row.get("reb_avg"),
        ast_avg=row.get("ast_avg"),
        efg_pct=row.get("efg_pct"),
    )


def form_item(row: dict) -> players_schemas.PlayerFormItem:
    """Convierte una fila de `get_player_recent_form` a `PlayerFormItem`."""
    return players_schemas.PlayerFormItem(
        game_id=row["game_id"],
        game_date=row["game_date"],
        pts=row.get("pts"),
        reb=row.get("reb"),
        ast=row.get("ast"),
        efg_pct=row.get("efg_pct"),
    )


def load_item(player_id: str, name: str, total_minutes: float) -> players_schemas.LoadItem:
    """Construye un `LoadItem` a partir de un jugador y su carga de minutos."""
    return players_schemas.LoadItem(
        player_id=player_id,
        name=name,
        total_minutes=total_minutes,
    )


def rating_trend_item(row: dict) -> teams_schemas.RatingTrendItem:
    """Convierte una fila de `get_rating_trend` a `RatingTrendItem`."""
    return teams_schemas.RatingTrendItem(
        game_id=row["game_id"],
        game_date=row["game_date"],
        ortg=row.get("ortg"),
        drtg=row.get("drtg"),
    )


def difficulty_opponent(row: dict) -> matchups_schemas.DifficultyOpponent:
    """Convierte una fila de `get_upcoming_matchups` a `DifficultyOpponent`."""
    return matchups_schemas.DifficultyOpponent(
        opponent_id=row["opponent_team_id"],
        opponent_name=row["opponent_name"],
        match_date=row["match_date"],
        is_home=bool(row.get("is_home", 0)),
        predicted_net_rating=row.get("predicted_net_rating"),
        predicted_pace=row.get("predicted_pace"),
        predicted_ortg=row.get("predicted_ortg"),
        has_scouting_data=bool(row.get("has_scouting_data", 0)),
        key_player_note=row.get("key_player_note"),
        h2h_wins=row.get("h2h_wins"),
        h2h_losses=row.get("h2h_losses"),
        h2h_last_result=row.get("h2h_last_result"),
    )


def upcoming_matchup(row: dict) -> matchups_schemas.UpcomingMatchup:
    """Convierte una fila de `get_upcoming_matchups` a `UpcomingMatchup`."""
    return matchups_schemas.UpcomingMatchup(
        id=row["id"],
        opponent=team_ref(row["opponent_team_id"], row["opponent_name"]),
        competition_name=row["competition_name"],
        match_date=row["match_date"],
        is_home=bool(row.get("is_home", 0)),
        predicted_net_rating=row.get("predicted_net_rating"),
        predicted_pace=row.get("predicted_pace"),
        predicted_ortg=row.get("predicted_ortg"),
        has_scouting_data=bool(row.get("has_scouting_data", 0)),
        key_player_note=row.get("key_player_note"),
        h2h_wins=row.get("h2h_wins"),
        h2h_losses=row.get("h2h_losses"),
        h2h_last_result=row.get("h2h_last_result"),
    )


def h2h_game(row: dict, team_id: str) -> matchups_schemas.HeadToHeadGame:
    """Convierte una fila de `get_games_for_team` a `HeadToHeadGame` (vista de `team_id`)."""
    is_home = row["home_team_id"] == team_id
    team_score = row["home_score"] if is_home else row["away_score"]
    opp_score = row["away_score"] if is_home else row["home_score"]
    return matchups_schemas.HeadToHeadGame(
        id=row["game_id"],
        date=row["game_date"],
        competition_name=row["competition_name"],
        team_score=team_score,
        opponent_score=opp_score,
        result=_result_label(
            team_id, row["home_team_id"], row["away_team_id"],
            row["home_score"], row["away_score"],
        ),
    )

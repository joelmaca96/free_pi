"""Pintado del impacto ajustado (RAPM) y del constructor de quintetos (propuesta 12).

El cálculo vive en `app/analytics/impact.py` y se cachea en
`queries_assistant.season_impact` (el ajuste es de TODA la liga: una vez por
temporada y competición, compartido con el asistente); aquí solo se decide
cómo se enseña.

**Prior de la temporada anterior** (propuesta 12 §6): la casilla "Usar la
temporada anterior como punto de partida" vive en la sección de RAPM y la
lee también el constructor (misma clave de `st.session_state`), así que las
dos secciones enseñan SIEMPRE el mismo ajuste.

Se importa con el mismo doble camino que el resto de `app/`.
"""
from typing import Dict, Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import impact
    from app.components.glossary import glossary_expander, help_text
    from app.data import queries_assistant
except ImportError:  # pragma: no cover
    from analytics import impact
    from components.glossary import glossary_expander, help_text
    from data import queries_assistant

_POS = "#008300"
_NEG = "#c0392b"

#: Minutos mínimos con el equipo para entrar por defecto en el constructor:
#: por debajo son jugadores de fondo de armario o altas de última hora, y su
#: RAPM es casi 0 por construcción (el ridge no tiene de dónde sacar más).
BUILDER_DEFAULT_MIN_MINUTES = 100.0

#: Clave de la casilla del prior, compartida por las dos secciones.
_USE_PRIOR_KEY = "impact_use_prior"


def _use_prior(engine, season_id: int, *, widget: bool) -> tuple:
    """`(usar_prior, temporada_anterior)`. Con `widget`, pinta la casilla; si no, lee su valor.

    Por defecto activada si hay temporada anterior con partidos; sin ella, la
    casilla sale desactivada y el ajuste es el de siempre (encogido hacia 0).
    """
    previous = queries_assistant.previous_season(engine, season_id)
    if widget:
        value = st.checkbox(
            "Usar la temporada anterior como punto de partida",
            value=previous is not None,
            disabled=previous is None,
            key=_USE_PRIOR_KEY,
            help=(
                "Con poca muestra, el RAPM se encoge hacia lo que el jugador hizo la temporada anterior "
                f"(×{impact.PRIOR_WEIGHT:.1f}) en lugar de hacia 0. Mejora sobre todo a los jugadores de "
                "rotación, con pocos cientos de minutos."
                if previous is not None
                else "No hay temporada anterior con partidos cargados."
            ),
        )
    else:
        value = st.session_state.get(_USE_PRIOR_KEY, previous is not None)
    return bool(value) and previous is not None, previous


def _lineup_label(players, names: Dict[str, str]) -> str:
    return " · ".join(names.get(p, p) for p in players)


def impact_section(engine, team_id: str, season_id: int, competition_id: Optional[int], team_label: str) -> None:
    """Tabla + gráfico de RAPM del equipo, con el On/Off al lado para comparar."""
    st.subheader("Impacto ajustado (RAPM)")
    use_prior, _ = _use_prior(engine, season_id, widget=True)
    with st.spinner("Ajustando el impacto de toda la liga…"):
        data = queries_assistant.season_impact(engine, season_id, competition_id, use_prior=use_prior)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    prior_season = data.get("prior_season")
    show_prior = bool(fit.get("prior_used")) and fit.get("prior_players", 0) > 0 and prior_season is not None
    team_minutes = impact.team_player_minutes(segments, team_id)
    if fit["players"].empty or team_minutes.empty:
        st.info(f"No hay tramos con los diez jugadores en pista de {team_label} en este corte.")
        return

    table = fit["players"][fit["players"]["player_id"].isin(team_minutes.index)].copy()
    table["player_name"] = table["player_id"].map(names)
    table["team_minutes"] = table["player_id"].map(team_minutes)
    onoff = queries_assistant.player_on_off(engine, team_id, season_id, competition_id)
    if not onoff.empty:
        table = table.merge(onoff[["player_id", "on_off_shrunk"]], on="player_id", how="left")
    else:
        table["on_off_shrunk"] = float("nan")
    table["muestra"] = table["reliable"].map({True: "Ok", False: "Insuficiente"})
    table = table.sort_values(["reliable", "rapm"], ascending=[False, False])

    chart_df = table[table["reliable"]]
    if not chart_df.empty:
        cap = float(chart_df["rapm"].abs().max() or 1.0)
        chart = (
            alt.Chart(chart_df)
            .mark_bar()
            .encode(
                y=alt.Y("player_name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, labelOverlap=False)),
                x=alt.X("rapm:Q", title="RAPM (+/- por 40, ajustado)", scale=alt.Scale(domain=[-cap, cap])),
                color=alt.condition(alt.datum.rapm >= 0, alt.value(_POS), alt.value(_NEG)),
                tooltip=[
                    alt.Tooltip("player_name:N", title="Jugador"),
                    alt.Tooltip("rapm:Q", title="RAPM", format="+.1f"),
                    alt.Tooltip("on_off_shrunk:Q", title="On/Off", format="+.1f"),
                    alt.Tooltip("minutes:Q", title="Min. en el ajuste", format=".0f"),
                ] + ([
                    alt.Tooltip("prior:Q", title="Punto de partida", format="+.1f"),
                    alt.Tooltip("rapm_no_prior:Q", title="Solo esta temporada", format="+.1f"),
                ] if show_prior else []),
            )
            .properties(height=alt.Step(26))
        )
        st.altair_chart(chart, width="stretch")

    prior_columns = ["prior", "rapm_no_prior"] if show_prior else []
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_order=["player_name", "rapm", *prior_columns, "on_off_shrunk", "team_minutes", "minutes", "muestra"],
        column_config={
            "player_name": st.column_config.TextColumn("Jugador"),
            "rapm": st.column_config.NumberColumn("RAPM", format="%+.1f", help=help_text("rapm")),
            "prior": st.column_config.NumberColumn(
                "Punto de partida", format="%+.1f", help=help_text("rapm_prior")
            ),
            "rapm_no_prior": st.column_config.NumberColumn(
                "Solo esta temporada", format="%+.1f", help=help_text("rapm_no_prior")
            ),
            "on_off_shrunk": st.column_config.NumberColumn("On/Off", format="%+.1f", help=help_text("on_off")),
            "team_minutes": st.column_config.NumberColumn(f"Min. con {team_label}", format="%.0f"),
            "minutes": st.column_config.NumberColumn(
                "Min. en el ajuste", format="%.0f",
                help="Minutos del jugador en todos los tramos de la liga que entran en el ajuste (con cualquier equipo).",
            ),
            "muestra": st.column_config.TextColumn("Muestra", help=help_text("sample_flag")),
        },
    )
    home_adv = fit["home_advantage"]
    shrink_target = (
        f"encogida hacia el RAPM de {prior_season['label']} ×{fit['prior_weight']:.1f} (hacia 0 quien no jugó "
        "esa temporada)"
        if show_prior
        else "encogida hacia 0"
    )
    st.caption(
        f"Regresión sobre {fit['segments']:,} tramos con los diez jugadores en pista de toda la liga, "
        f"{shrink_target} (λ = {fit['ridge']:.0f} minutos). Ventaja de campo estimada: "
        f"{home_adv:+.1f} por 40. Donde RAPM y On/Off discrepan mucho, el On/Off está contaminado por "
        f"con quién juega: ese es el dato interesante. Por debajo de {impact.MIN_RELIABLE_MINUTES:.0f} "
        "minutos, el número existe pero no sirve para decidir."
    )
    if show_prior:
        st.caption(
            f"**Punto de partida**: lo que se sabía del jugador antes de esta temporada (su RAPM de "
            f"{prior_season['label']}, rebajado al {impact.PRIOR_WEIGHT:.0%} porque un año cambia cosas); en "
            "blanco, sin minutos esa temporada. **Solo esta temporada**: el RAPM sin ese punto de partida. "
            f"Con pocos minutos manda el punto de partida; a partir de unos {impact.RIDGE_LAMBDA:.0f} minutos "
            "mandan los datos de este año."
        )
    elif use_prior:
        st.caption("Ningún jugador de este corte tiene minutos en la temporada anterior: RAPM encogido hacia 0.")
    glossary_expander(["rapm", "on_off", "sample_flag"] + (["rapm_prior", "rapm_no_prior"] if show_prior else []))


def lineup_builder_section(
    engine, team_id: str, season_id: int, competition_id: Optional[int], team_label: str, positions: Dict[str, str]
) -> None:
    """Constructor: el entrenador marca quién está disponible y recibe los mejores quintetos."""
    st.subheader("Constructor de quintetos")
    st.caption(
        "Marca quién está disponible (quita lesionados, no convocados o a quien quieras dosificar) y, si "
        "quieres, a quién hay que rodear. Salen los quintetos con mejor proyección según el RAPM, con lo "
        "que ese quinteto exacto ha hecho de verdad al lado."
    )
    # Mismo ajuste que la sección de RAPM: lee su casilla, no pinta otra.
    use_prior, _ = _use_prior(engine, season_id, widget=False)
    data = queries_assistant.season_impact(engine, season_id, competition_id, use_prior=use_prior)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    team_minutes = impact.team_player_minutes(segments, team_id)
    if team_minutes.empty:
        st.info(f"Sin tramos de {team_label} en este corte.")
        return

    roster = team_minutes.index.tolist()
    default = [p for p in roster if team_minutes[p] >= BUILDER_DEFAULT_MIN_MINUTES]
    label = lambda pid: f"{names.get(pid, pid)} ({team_minutes.get(pid, 0):.0f} min)"  # noqa: E731

    available = st.multiselect(
        "Disponibles", options=roster, default=default, format_func=label, key=f"builder_available_{team_id}"
    )
    col_must, col_pos = st.columns([3, 2])
    with col_must:
        # Opciones = plantilla entera (no solo los disponibles): si se quita de
        # "Disponibles" a alguien ya fijado aquí, el widget no se queda con un
        # valor fuera de sus opciones; se descarta abajo y se avisa.
        must = st.multiselect(
            "Tienen que estar", options=roster, format_func=label, max_selections=4,
            key=f"builder_must_{team_id}",
        )
    dropped = [p for p in must if p not in available]
    if dropped:
        st.caption(f"No disponibles, se ignoran como fijos: {', '.join(names.get(p, p) for p in dropped)}.")
        must = [p for p in must if p in available]
    known_positions = all(positions.get(p) for p in available)
    with col_pos:
        require = st.checkbox(
            "Al menos un base y un pívot",
            value=known_positions,
            disabled=not known_positions,
            help=None if known_positions else "No hay posición cargada para todos los disponibles.",
            key=f"builder_positions_{team_id}",
        )

    observed = impact.observed_lineups(segments, team_id)
    best = impact.best_lineups(
        fit["players"],
        available,
        observed=observed,
        positions=positions,
        required_positions={"Base": 1, "Pívot": 1} if require and known_positions else None,
        must_include=must,
        top=10,
    )
    if best.empty:
        st.info("Hacen falta al menos cinco disponibles (y que alguna combinación cumpla las condiciones).")
        return

    best = best.assign(quinteto=best["players"].map(lambda ps: _lineup_label(ps, names)))
    st.dataframe(
        best,
        hide_index=True,
        width="stretch",
        column_order=["quinteto", "projected_per_40", "observed_minutes", "observed_per_40"],
        column_config={
            "quinteto": st.column_config.TextColumn("Quinteto", width="large"),
            "projected_per_40": st.column_config.NumberColumn(
                "Proyección +/- 40", format="%+.1f", help=help_text("projected_per_40")
            ),
            "observed_minutes": st.column_config.NumberColumn(
                "Min. reales", format="%.0f", help=help_text("observed_minutes")
            ),
            "observed_per_40": st.column_config.NumberColumn(
                "+/- 40 real", format="%+.1f",
                help="Diferencia por 40 de ese quinteto exacto en sus minutos reales. Con pocos minutos, ruido.",
            ),
        },
    )
    untested = int((best["observed_minutes"] < 10).sum())
    if untested:
        st.caption(
            f"{untested} de estos quintetos apenas han jugado juntos (<10 min): la proyección es una idea para "
            "probar en entrenamiento o en minutos de poco riesgo, no una certeza."
        )
    st.caption(
        "Modelo aditivo: suma el impacto individual de los cinco y no capta química ni encaje táctico "
        "(dos creadores que necesitan el balón, dos pívots sin tiro). Úsalo para descubrir combinaciones "
        "que no se han probado y para ordenar las que sí, no para sustituir el criterio del cuerpo técnico."
        + (
            f" RAPM con {data['prior_season']['label']} como punto de partida, igual que en la tabla de arriba."
            if fit.get("prior_used") and data.get("prior_season")
            else ""
        )
    )
    glossary_expander(["projected_per_40", "observed_minutes", "rapm"])

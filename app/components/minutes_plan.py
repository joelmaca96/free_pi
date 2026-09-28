"""Pintado del planificador de minutos con carga (propuesta 17).

El cálculo vive en `app/analytics/minutes_plan.py`; el RAPM sale del mismo
ajuste cacheado que el constructor de quintetos
(`queries_assistant.season_impact` vía `components.impact.page_season_impact`,
que respeta la casilla del prior de la sección de RAPM) y la carga de `queries.rolling_load`
(la misma de "Carga acumulada" en "Estado del equipo"). Aquí solo se decide
cómo se enseña y qué puede tocar el entrenador.

Solo para el equipo propio: la carga de un rival se ve desde fuera, pero
planificarle los minutos no tiene sentido, y la posición (para la cobertura
de base y pívot) solo existe para nuestra plantilla.

Se importa con el mismo doble camino que el resto de `app/`.
"""
import datetime as dt
from typing import Dict, Optional

import altair as alt
import pandas as pd
import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import impact
    from app.analytics import minutes_plan as mp
    from app.components.glossary import glossary_expander, help_text
    from app.components.impact import _use_prior, page_season_impact
    from app.data import queries
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics import minutes_plan as mp
    from components.glossary import glossary_expander, help_text
    from components.impact import _use_prior, page_season_impact
    from data import queries

_PLAN = "#104281"
_RECENT = "#b8c4d6"
_EXPECTED = "Esperanza (RAPM)"
_PRUDENT = "Prudente (penaliza muestra corta)"


def minutes_plan_section(
    engine,
    team_id: str,
    season_id: int,
    competition_id: Optional[int],
    team_label: str,
    positions: Dict[str, str],
) -> None:
    """Reparto de los 200 minutos del próximo partido, con topes por carga editables."""
    st.subheader("Planificador de minutos")
    st.caption(
        "El paso siguiente al constructor: cuántos minutos juega cada uno en el próximo partido. Reparte "
        "los 200 minutos (5 × 40) buscando el mejor margen proyectado según el RAPM, sin pasar del tope "
        "de cada jugador — sugerido por su carga de los últimos días y corregible aquí."
    )

    # Mismo ajuste (con o sin temporada anterior) que la tabla de RAPM y el
    # constructor de esta pantalla: lee su casilla. `use_prior` también entra
    # en la clave del editor (cambiar el ajuste reordena la tabla).
    use_prior, _ = _use_prior(engine, season_id, widget=False)
    data = page_season_impact(engine, season_id, competition_id)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    team_minutes = impact.team_player_minutes(segments, team_id)
    roster = queries.roster_cards(engine, team_id, season_id)
    game_log = queries.rolling_load(engine, team_id, season_id, 7)
    if team_minutes.empty and roster.empty:
        st.info(f"Sin tramos ni plantilla de {team_label} en este corte.")
        return

    last_played = pd.Timestamp(game_log["game_date"].max()).date() if not game_log.empty else None
    next_game = queries.next_matchup(engine, dt.date.today())
    next_date = dt.date.fromisoformat(str(next_game["match_date"])[:10]) if next_game else None
    default_date = mp.default_reference_date(last_played, next_date)

    col_date, col_cap, col_mode = st.columns([2, 2, 3])
    with col_date:
        reference_date = st.date_input(
            "Fecha del partido", value=default_date, key=f"minutes_plan_date_{team_id}_{season_id}",
            help="La carga se cuenta en los días ANTERIORES a esta fecha. Por defecto, el próximo partido del "
            "calendario o, si no hay uno cercano, uno hipotético tres días después del último jugado.",
        )
    with col_cap:
        general_cap = st.number_input(
            "Tope general (min)", min_value=10, max_value=40, value=int(mp.LoadRules.default_max), step=1,
            key=f"minutes_plan_cap_{team_id}", help=help_text("minutes_cap"),
        )
    with col_mode:
        mode = st.radio(
            "Criterio", options=[_EXPECTED, _PRUDENT], horizontal=True, key=f"minutes_plan_mode_{team_id}",
            help=help_text("rapm_prudent"),
        )

    rules = mp.LoadRules(default_max=float(general_cap))
    summary = mp.recent_minutes_summary(game_log, reference_date, days=rules.window_days)
    rest = mp.rest_days_before(game_log, reference_date)
    caps = mp.load_caps(summary, rest_days=rest, rules=rules)
    base = mp.build_plan_input(
        fit["players"], team_minutes, roster, summary, caps, names, default_max=rules.default_max
    )
    if base.empty:
        st.info(f"Sin jugadores de {team_label} que planificar.")
        return

    rest_text = (
        f"{rest} día{'s' if rest != 1 else ''} de descanso" if rest is not None else "sin partido anterior"
    )
    st.caption(
        f"Partido del {reference_date.strftime('%d/%m/%Y')}: {rest_text}. Topes sugeridos: {rules.default_max:.0f} min "
        f"en general, lo que le falte para cruzar {rules.alert_minutes:.0f} min en {rules.window_days} días (aviso "
        f"de «Carga acumulada», nunca por debajo de {rules.min_load_cap:.0f}) y {rules.short_rest_max:.0f} con "
        f"≤{rules.short_rest_days} días de descanso tras un partido de {rules.short_rest_last_game:.0f}+ min."
    )

    # La clave del editor lleva todo lo que cambia la tabla de partida: al
    # mover la fecha o el tope general, la tabla vuelve a las sugerencias en
    # vez de arrastrar ediciones hechas sobre otras; y como `data_editor`
    # guarda las ediciones por POSICIÓN de fila, y la tabla se ordena por
    # RAPM, cambiar de temporada, competición o prior (otro orden) no puede
    # reaplicarlas a otros jugadores.
    editor_key = (
        f"minutes_plan_editor_{team_id}_{season_id}_{competition_id}_{use_prior}_"
        f"{reference_date.isoformat()}_{general_cap}"
    )
    edited = st.data_editor(
        base,
        hide_index=True,
        width="stretch",
        key=editor_key,
        column_order=[
            "available", "player_name", "position", "rapm", "load_minutes", "recent_avg_minutes",
            "min_minutes", "max_minutes",
        ],
        disabled=["player_name", "position", "rapm", "load_minutes", "recent_avg_minutes"],
        column_config={
            "available": st.column_config.CheckboxColumn("Disponible"),
            "player_name": st.column_config.TextColumn("Jugador"),
            "position": st.column_config.TextColumn("Posición"),
            "rapm": st.column_config.NumberColumn("RAPM", format="%+.1f", help=help_text("rapm")),
            "load_minutes": st.column_config.NumberColumn(
                f"Min. {rules.window_days} días", format="%.0f", help=help_text("rolling_minutes")
            ),
            "recent_avg_minutes": st.column_config.NumberColumn(
                "Media reciente", format="%.1f", help=help_text("recent_avg_minutes")
            ),
            "min_minutes": st.column_config.NumberColumn(
                "Mín.", min_value=0, max_value=40, step=1, format="%.0f", required=True,
                help=help_text("minutes_floor"),
            ),
            "max_minutes": st.column_config.NumberColumn(
                "Tope", min_value=0, max_value=40, step=1, format="%.0f", required=True,
                help=help_text("minutes_cap"),
            ),
        },
    )
    # Un tope tocado a mano deja de ser "de carga" o "general": ahora manda el
    # entrenador, y la explicación tiene que decirlo.
    edited = edited.copy()
    touched = (edited["max_minutes"].astype(float) - base["max_minutes"].astype(float)).abs() > 1e-9
    edited.loc[touched, "cap_source"] = "entrenador"
    edited.loc[touched, "cap_detail"] = ""

    available = edited[edited["available"]]
    known_positions = not available.empty and all(bool(positions.get(p)) for p in available["player_id"])
    require = st.checkbox(
        "Cobertura de base y pívot (40 min de cada posición: uno en pista siempre)",
        value=known_positions,
        disabled=not known_positions,
        help=help_text("position_coverage") if known_positions else "No hay posición cargada para todos los disponibles.",
        key=f"minutes_plan_positions_{team_id}",
    )
    floors = None
    if require and known_positions:
        # Misma regla que el asistente: sin ningún disponible de una posición
        # (los dos pívots fuera), su cobertura es imposible y no se exige.
        floors, missing = mp.applicable_position_floors(available["position"])
        if missing:
            st.caption(f"Sin ningún disponible de posición {', '.join(missing)}: esa cobertura no se exige.")

    # Semana cargada: si con los topes SUGERIDOS no salen los 200 minutos (o
    # los 40 de una posición), se suben lo justo —los que ha fijado el
    # entrenador no se tocan— y se avisa.
    edited, relax_notes = mp.relax_caps(edited, position_floors=floors)
    if relax_notes:
        st.warning(
            "Con los topes sugeridos no había reparto posible (o chocaban con un mínimo fijado): se han subido " + "; ".join(relax_notes)
            + " (los que has cambiado a mano no se tocan). Es la señal de una semana para dosificar a alguien "
            "del todo o dar minutos al fondo de armario."
        )

    value_column = "prudent" if mode == _PRUDENT else "rapm"
    result = mp.plan_minutes(edited, value_column=value_column, position_floors=floors)
    if not result["feasible"]:
        st.error(f"No hay reparto posible: {result['message']}")
        return
    other = mp.plan_minutes(edited, value_column="rapm" if value_column == "prudent" else "prudent", position_floors=floors)
    recent_margin = mp.recent_distribution_margin(edited)

    col_a, col_b, col_c = st.columns(3)
    col_a.metric(
        "Margen proyectado del plan", f"{result['projected_margin']:+.1f}", help=help_text("projected_margin"),
    )
    if pd.notna(recent_margin):
        col_b.metric(
            "Con el reparto reciente", f"{recent_margin:+.1f}",
            delta=f"{result['projected_margin'] - recent_margin:+.1f} el plan", delta_color="off",
            help="Mismo cálculo con la media de minutos reciente de los disponibles, reescalada a 200 minutos. "
            "Aproximación: quitar a un jugador no reparte sus minutos en proporción.",
        )
    if other["feasible"]:
        col_c.metric(
            f"Con el criterio {'prudente' if value_column == 'rapm' else 'de esperanza'}",
            f"{other['projected_margin']:+.1f}",
            help="Margen proyectado (con el RAPM) del plan que saldría con el otro criterio.",
        )

    table = result["table"]
    shown = table[table["available"] | (table["recent_avg_minutes"].fillna(0) > 0)]
    st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        column_order=["player_name", "planned_minutes", "recent_avg_minutes", "delta", "binding", "detail"],
        column_config={
            "player_name": st.column_config.TextColumn("Jugador"),
            "planned_minutes": st.column_config.NumberColumn("Plan (min)", format="%.0f", help=help_text("planned_minutes")),
            "recent_avg_minutes": st.column_config.NumberColumn(
                "Media reciente", format="%.1f", help=help_text("recent_avg_minutes")
            ),
            "delta": st.column_config.NumberColumn("Cambio", format="%+.0f"),
            "binding": st.column_config.TextColumn("Qué lo limita", help=help_text("binding_constraint")),
            "detail": st.column_config.TextColumn("Detalle", width="large"),
        },
    )

    chart_df = shown.melt(
        id_vars=["player_name"], value_vars=["planned_minutes", "recent_avg_minutes"],
        var_name="serie", value_name="minutos",
    ).dropna(subset=["minutos"])
    chart_df["serie"] = chart_df["serie"].map({"planned_minutes": "Plan", "recent_avg_minutes": "Media reciente"})
    order = shown["player_name"].tolist()
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            y=alt.Y("player_name:N", sort=order, title=None, axis=alt.Axis(labelLimit=220, labelOverlap=False)),
            yOffset=alt.YOffset("serie:N", sort=["Plan", "Media reciente"]),
            x=alt.X("minutos:Q", title="Minutos", scale=alt.Scale(domain=[0, 40])),
            color=alt.Color(
                "serie:N", scale=alt.Scale(domain=["Plan", "Media reciente"], range=[_PLAN, _RECENT]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("player_name:N", title="Jugador"),
                alt.Tooltip("serie:N", title="Serie"),
                alt.Tooltip("minutos:Q", title="Minutos", format=".1f"),
            ],
        )
        .properties(height=alt.Step(12))
    )
    st.altair_chart(chart, width="stretch")

    st.caption(
        "Modelo aditivo, el del constructor: cada minuto de un jugador sustituye a uno medio y el margen es "
        "Σ RAPM × minutos / 40, sin química ni encaje. Reparte TOTALES, no la rotación: la cobertura de 40 "
        "minutos de base y de pívot garantiza que existe una rotación con uno de cada en pista siempre, pero "
        "cuál es sigue siendo cosa del banquillo. Los topes por carga son una regla de calendario sobre "
        "minutos de partido (sin entrenamientos ni datos médicos), y no hay prórroga. RAPM "
        + (
            "con la temporada anterior como punto de partida (el de la casilla de arriba)"
            if fit.get("prior_used") else "de una sola temporada"
        )
        + ": con poca muestra, usa el criterio prudente."
    )
    glossary_expander(
        ["planned_minutes", "minutes_cap", "minutes_floor", "recent_avg_minutes", "projected_margin",
         "rapm_prudent", "binding_constraint", "position_coverage", "rapm", "rolling_minutes"]
    )


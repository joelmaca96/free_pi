"""Pintado del plan de rotación contra el rival (propuesta 15).

El cálculo vive en `app/analytics/rotation_plan.py` (ventanas del rival ×
constructor de quintetos); aquí se decide cómo se enseña: primero las frases
(lo que se lleva a la pizarra), después una pestaña por ventana con los
quintetos propuestos y lo que dicen los datos reales de cada uno.

Todo sale de la temporada de SCOUTING del rival (`scouting_season_id` en
"Próximo rival"): el RAPM de los dos equipos tiene que salir del mismo ajuste
para que "nuestros cinco menos sus cinco" tenga sentido.

Se importa con el mismo doble camino que el resto de `app/`.
"""
from typing import Dict

import streamlit as st

try:  # pragma: no cover - ver nota en app/assistant/tools/context.py
    from app.analytics import impact
    from app.analytics import rotation_plan as rpl
    from app.components.glossary import glossary_expander, help_text
    from app.data import queries, queries_assistant
except ImportError:  # pragma: no cover
    from analytics import impact
    from analytics import rotation_plan as rpl
    from components.glossary import glossary_expander, help_text
    from data import queries, queries_assistant

#: Quintetos propuestos por ventana. Tres: el entrenador elige uno y tiene
#: dos alternativas si el primero no encaja por algo que el modelo no ve.
_TOP = 3


def _label(players, names: Dict[str, str]) -> str:
    return " · ".join(names.get(p, p) for p in players)


def rotation_plan_section(
    engine, rival_team_id: str, own_team_id: str, season_id: int, rival_name: str, *, is_home, key: str
) -> None:
    """Sección entera: disponibles, frases del plan y una pestaña por ventana de ataque."""
    st.subheader("Plan de rotación contra el rival")
    st.caption(
        f"Cruza las ventanas en que {rival_name} flojea (descansos de sus principales y sus peores tramos "
        "de reloj) con nuestros mejores quintetos disponibles, proyectados contra los cinco que el rival "
        "suele tener en pista en esos minutos."
    )
    with st.spinner("Ajustando el impacto de toda la liga…"):
        data = queries_assistant.season_impact(engine, season_id, None)
    fit, segments, names = data["fit"], data["segments"], data["names"]
    rival_rows = queries.team_stint_rows(engine, rival_team_id, season_id)
    team_minutes = impact.team_player_minutes(segments, own_team_id)
    own_label = queries.team_name(engine, own_team_id) or own_team_id
    if rival_rows.empty or team_minutes.empty or fit["players"].empty:
        st.info(
            f"Hacen falta tramos de quinteto de {rival_name} y de {own_label} en la misma temporada para "
            "proyectar un quinteto contra otro."
        )
        return

    roster = team_minutes.index.tolist()
    label = lambda pid: f"{names.get(pid, pid)} ({team_minutes.get(pid, 0):.0f} min)"  # noqa: E731
    available = st.multiselect(
        f"Disponibles de {own_label}", options=roster, default=rpl.default_candidates(segments, own_team_id),
        format_func=label, key=f"{key}_available",
        help="Quita lesionados, no convocados o a quien quieras dosificar.",
    )
    roster_positions = queries.roster_cards(engine, own_team_id, season_id)
    positions = (
        dict(zip(roster_positions["id"], roster_positions["position"])) if not roster_positions.empty else {}
    )
    known_positions = bool(available) and all(positions.get(p) for p in available)
    require = st.checkbox(
        "Al menos un base y un pívot",
        value=known_positions,
        disabled=not known_positions,
        help=None if known_positions else "No hay posición cargada para todos los disponibles.",
        key=f"{key}_positions",
    )

    plan = rpl.build_plan(
        rival_rows,
        queries.team_stint_rows(engine, own_team_id, season_id),
        fit,
        segments,
        own_team_id,
        available,
        rival_on_off=queries_assistant.player_on_off(engine, rival_team_id, season_id),
        is_home=bool(is_home),
        positions=positions,
        required_positions={"Base": 1, "Pívot": 1} if require and known_positions else None,
        top=_TOP,
    )
    if not plan:
        st.info(f"{rival_name} no tiene descansos fijos ni tramos del partido claramente malos en esta temporada.")
        return
    if plan[0]["lineups"].empty:
        st.info("Hacen falta al menos cinco disponibles (y que alguna combinación cumpla las condiciones).")

    lines = rpl.plan_insights(rival_name, plan, names)
    st.markdown("\n".join(f"- {line}" for line in lines))

    tabs = st.tabs([
        f"Min {w['label']}" + (f" · sin {w['player_name']}" if w["kind"] == "descanso" else " · tramo flojo")
        for w in plan
    ])
    for tab, window in zip(tabs, plan):
        with tab:
            if window["rival_five"]:
                text = (
                    f"Quinteto habitual de {rival_name} en esos minutos: {_label(window['rival_five'], names)} "
                    f"(en pista el {window['rival_five_share']:.0%} de esos minutos, de media)."
                )
                if window["rival_minutes"] > 0:
                    text += (
                        f" {rival_name} en esa ventana: {window['rival_per_40']:+.1f} por 40 en "
                        f"{window['rival_minutes']:.0f} minutos de muestra."
                    )
                st.caption(text)
            if window["own_usual"]:
                st.caption(
                    f"Lo que solemos tener en pista ahí (sin los no disponibles): "
                    f"{_label(window['own_usual'], names)}, {window['own_usual_margin']:+.1f} proyectado."
                )
            lineups = window["lineups"]
            if lineups.empty:
                continue
            table = lineups.assign(quinteto=lineups["players"].map(lambda ps: _label(ps, names)))
            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                column_order=["quinteto", "projected_margin", "gain_vs_usual", "observed_minutes", "observed_per_40"],
                column_config={
                    "quinteto": st.column_config.TextColumn("Quinteto", width="large"),
                    "projected_margin": st.column_config.NumberColumn(
                        "Vs. su quinteto", format="%+.1f", help=help_text("margin_vs_five")
                    ),
                    "gain_vs_usual": st.column_config.NumberColumn(
                        "Vs. lo habitual", format="%+.1f", help=help_text("gain_vs_usual")
                    ),
                    "observed_minutes": st.column_config.NumberColumn(
                        "Min. reales", format="%.0f", help=help_text("observed_minutes")
                    ),
                    "observed_per_40": st.column_config.NumberColumn(
                        "+/- 40 real", format="%+.1f",
                        help="Diferencia por 40 de ese quinteto exacto en sus minutos reales (contra cualquier rival).",
                    ),
                },
            )

    home = rpl.home_term(fit["home_advantage"], bool(is_home))
    venue = "en casa" if is_home else "fuera"
    st.caption(
        f"RAPM de la temporada de scouting, con los dos equipos en el mismo ajuste. Jugamos {venue}: la "
        f"ventaja de campo que estima el ajuste suma {home:+.1f} por 40 a cada proyección. Modelo aditivo: el ORDEN de nuestros quintetos es el mismo en todas "
        "las ventanas (restar un rival no reordena); lo que cambia es el margen esperado y, sobre todo, "
        "cuánto se gana respecto a lo que solemos tener en pista en esos minutos. No capta química ni "
        "emparejamientos concretos: es un punto de partida para la pizarra, no un sustituto del criterio."
    )
    glossary_expander(["attack_window", "margin_vs_five", "gain_vs_usual", "observed_minutes", "rapm"])

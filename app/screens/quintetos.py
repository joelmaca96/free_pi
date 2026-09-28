"""Pantalla — On/off y duplas (propuesta 07).

Diseño completo en `doc/features/propuestas/07_onoff_y_duplas.md`. Punto de
partida del documento (§1): el quinteto de cinco jugadores casi nunca tiene
muestra — en la temporada 2025-2026 el Baskonia usó 724 combinaciones
distintas y solo 2 llegaron a 50 minutos juntas. Ese dato concreto se queda
AQUÍ, en la docstring, y no en la pantalla: es de una temporada y un equipo,
y el selector de arriba sirve para cualquiera de los dos. Por eso esta
pantalla se queda un nivel por debajo, donde el número empieza a significar
algo:

- **On/Off por jugador** (§2a): diferencia por 40 minutos con él en pista
  menos sin él, jugador a jugador.
- **Duplas y tríos** (§2b): matriz de parejas (color = diferencia por 40
  juntos) y ranking de mejores/peores tríos.
- **Con y sin** (§2c): las cuatro situaciones de dos jugadores concretos —
  ya estaba implementado para el chat (`queries_assistant.player_pair_impact`),
  aquí se le da pantalla propia.

- **Impacto ajustado (RAPM) y constructor de quintetos** (propuesta 12,
  `components/impact.py`): el On/Off descontando con quién y contra quién se
  juega, y los mejores quintetos posibles con los jugadores disponibles.

Selector de equipo arriba (§2d): sirve igual para el Baskonia que para
cualquier rival con partidos cargados esta temporada — `lineup_stints`
guarda el `team_id` de los dos equipos de cada tramo, así que el cálculo es
idéntico, solo cambia qué equipo se mira.

On/Off y duplas se calculan sobre `lineup_stints` (§4), no sobre `lineups`:
es lo que permite separar "con él"/"sin él" tramo a tramo. El interruptor
`lineup_stints` de `app/assistant/capabilities.py` condiciona esas dos
secciones, igual que ya condiciona las herramientas del asistente (§4.4);
"Con y sin" no lo necesita (usa `lineups`/`lineup_team`, disponible siempre)
y se pinta igual sin tramos.
"""
import altair as alt
import pandas as pd
import streamlit as st

from assistant.capabilities import probe
from components.glossary import glossary_expander, help_text
from components.impact import impact_section, lineup_builder_section
from components.header import page_header
from data import queries, queries_assistant
from data.db import get_read_engine

# Mismo par verde/rojo que `components/rotation_chart.py` (favor/en contra):
# una sola paleta divergente en toda la interfaz para "a favor del equipo".
_POS = "#008300"
_NEG = "#c0392b"
_NEUTRAL = "#f2f1ec"

engine = get_read_engine()
own_team_id = queries.get_own_team_id(engine)
season_id = st.session_state["season_id"]
capabilities = probe(engine)

page_header("On/off y duplas")

st.caption(
    "El quinteto de cinco casi nunca tiene muestra: un equipo usa cientos de combinaciones "
    "distintas en una temporada y apenas un puñado llega a un rato juntas en pista (§1 de la "
    "propuesta 07). Esta pantalla se queda un nivel por debajo — jugador, pareja y trío — que es "
    "donde el número empieza a significar algo."
)

# ------------------------------------------------------------------ selector --
teams_df = queries.season_teams(engine, season_id)
if teams_df.empty:
    st.info("No hay equipos con partidos cargados en esta temporada.")
    st.stop()

team_ids = teams_df["id"].tolist()
team_names = teams_df.set_index("id")["name"].to_dict()
default_index = team_ids.index(own_team_id) if own_team_id in team_ids else 0

col_team, col_comp = st.columns(2)
with col_team:
    team_id = st.selectbox(
        "Equipo",
        options=team_ids,
        index=default_index,
        format_func=lambda tid: team_names.get(tid, tid),
        key="onoff_team",
    )
with col_comp:
    competitions = queries.list_competitions(engine)
    comp_choice = st.selectbox(
        "Competición", options=["Todas"] + competitions["name"].tolist(), key="onoff_competition"
    )
competition_id = None
if comp_choice != "Todas":
    competition_id = int(competitions.loc[competitions["name"] == comp_choice, "id"].iloc[0])

team_label = team_names.get(team_id, team_id)

st.caption(
    "El On/Off es de CONTEXTO, no de calidad del jugador: compartir pista siempre con los "
    "mejores infla el número, y al revés (§5). Todo va en diferencia por 40 minutos y no por 100 "
    "posesiones: `lineup_stints` no guarda posesiones por tramo, y estimarlas sería inventar una "
    "precisión que el dato no tiene."
)

st.divider()

# Sin `lineup_stints` no hay On/Off, ni duplas, ni tríos: las tres secciones
# necesitan tramos con marcador. Se dice UNA vez y esas tres secciones no se
# pintan, en lugar de repetir tres cabeceras con el mismo "no se puede
# calcular" debajo — el aviso ya explica qué falta, cómo activarlo y qué
# sigue funcionando.
if not capabilities.lineup_stints:
    st.warning(
        "Los quintetos de esta base de datos están agregados por partido, sin tramos de tiempo "
        "ni marcador (`lineup_stints`): el On/Off, las duplas y los tríos necesitan esos tramos "
        "y no se pueden calcular todavía. Reingiere para activarlos. La sección **Con y sin**, "
        "más abajo, no los necesita y sí funciona."
    )

# ----------------------------------------------------------------- on/off --
if capabilities.lineup_stints:
    st.subheader("On/Off por jugador")
    onoff = queries_assistant.player_on_off(engine, team_id, season_id, competition_id)
    if onoff.empty:
        st.info(f"No hay tramos de {team_label} en esta temporada.")
    else:
        table = onoff.copy()
        table["muestra"] = table["reliable"].map({True: "Ok", False: "Insuficiente"})
        # En blanco y no a cero para quien no llega al mínimo — mismo criterio
        # que `diff_shrunk` en la tabla de calidad de tiro: es la única forma
        # honesta de decir "todavía no se sabe" en vez de mentir con un 0.
        table.loc[~table["reliable"], "on_off_shrunk"] = float("nan")
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_order=[
                "player_name", "on_minutes", "on_per_40", "off_minutes", "off_per_40",
                "on_off_shrunk", "muestra",
            ],
            column_config={
                "player_name": st.column_config.TextColumn("Jugador"),
                "on_minutes": st.column_config.NumberColumn(
                    "Min. con él", format="%.1f", help=help_text("on_minutes")
                ),
                "on_per_40": st.column_config.NumberColumn(
                    "+/- 40 con él", format="%+.1f", help=help_text("on_per_40")
                ),
                "off_minutes": st.column_config.NumberColumn(
                    "Min. sin él", format="%.1f", help=help_text("off_minutes")
                ),
                "off_per_40": st.column_config.NumberColumn(
                    "+/- 40 sin él", format="%+.1f", help=help_text("off_per_40")
                ),
                "on_off_shrunk": st.column_config.NumberColumn(
                    "On/Off", format="%+.1f", help=help_text("on_off")
                ),
                "muestra": st.column_config.TextColumn("Muestra", help=help_text("sample_flag")),
            },
        )
        st.caption(
            f"On/Off regularizado hacia 0 con poca muestra (n/(n+{queries_assistant.ON_OFF_MIN_MINUTES:.0f})). "
            f"Por debajo de {queries_assistant.ON_OFF_MIN_MINUTES:.0f} minutos en pista, en blanco: "
            "insuficiente para decidir nada."
        )
        glossary_expander(["on_minutes", "on_per_40", "off_minutes", "off_per_40", "on_off", "sample_flag"])

    st.divider()

    # ------------------------------------------------------- duplas y tríos --
    st.subheader("Duplas")
    pairs = queries_assistant.player_combos(engine, team_id, season_id, size=2, competition_id=competition_id)
    reliable_pairs = pairs[pairs["reliable"]] if not pairs.empty else pairs
    if reliable_pairs.empty:
        st.info(
            f"Ninguna pareja de {team_label} llega a {queries_assistant.COMBO_MIN_MINUTES:.0f} "
            "minutos juntos esta temporada."
        )
    else:
        split_ids = reliable_pairs["player_ids"].str.split(",", expand=True)
        split_names = reliable_pairs["jugadores"].str.split(" · ", expand=True)
        matrix = reliable_pairs.assign(
            player_a=split_ids[0], player_b=split_ids[1],
            jugador_a=split_names[0], jugador_b=split_names[1],
        )
        # La matriz es simétrica: cada pareja se pinta en (a,b) Y (b,a), o la
        # mitad del cuadro quedaría en blanco sin motivo — la pregunta real
        # ("¿puedo juntar a estos dos?") es la misma mirando desde cualquiera
        # de los dos jugadores.
        mirrored = matrix.rename(
            columns={"player_a": "player_b", "player_b": "player_a", "jugador_a": "jugador_b", "jugador_b": "jugador_a"}
        )
        symmetric = pd.concat([matrix, mirrored], ignore_index=True)

        order = sorted(set(symmetric["jugador_a"]) | set(symmetric["jugador_b"]))
        cap = float(symmetric["plus_minus_per_40_shrunk"].abs().max() or 1.0)
        side = max(320, 34 * len(order))

        heatmap = (
            alt.Chart(symmetric)
            .mark_rect(stroke="#ffffff", strokeWidth=1)
            .encode(
                x=alt.X("jugador_a:N", sort=order, title=None),
                y=alt.Y("jugador_b:N", sort=order, title=None),
                color=alt.Color(
                    "plus_minus_per_40_shrunk:Q",
                    scale=alt.Scale(domain=[-cap, 0, cap], range=[_NEG, _NEUTRAL, _POS]),
                    legend=alt.Legend(title="+/- por 40 min"),
                ),
                tooltip=[
                    alt.Tooltip("jugador_a:N", title="Jugador"),
                    alt.Tooltip("jugador_b:N", title="Con"),
                    alt.Tooltip("minutes:Q", title="Min. juntos", format=".1f"),
                    alt.Tooltip("plus_minus_per_40_shrunk:Q", title="+/- por 40", format="+.1f"),
                    alt.Tooltip("stints:Q", title="Tramos"),
                ],
            )
            .properties(height=side, width=side)
        )
        st.altair_chart(heatmap, width="stretch")
        st.caption(
            f"Solo parejas con al menos {queries_assistant.COMBO_MIN_MINUTES:.0f} minutos juntos — "
            "celda vacía = no llegan al mínimo, no que jueguen mal juntos. Color por diferencia por "
            "40 minutos ya regularizada; satura en el valor más extremo de la temporada."
        )
        glossary_expander(["plus_minus_per_40"])

    st.divider()

    st.subheader("Tríos")
    trios = queries_assistant.player_combos(engine, team_id, season_id, size=3, competition_id=competition_id)
    reliable_trios = trios[trios["reliable"]] if not trios.empty else trios
    if reliable_trios.empty:
        st.info(
            f"Ningún trío de {team_label} llega a {queries_assistant.COMBO_MIN_MINUTES:.0f} "
            "minutos juntos esta temporada."
        )
    else:
        ranked = reliable_trios.sort_values("plus_minus_per_40_shrunk", ascending=False)
        trio_columns = {
            "jugadores": st.column_config.TextColumn("Trío", width="large"),
            "minutes": st.column_config.NumberColumn("Min. juntos", format="%.1f", help=help_text("lineup_minutes")),
            "plus_minus_per_40_shrunk": st.column_config.NumberColumn(
                "+/- por 40", format="%+.1f", help=help_text("plus_minus_per_40")
            ),
        }
        best_col, worst_col = st.columns(2)
        with best_col:
            st.caption("Mejores")
            st.dataframe(
                ranked.head(10)[["jugadores", "minutes", "plus_minus_per_40_shrunk"]],
                hide_index=True, width="stretch", column_config=trio_columns,
            )
        with worst_col:
            st.caption("Peores")
            st.dataframe(
                ranked.tail(10).sort_values("plus_minus_per_40_shrunk")[
                    ["jugadores", "minutes", "plus_minus_per_40_shrunk"]
                ],
                hide_index=True, width="stretch", column_config=trio_columns,
            )
        st.caption(
            f"Ambas listas solo con tríos que llegan a {queries_assistant.COMBO_MIN_MINUTES:.0f} "
            "minutos juntos; ordenadas por diferencia por 40 minutos ya regularizada."
        )

st.divider()

# ------------------------------------------ impacto ajustado y constructor --
# Propuesta 12: el paso siguiente al On/Off. Mismo interruptor que las
# secciones de arriba — sin tramos no hay diez jugadores por instante.
if capabilities.lineup_stints:
    impact_section(engine, team_id, season_id, competition_id, team_label)
    st.divider()
    roster_positions = queries.roster_cards(engine, team_id, season_id)
    positions = (
        dict(zip(roster_positions["id"], roster_positions["position"])) if not roster_positions.empty else {}
    )
    lineup_builder_section(engine, team_id, season_id, competition_id, team_label, positions)
    st.divider()

# ------------------------------------------------------------------ con y sin --
st.subheader("Con y sin")

if not capabilities.lineup_team:
    st.caption(
        "⚠ El equipo de estos quintetos se ha deducido del equipo ACTUAL de sus jugadores (la "
        "ingesta todavía no guarda el equipo del quinteto): un traspaso ensucia el histórico."
    )

roster = queries_assistant.team_roster_production(engine, team_id, season_id)
roster = roster[roster["gp"].notna()] if not roster.empty else roster
if roster.empty or len(roster) < 2:
    st.info(f"No hay suficientes jugadores de {team_label} con partidos esta temporada para comparar.")
else:
    name_to_id = dict(zip(roster.sort_values("name")["name"], roster.sort_values("name")["id"]))
    col_a, col_b = st.columns(2)
    with col_a:
        player_a_name = st.selectbox("Jugador A", options=list(name_to_id.keys()), key="onoff_pair_a")
    with col_b:
        options_b = [name for name in name_to_id if name != player_a_name]
        player_b_name = st.selectbox("Jugador B", options=options_b, key="onoff_pair_b")

    impact = queries_assistant.player_pair_impact(
        engine, team_id, name_to_id[player_a_name], name_to_id[player_b_name], season_id, competition_id
    )
    if impact.empty:
        st.info(f"No hay quintetos de {team_label} en esta temporada.")
    else:
        situacion_labels = {
            "juntos": "Juntos",
            "solo A": f"Solo {player_a_name}",
            "solo B": f"Solo {player_b_name}",
            "ninguno": "Ninguno de los dos",
        }
        display_impact = impact.assign(situacion=impact["situacion"].map(situacion_labels))
        st.dataframe(
            display_impact,
            hide_index=True,
            width="stretch",
            column_order=["situacion", "minutes", "plus_minus", "plus_minus_per_40"],
            column_config={
                "situacion": st.column_config.TextColumn("Situación"),
                "minutes": st.column_config.NumberColumn(
                    "Minutos", format="%.1f", help=help_text("lineup_minutes")
                ),
                "plus_minus": st.column_config.NumberColumn("+/-", help=help_text("lineup_plus_minus")),
                "plus_minus_per_40": st.column_config.NumberColumn(
                    "+/- por 40", format="%+.1f", help=help_text("plus_minus_per_40")
                ),
            },
        )
        st.caption(
            "Las cuatro situaciones del equipo con estos dos jugadores en la temporada: juntos, "
            "cada uno sin el otro, y ninguno de los dos. 'Juntos' incluye tramos con hasta tres "
            "compañeros más — no aísla el efecto de la pareja del resto del quinteto."
        )
        glossary_expander(["lineup_minutes", "lineup_plus_minus", "plus_minus_per_40"])

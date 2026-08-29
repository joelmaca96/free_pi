"""Tests de `app/components/court.py`: tiros sin ubicación medida y pie de gráfico.

Como `avatar.py`, es de las pocas piezas de `app/` con lógica pura (construye
un gráfico Altair a partir de un DataFrame, sin `st.*`), así que se puede
comprobar directamente el objeto que devuelve sin levantar Streamlit.
"""
import pandas as pd
import pytest

from app.components.court import shot_chart, shot_chart_caption

ZONES = pd.DataFrame(
    [
        {"id": 1, "label": "Pintura", "x_min": 195, "x_max": 305, "y_min": 300, "y_max": 455},
        {"id": 4, "label": "Triple esquina izq.", "x_min": 15, "x_max": 55, "y_min": 380, "y_max": 460},
        {"id": 5, "label": "Triple esquina der.", "x_min": 445, "x_max": 485, "y_min": 380, "y_max": 460},
        {"id": 6, "label": "Triple exterior", "x_min": 160, "x_max": 340, "y_min": 50, "y_max": 170},
    ]
)


def _shots(rows):
    return pd.DataFrame(rows, columns=["pos_x", "pos_y", "made", "located", "player_name"])


def test_unlocated_shots_collapse_into_one_marked_pile():
    """Los tiros que la fuente no localiza (mates de ACB) se apilan todos en el
    aro: van en su propia capa y como UNA marca con su recuento, no como N
    puntos indistinguibles encima unos de otros."""
    shots = _shots(
        [
            (250.0, 455.0, 1, 0, "Pívot"),   # mate sin coordenadas
            (250.0, 455.0, 1, 0, "Pívot"),   # otro, en el mismo punto exacto
            (200.0, 400.0, 1, 1, "Base"),
            (300.0, 120.0, 0, 1, "Alero"),
        ]
    )

    chart = shot_chart(shots, ZONES)
    pile = chart.layer[-1].data

    assert len(pile) == 1  # una marca, no dos
    assert pile.iloc[0]["shots_count"] == 2 and pile.iloc[0]["made_count"] == 2
    assert (pile.iloc[0]["pos_x"], pile.iloc[0]["pos_y"]) == (250.0, 455.0)


def test_located_shots_are_not_drawn_twice():
    """El tiro sin ubicación sale de la capa normal, no se pinta en las dos."""
    shots = _shots([(250.0, 455.0, 1, 0, "Pívot"), (200.0, 400.0, 1, 1, "Base")])

    made_layer = shot_chart(shots, ZONES).layer[-2]  # anotados (localizados)

    assert len(made_layer.data) == 1
    assert made_layer.data.iloc[0]["player_name"] == "Base"


def test_chart_has_no_pile_layer_without_unlocated_shots():
    all_located = _shots([(200.0, 400.0, 1, 1, "Base"), (300.0, 120.0, 0, 1, "Alero")])

    with_pile = _shots([(200.0, 400.0, 1, 1, "Base"), (250.0, 455.0, 1, 0, "Pívot")])

    assert len(shot_chart(with_pile, ZONES).layer) == len(shot_chart(all_located, ZONES).layer) + 1


def test_chart_works_without_the_located_column():
    """Un DataFrame sin `located` (BD anterior a esa columna) se dibuja entero
    como tiros localizados, en vez de romper."""
    shots = pd.DataFrame(
        [(200.0, 400.0, 1, "Base"), (300.0, 120.0, 0, "Alero")],
        columns=["pos_x", "pos_y", "made", "player_name"],
    )

    chart = shot_chart(shots, ZONES)

    assert len(chart.layer[-1].data) == 1  # capa de anotados, sin capa de pila detrás
    assert "sin ubicación" not in shot_chart_caption(shots)


def test_caption_declares_the_unlocated_pile():
    shots = _shots([(250.0, 455.0, 1, 0, "Pívot"), (200.0, 400.0, 0, 1, "Base")])

    caption = shot_chart_caption(shots)

    assert "2 tiros de campo" in caption
    assert "50% anotados" in caption
    assert "1 sin ubicación exacta" in caption and "◆" in caption


def test_caption_stays_quiet_when_every_shot_is_located():
    caption = shot_chart_caption(_shots([(200.0, 400.0, 1, 1, "Base")]))

    assert "sin ubicación exacta" not in caption


# ---------------------------------------------------- mapa de zonas: modos --
# `zone_heatmap` tiene dos escalas de color que contestan preguntas distintas:
# "¿dónde acertamos más?" (relativa a las zonas del propio gráfico) y "¿dónde
# somos mejores que los demás?" (absoluta, contra la línea base de liga de
# `app/analytics/shot_quality.py`). Propuesta 02, §2.

from app.components.court import _diff_color, _HEAT_EMPTY, _zone_styles, zone_heatmap, zone_heatmap_caption


def _zone_df(rows):
    return pd.DataFrame(rows, columns=["zone_label", "volume", "made", "fg_pct", "league_fg_pct", "diff_pp"])


def test_vs_league_mode_colors_by_the_distance_to_the_league_not_by_the_own_spread():
    """Dos zonas que aciertan lo mismo que la liga salen igual de neutras aunque
    una acierte mucho más que la otra: ese es justo el punto del modo. En modo
    "fg" la de más acierto saldría verde y la otra roja."""
    zone_df = _zone_df([
        ("Pintura", 200, 120, 60.0, 60.0, 0.0),
        ("Triple exterior", 100, 35, 35.0, 35.0, 0.0),
    ])

    vs_league = _zone_styles(zone_df, "vs_league")
    by_fg = _zone_styles(zone_df, "fg")

    assert vs_league["Pintura"][0] == vs_league["Triple exterior"][0]
    assert by_fg["Pintura"][0] != by_fg["Triple exterior"][0]


def test_vs_league_mode_writes_the_difference_in_percentage_points_with_its_sign():
    styles = _zone_styles(_zone_df([("Pintura", 200, 130, 65.0, 58.9, 6.1)]), "vs_league")

    assert styles["Pintura"][1] == "+6.1 pp"
    assert styles["Pintura"][2] == "65.0% vs 58.9%"


def test_diff_color_saturates_instead_of_letting_a_tiny_sample_dominate_the_map():
    """Un +30 pp de doce tiros no puede aplastar visualmente al resto del mapa:
    la escala satura donde satura y ahí se queda."""
    assert _diff_color(10.0) == _diff_color(45.0)
    assert _diff_color(-10.0) == _diff_color(-45.0)
    assert _diff_color(0.0) not in (_diff_color(10.0), _diff_color(-10.0))
    assert _diff_color(float("nan")) == _HEAT_EMPTY


def test_a_zone_without_data_stays_grey_in_both_modes():
    zone_df = _zone_df([("Pintura", 0, 0, float("nan"), float("nan"), float("nan"))])

    for mode in ("fg", "vs_league"):
        assert _zone_styles(zone_df, mode)["Pintura"] == (_HEAT_EMPTY, "Sin tiros", "")


def test_an_unknown_mode_fails_loudly_instead_of_drawing_the_wrong_map():
    with pytest.raises(ValueError):
        zone_heatmap(_zone_df([("Pintura", 10, 5, 50.0, 55.0, -5.0)]), ZONES, mode="liga")


def test_zone_heatmap_draws_both_modes_and_says_which_one_it_is_in_the_caption():
    zone_df = _zone_df([("Pintura", 200, 130, 65.0, 58.9, 6.1)])

    assert zone_heatmap(zone_df, ZONES, mode="vs_league").layer
    assert "media de la liga" in zone_heatmap_caption(zone_df, mode="vs_league")
    assert "media de la liga" not in zone_heatmap_caption(zone_df)

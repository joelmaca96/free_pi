"""Tests de `app/components/court.py`: tiros sin ubicación medida y pie de gráfico.

Como `avatar.py`, es de las pocas piezas de `app/` con lógica pura (construye
un gráfico Altair a partir de un DataFrame, sin `st.*`), así que se puede
comprobar directamente el objeto que devuelve sin levantar Streamlit.
"""
import pandas as pd

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

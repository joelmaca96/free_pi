"""Tests del timeline de rotaciones (`app/components/rotation_chart.py`).

Un gráfico no se puede "afirmar" como una cifra, así que aquí se comprueba lo
que sí es verificable y lo que de verdad rompe la lectura si se tuerce:

- que el reloj de un tooltip diga lo mismo que el acta (`clock_label`),
- que el eje X llegue hasta el final del partido, prórroga incluida — si la
  capa del margen y la de barras se ajustan cada una a lo suyo, las dos dicen
  minutos distintos en la misma vertical y el gráfico miente,
- que el gráfico siga saliendo con la mitad de los datos: un partido sin
  play-by-play o sin tramos es un caso ESPERADO, no un error.
"""
import pandas as pd
import pytest

from app.components.rotation_chart import clock_label, event_label, rotation_chart

_STINT_COLUMNS = [
    "stint_id", "start_seconds", "end_seconds", "points_for", "points_against",
    "margin_start", "player_id", "player_name",
]


def _stints(*rows) -> pd.DataFrame:
    """Tramos mínimos: `(stint_id, start, end, player_name)`."""
    return pd.DataFrame(
        [
            {
                "stint_id": stint_id,
                "start_seconds": start,
                "end_seconds": end,
                "points_for": 4,
                "points_against": 2,
                "margin_start": 0,
                "player_id": name.lower(),
                "player_name": name,
            }
            for stint_id, start, end, name in rows
        ],
        columns=_STINT_COLUMNS,
    )


def _fouls(*rows) -> pd.DataFrame:
    """Faltas mínimas: `(seconds, player_name)`."""
    return pd.DataFrame(
        [{"seconds": seconds, "quarter": "Q1", "game_clock": "10:00", "player_name": name} for seconds, name in rows]
    )


def _steps(*rows) -> pd.DataFrame:
    """Escalera mínima: `(seconds, margin)`."""
    return pd.DataFrame(
        [
            {
                "seconds": seconds,
                "quarter": "Q1",
                "game_clock": "10:00",
                "score_for": max(margin, 0),
                "score_against": max(-margin, 0),
                "margin": margin,
            }
            for seconds, margin in rows
        ]
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "Q1 10:00"),
        (150, "Q1 07:30"),
        (1234, "Q3 09:26"),
        (2400, "Q4 00:00"),   # final del reglamentario, no "Q4 10:00"
        (2401, "OT1 04:59"),
        (2700, "OT1 00:00"),  # final de la primera prórroga, no "OT2 05:00"
        (2701, "OT2 04:59"),
    ],
)
def test_clock_label_reads_like_the_game_sheet(seconds, expected):
    """Mismo formato que `play_events.game_clock`: reloj hacia atrás dentro del periodo."""
    assert clock_label(seconds) == expected


def test_the_x_axis_covers_the_whole_game_including_overtime():
    """El dominio del eje va en CADA capa: un padre solo lo heredan los hijos sin `x`.

    Las barras acaban en el 2670 y el marcador en el 2700; si cada capa se
    ajustara a sus propios datos, el mismo minuto caería en dos verticales
    distintas y el gráfico dejaría de poder leerse cruzado, que es lo único
    que aporta.
    """
    chart = rotation_chart(
        _stints((1, 0.0, 2670.0, "Howard")),
        _steps((0, 0), (2700, 6)),
    )

    domains = [
        layer["encoding"]["x"]["scale"]["domain"]
        for layer in chart.to_dict()["layer"]
        if "scale" in layer["encoding"].get("x", {})
    ]
    assert domains, "ninguna capa fija el dominio del eje X"
    assert all(domain == [0, 45.0] for domain in domains)


def test_the_margin_axis_is_symmetric_so_zero_sits_in_the_middle():
    """Ganar de 20 y perder de 20 tienen que pintar la misma mancha, del revés."""
    chart = rotation_chart(_stints((1, 0.0, 600.0, "Howard")), _steps((0, 0), (600, -12)))

    # Excluye la capa de barras (`mark_bar`): desde que también fija su
    # dominio Y explícito (ver test de faltas más abajo), tiene "scale" en su
    # encoding igual que las capas del margen, pero el suyo es el ordinal de
    # jugadores (`["Howard"]`), no el cuantitativo de puntos que comprueba
    # este test.
    domains = [
        layer["encoding"]["y"]["scale"]["domain"]
        for layer in chart.to_dict()["layer"]
        if "scale" in layer["encoding"].get("y", {}) and layer["mark"]["type"] != "bar"
    ]
    assert domains == [[-12.0, 12.0], [-12.0, 12.0]]


def test_the_chart_still_draws_without_play_by_play():
    """Partido sin marcador tipado: quedan las rotaciones, que ya valen por sí solas."""
    chart = rotation_chart(_stints((1, 0.0, 600.0, "Howard")), pd.DataFrame())

    layers = chart.to_dict()["layer"]
    # Solo las líneas de cuarto y las barras: ni área de margen ni línea de 0.
    assert len(layers) == 2


def test_the_chart_still_draws_without_stints():
    """Partido sin tramos: queda el marcador, y la altura mínima evita un gráfico plano."""
    spec = rotation_chart(pd.DataFrame(), _steps((0, 0), (600, 6))).to_dict()

    assert spec["height"] == 200
    assert len(spec["layer"]) == 3


def test_players_are_ordered_by_when_they_first_walk_on():
    """El entrenador lee su rotación de arriba abajo: los titulares arriba."""
    chart = rotation_chart(
        _stints((1, 600.0, 1200.0, "Suplente"), (2, 0.0, 600.0, "Titular")),
        pd.DataFrame(),
    )

    bars = chart.to_dict()["layer"][-1]
    assert bars["encoding"]["y"]["sort"] == ["Titular", "Suplente"]


def test_fouls_land_on_the_right_player_row_even_when_not_everyone_fouled():
    """Con jugadores que no cometen ninguna falta, la capa de faltas tiene MENOS
    categorías que la de tramos — sin dominio explícito compartido, Altair
    calcula el eje Y de cada capa a partir de solo sus propias categorías, y
    "fila 2" deja de apuntar al mismo jugador en las dos: la marca de falta
    cae en la fila de otro y las dos capas de etiquetas del eje se ven
    superpuestas (se leía como si el jugador con falta apareciera dos veces
    en el gráfico). Caso real: Jabari Parker con tramos y faltas resueltos a
    dos `player_id` de fuentes distintas producía justo este síntoma."""
    chart = rotation_chart(
        # Horas de entrada distintas a propósito: `_player_order` desempata
        # por minutos jugados, y con un empate total (mismo inicio, misma
        # duración) el orden real pasa a ser el alfabético de `groupby`, no
        # el de inserción — este test necesita el orden fijado sin ambigüedad.
        _stints((1, 0.0, 600.0, "Titular"), (2, 100.0, 700.0, "Suplente"), (3, 200.0, 800.0, "Otro")),
        pd.DataFrame(),
        fouls=_fouls((320, "Otro")),  # solo el tercero, ni "Titular" ni "Suplente" cometen falta
    )
    layers = chart.to_dict()["layer"]
    order = ["Titular", "Suplente", "Otro"]  # por hora de entrada, ver `test_players_are_ordered_by_when_they_first_walk_on`

    stint_layer = next(layer for layer in layers if layer["mark"]["type"] == "bar")
    foul_layer = next(layer for layer in layers if layer["mark"]["type"] == "tick")
    assert stint_layer["encoding"]["y"]["scale"]["domain"] == order
    assert foul_layer["encoding"]["y"]["scale"]["domain"] == order


def test_only_the_stint_layer_draws_the_player_axis():
    """Con `resolve_scale(y="independent")`, Vega dibuja UN EJE POR CAPA: si la de faltas
    también pide el suyo, salen dos columnas de nombres superpuestas y, al medir el
    solapamiento entre las dos, Vega descarta una de cada dos filas — el gráfico se queda con
    diez filas de barras y cinco nombres. Las filas las etiqueta la capa de tramos y solo ella."""
    chart = rotation_chart(
        _stints((1, 0.0, 600.0, "Titular"), (2, 100.0, 700.0, "Suplente")),
        pd.DataFrame(),
        fouls=_fouls((320, "Titular")),
    )
    layers = chart.to_dict()["layer"]

    stint_layer = next(layer for layer in layers if layer["mark"]["type"] == "bar")
    foul_layer = next(layer for layer in layers if layer["mark"]["type"] == "tick")
    assert foul_layer["encoding"]["y"]["axis"] is None
    assert stint_layer["encoding"]["y"].get("axis") is not None


def test_the_chart_grows_enough_to_label_every_player():
    """La altura es la del CONTENEDOR: el título, el eje X y su rótulo se comen un alto fijo, y
    lo que no se reserve para ellos se lo quitan a las filas. Con una plantilla entera, cada
    jugador tiene que seguir teniendo una banda más alta que su propia etiqueta o Vega deja de
    pintar nombres."""
    from app.components import rotation_chart as rc

    names = [f"Jugador {i}" for i in range(10)]
    chart = rotation_chart(_stints(*[(i, i * 10.0, 600.0, n) for i, n in enumerate(names)]), pd.DataFrame())

    height = chart.to_dict()["height"]
    row_space = (height - rc._CHROME_H) / len(names)
    assert row_space >= 20, f"solo {row_space:.0f}px por jugador: las etiquetas del eje no caben"


def test_event_label_translates_the_schema_into_the_bench_vocabulary():
    """El entrenador no busca `oreb`, busca 'rebote ofensivo'."""
    assert event_label("oreb") == "Rebote ofensivo"
    # Un tipo de evento que la ingesta añada mañana se enseña tal cual en vez
    # de desaparecer de la lista.
    assert event_label("jump_ball") == "jump_ball"

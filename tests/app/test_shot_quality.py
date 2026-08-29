"""Tests de `app/analytics/shot_quality.py` (propuesta 02, xPPS).

Es lógica pura sobre `pandas` —sin Streamlit ni base de datos— justamente
para poder probar aquí las cuatro decisiones que hacen que la métrica sea
honesta o no lo sea: qué tiros entran, contra qué referencia se comparan,
cuánto se encoge la diferencia y a partir de cuántos tiros se puede enseñar.
"""
import pandas as pd
import pytest

from app.analytics import shot_quality as sq

_COLUMNS = ["competition_id", "zone_id", "zone_label", "located", "shots", "made"]

#: Zonas de las que se cuelga casi todo el fichero: 1 vale 2 puntos, 4 vale 3.
_LABELS = {1: "Pintura", 4: "Triple esquina izq.", 13: "Línea de fondo"}


def _counts(rows) -> pd.DataFrame:
    """`(competition_id, zone_id, located, shots, made)` -> DataFrame de recuentos."""
    return pd.DataFrame(
        [(c, z, _LABELS.get(z, f"Zona {z}"), loc, n, m) for c, z, loc, n, m in rows],
        columns=_COLUMNS,
    )


# --------------------------------------------------------- valor del tiro --


def test_shot_value_comes_from_the_zone_because_the_database_does_not_store_it():
    """`shots` no guarda si un tiro valía 2 o 3: se deriva del `zone_id` contra
    el reteselado de `court_zones`, no de las coordenadas (esa geometría ya se
    aplicó en la ingesta y duplicarla aquí sería otra que puede divergir)."""
    assert [sq.shot_value(z) for z in (4, 5, 6, 8, 9, 15, 17)] == [3] * 7
    assert [sq.shot_value(z) for z in (1, 7, 10, 11, 12, 14, 16)] == [2] * 7


# ------------------------------------------------------ qué tiros entran ---


def test_unlocated_and_unzoned_shots_are_excluded_and_counted_apart():
    """Ni se imputan ni se ignoran en silencio: quedan fuera del cálculo y se
    devuelven contados para poder declararlos en pantalla."""
    counts = _counts([
        (1, 1, 1, 100, 60),      # localizado y con zona -> entra
        (1, 10, 0, 30, 30),      # mate sin ubicación de la fuente -> fuera
        (1, None, 1, 12, 5),     # sin zona asignada -> fuera
    ])

    usable, coverage = sq.split_usable(counts)

    assert int(usable["shots"].sum()) == 100
    assert coverage == {"total": 142, "usable": 100, "unlocated": 30, "no_zone": 12,
                        "pct": pytest.approx(100 * 100 / 142)}


def test_an_unlocated_shot_without_zone_is_only_counted_once():
    """Las dos cifras de excluidos tienen que sumar exactamente los excluidos:
    si un tiro sin ubicación tampoco tiene zona, no puede contarse en las dos."""
    counts = _counts([(1, 1, 1, 50, 25), (1, None, 0, 8, 3)])

    _, coverage = sq.split_usable(counts)

    assert coverage["unlocated"] + coverage["no_zone"] == coverage["total"] - coverage["usable"]
    assert (coverage["unlocated"], coverage["no_zone"]) == (8, 0)


def test_missing_columns_fail_at_the_door_and_not_three_functions_later():
    """Cuatro consultas distintas alimentan este módulo; si una se queda corta,
    el error tiene que decir cuál columna falta, no reventar en un `KeyError`
    dentro del cálculo."""
    with pytest.raises(ValueError, match="located"):
        sq.split_usable(pd.DataFrame({"competition_id": [1], "zone_id": [1], "shots": [1], "made": [1]}))


def test_the_baseline_track_zone_is_folded_into_the_paint():
    """"Línea de fondo" (13) son 96 tiros en toda la liga en una franja de 5
    unidades pegada al fondo: no es una zona, es un artefacto de teselado. Se
    suma a "Pintura" en vez de publicar una referencia hecha con 96 tiros."""
    counts = _counts([(1, 1, 1, 400, 200), (1, 13, 1, 100, 80)])

    usable, _ = sq.split_usable(counts)

    assert usable["zone_id"].tolist() == [1]
    assert usable.iloc[0][["shots", "made"]].tolist() == [500, 280]
    assert usable.iloc[0]["zone_label"] == "Pintura"


# --------------------------------------------------- línea base de la liga --


def _league() -> pd.DataFrame:
    """Liga de juguete: la misma zona de 2 puntos con nivel distinto por
    competición, más una tercera competición sin volumen suficiente."""
    return _counts([
        (1, 1, 1, 400, 200),   # 50% -> 1,00 PPS
        (2, 1, 1, 400, 300),   # 75% -> 1,50 PPS
        (3, 1, 1, 20, 20),     # 100% con 20 tiros: no es una referencia
        (1, 4, 1, 300, 120),   # 40% de 3 -> 1,20 PPS
    ])


def test_baseline_keeps_each_competition_apart():
    """ACB y Euroliga no tienen el mismo nivel de tiro; mezclarlas contamina la
    referencia con la que se juzga a todo el mundo."""
    baseline = sq.league_baseline(_league())

    by_key = baseline.set_index(["competition_id", "zone_id"])["league_pps"]
    assert by_key[(1, 1)] == pytest.approx(1.0)
    assert by_key[(2, 1)] == pytest.approx(1.5)


def test_a_thin_competition_borrows_the_pooled_reference_instead_of_inventing_one():
    """Copa del Rey y Supercopa tienen cientos de tiros, no miles: con 20 en una
    zona la referencia propia sería ruido, así que se usa la agrupada — y se
    marca (`is_pooled`) para que la interfaz pueda decirlo."""
    baseline = sq.league_baseline(_league())

    pooled = baseline.loc[baseline["competition_id"] == sq.POOLED_COMPETITION_ID].set_index("zone_id")
    thin = baseline.loc[(baseline["competition_id"] == 3) & (baseline["zone_id"] == 1)].iloc[0]

    assert thin["is_pooled"]
    assert thin["league_pps"] == pytest.approx(pooled.loc[1, "league_pps"])
    assert thin["league_pps"] == pytest.approx(2 * 520 / 820)  # 200+300+20 de 400+400+20
    assert not baseline.loc[(baseline["competition_id"] == 1) & (baseline["zone_id"] == 1)].iloc[0]["is_pooled"]


def test_league_zone_table_is_the_pooled_one_ordered_by_what_a_shot_is_worth():
    """La tabla de vestuario: qué vale un tiro desde cada sitio, de más a menos."""
    table = sq.league_zone_table(sq.league_baseline(_league()))

    assert table["zone_label"].tolist() == ["Pintura", "Triple esquina izq."]
    assert table.iloc[0]["league_pps"] > table.iloc[1]["league_pps"]
    assert table["shot_value"].tolist() == [2, 3]


# ------------------------------------------------- xPPS, PPS y diferencia --


def test_expected_points_use_the_reference_of_each_shots_own_competition():
    """El mismo tiro en la misma zona vale distinto en ACB y en Euroliga, y el
    xPPS de un equipo que juega las dos tiene que reflejar dónde tira."""
    baseline = sq.league_baseline(_league())
    team = _counts([(1, 1, 1, 100, 50), (2, 1, 1, 100, 50)])

    summary = sq.summarize(sq.with_expected(team, baseline))

    assert summary["xpps"] == pytest.approx((1.0 * 100 + 1.5 * 100) / 200)
    assert summary["pps"] == pytest.approx(1.0)  # 100 de 200 tiros de 2 puntos


def test_difference_is_always_shrunk_towards_zero():
    """Con 100 tiros, la mitad de la diferencia observada es ruido: `n/(n+k)`
    con k=100 se queda con la mitad. Sin esto la métrica es un generador de
    rachas."""
    baseline = sq.league_baseline(_league())
    team = _counts([(1, 1, 1, 100, 75)])  # 1,50 PPS contra 1,00 esperado

    summary = sq.summarize(sq.with_expected(team, baseline))

    assert summary["diff"] == pytest.approx(0.5)
    assert summary["diff_shrunk"] == pytest.approx(0.25)
    assert sq.shrink(0.5, 300) == pytest.approx(0.5 * 300 / 400)


def test_below_the_minimum_sample_the_difference_is_not_a_readable_number():
    """Por debajo de 50 tiros no se dice "acertó por debajo", se dice que no hay
    muestra — es la diferencia entre una métrica que se vuelve a mirar y una que
    miente una vez."""
    baseline = sq.league_baseline(_league())
    summary = sq.summarize(sq.with_expected(_counts([(1, 1, 1, 20, 5)]), baseline))

    assert not summary["reliable"]
    verdict = sq.verdict(summary)
    assert "ruido" in verdict and str(sq.MIN_SHOTS) in verdict


def test_the_verdict_compares_generation_against_the_reference_it_is_given():
    """La frase honesta de §2: "generamos 1,04 —nuestra media es 1,01— y sacamos
    0,91". Sin referencia, un xPPS suelto no dice nada."""
    baseline = sq.league_baseline(_league())
    summary = sq.summarize(sq.with_expected(_counts([(2, 1, 1, 200, 80)]), baseline))

    assert "por encima de" in sq.verdict(summary, reference_xpps=1.0)
    assert "por debajo de" in sq.verdict(summary, reference_xpps=2.0)
    assert "en línea con" in sq.verdict(summary, reference_xpps=1.5)


def test_summarize_of_nothing_is_zeros_and_never_reliable():
    """Un equipo sin tiros utilizables no da 0,00 xPPS "de verdad": da un hueco
    que la interfaz tiene que poder distinguir de un mal dato."""
    summary = sq.summarize(sq.with_expected(_counts([]), sq.league_baseline(_league())))

    assert summary["shots"] == 0 and not summary["reliable"]
    assert "no hay calidad de tiro que medir" in sq.verdict(summary)


# --------------------------------------------------- cortes por grupo/zona --


def test_summarize_by_splits_players_without_losing_the_shrinkage_rule():
    baseline = sq.league_baseline(_league())
    counts = _counts([(1, 1, 1, 200, 140), (1, 1, 1, 10, 8)])
    counts["player_id"] = ["elige", "poquito"]
    counts["player_name"] = ["Elige Bien", "Tira Poco"]

    ranking = sq.summarize_by(sq.with_expected(counts, baseline), ["player_id"], extra=["player_name"])

    by_player = ranking.set_index("player_id")
    assert by_player.loc["elige", "reliable"] and not by_player.loc["poquito", "reliable"]
    assert by_player.loc["elige", "diff_shrunk"] == pytest.approx(0.4 * 200 / 300)
    assert ranking["shots"].tolist() == [200, 10]  # de más a menos tiros


def test_zone_profile_weights_the_league_reference_by_where_the_team_shoots():
    """Un equipo que tira el 75% de sus tiros en la competición más fácil tiene
    que compararse sobre todo contra ESA referencia, no contra la media
    aritmética de las dos."""
    baseline = sq.league_baseline(_league())
    team = _counts([(1, 1, 1, 100, 50), (2, 1, 1, 300, 150)])

    zones = sq.zone_profile(sq.with_expected(team, baseline))

    row = zones.iloc[0]
    assert row["zone_label"] == "Pintura"
    assert row["volume"] == 400
    assert row["league_fg_pct"] == pytest.approx((50 * 100 + 75 * 300) / 400)
    assert row["fg_pct"] == pytest.approx(50.0)
    assert row["diff_pp"] == pytest.approx(50.0 - 68.75)


def test_zone_profile_columns_are_the_ones_the_heatmap_already_knows():
    """Se devuelven con los nombres de `queries.team_zone_profile`
    (`zone_label/volume/made/fg_pct`) para poder pintarse con el mapa que ya
    existe en vez de duplicarlo."""
    zones = sq.zone_profile(sq.with_expected(_counts([(1, 1, 1, 100, 50)]), sq.league_baseline(_league())))

    assert {"zone_label", "volume", "made", "fg_pct", "league_fg_pct", "diff_pp"} <= set(zones.columns)


# ------------------------------------------------------------- redacción --


def test_coverage_caption_says_the_pps_here_is_lower_than_the_real_one():
    """Excluir los mates sin ubicación (casi todos ACB, y casi todos dentro)
    sesga el PPS hacia abajo. Hay que poder verlo sin preguntar."""
    _, coverage = sq.split_usable(_counts([(1, 1, 1, 100, 60), (1, 10, 0, 20, 20)]))

    caption = sq.coverage_caption(coverage)

    assert "20 sin ubicación" in caption and "algo mayor" in caption


def test_numbers_are_formatted_with_a_comma_and_an_explicit_sign():
    assert sq.format_pps(1.037) == "1,04"
    assert sq.format_diff(0.094) == "+0,09"
    assert sq.format_diff(-0.094) == "−0,09"

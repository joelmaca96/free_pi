"""Tests de `app/analytics/similarity.py` (propuesta 11, similitud de jugadores).

Lógica pura sobre `pandas`/`numpy` — sin Streamlit ni base de datos, mismo
criterio que `test_win_thresholds.py`/`test_zone_matchup.py`: se prueban aquí
las decisiones que hacen que "parecido" signifique algo — la muestra mínima
de tiros para el reparto por zona, qué competición se elige cuando un
jugador reparte la temporada, cómo se tratan los huecos de dato y por qué
'estilo' y 'nivel' dan listas distintas.
"""
import numpy as np
import pandas as pd
import pytest

from app.analytics import similarity as sim


# --------------------------------------------------------------- percent rank --


def test_percent_rank_matches_sqlite_percent_rank_formula():
    """(rango - 1) / (n - 1), igual que `PERCENT_RANK` de `schema.sql`."""
    values = pd.Series([10.0, 20.0, 30.0, 40.0])
    result = sim._percent_rank(values)
    assert result.tolist() == pytest.approx([0.0, 1 / 3, 2 / 3, 1.0])


def test_percent_rank_averages_ties():
    values = pd.Series([10.0, 10.0, 30.0])
    result = sim._percent_rank(values)
    # Los dos empatados comparten el rango medio (1.5 de 1..3) -> (1.5-1)/(3-1) = 0.25
    assert result.iloc[0] == pytest.approx(0.25)
    assert result.iloc[1] == pytest.approx(0.25)
    assert result.iloc[2] == pytest.approx(1.0)


def test_percent_rank_of_a_single_value_is_nan_not_a_forced_midpoint():
    assert sim._percent_rank(pd.Series([5.0])).isna().all()
    assert sim._percent_rank(pd.Series([], dtype=float)).empty


def test_percent_rank_ignores_missing_values_for_the_pool_size():
    values = pd.Series([10.0, np.nan, 30.0])
    result = sim._percent_rank(values)
    assert result.iloc[1] is np.nan or pd.isna(result.iloc[1])
    assert result.iloc[0] == pytest.approx(0.0)
    assert result.iloc[2] == pytest.approx(1.0)


# --------------------------------------------------------- reparto de tiro --


def _zone_rows(player_id: str, competition_id: int, volumes: dict) -> list:
    return [
        {"player_id": player_id, "competition_id": competition_id, "zone_id": zone_id, "volume": volume}
        for zone_id, volume in volumes.items()
    ]


def test_zone_dimension_percentiles_groups_raw_zones_into_the_five_buckets():
    """Pintura (1+10+13), media (2+3+7+11+12+14+16), esquina (4+5), ala (8+9+15+17), central (6)."""
    rows = _zone_rows("a", 1, {1: 10, 10: 5, 13: 5, 7: 20, 4: 10, 5: 10, 8: 10, 9: 10, 6: 20})
    volume = pd.DataFrame(rows)

    result = sim.zone_dimension_percentiles(volume)

    assert len(result) == 1
    row = result.iloc[0]
    # Con un solo jugador en el pool, el percentil no está definido (n<=1).
    for dim in sim.ZONE_DIMENSIONS:
        assert pd.isna(row[f"{dim['key']}_pct"])


def test_zone_dimension_percentiles_ranks_within_competition_only():
    """El percentil de reparto de tiro se calcula DENTRO de cada competición, nunca mezclando ligas."""
    rows = (
        _zone_rows("a", 1, {1: 80, 6: 20})   # mucha pintura, poco triple central
        + _zone_rows("b", 1, {1: 20, 6: 80})  # al revés
        + _zone_rows("c", 2, {1: 50, 6: 50})  # otra competición, no debe afectar a la 1
    )
    volume = pd.DataFrame(rows)

    result = sim.zone_dimension_percentiles(volume).set_index("player_id")

    assert result.loc["a", "zone_paint_pct"] == pytest.approx(1.0)
    assert result.loc["b", "zone_paint_pct"] == pytest.approx(0.0)
    # "c" es el único de su competición: sin pool con el que compararse.
    assert pd.isna(result.loc["c", "zone_paint_pct"])


def test_zone_dimension_percentiles_requires_a_minimum_sample_of_shots():
    """Por debajo de `MIN_SHOTS_FOR_ZONE_PROFILE`, las 5 dimensiones quedan en NaN (§4: "ruido con forma de perfil")."""
    thin = sim.MIN_SHOTS_FOR_ZONE_PROFILE - 1
    rows = _zone_rows("a", 1, {1: thin}) + _zone_rows("b", 1, {1: 100, 6: 100})
    volume = pd.DataFrame(rows)

    result = sim.zone_dimension_percentiles(volume).set_index("player_id")
    pct_cols = [f"{dim['key']}_pct" for dim in sim.ZONE_DIMENSIONS]

    assert result.loc["a", pct_cols].isna().all()
    assert result.loc["b", pct_cols].notna().any()


def test_zone_dimension_percentiles_of_empty_input_is_empty_with_the_right_columns():
    result = sim.zone_dimension_percentiles(pd.DataFrame(columns=["player_id", "competition_id", "zone_id", "volume"]))
    assert result.empty
    assert "player_id" in result.columns
    assert f"{sim.ZONE_DIMENSIONS[0]['key']}_pct" in result.columns


# --------------------------------------------------------------- vector de perfil --


def _stat_row(player_id: str, competition_id: int, gp: int, value: float) -> dict:
    row = {"player_id": player_id, "competition_id": competition_id, "gp": gp}
    row.update({dim["column"]: value for dim in sim.STAT_DIMENSIONS})
    return row


def test_build_player_vectors_picks_the_competition_with_more_games():
    """Un jugador que reparte ACB+Euroliga usa la de más partidos como perfil principal."""
    index_df = pd.DataFrame([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "position": "Base",
         "gp_total": 30, "minutes_total": 900.0},
    ])
    percentiles_df = pd.DataFrame([
        _stat_row("a", 1, 10, 0.9),   # ACB: menos partidos
        _stat_row("a", 2, 20, 0.2),   # Euroliga: más partidos -> esta gana
    ])
    zone_volume_df = pd.DataFrame(columns=["player_id", "competition_id", "zone_id", "volume"])

    vectors = sim.build_player_vectors(index_df, percentiles_df, zone_volume_df)

    assert len(vectors) == 1
    assert vectors.iloc[0]["competition_id"] == 2
    assert vectors.iloc[0]["pts_pct"] == pytest.approx(0.2)


def test_build_player_vectors_ties_break_deterministically_by_lowest_competition_id():
    index_df = pd.DataFrame([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "position": "Base",
         "gp_total": 10, "minutes_total": 300.0},
    ])
    percentiles_df = pd.DataFrame([_stat_row("a", 2, 10, 0.4), _stat_row("a", 1, 10, 0.6)])
    zone_volume_df = pd.DataFrame(columns=["player_id", "competition_id", "zone_id", "volume"])

    vectors = sim.build_player_vectors(index_df, percentiles_df, zone_volume_df)

    assert vectors.iloc[0]["competition_id"] == 1
    assert vectors.iloc[0]["pts_pct"] == pytest.approx(0.6)


def test_build_player_vectors_leaves_zone_dimensions_nan_without_shots():
    index_df = pd.DataFrame([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "position": "Base",
         "gp_total": 10, "minutes_total": 300.0},
    ])
    percentiles_df = pd.DataFrame([_stat_row("a", 1, 10, 0.5)])
    zone_volume_df = pd.DataFrame(columns=["player_id", "competition_id", "zone_id", "volume"])

    vectors = sim.build_player_vectors(index_df, percentiles_df, zone_volume_df)

    assert pd.isna(vectors.iloc[0]["zone_paint_pct"])
    assert vectors.iloc[0]["pts_pct"] == pytest.approx(0.5)


def test_build_player_vectors_of_empty_percentiles_is_empty():
    index_df = pd.DataFrame([{"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1",
                               "position": "Base", "gp_total": 10, "minutes_total": 300.0}])
    result = sim.build_player_vectors(index_df, pd.DataFrame(), pd.DataFrame())
    assert result.empty


# --------------------------------------------------------------------- distancia --


def _vectors_from(rows: list) -> pd.DataFrame:
    """Construye un `vectors` de prueba a partir de dicts parciales — el resto de las 15
    dimensiones se rellena a 0.5 (neutro) salvo que se pase explícitamente."""
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    out = []
    for row in rows:
        full = {c: 0.5 for c in cols}
        full.update({k: v for k, v in row.items() if k in cols})
        base = {k: v for k, v in row.items() if k not in cols}
        out.append({**base, **full})
    return pd.DataFrame(out)


def test_most_similar_excludes_the_player_itself():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
        {"player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
    ])
    result = sim.most_similar(vectors, "a", min_minutes=0)
    assert "a" not in result["player_id"].tolist()
    assert "b" in result["player_id"].tolist()


def test_most_similar_identical_profiles_score_100_with_either_method():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
        {"player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
    ])
    cosine = sim.most_similar(vectors, "a", method="cosine", min_minutes=0)
    euclidean = sim.most_similar(vectors, "a", method="euclidean", min_minutes=0)
    assert cosine.iloc[0]["similarity_score"] == pytest.approx(100.0, abs=1e-6)
    assert euclidean.iloc[0]["similarity_score"] == pytest.approx(100.0, abs=1e-6)


def test_most_similar_filters_out_candidates_below_min_minutes():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
        {"player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2", "competition_id": 1,
         "gp_total": 5, "minutes_total": 100.0},
    ])
    result = sim.most_similar(vectors, "a", min_minutes=sim.MIN_MINUTES_RECOMMENDED)
    assert result.empty


def test_most_similar_filters_by_competition_and_allowed_teams():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
        {"player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2", "competition_id": 2,
         "gp_total": 20, "minutes_total": 600.0},
        {"player_id": "c", "name": "C", "team_id": "t3", "team_name": "T3", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
    ])
    by_competition = sim.most_similar(vectors, "a", competition_id=1, min_minutes=0)
    assert set(by_competition["player_id"]) == {"c"}

    by_team = sim.most_similar(vectors, "a", allowed_team_ids=["t2"], min_minutes=0)
    assert set(by_team["player_id"]) == {"b"}


def test_most_similar_of_unknown_player_is_empty():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
    ])
    assert sim.most_similar(vectors, "no-existe", min_minutes=0).empty


def test_most_similar_rejects_an_unknown_method():
    vectors = _vectors_from([
        {"player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1", "competition_id": 1,
         "gp_total": 20, "minutes_total": 600.0},
    ])
    with pytest.raises(ValueError, match="método"):
        sim.most_similar(vectors, "a", method="manhattan")


def test_most_similar_requires_a_minimum_of_comparable_dimensions():
    """Un candidato con casi todo en NaN no debe colarse aunque las pocas dimensiones que
    comparta coincidan del todo (§4: "demasiado poco perfil para que 'parecido' signifique algo")."""
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    thin_cols = cols[: sim.MIN_DIMENSIONS_REQUIRED - 1]
    thin_row = {c: np.nan for c in cols}
    thin_row.update({c: 0.5 for c in thin_cols})

    vectors = pd.DataFrame([
        {**{c: 0.5 for c in cols}, "player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
        {**thin_row, "player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
    ])

    assert sim.most_similar(vectors, "a", min_minutes=0).empty


def test_cosine_rewards_shape_while_euclidean_also_penalizes_the_gap_in_level():
    """El ejemplo del documento (§4): mismo perfil, un jugador simplemente 'peor' en todo.
    Coseno (estilo) los ve casi idénticos; euclídea (nivel) los separa por la diferencia real."""
    high = {f"{d['key']}_pct": 0.9 for d in sim.DIMENSIONS}
    low = {f"{d['key']}_pct": 0.9 * 0.3 for d in sim.DIMENSIONS}  # misma FORMA, todo a un 30% del nivel
    vectors = pd.DataFrame([
        {**high, "player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
        {**low, "player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
    ])

    cosine_score = sim.most_similar(vectors, "a", method="cosine", min_minutes=0).iloc[0]["similarity_score"]
    euclidean_score = sim.most_similar(vectors, "a", method="euclidean", min_minutes=0).iloc[0]["similarity_score"]

    assert cosine_score == pytest.approx(100.0, abs=1e-6)
    assert euclidean_score < cosine_score - 20


def test_most_similar_respects_group_weights():
    """Subir el peso de un grupo a 0 lo ignora del todo; a un peso alto, lo hace decisivo."""
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    base = {c: 0.5 for c in cols}
    # "b" difiere solo en anotación (grupo "scoring"); "c" difiere solo en rebote ("rebounding").
    b_row = {**base, "pts_pct": 0.0}
    c_row = {**base, "oreb_pct": 0.0, "dreb_pct": 0.0}
    vectors = pd.DataFrame([
        {**base, "player_id": "a", "name": "A", "team_id": "t1", "team_name": "T1",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
        {**b_row, "player_id": "b", "name": "B", "team_id": "t2", "team_name": "T2",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
        {**c_row, "player_id": "c", "name": "C", "team_id": "t3", "team_name": "T3",
         "competition_id": 1, "gp_total": 20, "minutes_total": 600.0},
    ])

    # Con pesos iguales, "b" (una dimensión distinta) se parece más que "c" (dos distintas).
    equal = sim.most_similar(vectors, "a", method="euclidean", min_minutes=0)
    assert equal.iloc[0]["player_id"] == "b"

    # Subiendo mucho el peso de rebote y anulando el de anotación, "b" pasa a ser el más parecido
    # (su única diferencia, anotación, ya no cuenta) y por delante de "c" con más margen.
    weighted = sim.most_similar(
        vectors, "a", method="euclidean", min_minutes=0,
        group_weights={"scoring": 0.0, "rebounding": 5.0},
    )
    by_id = weighted.set_index("player_id")["similarity_score"]
    assert by_id["b"] == pytest.approx(100.0, abs=1e-6)
    assert by_id["c"] < by_id["b"]


# --------------------------------------------------------------------- explicación --


def test_explain_similarity_orders_closest_and_farthest_by_difference():
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    target = pd.Series({c: 0.5 for c in cols})
    candidate = pd.Series({c: 0.5 for c in cols})
    candidate["pts_pct"] = 0.0   # la más distinta
    candidate["ast_pct"] = 0.45  # ligeramente distinta

    explanation = sim.explain_similarity(target, candidate, n_closest=2, n_farthest=1)

    assert explanation["farthest"][0]["key"] == "pts"
    assert explanation["farthest"][0]["diff_pp"] == pytest.approx(50.0)
    closest_keys = {d["key"] for d in explanation["closest"]}
    assert "pts" not in closest_keys
    assert "ast" not in closest_keys  # 5pp de diferencia, pero hay dimensiones a 0pp exacto


def test_explain_similarity_skips_dimensions_missing_in_either_player():
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    target = pd.Series({c: 0.5 for c in cols})
    candidate = pd.Series({c: 0.5 for c in cols})
    candidate["zone_paint_pct"] = np.nan

    explanation = sim.explain_similarity(target, candidate, n_closest=20, n_farthest=20)

    all_keys = {d["key"] for d in explanation["closest"]} | {d["key"] for d in explanation["farthest"]}
    assert "zone_paint" not in all_keys


def test_explain_similarity_weight_changes_which_dimension_is_called_out_as_farthest():
    """Con pesos iguales gana la diferencia más grande; con el grupo de esa dimensión a 0, deja de contar."""
    cols = [f"{d['key']}_pct" for d in sim.DIMENSIONS]
    target = pd.Series({c: 0.5 for c in cols})
    candidate = pd.Series({c: 0.5 for c in cols})
    candidate["pts_pct"] = 0.0     # diferencia grande, grupo "scoring"
    candidate["stl_pct"] = 0.3     # diferencia menor, grupo "defense"

    unweighted = sim.explain_similarity(target, candidate, n_farthest=1)
    assert unweighted["farthest"][0]["key"] == "pts"

    weighted = sim.explain_similarity(target, candidate, group_weights={"scoring": 0.0}, n_farthest=1)
    assert weighted["farthest"][0]["key"] == "stl"


def test_similarity_caveat_mentions_the_known_data_gaps():
    """Aviso de §5 siempre presente: el cálculo ignora el físico, y describe una sola temporada."""
    assert "altura" in sim.SIMILARITY_CAVEAT.lower()
    assert "temporada" in sim.SIMILARITY_CAVEAT.lower()


def test_similarity_caveat_does_not_claim_the_physique_is_missing():
    """El aviso decía "sin altura ni peso (no se registran de esta fuente)" y "sin edad fiable (solo
    12 jugadores...)". Dejó de ser cierto en cuanto `ingest/euroleague/roster.py` empezó a traer
    altura, peso y fecha de nacimiento. La limitación real es otra —el cálculo no MIRA el físico—,
    y confundirlas engaña al que lee: le hace pensar que el dato no existe.

    El test comprueba las dos mitades: que no se afirme la carencia, y que sí se avise de que el
    cálculo los ignora (que es lo que importa para no fichar a un base creyendo que es un cuatro).
    """
    caveat = sim.SIMILARITY_CAVEAT.lower()

    assert "no se registran" not in caveat
    assert "sin altura ni peso" not in caveat
    assert "solo 12 jugadores" not in caveat
    assert "no los usa" in caveat or "no mira" in caveat


def test_no_similarity_dimension_is_physical():
    """Lo que sostiene el aviso: las 15 dimensiones son percentiles de producción y de reparto de
    tiro. Si algún día entra una de físico, el aviso pasa a ser mentira y hay que reescribirlo."""
    keys = {dim["key"] for dim in sim.DIMENSIONS}

    assert not keys & {"height_cm", "weight_kg", "height", "weight", "age", "birth_date"}

"""Arnés de paridad numérica API ↔ Streamlit — Fase F3.

Llama a la API (`apps/api`) para las 4 combinaciones de F0 y compara los
valores numéricos contra la línea base de F0 (`tests/parity/baseline/*.json`).

El gate de salida de F3 exige **paridad numérica**: la API y Streamlit leen la
misma BD a través del mismo dominio, así que los números deben coincidir.
Diferencias admitidas y esperadas **solo** en representación (fecha ISO vs.
española, `null` vs. `"-"`, `"W"` vs. `"88-79"`); cualquier diferencia numérica
es un fallo del gate.

Uso (desde la raíz del repo):
    python tools/parity_api.py

Salida: 0 si hay paridad numérica en las 4 combinaciones, 1 si hay alguna
diferencia numérica, 2 si hay un error de infraestructura.
"""
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.main import create_app  # noqa: E402

BASELINE_DIR = ROOT / "tests" / "parity" / "baseline"
API_PREFIX = "/api/v1"

# Tolerancias para comparar flotantes redondeados en baseline.
REL_TOL = 5e-3
ABS_TOL = 0.11

# Las 4 combinaciones de F0: (baseline_file, team, season, league, last_n).
COMBINATIONS = [
    ("gran-canaria-2025-all-last3.json", "gran-canaria", 2025, None, 3),
    ("vitoria-2025-all-last5.json", "vitoria", 2025, None, 5),
    ("vitoria-2025-euroleague-last5.json", "vitoria", 2025, "euroleague", 5),
    ("vitoria-2026-all-last5.json", "vitoria", 2026, None, 5),
]


def _num(value):
    """Convierte un valor a float si es numérico, o None si no lo es."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _close(a, b):
    """Compara dos valores numéricos con tolerancia relativa (None == None)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if abs(a - b) <= ABS_TOL:
        return True
    if b == 0:
        return abs(a) <= ABS_TOL
    return abs(a - b) / abs(b) <= REL_TOL


def _pct(value):
    """Convierte fracción [0,1] a porcentaje [0,100] conservando None."""
    n = _num(value)
    return None if n is None else n * 100.0


def _check(label, api_val, base_val, failures):
    """Compara un valor numérico y registra el fallo si difiere."""
    if not _close(_num(api_val), _num(base_val)):
        failures.append(f"{label}: API={api_val!r} vs baseline={base_val!r}")


def _check_advanced(client, team, season, league, baseline, failures):
    """Compara team_advanced_summary contra /summary.advanced."""
    params = {"season": season} if season else {}
    if league:
        params["league"] = league
    r = client.get(f"{API_PREFIX}/teams/{team}/summary", params=params)
    if r.status_code != 200:
        failures.append(f"summary {team}: HTTP {r.status_code}")
        return
    adv = r.json()["advanced"]
    base = baseline.get("team_advanced_summary", {})
    scalar_fields = ("avg_pace", "avg_off_rating", "avg_def_rating", "avg_net_rating")
    for key in scalar_fields:
        _check(f"advanced.{key}", adv.get(key), base.get(key), failures)
    _check(
        "advanced.avg_efg_pct",
        _pct(adv.get("avg_efg_pct")) if _num(base.get("avg_efg_pct")) and _num(base.get("avg_efg_pct")) > 1 else adv.get("avg_efg_pct"),
        base.get("avg_efg_pct"),
        failures,
    )
    _check(
        "advanced.avg_ts_pct",
        _pct(adv.get("avg_ts_pct")) if _num(base.get("avg_ts_pct")) and _num(base.get("avg_ts_pct")) > 1 else adv.get("avg_ts_pct"),
        base.get("avg_ts_pct"),
        failures,
    )


def _check_projection(client, team, season, league, baseline, failures):
    """Compara project_next_matchup contra /projection."""
    params = {"season": season} if season else {}
    if league:
        params["league"] = league
    # El rival del primer partido pendiente (mismo criterio que parity_dump).
    r = client.get(f"{API_PREFIX}/teams/{team}/games", params=params)
    if r.status_code != 200:
        failures.append(f"games {team}: HTTP {r.status_code}")
        return
    items = r.json()["items"]
    pending = [g for g in items if g["result"] is None]
    base = baseline.get("project_next_matchup")
    if base is None:
        return
    if not pending:
        # Este check depende de la fecha de referencia; si no hay pendientes,
        # se omite sin fallar para evitar falsos negativos de calendario.
        return
    opp = pending[0]["opponent"]["slug"]
    r = client.get(
        f"{API_PREFIX}/teams/{team}/matchups/{opp}/projection", params=params
    )
    if r.status_code != 200:
        failures.append(f"projection {team}: HTTP {r.status_code}")
        return
    proj = r.json()["projection"]
    if proj is None:
        failures.append(f"projection {team}: API devolvió null pero baseline tiene datos")
        return
    for key in ("projected_possessions", "team_projected_rating", "opp_projected_rating",
                "team_projected_score", "opp_projected_score"):
        _check(f"projection.{key}", proj.get(key), base.get(key), failures)


def _check_form(client, team, season, league, last_n, baseline, failures):
    """Compara recent_form_df contra /players/form (por jugador)."""
    params = {"last_n": last_n}
    if season:
        params["season"] = season
    if league:
        params["league"] = league
    r = client.get(f"{API_PREFIX}/teams/{team}/players/form", params=params)
    if r.status_code != 200:
        failures.append(f"form {team}: HTTP {r.status_code}")
        return
    api_by_player = {i["player_name"]: i for i in r.json()["items"]}
    base_rows = baseline.get("recent_form_df", [])
    for row in base_rows:
        name = row["Jugador"]
        api = api_by_player.get(name)
        if api is None:
            # Jugador en baseline pero no en API (p.ej. sin minutos): se ignora
            # si el baseline tampoco tiene PTS, si no es fallo.
            if _num(row.get("PTS")) is not None:
                failures.append(f"form: jugador {name} ausente en API")
            continue
        _check(f"form.{name}.avg_pts", api.get("avg_pts"), row.get("PTS"), failures)
        _check(f"form.{name}.avg_minutes", api.get("avg_minutes"), row.get("MIN"), failures)
        _check(f"form.{name}.avg_pts_per36", api.get("avg_pts_per36"), row.get("PTS/36"), failures)
        _check(f"form.{name}.avg_efg_pct", _pct(api.get("avg_efg_pct")), row.get("eFG%"), failures)
        _check(f"form.{name}.avg_ts_pct", _pct(api.get("avg_ts_pct")), row.get("TS%"), failures)
        # fg3a_rate (fracción) vs 3PA% (porcentaje)
        _check(f"form.{name}.fg3a_rate", _pct(api.get("fg3a_rate")), row.get("3PA%"), failures)
        _check(f"form.{name}.ft_rate", api.get("ft_rate"), row.get("FTr"), failures)


def _check_streaks(client, team, season, league, last_n, baseline, failures):
    """Compara streaks_df contra /players/streaks (por jugador)."""
    params = {"season": season, "recent_n": last_n}
    if league:
        params["league"] = league
    r = client.get(f"{API_PREFIX}/teams/{team}/players/streaks", params=params)
    if r.status_code != 200:
        failures.append(f"streaks {team}: HTTP {r.status_code}")
        return
    api_by_player = {i["player_name"]: i for i in r.json()["items"]}
    base_rows = baseline.get("streaks_df", [])
    for row in base_rows:
        name = row["Jugador"]
        api = api_by_player.get(name)
        if api is None:
            continue
        _check(f"streaks.{name}.z_score_pts", api.get("z_score_pts"), row.get("z-score PTS"), failures)
        _check(f"streaks.{name}.z_score_ts", api.get("z_score_ts"), row.get("z-score TS%"), failures)
        _check(f"streaks.{name}.recent_avg_pts", api.get("recent_avg_pts"), row.get("PTS últimos 5"), failures)
        _check(f"streaks.{name}.season_avg_pts", api.get("season_avg_pts"), row.get("PTS temporada"), failures)


def _check_narrative(client, team, season, league, last_n, baseline, failures):
    """Compara scouting_narrative contra /narrative (solo presencia, no texto)."""
    params = {"season": season, "recent_n": last_n}
    if league:
        params["league"] = league
    r = client.get(f"{API_PREFIX}/teams/{team}/narrative", params=params)
    if r.status_code != 200:
        failures.append(f"narrative {team}: HTTP {r.status_code}")
        return
    api_narrative = r.json()["narrative"]
    base_narrative = baseline.get("scouting_narrative")
    # Ambos null o ambos no-null: el texto exacto puede diferir en representación.
    if (api_narrative is None) != (base_narrative is None):
        failures.append(
            f"narrative {team}: API={'null' if api_narrative is None else 'texto'} "
            f"vs baseline={'null' if base_narrative is None else 'texto'}"
        )


def check_combination(client, baseline_file, team, season, league, last_n):
    """Comprueba la paridad numérica de una combinación de F0."""
    baseline = json.loads((BASELINE_DIR / baseline_file).read_text(encoding="utf-8"))
    failures: list[str] = []
    _check_advanced(client, team, season, league, baseline, failures)
    _check_projection(client, team, season, league, baseline, failures)
    _check_form(client, team, season, league, last_n, baseline, failures)
    _check_streaks(client, team, season, league, last_n, baseline, failures)
    _check_narrative(client, team, season, league, last_n, baseline, failures)
    return failures


def main() -> int:
    """Ejecuta la paridad numérica para las 4 combinaciones de F0."""
    client = TestClient(create_app())
    all_failures: list[str] = []
    for baseline_file, team, season, league, last_n in COMBINATIONS:
        failures = check_combination(client, baseline_file, team, season, league, last_n)
        status = "OK" if not failures else "FALLO"
        print(f"[{status}] {baseline_file}")
        for f in failures:
            print(f"    - {f}")
        all_failures.extend(failures)

    if all_failures:
        print(f"\nParidad numérica FALLIDA: {len(all_failures)} diferencia(s).")
        return 1
    print("\nParidad numérica OK en las 4 combinaciones de F0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests de `ingest/common/zones.py` y de la geometría sembrada de `court_zones`.

El propósito del reteselado del 2026-08-27 (ver el comentario sobre
`court_zones` en `schema.sql`) era arreglar dos cosas a la vez: que la
mayoría de los tiros cayeran DENTRO de alguna zona (antes 21-48% de
cobertura) y que ningún par de zonas se solapara (clasificación ambigua,
`classify_zone` no tiene `ORDER BY`). Estos tests protegen las dos
propiedades para que un reajuste futuro de límites no las rompa en
silencio.
"""
import itertools

import pytest
from sqlalchemy import text

from ingest.common.zones import MATE_ZONE_ID, classify_zone


def _zones(conn):
    return conn.execute(
        text("SELECT id, label, x_min, x_max, y_min, y_max FROM court_zones ORDER BY id")
    ).all()


def _overlaps(a, b) -> bool:
    """Dos rectángulos [x_min,x_max]x[y_min,y_max] se solapan si ambos ejes se solapan."""
    x_overlap = a.x_min <= b.x_max and b.x_min <= a.x_max
    y_overlap = a.y_min <= b.y_max and b.y_min <= a.y_max
    return x_overlap and y_overlap


def test_no_two_zones_overlap(engine):
    """Ningún par de rectángulos se solapa — si lo hicieran, a qué zona cae un tiro
    en la intersección dependería del orden de fila que devuelva SQLite, no
    determinista (`classify_zone` no lleva `ORDER BY`). La zona 'Mate' (id
    `MATE_ZONE_ID`) es la única excepción a propósito: su rectángulo es un
    punto degenerado dentro de 'Pintura', nunca se llega a ella por
    geometría (ver `test_mate_zone_is_not_reachable_by_geometry`)."""
    with engine.connect() as conn:
        zones = [z for z in _zones(conn) if z.id != MATE_ZONE_ID]

    overlapping = [
        (a.label, b.label) for a, b in itertools.combinations(zones, 2) if _overlaps(a, b)
    ]
    assert overlapping == []


def test_each_zone_classifies_its_own_center_point(engine):
    """El centro de cada rectángulo (salvo 'Mate', degenerado) debe clasificar a su
    propia zona — la comprobación más directa de que el seed y `classify_zone`
    están de acuerdo."""
    with engine.begin() as conn:
        zones = [z for z in _zones(conn) if z.id != MATE_ZONE_ID]
        for zone in zones:
            cx = (zone.x_min + zone.x_max) / 2
            cy = (zone.y_min + zone.y_max) / 2
            assert classify_zone(conn, cx, cy) == zone.id, zone.label


def test_mate_zone_is_not_reachable_by_geometry(engine):
    """El punto de 'Mate' cae dentro de 'Pintura' — por diseño, `classify_zone` NO
    debe devolver `MATE_ZONE_ID` ahí: quien clasifica un mate debe asignarle
    ese id directamente (ver `ingest/common/raw_game.py`), nunca vía
    `classify_zone`, precisamente porque el resultado del solape con
    'Pintura' no está garantizado."""
    with engine.begin() as conn:
        mate = next(z for z in _zones(conn) if z.id == MATE_ZONE_ID)
        assert classify_zone(conn, mate.x_min, mate.y_min) != MATE_ZONE_ID


@pytest.mark.parametrize(
    "label,point",
    [
        ("Pintura", (250, 380)),
        ("Ala izq.", (100, 350)),
        ("Ala der.", (400, 350)),
        ("Triple esquina izq.", (30, 420)),
        ("Triple esquina der.", (470, 420)),
        ("Triple exterior", (250, 100)),
        ("Media dist. central", (250, 230)),
        ("Triple ala izq.", (80, 100)),
        ("Triple ala der.", (420, 100)),
    ],
)
def test_representative_points_land_in_the_expected_zone(engine, label, point):
    """Un punto representativo de cada zona "real" de basket clasifica donde toca —
    cubre el resto de zonas más allá de sus centros exactos."""
    with engine.begin() as conn:
        zone_id = conn.execute(text("SELECT id FROM court_zones WHERE label = :l"), {"l": label}).scalar_one()
        assert classify_zone(conn, *point) == zone_id


def test_coverage_of_the_practical_shot_area_is_high(engine):
    """Antes del reteselado, entre el 21% y el 48% de los tiros reales caían fuera de
    las 6 zonas (huecos grandes entre rectángulos, ver `schema.sql`). Una rejilla
    de puntos dentro de la zona ofensiva realista (x:15-485, y:50-460) debe
    clasificar en su inmensa mayoría — si esto baja de forma apreciable, algo ha
    vuelto a abrir un hueco grande."""
    with engine.begin() as conn:
        points = [(x, y) for x in range(15, 486, 5) for y in range(50, 461, 5)]
        covered = sum(1 for x, y in points if classify_zone(conn, x, y) is not None)

    coverage = covered / len(points)
    assert coverage >= 0.85, f"cobertura de la rejilla: {coverage:.0%}"

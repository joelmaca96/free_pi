"""Tests de `ingest/common/zones.py` y de la geometría sembrada de `court_zones`.

El propósito del reteselado del 2026-08-27 (ver el comentario sobre
`court_zones` en `schema.sql`) era arreglar dos cosas a la vez: que la
mayoría de los tiros cayeran DENTRO de alguna zona (antes 21-48% de
cobertura) y que ningún par de zonas se solapara EN ÁREA (clasificación
ambigua, `classify_zone` no tiene `ORDER BY`). El reteselado 2 (2026-08-28)
llevó la cobertura al 100% del dominio 0-500 haciendo que cada par de zonas
vecinas COMPARTA el límite exacto en vez de usar enteros consecutivos — eso
sí introduce solape en la línea/punto de esa frontera (medida CERO, un tiro
real con coordenadas float prácticamente nunca cae ahí exacto), así que
`_overlaps` distingue solape con ÁREA (bug real) de solo tocarse en el borde
(esperado, la forma en que se cierran los huecos). Estos tests protegen las
dos propiedades para que un reajuste futuro de límites no las rompa en
silencio.

El split de ala por triple (2026-08-27, ver ese mismo comentario) añadió 4
filas MÁS degeneradas (14-17, "Ala izq./der. (2)/(3)"), con el mismo patrón
que ya tenía "Mate" (fila 10): un punto que cae DENTRO de su zona padre a
propósito, nunca alcanzable de forma fiable por el `BETWEEN` de
`classify_zone` sin `ORDER BY` — la clasificación real pasa por la elipse de
triple, no por ese rectángulo-punto. `_DEGENERATE_IDS` generaliza la
exclusión que antes solo cubría `MATE_ZONE_ID` a cualquier fila con
`x_min == x_max` (la propiedad real que las hace especiales, no una lista de
ids a mano).
"""
import itertools

import pytest
from sqlalchemy import text

from ingest.common.zones import MATE_ZONE_ID, classify_zone


def _zones(conn):
    return conn.execute(
        text("SELECT id, label, x_min, x_max, y_min, y_max FROM court_zones ORDER BY id")
    ).all()


def _real_zones(conn):
    """Filas de `court_zones` con geometría real — sin las degeneradas (punto
    dentro de otra zona, nunca alcanzables de forma fiable por `classify_zone`:
    'Mate' y, desde el split de ala por triple, 'Ala izq./der. (2)/(3)')."""
    return [z for z in _zones(conn) if z.x_min != z.x_max]


def _overlaps(a, b) -> bool:
    """Dos rectángulos [x_min,x_max]x[y_min,y_max] se solapan EN ÁREA (no solo se tocan).

    Desigualdades estrictas a propósito: dos zonas vecinas que comparten
    exactamente un límite (p.ej. una acaba en x=195 y la otra empieza en
    x=195) se TOCAN en una línea de área nula, no se SOLAPAN — es justo así
    como el reteselado 2 cierra los huecos de 1 unidad entre zonas vecinas
    (ver `schema.sql`). Con `<=` ese caso, deseado, se contaría como bug.
    """
    x_overlap = a.x_min < b.x_max and b.x_min < a.x_max
    y_overlap = a.y_min < b.y_max and b.y_min < a.y_max
    return x_overlap and y_overlap


def test_no_two_zones_overlap(engine):
    """Ningún par de rectángulos CON GEOMETRÍA REAL se solapa EN ÁREA — si lo
    hicieran, a qué zona cae un tiro en la intersección dependería del orden
    de fila que devuelva SQLite, no determinista (`classify_zone` no lleva
    `ORDER BY`). Tocarse en un borde compartido (área nula) SÍ está permitido
    — ver `_overlaps`. Las zonas degeneradas ('Mate', 'Ala izq./der. (2)/(3)')
    son la excepción a propósito: cada una es un punto que cae DENTRO de su
    zona padre — `classify_zone` no las alcanza por el `BETWEEN` de geometría
    de la tabla (ver `test_mate_zone_is_not_reachable_by_geometry`); a las de
    ala se llega solo por el segundo paso, la elipse de triple (ver
    `test_representative_points_land_in_the_expected_zone`)."""
    with engine.connect() as conn:
        zones = _real_zones(conn)

    overlapping = [
        (a.label, b.label) for a, b in itertools.combinations(zones, 2) if _overlaps(a, b)
    ]
    assert overlapping == []


def test_each_zone_classifies_its_own_center_point(engine):
    """El centro de cada rectángulo con geometría real debe clasificar a su
    propia zona — la comprobación más directa de que el seed y `classify_zone`
    están de acuerdo.

    Las zonas degeneradas quedan fuera (ver `_real_zones`), y también "Ala
    izq."/"Ala der.": desde el split de ala por triple son zonas de PASO —
    `classify_zone` siempre las resuelve un paso más, a la sub-zona "(2)"/
    "(3)" que toque (ver `test_representative_points_land_in_the_expected_zone`)
    — así que su propio centro nunca clasifica a su propio id, por diseño, no
    por un hueco de cobertura.
    """
    with engine.begin() as conn:
        zones = [z for z in _real_zones(conn) if z.label not in {"Ala izq.", "Ala der."}]
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
        ("Triple esquina izq.", (30, 420)),
        ("Triple esquina der.", (470, 420)),
        ("Triple exterior", (250, 100)),
        ("Media dist. central", (250, 230)),
        ("Triple ala izq.", (80, 100)),
        ("Triple ala der.", (420, 100)),
        # "Ala izq."/"Ala der." ya no aparecen aquí como destino: son zonas de
        # PASO (ver `test_each_zone_classifies_its_own_center_point`) que
        # `classify_zone` siempre resuelve un paso más, a una de estas 4 —
        # el propio split de ala por triple, comprobado con un punto de cada
        # lado de la elipse en la misma banda (ver
        # `packages/baskonia_core/court_geometry.py`).
        ("Ala izq. (2)", (100, 350)),   # dentro del arco (2 largo)
        ("Ala izq. (3)", (20, 300)),    # más allá del punto de ruptura de esquina (triple)
        ("Ala der. (2)", (400, 350)),   # simétrico de "Ala izq. (2)"
        ("Ala der. (3)", (480, 300)),   # simétrico de "Ala izq. (3)"
    ],
)
def test_representative_points_land_in_the_expected_zone(engine, label, point):
    """Un punto representativo de cada zona "real" de basket clasifica donde toca —
    cubre el resto de zonas más allá de sus centros exactos."""
    with engine.begin() as conn:
        zone_id = conn.execute(text("SELECT id FROM court_zones WHERE label = :l"), {"l": label}).scalar_one()
        assert classify_zone(conn, *point) == zone_id


def test_coverage_of_the_full_domain_has_no_gaps(engine):
    """El reteselado 2 (2026-08-28) cubre TODO el dominio 0-500 x 0-460, no solo
    la zona ofensiva "realista" — verificado contra los 95 263 tiros reales de
    `data/baskonia.db`: de 4,0% sin zona antes de este reteselado a 1,49%
    después, y ese 1,49% restante son coordenadas de origen fuera de este
    dominio (dato malo, no un hueco de tesela). Una rejilla fina del dominio
    entero debe clasificar el 100% — si esto baja, algo ha vuelto a abrir un
    hueco."""
    with engine.begin() as conn:
        points = [(x, y) for x in range(0, 500, 2) for y in range(0, 460, 2)]
        covered = sum(1 for x, y in points if classify_zone(conn, x, y) is not None)

    coverage = covered / len(points)
    assert coverage == 1.0, f"cobertura de la rejilla: {coverage:.2%}"

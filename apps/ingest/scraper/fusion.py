"""Normalización y fusión de fuentes de datos (capa `scraper/`).

Fusiona los partidos de varias fuentes (RealGM, BBR, CMS, ACB) en una lista
única y deduplicada, priorizando RealGM sobre el resto. Respeta la regla de
capas: no importa `db/`; trabaja con dicts planos normalizados.

La deduplicación es por clave natural `(date, opponent_normalizado, is_home)`
(mismo criterio que el upsert de BD por `(date, home, away)`). Cuando dos
fuentes aportan el mismo partido, gana la de mayor prioridad según
`SOURCE_PRIORITY` (RealGM primero); a igual prioridad, la que trae más datos
(resultado y boxscore_url sobre solo fecha). Los campos que la fuente ganadora
deja vacíos se completan con los de las perdedoras, para no perder un dato
(p.ej. el `boxscore_url` que solo publica BBR) solo por prioridad de fuente.
"""
import logging
import re
import unicodedata
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Orden de prioridad de fuentes: RealGM > BBR > CMS > ACB. Cuando dos fuentes
# aportan el mismo partido, gana la de mayor prioridad (y, dentro de la misma
# prioridad, la que trae más datos).
SOURCE_PRIORITY = {"realgm": 0, "bbr": 1, "cms": 2, "acb": 3}


def _normalize_name(name: str) -> str:
    """Normaliza el nombre de un equipo para comparar entre fuentes.

    Elimina acentos, pasa a minúsculas y quita sufijos comunes ("BC", "Basket",
    "Club", "SAD", etc.) que varían entre fuentes (p.ej. "Baskonia" vs
    "Baskonia SAD").

    Args:
        name: Nombre del equipo tal como lo da una fuente.

    Returns:
        Nombre normalizado para comparación.
    """
    if not name:
        return ""
    # Quitar acentos y pasar a minúsculas.
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    # Quitar sufijos comunes de entidad deportiva.
    text = re.sub(r"\b(bc|basket|basketball|club|sad|sporting|sports)\b", "", text)
    # Colapsar espacios.
    return re.sub(r"\s+", " ", text).strip()


def _game_key(game: Dict[str, object]) -> Tuple[str, str, bool]:
    """Devuelve la clave natural de un partido para deduplicación.

    Args:
        game: Partido con el contrato plano de `scraper/`.

    Returns:
        Tupla `(date, opponent_normalizado, is_home)`.
    """
    return (
        str(game.get("date") or ""),
        _normalize_name(str(game.get("opponent") or "")),
        bool(game.get("is_home")),
    )


def _data_score(game: Dict[str, object]) -> int:
    """Puntúa cuántos datos aporta un partido (para desempatar a igual prioridad).

    Un partido con resultado y boxscore_url aporta más que uno solo con fecha.

    Args:
        game: Partido con el contrato plano de `scraper/`.

    Returns:
        Puntuación (mayor = más datos).
    """
    score = 0
    if game.get("points") is not None or game.get("opp_points") is not None:
        score += 1
    if game.get("boxscore_url"):
        score += 1
    return score


def merge_sources(
    sources: List[Tuple[str, List[Dict[str, object]]]],
) -> List[Dict[str, object]]:
    """Fusiona los partidos de varias fuentes en una lista única y deduplicada.

    Cada elemento de `sources` es `(nombre_fuente, lista_de_partidos)`. La
    deduplicación es por clave natural `(date, opponent_normalizado, is_home)`.
    Cuando dos fuentes aportan el mismo partido, se prefiere la de mayor
    prioridad según `SOURCE_PRIORITY` (RealGM primero); a igual prioridad, la
    que trae más datos (resultado y boxscore_url sobre solo fecha).

    Args:
        sources: lista de `(fuente, partidos)` con el contrato plano de
            `scraper/`.

    Returns:
        Lista única de partidos, sin duplicados. En caso de conflicto mandan
        los campos de la fuente ganadora, pero los campos que esa fuente deja
        vacíos se completan con los de las fuentes perdedoras (ver abajo).
    """
    # Mapa clave -> (prioridad, score, partido)
    # Menor número de prioridad = mayor prioridad (RealGM=0 es la principal).
    best: Dict[Tuple[str, str, bool], Tuple[int, int, Dict[str, object]]] = {}
    # Mapa clave -> partidos descartados, con su prioridad, para rellenar huecos.
    losers: Dict[Tuple[str, str, bool], List[Tuple[int, Dict[str, object]]]] = {}

    for source_name, games in sources:
        priority = SOURCE_PRIORITY.get(source_name, 99)
        for game in games:
            key = _game_key(game)
            score = _data_score(game)
            current = best.get(key)
            if current is None or (
                priority < current[0]
                or (priority == current[0] and score > current[1])
            ):
                if current is not None:
                    losers.setdefault(key, []).append((current[0], current[2]))
                best[key] = (priority, score, game)
            else:
                losers.setdefault(key, []).append((priority, game))

    merged = [
        _fill_gaps(entry[2], [g for _, g in sorted(losers.get(key, []), key=lambda p: p[0])])
        for key, entry in best.items()
    ]
    logger.info("Fusión: %d partidos únicos a partir de %d fuentes", len(merged), len(sources))
    return merged


def _fill_gaps(
    winner: Dict[str, object], others: List[Dict[str, object]]
) -> Dict[str, object]:
    """Completa los campos vacíos del partido ganador con los de las otras fuentes.

    La fuente de mayor prioridad manda, pero puede no traer todo: RealGM puede
    dar un partido sin `boxscore_url` que BBR sí publica. Sin este relleno ese
    dato se perdería solo por haber perdido la fusión, aunque no hubiera
    conflicto real (el ganador no aporta valor para ese campo).

    Solo se rellenan claves ausentes o con valor vacío (`None`/`""`): nunca se
    sobrescribe un valor que el ganador sí trae.

    Args:
        winner: Partido de la fuente de mayor prioridad.
        others: Partidos descartados para la misma clave natural, ordenados de
            mayor a menor prioridad de fuente.

    Returns:
        Copia del partido ganador con los huecos rellenados.
    """
    if not others:
        return winner

    filled = dict(winner)
    for other in others:
        for field, value in other.items():
            if value is None or value == "":
                continue
            if filled.get(field) is None or filled.get(field) == "":
                filled[field] = value
    return filled

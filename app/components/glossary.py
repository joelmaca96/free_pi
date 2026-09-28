"""Glosario de siglas: qué significa cada abreviatura y qué mide de verdad.

Una pantalla de scouting está llena de siglas —ORtg, eFG%, TOV%, PIR, xPPS—
y cada una de ellas es una barrera de entrada para quien no las usa a diario.
Peor: una sigla mal entendida no se nota, se malinterpreta en silencio (eFG%
y TS% no son lo mismo, y confundirlas cambia la conclusión sobre un tirador).

Este módulo es la ÚNICA definición de cada término en la interfaz, y se
sirve por tres vías, según lo que se esté pintando:

- `help_text(clave)` para el argumento `help=` de `st.column_config.*` y de
  `st.metric`. Es el hover nativo de Streamlit: la sigla se explica donde
  está, sin ocupar sitio en la pantalla ni desviar la mirada.
- `abbr(clave)` para el texto suelto, donde no hay `help=` que valga. Pinta
  un `<abbr title="...">` de HTML, que es el mismo gesto (pasar por encima)
  con el tooltip del navegador.
- `glossary_expander(claves)` para el desplegable del pie de una sección: la
  lista entera de lo que sale EN ESA pantalla, para leerla de corrido cuando
  el hover uno a uno se hace pesado.

Las claves son, siempre que se puede, **el nombre de la columna en la base de
datos** (`efg_pct`, `pir`, `tov`): así el mismo diccionario sirve para poner
el `help` y para poner la etiqueta, y no hay forma de que la sigla de una
pantalla se desincronice de la de otra.

Cuando la misma columna significa cosas distintas según el sujeto, son dos
entradas y no una (`minutes` de un jugador contra `lineup_minutes` de un
quinteto, `plus_minus` de un jugador contra `lineup_plus_minus`): fundirlas
sería justo el tipo de ambigüedad que este módulo existe para quitar.
"""
import html
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import streamlit as st


@dataclass(frozen=True)
class Term:
    """Una sigla: cómo se pinta, cómo se lee y qué mide."""

    sigla: str
    nombre: str
    descripcion: str


#: Todas las siglas de la interfaz. El orden importa: es el del desplegable,
#: agrupado por familia (básicas, avanzadas, tiro, quintetos), que es como se
#: leen y no como se ordenarían alfabéticamente.
TERMS: Dict[str, Term] = {
    # -------------------------------------------------- boxscore y medias --
    "gp": Term("PJ", "Partidos jugados", "Cuántos partidos ha disputado en el corte que se está mirando."),
    "minutes": Term("Min", "Minutos jugados", "Minutos en pista. En las medias, minutos por partido."),
    "pts": Term(
        "Pts", "Puntos",
        "Puntos anotados. En un boxscore son los de ESE partido; en las medias de la ficha de "
        "jugador, los de por partido.",
    ),
    "reb": Term(
        "Reb", "Rebotes totales",
        "Rebotes ofensivos más defensivos. El desglose ofensivo/defensivo va aparte cuando la fuente lo da.",
    ),
    "oreb": Term(
        "RO", "Rebote ofensivo",
        "Rebotes capturados en el aro contrario: cada uno es una posesión extra para el ataque.",
    ),
    "dreb": Term("RD", "Rebote defensivo", "Rebotes capturados en el propio aro: cierran la posesión del rival."),
    "ast": Term("Ast", "Asistencias", "Pases que acaban directamente en canasta."),
    "stl": Term("Rob", "Robos", "Balones robados al rival."),
    "blk": Term("Tap", "Tapones", "Tiros del rival bloqueados."),
    "tov": Term(
        "PP", "Pérdidas",
        "Balones perdidos: posesiones que se van sin llegar a tirar. Cuanto más bajo, mejor.",
    ),
    "pf": Term("Faltas", "Faltas personales", "Faltas cometidas. A la quinta, el jugador queda eliminado."),
    "plus_minus": Term(
        "+/-", "Más/menos",
        "Diferencia de puntos del equipo mientras ESE jugador estaba en pista. +8 = el equipo ganó "
        "esos minutos por 8. No es mérito solo suyo: depende de con quién juegue y contra quién.",
    ),
    "pir": Term(
        "PIR", "Valoración",
        "Índice oficial de ACB y Euroliga: suma lo positivo (puntos, rebotes, asistencias, robos, "
        "tapones, faltas recibidas) y resta lo negativo (tiros fallados, pérdidas, faltas cometidas, "
        "tapones recibidos). Un número para ordenar de un vistazo, no un juicio.",
    ),
    # ------------------------------------------------------------ avanzadas --
    "ortg": Term(
        "ORtg", "Rating ofensivo",
        "Puntos anotados por cada 100 posesiones. Es el ataque ya limpio de ritmo: un equipo lento "
        "que anota poco puede tener mejor ORtg que uno rápido que anota mucho.",
    ),
    "drtg": Term(
        "DRtg", "Rating defensivo",
        "Puntos encajados por cada 100 posesiones. Cuanto más bajo, mejor defensa.",
    ),
    "net_rating": Term(
        "Net", "Net rating",
        "ORtg menos DRtg: cuántos puntos por 100 posesiones se le sacan al rival. Es el resumen de "
        "una temporada en un número.",
    ),
    "pace": Term(
        "Ritmo", "Posesiones por partido",
        "Cuántas posesiones se juegan. No es bueno ni malo: dice si el partido se va a decidir en "
        "un intercambio de golpes o en pocas posesiones muy trabajadas.",
    ),
    "efg_pct": Term(
        "eFG%", "Porcentaje de tiro efectivo",
        "Como el porcentaje de tiros de campo, pero contando que un triple vale 1,5 veces un tiro de "
        "dos. Es la forma correcta de comparar a un tirador exterior con un interior.",
    ),
    "ts_pct": Term(
        "TS%", "True Shooting",
        "Acierto real teniendo en cuenta TODO lo que produce puntos: dos, triples y tiros libres. "
        "A diferencia del eFG%, premia a quien vive en la línea de personal.",
    ),
    "fg_pct": Term("FG%", "Porcentaje de tiros de campo", "Tiros anotados sobre tiros intentados, sin contar libres."),
    "ft_pct": Term("FT%", "Porcentaje de tiros libres", "Tiros libres anotados sobre intentados."),
    "ft_rate": Term(
        "Tasa TL", "Tasa de tiros libres",
        "Tiros libres intentados por cada tiro de campo. Mide cuánto se ataca el aro y se provoca "
        "falta, no si se acierta desde la línea.",
    ),
    "tov_pct": Term(
        "TOV%", "Porcentaje de pérdidas",
        "Qué parte de las posesiones acaba en pérdida. Cuanto más bajo, mejor cuidado del balón.",
    ),
    "orb_pct": Term(
        "ORB%", "Porcentaje de rebote ofensivo",
        "Qué parte de los rebotes ofensivos disponibles se captura. Mide segundas oportunidades.",
    ),
    "ast_pct": Term(
        "% Asistidas", "Canastas asistidas",
        "Qué parte de las canastas del equipo llega tras un pase de asistencia. Alto = juego "
        "colectivo; bajo = mucho uno contra uno.",
    ),
    "stl_pct": Term("% Robo", "Tasa de robo", "Qué parte de las posesiones del rival acaba en robo."),
    "blk_pct": Term("% Tapón", "Tasa de tapón", "Qué parte de los tiros del rival acaba taponada."),
    "ast_to_ratio": Term(
        "AST/TOV", "Asistencias por pérdida",
        "Asistencias divididas por pérdidas. Por encima de 2 se considera buen cuidado del balón.",
    ),
    # ---------------------------------------------------------- calidad de tiro --
    "pps": Term(
        "PPS", "Puntos por tiro",
        "Puntos que se sacaron de verdad por cada tiro intentado. Es el ACIERTO.",
    ),
    "xpps": Term(
        "xPPS", "Puntos por tiro esperados",
        "Lo que valían los tiros que se generaron, según desde dónde se tiraron y lo que la liga "
        "entera saca desde ahí. Es la DECISIÓN, independiente de si entraron.",
    ),
    "diff_shrunk": Term(
        "Acierto sobre lo esperado", "PPS menos xPPS, regularizado",
        "Cuánto se acertó por encima (o por debajo) de lo que valían esos tiros. Va regularizado "
        "hacia cero con pocos tiros: con muestra pequeña, el acierto es sobre todo suerte.",
    ),
    "shots": Term("Tiros", "Tiros intentados", "Tiros de campo intentados (los libres no cuentan)."),
    "zone_label": Term("Zona", "Zona de cancha", "Zona desde la que se tira, según el mapa de `court_zones`."),
    # ----------------------------------------------- quintetos y rotaciones --
    "lineup_minutes": Term(
        "Min. juntos", "Minutos del quinteto",
        "Minutos que esos cinco han coincidido en pista, no los de cada jugador por su cuenta.",
    ),
    "lineup_plus_minus": Term(
        "+/- del quinteto", "Más/menos del quinteto",
        "Diferencia de puntos mientras esos cinco estaban juntos en pista.",
    ),
    "stints": Term(
        "Tramos", "Tramos en pista",
        "Cuántas veces esos cinco han saltado juntos a la pista. Diez minutos en un tramo y diez en "
        "seis tramos sueltos no dicen lo mismo.",
    ),
    # ------------------------------------------ on/off y duplas (propuesta 07) --
    "on_minutes": Term(
        "Min. con él", "Minutos con el jugador en pista",
        "Minutos del equipo mientras ESE jugador estaba en la cancha, sumando todos sus tramos de "
        "la temporada.",
    ),
    "off_minutes": Term(
        "Min. sin él", "Minutos sin el jugador en pista",
        "Minutos del equipo mientras ese jugador estaba en el banquillo. Con on_minutes suman los "
        "minutos totales del equipo en la temporada.",
    ),
    "on_per_40": Term(
        "+/- 40 con él", "Diferencia por 40 minutos, con él en pista",
        "Diferencia de puntos del equipo mientras jugaba, normalizada a 40 minutos.",
    ),
    "off_per_40": Term(
        "+/- 40 sin él", "Diferencia por 40 minutos, sin él en pista",
        "Diferencia de puntos del equipo mientras estaba en el banquillo, normalizada a 40 minutos. "
        "Es la referencia con la que se compara on_per_40 para sacar el On/Off.",
    ),
    "on_off": Term(
        "On/Off", "Diferencia por 40 con él, menos sin él",
        "Cuánto mejor (o peor) le va al equipo con ese jugador en pista frente a sin él. NO es una "
        "medida de la calidad del jugador, es de contexto: compartir pista siempre con los buenos "
        "infla el número, y al revés. Va regularizado hacia 0 con poca muestra.",
    ),
    "plus_minus_per_40": Term(
        "+/- por 40", "Diferencia de puntos por 40 minutos juntos",
        "Diferencia de puntos del equipo mientras esa pareja o trío coincidían en pista, normalizada "
        "a 40 minutos para poder comparar combinaciones con minutos muy distintos.",
    ),
    "sample_flag": Term(
        "Muestra", "Aviso de minutos mínimos",
        "'Ok' si llega al mínimo no negociable de la propuesta 07 (200 minutos en pista para el "
        "On/Off individual, 100 minutos juntos para parejas y tríos); por debajo, 'Insuficiente' — "
        "el número existe pero no debe usarse para decidir.",
    ),
    "run_swing": Term(
        "Swing", "Puntos que se mueve el marcador",
        "Cuánto cambia la diferencia en el marcador durante el parcial. +10 = se le sacaron diez "
        "puntos al rival en esos minutos.",
    ),
    "run_share": Term(
        "% del parcial", "Presencia en el parcial",
        "Qué parte de los minutos del parcial estuvo ese jugador en pista. 100% = lo jugó entero.",
    ),
    "run_minutes": Term(
        "Min", "Minutos dentro del parcial",
        "Minutos de ese jugador DENTRO de la ventana del parcial, no los del partido.",
    ),
    "margin": Term(
        "Margen", "Diferencia en el marcador",
        "Puntos a favor menos puntos en contra en ese instante. Positivo = ganando.",
    ),
    # -------------------------------------- fatiga y calendario (propuesta 04) --
    "rest_days": Term(
        "Descanso", "Días de descanso",
        "Días desde el partido anterior del equipo, en CUALQUIER competición — ACB y Euroliga cuentan "
        "igual. Vacío en el primer partido registrado: no hay 'anterior' del que restar.",
    ),
    "rolling_minutes": Term(
        "Carga", "Minutos en la ventana",
        "Minutos acumulados de un jugador en los últimos N días de calendario, no en los últimos N "
        "partidos: cinco partidos en nueve días y cinco en tres semanas no son la misma carga.",
    ),
    "minutes_per_day": Term(
        "Min/día", "Minutos por día disponible",
        "Carga de la ventana dividida entre sus días. Compara jugadores con roles distintos sin que un "
        "partido más o menos en la ventana desnivele la lectura.",
    ),
    "rest_bucket": Term(
        "Tramo de descanso", "Días de descanso agrupados",
        "≤1, 2, 3-4 o ≥5 días desde el partido anterior. Con 78 partidos como mucho por temporada de "
        "doble competición, la muestra de cada tramo puede ser pequeña — mira siempre el PJ.",
    ),
    # ------------------------------------------- gestión de faltas (propuesta 06) --
    "pf_per40": Term(
        "Faltas/40", "Faltas cometidas por 40 minutos",
        "Faltas personales normalizadas a 40 minutos en pista. Compara a un titular y a un suplente en "
        "las mismas condiciones — un total de temporada por sí solo premia a quien juega menos.",
    ),
    "pf_drawn_per40": Term(
        "Provocadas/40", "Faltas provocadas por 40 minutos",
        "Faltas del rival que provoca ese jugador, por 40 minutos en pista. Alto = a quien no conviene "
        "ponerle la mano encima.",
    ),
    "fta_per40": Term(
        "TL/40", "Tiros libres intentados por 40 minutos",
        "Tiros libres que tira ese jugador, normalizados a 40 minutos. Dice si convierte las faltas que "
        "provoca en viajes a la línea — no hay 'tasa de tiro libre' por jugador (esa se define sobre "
        "tiros de campo intentados, que no se guardan por jugador; solo por equipo, ver ft_rate).",
    ),
    "min_2nd_foul_avg": Term(
        "Min. 2.ª falta", "Minuto medio de la 2.ª falta",
        "Minuto de partido en el que llega, de media, la segunda falta personal. Cuanto más bajo, antes "
        "se carga — 'dos faltas en el minuto 6' es el caso que más banquillo cuesta.",
    ),
    "min_3rd_foul_avg": Term(
        "Min. 3.ª falta", "Minuto medio de la 3.ª falta",
        "Igual que el minuto de la 2.ª falta, pero de la tercera — el aviso relevante de cara al descanso.",
    ),
    "pf_quarter_share": Term(
        "Reparto por cuarto", "% de faltas por cuarto",
        "Qué parte de las faltas de la temporada de ese jugador cae en cada cuarto (suman 100% por fila). "
        "Dice si se carga pronto (Q1 alto) o tarde (Q4 alto), no cuántas faltas hace en total.",
    ),
    "early_trouble_games": Term(
        "Cargas tempranas", "Partidos con carga temprana",
        "Partidos en los que llegó a la 2.ª falta antes del minuto o a la 3.ª antes del otro minuto que "
        "marquen los controles de arriba (2 antes del 10 y 3 antes del 20, por defecto).",
    ),
    "early_trouble_rate": Term(
        "% cargas tempranas", "Partidos con carga temprana, sobre el total",
        "Cargas tempranas dividido entre partidos jugados. Compara a un jugador con pocos partidos con "
        "otro que ha jugado toda la temporada sin que el total desnivele la lectura.",
    ),
    "minutes_lost_avg": Term(
        "Min. perdidos (aprox.)", "Minutos por debajo de su media, en partidos con carga temprana",
        "Minutos jugados esa noche frente a su media de temporada, en los partidos con carga temprana. "
        "Aproximado a propósito: mezcla el efecto de la falta con cualquier otro motivo por el que "
        "jugara distinto esa noche (lesión, partido ya decidido) — para la medida honesta, ver el hueco "
        "real en pista.",
    ),
    "bench_gap_avg_min": Term(
        "Hueco real en pista", "Minutos reales sentado tras la falta",
        "Desde la falta que dispara el aviso hasta que ese jugador vuelve a pisar la pista (o el final "
        "del partido, si no vuelve), medido en los tramos de quinteto reales — no una media, el hueco "
        "de cada partido concreto.",
    ),
    "bench_margin_per_min": Term(
        "Margen durante el hueco", "Diferencia de puntos del equipo, por minuto sentado",
        "Diferencia de puntos del equipo mientras ese jugador estaba fuera por la falta, por minuto. "
        "DESCRIPTIVO, no causal: el rival, el momento y el marcador de esos minutos concretos no son "
        "comparables sin más con un minuto cualquiera — no leer como 'sentarlo costó X puntos'.",
    ),
    "team_margin_per_min_season": Term(
        "Margen habitual", "Diferencia de puntos del equipo por minuto, toda la temporada",
        "La referencia con la que comparar el margen durante el hueco: cómo le va al equipo por minuto "
        "en un partido cualquiera, no solo cuando falta este jugador.",
    ),
    # -------------------------------------- perfil arbitral (propuesta 05) --
    # Prefijo "referee_" a propósito, aunque casi todo son faltas/tiros libres:
    # miden la SUMA de los dos equipos en el partido, no lo de un jugador o un
    # equipo — fundirlas con "pf"/"fta" sería la ambigüedad que este módulo
    # existe para evitar (ver la nota del docstring del fichero).
    "referee_pf_avg": Term(
        "Faltas/partido", "Faltas totales señaladas por partido (los dos equipos)",
        "Suma de faltas personales de ambos equipos en los partidos que dirige. Sin ajustar por "
        "competición — la que ordena el ranking es la ajustada (ver 'Faltas/partido (ajustado)').",
    ),
    "referee_pf_residual": Term(
        "Faltas/partido (ajustado)", "Faltas totales frente a la media de su competición",
        "Faltas del partido menos la media de faltas de ESA competición esa temporada, promediado por "
        "árbitro. Es la métrica que ordena el ranking: ACB y Euroliga no se pitan igual (unas 4 faltas de "
        "diferencia de media), así que comparar por la falta bruta mide sobre todo dónde le designan, no "
        "cómo pita.",
    ),
    "referee_fta_avg": Term(
        "TL/partido", "Tiros libres totales intentados por partido (los dos equipos)",
        "Igual que 'Faltas/partido' pero en tiros libres concedidos, sin ajustar por competición.",
    ),
    "referee_home_bias_fta": Term(
        "Sesgo local (TL)", "Diferencia de tiros libres entre el equipo local y el visitante",
        "Tiros libres del equipo local menos los del visitante, de media en los partidos que dirige. La "
        "media de la liga es positiva (favorece algo al local): un valor muy por encima o por debajo de "
        "esa media es la señal, no el signo por sí solo.",
    ),
    "referee_pace_residual": Term(
        "Ritmo (ajustado)", "Posesiones del partido frente a la media de su competición",
        "Ritmo de los partidos que dirige, comparado con la media de su competición — los partidos muy "
        "pitados suelen ser más lentos, y esta es la conexión entre 'pita mucho' y 'el partido se corta'.",
    ),
    "referee_sample_size": Term(
        "Muestra", "Aviso de tamaño de muestra del árbitro",
        "'ok' con 15 partidos o más esta temporada (entra en el ranking de la liga); 'caution' entre 8 y "
        "14 (se enseña su ficha, pero como tendencia gruesa, no como afirmación fina). Por debajo de 8 no "
        "se muestra ficha.",
    ),
    # --------------------------------- dónde castigar al rival (propuesta 08) --
    "concede_diff_pp": Term(
        "Concede vs. liga", "Acierto que concede en la zona, frente a la media de la liga",
        "Puntos porcentuales de diferencia entre lo que se acierta EN ESA ZONA contra este equipo y lo "
        "que acierta ahí toda la liga. Positivo = defensa floja en esa zona. Ya regularizado hacia 0 "
        "con poca muestra.",
    ),
    "produce_diff_pp": Term(
        "Produce vs. liga", "Acierto propio en la zona, frente a la media de la liga",
        "Igual que 'Concede vs. liga' pero del lado que TIRA: cuánto se acierta ahí por encima (o por "
        "debajo) de lo que acierta la liga entera desde esa misma zona.",
    ),
    "shots_per_game": Term(
        "Tiros/partido", "Volumen medio de tiros por partido en esa zona",
        "Cuántos tiros de media se lanzan en esa zona por partido. Una zona con mucha diferencia de "
        "acierto pero poco volumen mueve menos puntos que una con menos diferencia y más volumen — por "
        "eso el ranking ordena por puntos por partido, no por el porcentaje.",
    ),
    "value_pts_per_game": Term(
        "Puntos/partido en juego", "Puntos por partido que se pueden ganar (o perder) en esa zona",
        "(Diferencia de acierto contra la liga) × (2 o 3, lo que vale el tiro) × (tiros por partido en "
        "esa zona). Es la unidad en la que decide un entrenador: ordena la lista de zonas a atacar (o a "
        "cuidar), no el porcentaje suelto.",
    ),
    # ------------------------------------ similitud de jugadores (propuesta 11) --
    "similarity_score": Term(
        "Parecido", "Puntuación de parecido (0-100)",
        "Qué tan cerca están dos perfiles de percentiles en las dimensiones que se pudieron comparar. "
        "100 = idéntico en todo lo disponible. Depende del método elegido (estilo o nivel) y de los "
        "pesos ajustados — no es una cifra absoluta, cambia según qué se le pida que compare.",
    ),
    # --- momentos clave (propuesta 18) --
    "win_probability": Term(
        "Prob. victoria", "Probabilidad de victoria",
        "Probabilidad de ganar el partido en ese instante según el margen, el tiempo que queda y "
        "quién juega en casa — nada más (no sabe quién tiene la posesión ni cómo de bueno es cada "
        "equipo). Sale de un modelo ajustado con todos los partidos de la liga en la temporada.",
    ),
    "wpa": Term(
        "WPA", "Probabilidad de victoria añadida (win probability added)",
        "Cuántos puntos porcentuales subió (o bajó) la probabilidad de ganar durante una jugada o "
        "tramo. Es lo que ordena los momentos clave: un 5-0 con el partido igualado a falta de dos "
        "minutos mueve mucho más que un 10-0 en el primer cuarto.",
    ),
    "wpa_lineup": Term(
        "WPA quinteto", "Probabilidad de victoria añadida con ese quinteto en pista",
        "Suma de todo lo que se movió la probabilidad de ganar mientras esos cinco estaban juntos en "
        "pista (en puntos porcentuales). Todos los quintetos de un equipo suman su probabilidad final "
        "menos la de salida. Dice cuándo se ganó o perdió el partido, no quién jugó mejor.",
    ),
}


def term(key: str) -> Term:
    """El término de `key`.

    Raises:
        KeyError: la clave no está en el glosario. Es un error de programación
            —una sigla que se pinta sin haberla definido— y conviene verlo al
            cargar la página y no como un hueco silencioso en un tooltip.
    """
    return TERMS[key]


def help_text(key: str) -> str:
    """Texto para el `help=` de `st.column_config.*` y de `st.metric`.

    Nombre completo primero y descripción después: al pasar por encima, lo
    primero que se lee es qué es la sigla, y solo después qué mide.
    """
    entry = TERMS[key]
    return f"{entry.nombre} — {entry.descripcion}"


def abbr(key: str, text: Optional[str] = None) -> str:
    """`<abbr>` HTML con el significado en el tooltip del navegador.

    Para el texto suelto (títulos, pies, listas de métricas hechas a mano),
    donde no hay ningún `help=` de Streamlit al que agarrarse. Se escapa el
    contenido porque acaba en un `unsafe_allow_html=True`: el texto sale de
    este módulo, pero la regla de no concatenar HTML sin escapar no admite
    excepciones "porque este dato es de confianza".

    Args:
        text: qué se pinta; por defecto, la sigla del glosario.
    """
    entry = TERMS[key]
    shown = html.escape(text if text is not None else entry.sigla)
    title = html.escape(f"{entry.nombre} — {entry.descripcion}")
    return (
        f'<abbr title="{title}" style="text-decoration:underline dotted;'
        f'text-underline-offset:3px;cursor:help">{shown}</abbr>'
    )


def rows(keys: Iterable[str]) -> List[Term]:
    """Los términos de `keys`, en el orden del glosario y sin repetidos."""
    wanted = set(keys)
    return [entry for key, entry in TERMS.items() if key in wanted]


def glossary_expander(keys: Iterable[str], *, title: str = "¿Qué significa cada sigla?") -> None:
    """Desplegable con la lista de siglas de ESTA sección.

    Se pasa qué siglas salen en la pantalla en vez de volcar el glosario
    entero: una lista de treinta términos de los que solo ocho están en
    pantalla se deja de leer a la segunda vez.
    """
    entries = rows(keys)
    if not entries:
        return
    with st.expander(title):
        st.markdown(
            "\n".join(
                ["| Sigla | Significado | Qué mide |", "|---|---|---|"]
                + [f"| **{e.sigla}** | {e.nombre} | {e.descripcion} |" for e in entries]
            )
        )

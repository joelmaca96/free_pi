# Solicitud — 002-competicion-real-por-partido

## Origen

Feature derivada de un bloqueo real encontrado en el ciclo 3 de diseño de
`local/features/001-analisis-diferencial-7-3/` (ver esa feature, `01_design.md` sección "Preguntas
abiertas para el usuario" → "Ciclo 3 — bloqueo real", y `STATUS.md`). Al diseñar un selector de
competición (ACB/Euroleague/Supercopa/"todas") para la feature 001, se investigó contra
`data/baskonia.db` real y se encontró que `Game.league` **no es fiable** como filtro por partido.

## Petición del usuario (verbatim, trasladada por el coordinador)

> El usuario NO renuncia al filtro de competición y NO quiere meter el arreglo dentro de la
> feature 001. Secuencia elegida: feature nueva que arregle el etiquetado de competición → después
> se retoma la 001 ya con el selector.

## Evidencia del problema (ya verificada, no volver a re-descubrirla desde cero)

- `Game.league` tiene 3 valores limpios en la BD real (220 partidos): `acb` (181), `euroleague`
  (38), `supercopa` (1). 0 nulos, sin inconsistencias de formato.
- Pero de los **139 partidos ya jugados**, el 100% tiene `league='acb'` — 0 con `'euroleague'` o
  `'supercopa'` — aunque el Baskonia es club de Euroliga y ha jugado partidos reales de Euroliga en
  la temporada 2025-26. Los 39 partidos con liga distinta de `'acb'` son, sin excepción, partidos
  **pendientes** (`home_score IS NULL`) procedentes del calendario oficial de baskonia.com
  (`scraper/baskonia_official.py`).
- **Causa raíz verificada en el código**: `main.py:466-468` asigna la liga de un equipo de forma
  fija al crearlo (`upsert_team(session, team_slug, team_name, "acb")`, con el comentario propio del
  código "asumimos liga ACB para el caso de uso"), y `main.py:556` copia esa misma liga fija a cada
  partido de ese equipo (`league=source_team.league`) sin mirar la competición real de cada partido
  concreto. La página de BBR que se scrapea sí separa los partidos por competición en tablas
  distintas (ver `scraper/parser.py:174-178` y el sufijo de fase que ya se recorta en
  `scraper/parser.py:123`, `-regular-season`/`-playoffs`), pero `_parse_schedule_table` descarta esa
  información antes de que llegue a `main.py`.
- Es una limitación ya conocida y documentada como menor en `README.md` (líneas ~176-178, 653-654);
  este es el primer intento de *usar* ese dato como filtro, lo que expone que la limitación es más
  severa de lo que su descripción original sugería.

## Alcance de esta feature

Que `Game.league` refleje la **competición real de cada partido**, no la liga fija del equipo de
origen. Concretamente:

1. **Captura en origen**: extraer la competición real por partido en `scraper/parser.py` al parsear
   el calendario de BBR (el dato existe en el HTML — tablas separadas por competición — y hoy se
   descarta). Confirmar exactamente de dónde sale y qué valores reales aparecen antes de diseñar.
2. **Persistencia**: guardarla vía `db/storage.py` y dejar de sobreescribirla con la liga fija del
   equipo en `main.py`.
3. **Backfill de los 139 partidos ya jugados** mal etiquetados. Dos puntos a resolver con evidencia,
   no por asunción:
   - ¿Se puede corregir sin volver a la red (derivando la competición de algo ya persistido: rival,
     fecha, cruce con el calendario oficial de `baskonia_official.py`), o obliga a re-scrapear? Si
     hay una vía sin red que cubra el 100% de los casos, es preferible.
   - Si obliga a re-scrapear: `README.md` avisa de que BBR puede bloquear con demasiadas peticiones
     (`REQUEST_DELAY`, ver `config.py`) — el plan debe decir cuántas peticiones supone y cuánto
     tarda, y respetar el rate-limit existente.
4. **Seguridad del dato**: el backfill muta `data/baskonia.db` (datos reales, no triviales de
   recuperar). El diseño DEBE incluir copia de seguridad del fichero antes de cualquier escritura
   masiva, y el backfill debe ser repetible/idempotente. Esto es requisito, no opcional.
5. **Verificación de salida**: tras el backfill, los partidos de Euroliga del Baskonia deben
   aparecer con `league='euroleague'` (hoy 0 de 139), y el reparto por competición debe cuadrar con
   la realidad conocida de la temporada 2025-26 — con cifras concretas comprobadas contra la BD,
   igual de rigurosas que las del ciclo 3 de la feature 001.

**Fuera de alcance**: el selector de competición en la UI de `app.py` en sí (eso es la Ampliación B
de la feature 001, que se retomará después de esta). Esta feature solo arregla el dato en origen.

## Docs / tests

Arrastrados de la feature 001 (mismo usuario, misma sesión, no se vuelve a preguntar):
- `docs: yes` — el Documenter debe reflejar el fix en `README.md` (la limitación conocida
  documentada en líneas ~176-178/653-654 deja de aplicar, o se corrige su descripción).
- `tests: no`.

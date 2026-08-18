# Docs: Competición real por partido (`Game.league`)

## Documentos actualizados

| Fichero | Secciones tocadas | Motivo |
|---|---|---|
| `README.md` | "1. Contexto del proyecto" → "Estado actual" (✅ Implementado y validado) | Nueva entrada describiendo el bug de raíz (liga fija del equipo de origen en `main.py`), el fix en `parser.py`/`db/storage.py`/`main.py`, y el resultado numérico del backfill real (139 partidos jugados: antes 139 `acb`/0 `euroleague`, ahora 101/38). Sigue el mismo nivel de detalle que las entradas ya existentes (bug de encoding, migración de slugs falsos). |
| `README.md` | "1. Contexto del proyecto" → "Limitaciones que sí siguen abiertas" | Nueva entrada documentando la limitación conocida no corregida (Riesgo #2 de `01_design.md`, confirmado en `02_implementation.md`): la tabla `SPA` de BBR agrupa liga regular ACB con playoffs/Copa bajo el mismo código, con evidencia numérica (real-madrid/barcelona 4 partidos, valencia 5). |
| `README.md` | "7.1 Tareas inmediatas del pipeline (scraping)" (checklist) | Se retiró de la entrada `[x]` de resolución de slug real de rival (líneas ~653-654 antes del cambio) la frase obsoleta "La liga asignada al rival sigue siendo una suposición basada en el equipo de origen (no está resuelto...)", ya falsa tras este fix. Se añadió un nuevo `[x]` propio para esta feature, con referencia cruzada a "Estado actual" y a la limitación de la tabla `SPA`. |
| `README.md` | "4. Fuente de datos: Basketball-Reference (BBR)" | Nuevo párrafo "Competición real por partido (`league`)", mismo estilo que el párrafo ya existente sobre `opponent_slug`: documenta `_table_competition()`, el mapeo `SPA`→`acb`/`ELG`→`euroleague`, que la resolución es una vez por tabla, y que `Team.league` pasa a ser solo valor de reserva. |
| `README.md` | "5. Modelo de datos (SQLAlchemy)" (tabla de esquema) | Aclarado el contrato de las columnas `teams.league` (liga fija de referencia del equipo, solo fallback) y `games.league` (competición real de ese partido concreto). Objetivo: evitar que una futura feature vuelva a confundir ambos campos (la causa raíz de este bug era exactamente esa confusión). |
| `README.md` | "6. Cómo ejecutar" | Añadida la línea `python main.py --fix-league` al bloque de comandos, junto a `--refresh-teams` (que ya estaba documentado ahí), y un párrafo en el mismo formato de blockquote explicando qué hace el backfill (backup previo, qué equipos re-descarga, que es idempotente y no toca box scores/plantilla). |

## Documentos creados

Ninguno.

## Huecos detectados

1. **Referencia de línea imprecisa en `01_design.md`** (no bloqueante, decisión razonada sin preguntar por restricción operativa de la sesión): el diseño pide actualizar "la limitación conocida descrita en líneas ~176-178" del README. En el README real esas líneas describen una limitación distinta y aún válida — `upcoming_games()` no distingue competición al listar partidos **pendientes** (Supercopa mezclada con Liga Endesa/Euroleague), que trata sobre el calendario oficial de baskonia.com, no sobre `Game.league` de partidos ya jugados capturado de BBR, y está fuera de alcance de este diseño (Ampliación B de la feature 001). No se tocó esa entrada. El único texto que realmente decía "una suposición basada en el equipo de origen" estaba en las líneas ~653-654 (sección 7.1, checklist), que sí se corrigió. Si el diseño quería decir otra cosa con "~176-178", conviene que un humano lo revise; lo más parecido en contenido es el bullet de la sección 8 ("el parser asume... calendario por `id` de competición"), que no necesitaba corrección (sigue siendo cierto) pero ya quedó reforzado indirectamente por el nuevo párrafo de la sección 4.
2. **Doxygen/docstrings**: no aplica gap — `_table_competition()`, el `Returns:` ampliado de `parse_schedule_games()`, `upsert_game()`, `backfill_league()`, `_league_counts()` y `_backup_database()` ya tienen docstring Google en español según `02_implementation.md` (verificado también por el reviewer). No se detectaron símbolos públicos nuevos sin documentar.
3. **Copias de seguridad en `data/`**: quedan 4 ficheros `baskonia.db.bak-*` (~560 KB) según nota del reviewer en `STATUS.md`. Es una nota operativa, no un hueco de documentación; no se ha tocado `data/` ni ningún `.py` en esta etapa.
4. **`Team.league`**: sigue fijo a `"acb"` para todos los equipos (decisión explícita del diseño, fuera de alcance). Ahora está documentado en el README como "valor de reserva", pero si una futura feature necesita la liga doméstica real de un equipo como dato propio, esa semántica seguirá siendo insuficiente — ya señalado como riesgo #4 en `01_design.md`, sin acción pendiente de esta etapa.

## Nota de alcance

No se ha modificado ningún fichero `.py` ni `data/baskonia.db` — solo `README.md`, `STATUS.md` y este `04_docs.md`.

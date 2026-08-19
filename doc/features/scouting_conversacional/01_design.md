# Diseño — Asistente conversacional de scouting (scouting_conversacional)

Documento de **diseño** (etapa de arquitectura del pipeline de features) para el caso de uso
3.1 de [01_training.md](../../ideas/01_training.md), desarrollado en
[02_scouting_conversacional.md](../../ideas/02_scouting_conversacional.md). Este documento
convierte esa idea en un diseño concreto que se integra en la **arquitectura destino** de la
aplicación (pipeline + API + SPA, ver [01_design.md](../../arquitectura/01_design.md)) una vez
completada la migración.

> **Corrección de alcance idiomático respecto a la idea original:** el asistente se diseña para
> **castellano e inglés** (bilingüe ES/EN). **No se contempla euskera** en esta feature. El
> idioma de la respuesta sigue al idioma de la pregunta (detección automática), con un selector
> manual opcional en la UI.

---

## 1. Alcance

Construir un **asistente conversacional de solo lectura** que permita al cuerpo técnico del
Baskonia consultar la base de datos en lenguaje natural, sin terminal ni SQL. Cada respuesta se
apoya en una **función del dominio ya existente y testeada** (`insights.py` / `services/`), de
modo que **el asistente no inventa datos** (cero alucinación de números).

**Entra en esta feature:**
- **Nivel 1 (RAG PoC)**: resolvedor de intención → ejecución de la función del dominio → inyección
  del resultado real en el prompt → redacción de la respuesta por la LLM. Bilingüe ES/EN.
- **Servicio `services/assistant.py`** en `packages/baskonia_core`: orquesta el flujo RAG.
- **Endpoint `/assistant`** en `apps/api` (chat stateless, una pregunta → una respuesta).
- **Generador de dataset sintético** `tools/gen_scouting_dataset.py` (fiel a la BD, bilingüe).
- **Chat embebido** en la SPA (`apps/web`) como componente reutilizable.
- **Verificabilidad**: cada respuesta traza la función y los parámetros que la generaron.

**Fuera de alcance (fases posteriores):**
- **Nivel 2 (agente RLVR con `lora_grpo`)**: encadenamiento autónomo de funciones para preguntas
  compuestas. Se diseña el contrato de herramientas y el dataset, pero el fine-tuning y el agente
  autónomo son una fase posterior (requiere GPU y solo tiene sentido cuando el RAG se queda corto).
- **Autenticación / multiusuario**: se reutiliza el punto de enganche de la arquitectura (sin
  flujo de identidad en esta feature).
- **Escritura en la BD**: el asistente es **solo lectura**, igual que la API.
- **Euskera**: explícitamente fuera de alcance (ver corrección arriba).

## 2. Clase de complejidad

**Normal** (no `trivial`): el RAG PoC es acotado y reutiliza funciones existentes, pero implica
un servicio nuevo, un endpoint, un generador de dataset y un componente de UI. El gate de
aprobación humana del diseño es **obligatorio** (no fast-path).

## 3. Principios que gobiernan el diseño

1. **El asistente no inventa datos.** Toda cifra de una respuesta procede de la salida real de una
   función del dominio. La LLM solo redacta; nunca calcula ni recuerda números.
2. **Solo lectura.** El asistente lee `data/baskonia.db` vía el dominio compartido; nunca escribe.
3. **Verificabilidad.** Cada respuesta incluye la fuente (`según player_recent_form, últimos 5
   partidos`) para que el usuario confíe sin re-comprobar.
4. **Bilingüe ES/EN.** El idioma de la respuesta sigue al de la pregunta. Sin euskera.
5. **Reutilización, no reimplementación.** El asistente llama a las funciones ya existentes de
   `insights.py` / `services/`; no duplica su lógica.
6. **Sin red en tests.** La suite sigue siendo offline al 100% (el LLM se mockea).
7. **Regla de capas.** `services/assistant.py` vive en `packages/baskonia_core` (sin red saliente
   propia; la llamada a la LLM se inyecta como dependencia). `apps/api` expone el endpoint.
   `apps/web` consume por HTTP.

## 4. Arquitectura del asistente (Nivel 1 — RAG)

```
Usuario: "¿Cómo está la forma de Markus Howard?" / "How is Markus Howard's form?"
   │
   ▼
[apps/web]  Chat embebido  ── POST /api/v1/assistant {question, lang?}
   │
   ▼
[apps/api]  router/assistant.py  ── valida, inyecta sesión + LLM client
   │
   ▼
[packages/baskonia_core/services/assistant.py]
   │
   ├─ 1. Resolvedor de intención  →  identifica player_recent_form(team, player, n=5)
   │        (reglas + LLM con catálogo de funciones como contexto)
   │
   ├─ 2. Ejecución  →  insights.player_recent_form(...)  →  DataFrame real desde la BD
   │
   ├─ 3. Serialización  →  resultado real → JSON canónico (mismo contrato que la API)
   │
   └─ 4. Redacción  →  LLM redacta la respuesta en prosa con los datos reales inyectados
   │        (prompt con los datos + instrucciones de idioma y tono)
   │
   ▼
Respuesta: {answer, source: {function, args}, lang}
```

**Puntos clave del flujo:**
- El **resolvedor** decide qué función llamar. En el Nivel 1 se hace con **reglas + LLM**: el
  catálogo de funciones (nombre, descripción, parámetros) se inyecta en el prompt y la LLM emite
  una llamada estructurada (JSON). Si la confianza es baja, el asistente pide aclaración en vez de
  inventar.
- La **ejecución** siempre usa la función real del dominio. El resultado se serializa con el mismo
  contrato que la API (números como números, `null` para ausencia) para que la paridad sea
  verificable.
- La **redacción** recibe los datos reales y las instrucciones de idioma/tono. La LLM **no** tiene
  acceso a la BD directamente: solo ve el JSON de la función.

### 4.1 Catálogo de capacidades (mapeado a funciones reales)

El catálogo es la lista de funciones que el resolvedor puede invocar. Cada entrada define nombre,
descripción (para la LLM), parámetros y la función del dominio que la implementa:

| # | Capacidad | Función del dominio | Parámetros |
|---|---|---|---|
| 1 | Forma de un jugador | `insights.player_recent_form` | `team`, `player`, `n` |
| 2 | Rachas (hot/cold) | `insights.player_form_zscore` | `team`, `recent_n`, `min_season_games` |
| 3 | Carga de minutos | `insights.player_load` | `team`, `window_days` |
| 4 | Resumen avanzado del equipo | `insights.team_advanced_summary` | `team`, `season`, `league` |
| 5 | Dificultad del calendario | `insights.schedule_difficulty` | `team`, `next_n` |
| 6 | Proyección del próximo partido | `insights.project_next_matchup` | `team`, `opponent` |
| 7 | Narrativa de scouting | `insights.scouting_narrative` | `team`, `recent_n` |
| 8 | Partidos pasados | `services.calendar.past_games` | `team`, `season`, `league` |
| 9 | Próximos partidos | `services.calendar.upcoming_games` | `team` |
| 10 | Head-to-head | `services.matchup.head_to_head_games` | `team`, `opponent` |
| 11 | Box score de un partido | `services.boxscore.boxscore_rows` | `game_id`, `team_slug` |
| 12 | Plantilla actual | `services.roster.current_roster` | `team` |
| 13 | Validación de datos | `insights.validate_data` | — |

**Preguntas compuestas (Nivel 2, fuera de alcance):** el catálogo ya permite encadenar funciones
(`schedule_difficulty` → `head_to_head_games` → `project_next_matchup`), pero el encadenamiento
**autónomo** es del Nivel 2. En el Nivel 1, una pregunta compuesta se resuelve ejecutando la
función principal y, si procede, una secundaria de forma **orquestada por reglas** (no por la LLM).

## 5. Estructura de ficheros a crear

```
packages/baskonia_core/
├── services/
│   ├── assistant.py            # NUEVO: orquesta el flujo RAG (resolvedor → ejecución → redacción)
│   └── assistant_catalog.py    # NUEVO: catálogo de capacidades (nombre, desc, params, función)
│   └── llm.py                  # NUEVO: cliente LLM abstracto (interfaz, sin implementación de red)

apps/api/
├── routers/
│   └── assistant.py            # NUEVO: POST /assistant
├── schemas/
│   └── assistant.py            # NUEVO: AssistantRequest, AssistantResponse, SourceRef
└── deps.py                     # MOD: get_llm (inyección del cliente LLM)

apps/web/src/
├── components/
│   └── AssistantChat.tsx       # NUEVO: chat embebido (F5, cuando exista la SPA)
└── lib/
    └── assistant.ts            # NUEVO: cliente TS del endpoint /assistant

tools/
└── gen_scouting_dataset.py     # NUEVO: generador de dataset sintético bilingüe

tests/
├── test_assistant.py           # NUEVO: tests del servicio (LLM mockeado)
└── api/
    └── test_assistant.py       # NUEVO: tests del endpoint (LLM mockeado)
```

## 6. Contrato del endpoint `/assistant`

`POST /api/v1/assistant` — chat **stateless** (una pregunta → una respuesta; sin historial en esta
feature). El estado conversacional (si se quiere) es responsabilidad del frontend.

### 6.1 Request

```json
{
  "question": "¿Cómo está la forma de Markus Howard?",
  "lang": "auto"
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `question` | `string` (obligatorio, 1-500 chars) | Pregunta en lenguaje natural |
| `lang` | `"auto" \| "es" \| "en"` (opcional, default `"auto"`) | Idioma de la respuesta. `auto` = detectar del texto |

### 6.2 Response (200)

```json
{
  "answer": "Markus Howard está en gran forma: promedia 18.4 puntos con un 58.1% de eFG% en los últimos 5 partidos.",
  "lang": "es",
  "source": {
    "function": "player_recent_form",
    "args": {"team": "vitoria", "player": "Markus Howard", "n": 5}
  },
  "needs_clarification": false
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `answer` | `string` | Respuesta redactada en prosa |
| `lang` | `"es" \| "en"` | Idioma efectivo de la respuesta |
| `source` | `SourceRef \| null` | Función y parámetros que generaron los datos (verificabilidad). `null` si no se resolvió función |
| `needs_clarification` | `boolean` | `true` si el asistente pide aclaración en vez de responder |

### 6.3 Errores

Mismo modelo RFC 9457 `problem+json` que el resto de la API:
- `422` — `question` vacía o demasiado larga (validación Pydantic).
- `404` — equipo/oponente no encontrado (reutiliza `TeamNotFound`).
- `503` — el cliente LLM no está disponible (configurado pero falla). El asistente degrada a
  "no puedo responder ahora" sin inventar datos.

## 7. Servicio `services/assistant.py`

Interfaz pública:

```python
def answer(
    session: Session,
    question: str,
    llm: LLMClient,
    lang: str = "auto",
    team_slug: str = "vitoria",
) -> AssistantResult:
    """Responde a una pregunta de scouting en lenguaje natural.

    Args:
        session: sesión SQLAlchemy (solo lectura).
        question: pregunta del usuario en lenguaje natural.
        llm: cliente LLM inyectado (interfaz, sin red propia en el servicio).
        lang: idioma de la respuesta ("auto" | "es" | "en").
        team_slug: slug del equipo por defecto (Baskonia).

    Returns:
        AssistantResult con answer, lang, source y needs_clarification.

    Raises:
        LLMUnavailable: si el cliente LLM falla (se traduce a 503 en la API).
    """
```

**Dependencia inyectada (`LLMClient`):** el servicio **no** hace llamadas de red directamente.
Recibe un cliente LLM (interfaz con `complete(prompt) -> str`). Esto permite:
- Testear el servicio **sin red** (mock del cliente).
- Sustituir el proveedor (API remota, vLLM local, etc.) sin tocar el servicio.
- Mantener la regla de capas: `packages/baskonia_core` no depende de un SDK de LLM concreto.

**Flujo interno:**
1. **Detectar idioma** (`lang="auto"`): heurística simple (presencia de palabras funcionales ES/EN)
   o delegar en la LLM. Devuelve `es`/`en`.
2. **Resolver intención**: construir el prompt con el catálogo de funciones (nombre, descripción,
   parámetros) + la pregunta. La LLM emite un JSON `{"function": "...", "args": {...}}`. Validar
   contra el catálogo (función conocida, args válidos). Si no hay match con confianza → devolver
   `needs_clarification=true` con una pregunta de aclaración.
3. **Ejecutar** la función del dominio con los args resueltos (mapear nombres de jugadores/equipos
   a slugs vía `services.roster.team_by_slug` y búsqueda de jugador).
4. **Serializar** el resultado a JSON canónico (mismo contrato que la API: números, `null`).
5. **Redactar**: prompt con los datos reales + instrucciones de idioma y tono (conciso, orientado a
   decisión). La LLM produce `answer`.
6. **Devolver** `AssistantResult` con `source` (función + args) para trazabilidad.

## 8. Bilingüe ES/EN (corrección de alcance)

La idea original contemplaba español + euskera. **Esta feature corrige ese alcance: castellano e
inglés, sin euskera.**

| Aspecto | Decisión |
|---|---|
| Idiomas soportados | **Castellano (es)** e **inglés (en)** |
| Idioma de la respuesta | Sigue al idioma de la pregunta (detección automática) |
| Selector manual | Opcional en la UI (`auto`/`es`/`en`), override de la detección |
| Dataset de entrenamiento | Generado en ambos idiomas (ver §9) |
| Catálogo de funciones | Descripciones en ambos idiomas para que la LLM resuelva intención en ES y EN |
| Tono | Conciso y orientado a decisión en ambos idiomas |

**Motivo de la corrección:** el cuerpo técnico trabaja en castellano (y el análisis de datos en
inglés, idioma de las fuentes BBR). El euskera no es un requisito actual y añadir un tercer idioma
duplica el coste de dataset, prompts y validación sin valor demostrado. Si en el futuro se
requiere, se añade como idioma adicional sin cambiar la arquitectura (el diseño ya es
idioma-agnóstico: el idioma es un parámetro del prompt).

## 9. Generador de dataset sintético (`tools/gen_scouting_dataset.py`)

Prepara el dataset para el **Nivel 2** (fine-tuning LoRA) y para **evaluar** el Nivel 1. Lee
`data/baskonia.db` y genera pares pregunta→respuesta **fieles a la BD**:

1. **Recorre el catálogo** con distintos parámetros (jugadores, rivales, temporadas, `last_n`).
2. **Ejecuta cada función** y obtiene el resultado real.
3. **Genera la pregunta** con plantillas de redacción variadas, **en ES y EN** (sinónimos, orden de
   palabras, nivel de detalle).
4. **Genera la respuesta** a partir de los valores reales devueltos, garantizando fidelidad.
5. **Valida** cada ejemplo: la respuesta debe ser reproducible ejecutando la función. Un ejemplo
   cuya respuesta no coincide se descarta.

**Formato de salida** (JSONL, formato `messages` con tool-calling para el Nivel 2):

```jsonl
{"messages": [
  {"role": "user", "content": "¿Cómo está la forma de Markus Howard en los últimos 5 partidos?"},
  {"role": "assistant", "content": "{\"tool\": \"player_recent_form\", \"args\": {\"player\": \"Markus Howard\", \"n\": 5}}"},
  {"role": "tool", "content": "{\"avg_pts\": 18.4, \"avg_reb\": 2.1, \"avg_ast\": 3.2, \"efg_pct\": 0.581}"},
  {"role": "assistant", "content": "Markus Howard está en gran forma: promedia 18.4 puntos con un 58.1% de eFG% en los últimos 5 partidos."}
]}
```

**Volumen objetivo:** miles de ejemplos (mínimo ~2.000-5.000 para un PoC con LoRA), divididos
train/validation (90/10), **balanceados ES/EN**.

## 10. Integración en la arquitectura destino

La feature se integra en la arquitectura migrada (pipeline + API + SPA). No toca Streamlit.

| Capa | Integración |
|---|---|
| `packages/baskonia_core/services/assistant.py` | Nuevo servicio de dominio, junto a `calendar.py`/`roster.py`/`matchup.py`/`boxscore.py`. Solo lectura, sin red propia |
| `apps/api` | Nuevo router `/assistant` + schema. Reutiliza `deps.get_session`, `errors.py` (problem+json), `settings.py` (config del cliente LLM) |
| `apps/web` | Componente `AssistantChat.tsx` (F5). Consume `POST /api/v1/assistant` por HTTP |
| `tools/` | `gen_scouting_dataset.py` (generador de dataset) |
| `tests/` | `test_assistant.py` (servicio, LLM mockeado) + `tests/api/test_assistant.py` (endpoint) |

**Configuración** (nuevas variables de entorno, vía `settings.py`):
- `LLM_PROVIDER` — `"none"` (default, asistente deshabilitado) | `"openai"` | `"vllm"` | ...
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` — endpoint, credencial y modelo.
- `ASSISTANT_ENABLED` — si `false` (default), el endpoint responde `503`/`501` sin exponer el LLM.

> **Seguridad:** la API key del LLM se lee de variables de entorno, nunca se hardcodea ni se
> expone en la respuesta. El asistente es solo lectura y no permite inyección de prompts que
> alteren datos (la BD nunca se escribe desde este flujo).

## 11. Criterios de aceptación (gate de salida)

1. `POST /api/v1/assistant` responde `200` sobre `data/baskonia.db` real con un cliente LLM
   configurado, y `503`/`501` cuando `ASSISTANT_ENABLED=false`.
2. Las 13 capacidades del catálogo responden con datos reales (verificables contra la función del
   dominio).
3. **Cero alucinación de números**: en tests, el `answer` solo contiene cifras presentes en el
   `source` (la salida real de la función). Verificable con un test que compara los números del
   `answer` contra los del `source`.
4. Bilingüe: una pregunta en ES responde en ES; una en EN responde en EN. Sin euskera.
5. `source` siempre presente cuando se resolvió función (trazabilidad).
6. `tests/test_assistant.py` y `tests/api/test_assistant.py` verdes; suite completa verde; sin red
   en tests (LLM mockeado).
7. `tools/gen_scouting_dataset.py` genera un dataset JSONL válido, balanceado ES/EN, con cada
   ejemplo reproducible contra la BD.
8. Regla de capas verificada: `packages/baskonia_core` no importa `apps/*`; `apps/api` no importa
   `apps/ingest`; el servicio no hace llamadas de red directas (LLM inyectado).

## 12. Paquetes de trabajo

| WP | Descripción | Ámbito | depende_de |
|---|---|---|---|
| WP-1 | `services/assistant_catalog.py` (catálogo de capacidades) + `services/llm.py` (interfaz LLM) | core | — |
| WP-2 | `services/assistant.py` (flujo RAG: idioma → intención → ejecución → redacción) | core | WP-1 |
| WP-3 | Endpoint `/assistant` + schema + `deps.get_llm` + settings (LLM_*) | api | WP-2 |
| WP-4 | `tests/test_assistant.py` + `tests/api/test_assistant.py` (LLM mockeado) | tests | WP-2, WP-3 |
| WP-5 | `tools/gen_scouting_dataset.py` (dataset bilingüe) | tools | WP-1 |
| WP-6 | `AssistantChat.tsx` + `lib/assistant.ts` (F5, cuando exista la SPA) | web | WP-3 |

WP-1 es la raíz. WP-2 depende de WP-1. WP-3 de WP-2. WP-4 depende de WP-2/WP-3. WP-5 depende de
WP-1 y puede ir en paralelo con WP-2/WP-3. WP-6 depende de WP-3 y de que exista la SPA (F5).

## 13. Fases posteriores (fuera de alcance, diseñadas como contrato)

**Nivel 2 — Agente RLVR (`lora_grpo`):** el asistente decide por sí mismo qué función llamar y en
qué orden, encadenando varias para preguntas compuestas. Requisitos ya diseñados aquí:
- El **catálogo de capacidades** (§4.1) es el contrato de herramientas del agente.
- El **dataset** (§9) ya está en formato `messages` con tool-calling, listo para `lora_grpo`.
- La **recompensa verificable** es que la llamada a la función correcta produzca el resultado
  esperado (paridad con la BD).

**Recomendación:** empezar por el Nivel 1 (RAG PoC) porque valida el valor con riesgo mínimo y sin
GPU. El Nivel 2 solo tiene sentido cuando el RAG se queda corto en estilo o en encadenamiento de
preguntas compuestas.

## 14. Rollback

La feature es **aditiva**: `services/assistant.py`, el router `/assistant`, el generador de dataset
y el componente de UI son nuevos. Revertir es borrar los ficheros nuevos y las variables `LLM_*`
de `settings.py`. No hay cambios de esquema ni de comportamiento de las funciones existentes.

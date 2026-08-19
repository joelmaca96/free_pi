# Caso de uso: Asistente conversacional para el cuerpo técnico (scouting conversacional)

Documento de ideas (no de diseño) que profundiza en el caso de uso **3.1** de
[01_training.md](01_training.md). El objetivo es definir con detalle cómo sería un asistente
conversacional que permita al cuerpo técnico del Baskonia consultar la base de datos en lenguaje
natural, sin terminal ni SQL.

> **Principio rector**: el asistente **no inventa datos**. Cada respuesta se apoya en una
> función del dominio ya existente (`insights.py` / `services/`) que lee `data/baskonia.db`.
> Esto elimina la alucinación de números, que es el riesgo nº1 de una LLM en un contexto
> deportivo donde los datos deben ser exactos.

---

## 1. Usuarios y contexto de uso

| Usuario | Cómo lo usaría | Frecuencia | Dispositivo |
|---|---|---|---|
| **Asistente de entrenador** | Preparar partidos: forma de jugadores, scouting del rival, proyección | Diaria (días de partido) | Portátil / tablet en el banquillo |
| **Entrenador jefe** | Consultas rápidas de resumen antes de una reunión | Varias veces por semana | Portátil |
| **Analista de datos** | Validar datos, detectar anomalías, preparar informes | Diaria | Portátil |
| **Departamento de comunicación** | Extraer datos para notas de prensa | Semanal | Portátil |

**Contexto clave**: el asistente se usa **antes y durante la preparación de partidos**, en
sesiones cortas (5-10 min). Las respuestas deben ser **concisas, orientadas a decisión y
verificables** (el usuario debe poder confiar en el número sin re-comprobarlo).

---

## 2. Catálogo de capacidades (mapeado a funciones reales)

El asistente cubre las siguientes capacidades. Cada una se apoya en una función del dominio
que ya existe y está testeada:

| # | Capacidad | Ejemplo de pregunta | Función del dominio |
|---|---|---|---|
| 1 | **Forma de un jugador** | "¿Cómo está la forma de Markus Howard en los últimos 5 partidos?" | `insights.player_recent_form` |
| 2 | **Rachas (hot/cold)** | "¿Quién está en racha y quién en bajón esta temporada?" | `insights.player_form_zscore` (umbrales `ZSCORE_HOT/COLD`) |
| 3 | **Carga de minutos** | "¿A quién estamos cargando demasiado minutos?" | `insights.player_load` |
| 4 | **Resumen avanzado del equipo** | "¿Cómo está nuestro ataque y defensa esta temporada?" | `insights.team_advanced_summary` |
| 5 | **Dificultad del calendario** | "¿Qué tramo del calendario es el más duro?" | `insights.schedule_difficulty` |
| 6 | **Proyección del próximo partido** | "¿Qué podemos esperar del partido contra Bilbao?" | `insights.project_next_matchup` |
| 7 | **Narrativa de scouting** | "Cuéntame el scouting del próximo rival" | `insights.scouting_narrative` |
| 8 | **Partidos pasados** | "¿Cómo nos fue contra Gran Canaria esta temporada?" | `services.calendar.past_games` |
| 9 | **Próximos partidos** | "¿Qué partidos nos quedan en Euroliga?" | `services.calendar.upcoming_games` |
| 10 | **Head-to-head** | "¿Cuál es nuestro historial contra el Real Madrid?" | `services.matchup.head_to_head_games` |
| 11 | **Box score de un partido** | "¿Qué hizo cada jugador en el último partido?" | `services.boxscore.boxscore_rows` |
| 12 | **Plantilla actual** | "¿Quién está en la plantilla y en qué posición?" | `services.roster.current_roster` |
| 13 | **Validación de datos** | "¿Hay datos incompletos o sospechosos en la BD?" | `insights.validate_data` |

### 2.1 Preguntas compuestas (encadenan varias funciones)

Algunas preguntas requieren **encadenar** varias funciones. Son el caso de uso natural del
Nivel 2 (agente RLVR):

| Pregunta compuesta | Cadena de funciones |
|---|---|
| "¿Qué rival nos conviene más en playoffs?" | `schedule_difficulty` → `head_to_head_games` → `project_next_matchup` |
| "¿Quién es nuestro mejor jugador en forma para el próximo partido?" | `player_recent_form` (todos) → `player_form_zscore` → `project_next_matchup` |
| "¿Cómo deberíamos plantear el partido contra Bilbao?" | `scouting_narrative` → `head_to_head_games` → `project_next_matchup` |
| "¿Hay algún jugador sobrecargado de cara al tramo duro del calendario?" | `player_load` → `schedule_difficulty` |

---

## 3. Arquitectura propuesta (dos niveles)

### Nivel 1 — RAG (PoC, sin entrenar)

El sistema resuelve qué función llamar (por reglas o por la propia LLM), la ejecuta sobre la BD
y **inyecta el resultado real en el prompt** para que la LLM lo redacte.

```
Usuario: "¿Cómo está la forma de Markus Howard?"
   │
   ▼
[Resolvedor]  →  identifica player_recent_form(team, player, n=5)
   │
   ▼
[insights.player_recent_form]  →  DataFrame real desde data/baskonia.db
   │
   ▼
[LLM]  →  redacta la respuesta en prosa con los datos reales inyectados
```

**Ventajas:**
- **Cero alucinación de números** (los datos vienen de la BD, no del modelo).
- Rápido y barato de construir (no requiere GPU de entrenamiento).
- Reutiliza el arnés de paridad: la función devuelve exactamente lo mismo que muestra la app.

**Limitaciones:**
- El "resolvedor" (qué función llamar) es frágil si se hace por reglas; si se hace con la LLM,
  puede equivocarse de función.
- No encadena bien preguntas compuestas de forma fiable.

### Nivel 2 — Agente con herramientas (RLVR, `lora_grpo`)

El asistente **decide por sí mismo** qué función llamar y en qué orden, encadenando varias para
preguntas compuestas. Es el caso de uso exacto de `lora_grpo` (tool-calling con recompensas
verificables: la recompensa es que la llamada a la función correcta produzca el resultado
esperado).

```
Usuario: "¿Qué rival nos conviene más en playoffs?"
   │
   ▼
[Agente LLM]  →  decide: schedule_difficulty() → head_to_head_games() → project_next_matchup()
   │
   ▼
[Funciones del dominio]  →  resultados reales
   │
   ▼
[Agente LLM]  →  sintetiza la respuesta final
```

**Ventajas:**
- Maneja preguntas compuestas y ambiguas.
- El fine-tuning con `lora_grpo` alinea al modelo con las **funciones reales** del dominio.

**Requisitos:**
- Dataset de entrenamiento con tool-calling (ver §4).
- GPU para entrenar el adaptador LoRA.

---

## 4. Dataset de entrenamiento (para el Nivel 2)

### 4.1 Formato

Formato `messages` con **tool-calling**: el asistente debe emitir la llamada a la función
correcta antes de responder.

```jsonl
{"messages": [
  {"role": "user", "content": "¿Cómo está la forma de Markus Howard en los últimos 5 partidos?"},
  {"role": "assistant", "content": "{\"tool\": \"player_recent_form\", \"args\": {\"player\": \"Markus Howard\", \"n\": 5}}"},
  {"role": "tool", "content": "{\"avg_pts\": 18.4, \"avg_reb\": 2.1, \"avg_ast\": 3.2, \"efg_pct\": 0.581}"},
  {"role": "assistant", "content": "Markus Howard está en gran forma: promedia 18.4 puntos con un 58.1% de eFG% en los últimos 5 partidos."}
]}
```

### 4.2 Generación sintética

Un script (`tools/gen_scouting_dataset.py`) lee `data/baskonia.db` y genera miles de pares
pregunta→respuesta:

1. **Recorre las funciones del dominio** con distintos parámetros (jugadores, rivales,
   temporadas, `last_n`).
2. **Ejecuta cada función** y obtiene el resultado real.
3. **Genera la pregunta** con plantillas de redacción variadas (sinónimos, orden de palabras,
   nivel de detalle).
4. **Genera la respuesta** a partir de los valores reales devueltos, garantizando fidelidad.

**Ejemplo de plantillas de redacción para la misma consulta:**
- "¿Cómo está la forma de {jugador} en los últimos {n} partidos?"
- "¿Qué tal viene {jugador} últimamente?"
- "Dame la forma reciente de {jugador}."
- "¿Cómo ha rendido {jugador} en las últimas {n} jornadas?"

### 4.3 Volumen y calidad

- **Volumen objetivo**: miles de ejemplos (mínimo ~2.000-5.000 para un PoC con LoRA).
- **División**: train / validation (p.ej. 90/10) para detectar overfitting.
- **Calidad**: cada ejemplo se valida contra la BD (la respuesta debe ser reproducible
  ejecutando la función). Un ejemplo cuya respuesta no coincide con la función se descarta.

---

## 5. Consideraciones específicas

### 5.1 Idioma
El cuerpo técnico trabaja en español (y parte en euskera). El dataset debe incluir variantes de
redacción en ambos idiomas para que el asistente responda en el idioma de la pregunta.

### 5.2 Tono
Respuestas **concisas y orientadas a decisión**. El asistente de entrenador no quiere párrafos
largos: quiere el dato y la implicación táctica. Ejemplo de estilo deseado:

> "Howard está en racha: 18.4 pts y 58.1% eFG% en los últimos 5. Es nuestra principal amenaza
> ofensiva; conviene diseñar acciones para él en el próximo partido."

### 5.3 Seguridad de datos
El asistente **solo lee** la BD (nunca escribe). Encaja con la frontera de solo-lectura del
arnés de paridad. No debe exponer credenciales ni permitir inyección de prompts que alteren
datos.

### 5.4 Verificabilidad
Cada respuesta debe poder **trazarse a la función que la generó**. Idealmente, el asistente
muestra la fuente (p.ej. "según `player_recent_form`, últimos 5 partidos") para que el usuario
pueda confiar sin re-comprobar.

### 5.5 Integración
- **Endpoint natural**: `/assistant` o `/narrative` en la API de F3, o un chat embebido en la
  SPA de F5.
- **Servicio**: el adaptador LoRA se carga como un servicio más en
  `packages/baskonia_core/services/` (p.ej. `services/assistant.py`).
- **Inferencia**: el modelo puede servirse con un servidor de inferencia aparte (vLLM) para no
  meter dependencias de GPU en la API.

---

## 6. Plan de implementación sugerido (incremental)

| Paso | Qué | Resultado | Esfuerzo |
|---|---|---|---|
| 1 | **RAG PoC** (Nivel 1) con resolvedor por reglas | Asistente funcional que responde las 13 capacidades con datos reales | Bajo |
| 2 | **Generador de dataset** (`tools/gen_scouting_dataset.py`) | Dataset JSONL fiel a la BD | Medio |
| 3 | **Fine-tuning LoRA + SFT** sobre el dataset | Adaptador que redacta en el estilo del cuerpo técnico | Medio (requiere GPU) |
| 4 | **Agente RLVR** (`lora_grpo`) para preguntas compuestas | Asistente que encadena funciones | Alto |
| 5 | **Integración en API/SPA** | Chat usable por el cuerpo técnico | Medio |

**Recomendación**: empezar por el paso 1 (RAG PoC) porque valida el valor con riesgo mínimo y
sin GPU. Los pasos 3-4 (fine-tuning) solo tienen sentido cuando el RAG se queda corto en estilo
o en encadenamiento de preguntas compuestas.

---

## 7. Métricas de éxito

| Métrica | Cómo se mide | Objetivo |
|---|---|---|
| **Precisión de datos** | % de respuestas cuyo número coincide con la función del dominio | 100% (no negociable) |
| **Acierto de función** | % de veces que el agente llama a la función correcta | >95% |
| **Utilidad percibida** | Encuesta al cuerpo técnico | >4/5 |
| **Tiempo de preparación de partido** | Tiempo medio de preparación antes/después | Reducción medible |
| **Tasa de abandono** | % de conversaciones sin respuesta útil | <10% |

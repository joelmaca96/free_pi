# Ideas: LLM entrenada para la aplicación del Baskonia

Documento de ideas (no de diseño) sobre cómo usar **Training Hub** (Red Hat AI Innovation Team)
para entrenar una LLM específica para esta aplicación y para el sector del baloncesto profesional.

> **Training Hub** — https://github.com/Red-Hat-AI-Innovation-Team/training_hub
> Librería Python (Apache-2.0) que abstrae algoritmos comunes de *post-training* de LLMs
> (SFT, OSFT, LoRA, GRPO) detrás de una interfaz unificada. No necesitas aprender la API de
> cada backend (InstructLab, Unsloth, verl, etc.): llamas a una función y ella se encarga del resto.

---

## 1. Qué es Training Hub

**Training Hub** es una capa de abstracción para entrenamiento de LLMs. Expone algoritmos
implementados y probados:

| Algoritmo | Para qué sirve | Backend | Estado |
|---|---|---|---|
| **SFT** (Supervised Fine-tuning) | Enseñar tareas concretas con datos supervisados | InstructLab | ✅ Implementado |
| **OSFT** (Orthogonal Subspace Fine-Tuning) | Continual learning sin olvidar lo aprendido | Mini-Trainer | ✅ Implementado |
| **LoRA + SFT** | Fine-tuning eficiente en memoria (poca VRAM) | Unsloth | ✅ Implementado |
| **LoRA + GRPO** | RL para agentes que llaman herramientas (RLVR) | ART / verl | ✅ Implementado |
| **GRPO** (full fine-tuning RLVR) | RL multi-GPU sobre todo el modelo | verl | ✅ Implementado |
| **DPO** (Direct Preference Optimization) | Alinear con preferencias humanas | — | 🔄 Planificado |

### Instalación

```bash
pip install training-hub[lora]        # LoRA (recomendado para empezar)
pip install training-hub[grpo,lora]   # LoRA + GRPO
pip install training-hub[cuda] --no-build-isolation   # GPU
```

### Ejemplo mínimo (LoRA + SFT)

```python
from training_hub import lora_sft

result = lora_sft(
    model_path="Qwen/Qwen2.5-1.5B-Instruct",  # modelo base pequeño
    data_path="./data/scouting_train.jsonl",  # dataset (formato messages o Alpaca)
    ckpt_output_dir="./models/baskonia-lora",
    lora_r=16,
    lora_alpha=32,
    num_epochs=3,
    learning_rate=2e-4,
)
```

### Cómo elegir algoritmo según el caso

- **GPU pequeña / probar rápido** → `lora_sft` (Unsloth: 2x más rápido, 70% menos VRAM, QLoRA 4-bit).
- **Aprender sin olvidar el comportamiento general** → `osft` (continual learning).
- **Agente que llame a funciones de `insights.py` como herramientas** → `lora_grpo` (RL sobre tool-calling).
- **Infra multi-GPU, entrenar todo el modelo** → `grpo` o `sft`.

### Consideraciones prácticas

1. **Hardware**: requiere GPU con CUDA. Estimar VRAM con `from training_hub import estimate`.
   Para un PoC, un modelo de 1.5B–3B con LoRA es suficiente.
2. **Datos**: la BD actual es pequeña (5 equipos, 8 partidos). Para fine-tuning hacen falta
   **miles de ejemplos** → generar datos sintéticos variando inputs (rivales, métricas, estilos).
3. **Alternativa más simple**: si solo se quiere que la LLM "hable de baloncesto del Baskonia",
   empezar con **RAG** (inyectar datos de la BD en el prompt) antes de invertir en fine-tuning.
   Training Hub es para cuando RAG se queda corto.

---

## 2. Caso de uso principal: informe de scouting en lenguaje natural

Hoy `insights.py` genera narrativas con plantillas/reglas (`scouting_narrative`,
`project_next_matchup`, `schedule_difficulty`). Una LLM fine-tuneada las generaría de forma
más natural y flexible.

**Flujo recomendado:**

1. **Preparar dataset JSONL** (formato `messages`):

   ```jsonl
   {"messages": [{"role": "user", "content": "Genera un scouting del próximo rival: Surne Bilbao Basket. Datos: ORtg 112.3, DRtg 108.1, pace 74.2, eFG% 54.1%."}, {"role": "assistant", "content": "Bilbao llega con un ataque eficiente (ORtg 112.3) pero defensa vulnerable (DRtg 108.1). Su ritmo es alto (pace 74.2), por lo que conviene controlar las posesiones..."}]}
   ```

2. **Generar los datos desde la propia BD** — script que lea `data/baskonia.db` y genere pares
   pregunta/respuesta usando las funciones de `insights.py` como "profesor" (*synthetic data*).

3. **Entrenar con LoRA + SFT** (ver ejemplo arriba).

4. **Usar el adaptador en la app** — cargar el adaptador LoRA sobre el modelo base y usarlo en
   `app.py` (o en la futura API de F3) para generar narrativas.

---

## 3. Más casos de uso para esta aplicación

### 3.1 Asistente conversacional para el cuerpo técnico

Un chat donde el asistente responde preguntas en lenguaje natural sobre la BD, pensado para
asistentes de entrenador que **no usan la terminal ni SQL**. El objetivo es que cualquier
consulta que hoy exige navegar por la app o escribir una query se resuelva conversando.

> **📄 Documento detallado**: [02_scouting_conversacional.md](02_scouting_conversacional.md)
> profundiza en este caso de uso: usuarios, catálogo de 13 capacidades mapeadas a funciones
> reales, arquitectura en dos niveles (RAG PoC → agente RLVR), dataset de entrenamiento,
> plan de implementación incremental y métricas de éxito.

**Resumen:**

- **Principio rector**: el asistente no inventa datos; cada respuesta se apoya en una función
  del dominio (`insights.py` / `services/`) que lee `data/baskonia.db`.
- **13 capacidades** mapeadas a funciones reales: forma de jugador (`player_recent_form`),
  rachas (`player_form_zscore`), carga (`player_load`), resumen del equipo
  (`team_advanced_summary`), dificultad de calendario (`schedule_difficulty`), proyección
  (`project_next_matchup`), narrativa (`scouting_narrative`), partidos pasados/próximos
  (`past_games`/`upcoming_games`), head-to-head (`head_to_head_games`), box score
  (`boxscore_rows`), plantilla (`current_roster`) y validación (`validate_data`).
- **Arquitectura en dos niveles**:
  - **Nivel 1 — RAG (PoC)**: un resolvedor identifica la función, la ejecuta sobre la BD y la
    LLM redacta el resultado real inyectado. Cero alucinación de números.
  - **Nivel 2 — Agente RLVR (`lora_grpo`)**: el asistente decide qué función llamar y encadena
    varias para preguntas compuestas (ej. "¿qué rival nos conviene más en playoffs?").
- **Dataset**: formato `messages` con tool-calling, generado sintéticamente desde la BD.
- **Recomendación**: empezar por el Nivel 1 (RAG PoC) por su bajo riesgo y coste; el
  fine-tuning solo tiene sentido cuando el RAG se queda corto en estilo o encadenamiento.

### 3.2 Resumen automático post-partido
Generar un resumen en prosa del último partido (quién destacó, rachas, claves del resultado)
a partir del box score. Útil para informes internos o para el departamento de comunicación.

### 3.3 Preparación de informes PDF/PPTX con narrativa
Los informes exportables (F6) hoy son tablas. Una LLM podría añadir un **bloque de análisis
narrativo** por sección (scouting, forma del rival, proyección del próximo partido).

### 3.4 Agente que consulta la BD como herramienta (RLVR)
Entrenar con `lora_grpo` un agente que **decida qué función de `insights.py`/`services/` llamar**
según la pregunta del usuario, en vez de hardcodear el flujo. Ejemplo: el usuario pregunta
"¿qué rival nos conviene más en playoffs?" y el agente encadena `schedule_difficulty` +
`head_to_head` + `project_next_matchup`.

### 3.5 Detección de anomalías / alertas
LLM que lea las estadísticas y **señale anomalías** (un jugador con caída brusca de eFG%,
un rival que cambia su ritmo en casa) y las explique en lenguaje natural para el scouting.

### 3.6 Traducción y adaptación de informes
Generar el mismo informe en varios idiomas (español, euskera, inglés) o en distintos niveles
de detalle (resumen ejecutivo para el entrenador jefe vs. detalle para el asistente).

---

## 4. Casos de uso para el sector (baloncesto profesional)

### 4.1 Scouting de rivales a escala
Un modelo entrenado con datos de múltiples equipos/ligas (ACB, Euroleague) que genere informes
de scouting automáticos para **cualquier rival**, no solo los de la BD actual. El pipeline de
scraping (BBR + baskonia.com) alimenta el dataset.

### 4.2 Análisis de mercado / fichajes
LLM que cruce estadísticas avanzadas de jugadores con contexto (edad, contrato, posición,
encaje en el sistema) para **apoyar decisiones de fichajes** y generar informes de valoración.

### 4.3 Preparación de ruedas de prensa / comunicación
Generar respuestas sugeridas, notas de prensa o resúmenes para el departamento de comunicación
a partir de los datos del partido, con el tono de la entidad.

### 4.4 Formación de cantera / academia
Adaptar los informes a un nivel formativo: explicar a jugadores jóvenes **por qué** una métrica
importa y qué mejorar, en lenguaje pedagógico.

### 4.5 Análisis de afición / ticketing (dato de negocio)
Si se cruzan datos de asistencia, venta de entradas o engagement con el calendario, una LLM
podría generar informes de **previsión de demanda** y campañas de comunicación por partido.

### 4.6 Benchmarking entre equipos de la liga
Generar comparativas automáticas "Baskonia vs. resto de la ACB" en métricas clave, con narrativa
que explique dónde está la ventaja/desventaja competitiva.

---

## 5. Notas de integración con la arquitectura actual

- El **dataset de entrenamiento** se puede generar desde `packages/baskonia_core` (dominio
  compartido) sin tocar la UI, y versionarse en `data/` o `tests/parity/`.
- El **adaptador entrenado** (LoRA) es un artefacto que se carga en la app; encaja como un
  servicio más en `packages/baskonia_core/services/` (p.ej. `services/narrative.py`).
- La **API de F3** es el punto natural para exponer un endpoint `/narrative` que use la LLM,
  manteniendo la frontera de `apps/api` sin red saliente salvo la del propio modelo (o servirlo
  con un servidor de inferencia aparte).
- **RAG primero, fine-tuning después**: para un PoC, inyectar los datos de la BD en el prompt
  suele bastar. Training Hub entra cuando se necesita consistencia de estilo, dominio profundo
  o llamadas a herramientas.

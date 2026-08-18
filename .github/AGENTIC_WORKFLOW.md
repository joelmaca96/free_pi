# Agentic Development Workflows

Pipelines de desarrollo dirigidos por agentes (GitHub Copilot custom agents). Los agentes del
pipeline son **globales** (perfil de usuario, `prompts/agents/*.agent.md`); toda la información
específica de cada proyecto vive en su `.github/workflow.config.md`. Para usar el pipeline en un
proyecto nuevo solo hace falta ese config — los agentes lo crean automáticamente la primera vez
que se invocan (ver "Portar a otro proyecto").

Dos pipelines que comparten agentes y convenciones:

| Pipeline | Para qué | Orquestador | Arranque |
|---|---|---|---|
| **Feature** | Desarrollar funcionalidad nueva | Feature Lead | `/new-feature` |
| **Audit** | Revisar código existente: bugs, mejoras, simplificación, documentación | Audit Lead | `/code-audit` |

## Flujo 1 — Feature

```
Usuario: solicitud de feature
        │
        ▼
┌─ Feature Lead (orquestador) ───────────────────────────────────────┐
│                                                                    │
│  0. ARRANQUE: pregunta al usuario si quiere docs (etapa 4) y       │
│     tests (etapa 5) → docs:/tests: en STATUS.md                    │
│                                                                    │
│  1. ARQUITECTURA   Feature Architect ──► 01_design.md              │
│        │            (incluye clase de complejidad + depende_de/WP)  │
│        ▼  ⛔ GATE: aprobación del usuario sobre el diseño          │
│           (fast-path: si complejidad=trivial el gate es informativo)│
│                                                                    │
│  2. DESARROLLO     Feature Developer ──► código + 02_implementation.md
│        │             └─ delega WPs independientes EN PARALELO       │
│        │                (según depende_de); build por oleada        │
│        ▼                                                           │
│  3. REVIEW         Feature Reviewer ──► 03_review.md               │
│        │   CHANGES_REQUESTED ──► vuelve a 2 (máx. 3 ciclos; 1 si    │
│        │   trivial). Ciclos 2+: solo delta (hallazgos + cambios)    │
│        ▼   APPROVED                                                │
│  4/5. DOC + TEST (opcionales, EN PARALELO entre si)                │
│        Feature Documenter ──► doc/ + 04_docs.md  (si docs: yes)    │
│        Feature Tester     ──► tests + 05_tests.md (si tests: yes)  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Cada etapa también es invocable de forma independiente (todos los agentes son `user-invocable`); el contrato entre etapas es el directorio de feature, no el orquestador.

## Directorio de feature (handoff)

Cada feature vive en `local/features/<NNN>-<slug>/` (configurable en `workflow.config.md`):

| Fichero | Lo escribe | Contenido |
|---|---|---|
| `00_request.md` | Feature Lead | Solicitud original + aclaraciones del usuario |
| `01_design.md` | Architect | Diseño: alcance, módulos afectados, plan por ficheros, contratos API, criterios de aceptación, clase de complejidad, paquetes de trabajo con especialista asignado y `depende_de` (para paralelizar) |
| `02_implementation.md` | Developer | Qué se implementó, desviaciones del diseño, resultado de build, delegaciones realizadas |
| `03_review.md` | Reviewer | Veredicto (`APPROVED` / `CHANGES_REQUESTED`) + hallazgos con severidad |
| `04_docs.md` | Documenter | Documentos creados/actualizados |
| `05_tests.md` | Tester | Tests añadidos, cómo ejecutarlos, resultado |
| `STATUS.md` | Todos | Estado actual del pipeline (ver abajo) |

### STATUS.md

```markdown
# Status: <feature-slug>
stage: design | development | review | documentation | testing | done
docs: yes | no        ↝ respuesta del usuario al arranque (¿generar documentación?)
tests: yes | no       ↝ respuesta del usuario al arranque (¿generar tests?)
review_cycles: 0
blocked_on: <vacío o motivo>
updated: <fecha> por <agente>
```

Cada agente actualiza `STATUS.md` al terminar su etapa. Es la fuente de verdad para retomar un pipeline interrumpido: cualquier agente lee primero `STATUS.md` y los artefactos previos.

## Flujo 2 — Audit (código existente)

```
Usuario: objetivo de auditoría (módulo / fichero / área)
        │
        ▼
┌─ Audit Lead (orquestador) ─────────────────────────────────────────┐
│                                                                    │
│  1. AUDITORÝA      Code Auditor ──► 01_audit.md                    │
│        │             (bugs, contrato, mejoras, simplificación, doc;│
│        │              hallazgos con ID, severidad y esfuerzo)      │
│        ▼  ⛔ GATE: usuario selecciona qué IDs corregir             │
│                                                                    │
│  2. SELECCIÓN      Audit Lead ──► 02_fixplan.md (WPs por ID)       │
│        │             ("report-only" es salida válida: fin aquí)    │
│        ▼                                                           │
│  3. FIXES          Feature Developer ──► código + 03_fixes.md      │
│        ▼                                                           │
│  4. REVIEW         Feature Reviewer ──► 04_review.md               │
│        │   CHANGES_REQUESTED ──► vuelve a 3 (máx. 3 ciclos)        │
│        ▼   APPROVED                                                │
│  5. DOCUMENTACIÓN  Feature Documenter ──► doc/ + 05_docs.md        │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Las etapas 3-5 **reutilizan los agentes del pipeline de features** con mapeo de artefactos indicado en el prompt (los agentes aceptan override de sus nombres de artefacto por defecto).

### Directorio de auditoría

`local/audits/<NNN>-<slug>/`:

| Fichero | Lo escribe | Contenido |
|---|---|---|
| `00_scope.md` | Audit Lead | Objetivo, ficheros en alcance, exclusiones |
| `01_audit.md` | Code Auditor | Hallazgos con ID estable (A-001…), categoría, severidad, esfuerzo, evidencia fichero:línea |
| `02_fixplan.md` | Audit Lead | IDs seleccionados por el usuario → paquetes de trabajo con especialista |
| `03_fixes.md` | Feature Developer | Fixes aplicados |
| `04_review.md` | Feature Reviewer | Veredicto |
| `05_docs.md` | Feature Documenter | Docs actualizadas |
| `STATUS.md` | Todos | `stage: scoping \| audit \| selection \| fixing \| review \| documentation \| done` |

Reglas propias del audit: el auditor es read-only sobre el código; los fixes nunca exceden los IDs seleccionados (hallazgos nuevos durante el fix se registran en `01_audit.md` como no-seleccionados); "report-only" cierra el pipeline en la etapa 2.

## Reglas transversales

1. **Config primero**: todo agente del pipeline lee `.github/workflow.config.md` antes de actuar. El **bootstrap (crearlo si no existe) es responsabilidad exclusiva del Feature/Audit Lead**; los subagentes asumen que ya existe y, si falta, abortan devolviendo el control al Lead. Prohibido hardcodear rutas/comandos del proyecto en los agentes.
2. **Artefactos = contrato**: un agente solo consume los artefactos de etapas anteriores y produce el suyo. No se comunica con otros agentes fuera de esos ficheros. Los Leads pasan solo rutas/punteros, nunca el contenido completo de los artefactos.
3. **Gate humano tras el diseño**: el Lead nunca pasa de la etapa 1 a la 2 sin aprobación explícita del usuario, salvo fast-path cuando la clase de complejidad es `trivial` (gate informativo).
4. **Bucle de review acotado**: máximo 3 ciclos developer↔reviewer (1 si `trivial`); al agotarlos sin `APPROVED`, el Lead escala al usuario. En ciclos 2+ solo se pasa el delta (hallazgos + ficheros cambiados), no el diseño completo.
5. **Los especialistas no escriben artefactos**: son subagentes del Developer (o del Architect para consultas); su salida la integra quien los invoca.
6. **Idioma**: artefactos y respuestas en español técnico conciso; código y commits según las normas del proyecto.
7. **Ante dudas, preguntar**: ningún agente asume ante ambigüedad; pregunta al usuario vía `vscode/askQuestions` (agrupando preguntas) o, si corre como subagente sin acceso al usuario, devuelve la pregunta a su invocador.
8. **Etapas 4 y 5 opcionales (pipeline feature)**: el Feature Lead pregunta al arranque si se quiere documentación y tests; las respuestas quedan en `STATUS.md` (`docs:`/`tests:`) y las etapas marcadas `no` se saltan. Si ambas aplican, se ejecutan en paralelo (son independientes entre sí).
9. **Coste y paralelización**: el Architect clasifica la complejidad (`trivial`/`normal`/`complejo`) para el fast-path, y marca `depende_de` por WP; el Developer delega los WPs independientes en paralelo y compila por oleada + integración final en lugar de por cada WP.

## Portar a otro proyecto

Los agentes del pipeline ya son **globales** (perfil de usuario), así que no hay que copiarlos.
Para habilitar el pipeline en un proyecto nuevo:

1. Invoca cualquier agente del pipeline (o `/new-feature` / `/code-audit`). Si `.github/workflow.config.md`
   no existe, el agente hace **bootstrap**: lee la plantilla global
   `prompts/agents/templates/workflow.config.template.md`, inspecciona el workspace, entrevista al
   usuario para lo que no pueda inferir, y escribe `.github/workflow.config.md` +
   `.github/AGENTIC_WORKFLOW.md` (copia de la plantilla global).
2. Revisa el `workflow.config.md` generado: repos/layout, comandos de build y test, normas de código,
   mapa de documentación y roster de especialistas (vacío si no hay agentes de dominio locales).
3. Los agentes de dominio específicos del proyecto (si los hay) viven en el `.github/agents/` del
   propio repo y se referencian por nombre desde el roster del config.

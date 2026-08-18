# Status: 002-competicion-real-por-partido
stage: done
docs: yes
tests: no
review_cycles: 1
design_cycles: 1
blocked_on: (ninguno) — 03_review.md: veredicto APPROVED (ciclo 1, 0 BLOCKER, 0 MAJOR, 2 MINOR
  informativos sin acción bloqueante). El reviewer verificó de forma independiente contra
  `data/baskonia.db` real (sha256 de los 4 `.bak-*`, consultas SQL de conteos/reparto de liga) y
  ejecutando el build él mismo (py_compile + smoke import); toda la evidencia de
  `02_implementation.md` se reprodujo cifra por cifra. Etapa 4 (Documenter) completada:
  `README.md` actualizado (04_docs.md); `tests: no` → etapa 5 saltada. Pipeline de la feature
  cerrado.
updated: 2026-08-18 por feature-documenter

## Nota operativa de sesión
Esta sesión no puede lanzar diálogos interactivos desde subagentes (sin AskUserQuestion). Ningún
agente del pipeline debe bloquear esperando input humano: ante una decisión que requiera al
usuario, el agente devuelve el control a quien lo invocó con un resumen y la pregunta concreta.

## Nota para el reviewer / usuario
En `data/` quedan 4 copias de seguridad (`baskonia.db.bak-20260818115031`, `...115112`,
`...115421`, `...115653`, 143360 bytes cada una) generadas como evidencia de seguridad de datos.
Se pueden borrar cuando el resultado se dé por bueno; no hay lógica de rotación (fuera de diseño).

## Feature relacionada
Esta feature bloquea la reanudación de `local/features/001-analisis-diferencial-7-3/` (en espera,
ver su STATUS.md). Al cerrar esta feature (`stage: done`, veredicto APPROVED), avisar y retomar la
001 con un delta corto de diseño para la Ampliación B (selector de competición).

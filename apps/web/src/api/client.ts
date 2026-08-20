import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Los paths generados en schema.d.ts ya incluyen el prefijo "/api/v1"
// (apps/api/main.py monta los routers con `prefix=API_PREFIX`), así que el
// cliente no añade uno propio — evita duplicarlo a "/api/v1/api/v1/...".
// `window.location.origin` (en vez de "") porque el `fetch` global de Node
// (usado también en jsdom/Vitest) no resuelve rutas relativas sin base.
export const apiClient = createClient<paths>({
  baseUrl: typeof window !== "undefined" ? window.location.origin : "",
});

/** Forma de un error `application/problem+json` (RFC 9457), ver apps/api/errors.py. */
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  request_id: string;
}

export class ApiError extends Error {
  problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(problem.detail);
    this.problem = problem;
  }
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.problem.status === 404;
}

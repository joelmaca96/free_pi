import type { ReactNode } from "react";
import { ApiError } from "@/api/client";

/**
 * Los 3 estados obligatorios por panel (diseño §F5, punto 5 de la SPA):
 * cargando, error (con request_id) y "sin datos suficientes" (no es un error).
 */

export function LoadingState({ label = "Cargando…" }: { label?: string }) {
  return <p className="text-sm text-slate-400 py-4">{label}</p>;
}

export function ErrorState({ error }: { error: unknown }) {
  const problem = error instanceof ApiError ? error.problem : null;
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
      <p className="font-medium">{problem?.title ?? "Error al cargar los datos"}</p>
      <p>{problem?.detail ?? String(error)}</p>
      {problem?.request_id && (
        <p className="mt-1 text-xs text-rose-500">request_id: {problem.request_id}</p>
      )}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
      {message}
    </div>
  );
}

interface QueryResultLike<T> {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
}

/**
 * Envuelve un panel con los 3 estados: delega en `children(data)` solo cuando
 * hay datos; usa `isEmpty` para decidir si mostrar el estado "sin datos".
 */
export function QueryPanel<T>({
  query,
  isEmpty,
  emptyMessage,
  loadingLabel,
  children,
}: {
  query: QueryResultLike<T>;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: string;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
}) {
  if (query.isLoading) return <LoadingState label={loadingLabel} />;
  if (query.isError) return <ErrorState error={query.error} />;
  const data = query.data as T;
  if (isEmpty?.(data)) return <EmptyState message={emptyMessage ?? "Sin datos."} />;
  return <>{children(data)}</>;
}

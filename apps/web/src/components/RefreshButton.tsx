import { useRefreshGame } from "@/api/hooks";

/**
 * Botón "Refrescar" de un partido concreto: pide a la API que vuelva a
 * descargarlo de su fuente y reconsulta la lectura de forma acotada.
 *
 * Vive en un componente propio (y no repetido en cada panel) porque cada
 * instancia necesita su propia mutación: así la lista de partidos ausentes de
 * `TeamOverviewPanel` puede ofrecer un botón por fila reusando el mismo flujo.
 *
 * El resultado no es inmediato: la API responde 202 y el trabajo real corre en
 * background. Si el fetch a la fuente falla, el panel sigue vacío — no se
 * fabrica ningún dato.
 */
export function RefreshButton({
  gameId,
  seasonLabel,
  label = "Refrescar",
}: {
  gameId: string;
  /** Solo necesario para cargar un partido que aún no existe en `games`. */
  seasonLabel?: string | null;
  label?: string;
}) {
  const refresh = useRefreshGame();

  const status = refresh.data?.status;
  const message = refresh.isError
    ? "No se pudo pedir el refresco."
    : status === "triggered" || status === "already_in_progress"
      ? "Descargando de la fuente… vuelve a mirar en unos segundos."
      : status === "rejected_busy"
        ? "Hay demasiados refrescos en curso; inténtalo en un momento."
        : null;

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => refresh.mutate({ gameId, seasonLabel })}
        disabled={refresh.isPending || status === "triggered"}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {refresh.isPending ? "Pidiendo…" : label}
      </button>
      {message && <span className="text-xs text-slate-500">{message}</span>}
    </div>
  );
}

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useEnqueueScout, useScoutStatus, invalidateTeamData } from "@/api/hooks";

/**
 * Sustituye al botón "Descargar datos de {rival}" de Streamlit
 * (`render_upcoming_tab`, app.py:660-683) por la versión asíncrona vía la
 * cola de trabajos (`ingest_jobs` + `apps/ingest/worker.py`). El scraping en
 * sí lo hace el worker, nunca la API — ver doc/arquitectura/01_design.md §2.
 */
export function ScoutRivalPanel({
  teamSlug,
  teamName,
  lastN,
}: {
  teamSlug: string;
  teamName: string;
  lastN: number;
}) {
  const statusQuery = useScoutStatus(teamSlug);
  const enqueue = useEnqueueScout(teamSlug, lastN);
  const queryClient = useQueryClient();
  const job = statusQuery.data;
  const previousStatus = useRef<string | null>(null);

  useEffect(() => {
    if (job?.status === "done" && previousStatus.current !== "done") {
      invalidateTeamData(queryClient, teamSlug);
    }
    previousStatus.current = job?.status ?? null;
  }, [job?.status, queryClient, teamSlug]);

  if (job?.status === "queued" || job?.status === "running") {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        <p>
          {job.status === "queued" ? "En cola: " : "Descargando: "}
          datos de {teamName}…
        </p>
        <p className="mt-1 text-xs text-slate-400">
          Respeta el rate-limit de Basketball-Reference: puede tardar varios minutos.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500">
      <p>Todavía no hay datos de {teamName} en la base de datos.</p>
      {job?.status === "failed" && (
        <p className="mt-1 text-xs text-rose-600">No se pudo descargar: {job.error}</p>
      )}
      <button
        type="button"
        onClick={() => enqueue.mutate()}
        disabled={enqueue.isPending}
        className="mt-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {job?.status === "failed" ? "🔄 Reintentar descarga" : `📥 Descargar datos de ${teamName}`}
      </button>
    </div>
  );
}

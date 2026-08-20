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
      <div className="panel-empty">
        <p>
          {job.status === "queued" ? "En cola: " : "Descargando: "}
          datos de {teamName}…
        </p>
        <p className="text-muted mt-1 text-xs">
          Respeta el rate-limit de Basketball-Reference: puede tardar varios minutos.
        </p>
      </div>
    );
  }

  return (
    <div className="card elev-sm flex flex-col items-start gap-3 py-6">
      <svg width="34" height="34" viewBox="0 0 256 256" fill="var(--color-neutral-500)">
        <path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm-8-80V80a8,8,0,0,1,16,0v56a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,172Z" />
      </svg>
      <p className="text-muted m-0 max-w-[44ch]">
        Todavía no hay datos de {teamName} en la base de datos.
      </p>
      {job?.status === "failed" && (
        <p className="m-0 text-xs text-brand">No se pudo descargar: {job.error}</p>
      )}
      <button
        type="button"
        onClick={() => enqueue.mutate()}
        disabled={enqueue.isPending}
        className="btn btn-primary"
      >
        {job?.status === "failed" ? "🔄 Reintentar descarga" : `📥 Descargar datos de ${teamName}`}
      </button>
    </div>
  );
}

/**
 * Botón de exportación (PDF/PPTX, endpoints 17/18). Ambos devuelven 501 hasta
 * F6 (apps/api/routers/reports.py) — se muestra deshabilitado con tooltip en
 * vez de intentar la descarga, listo para activarse sin rehacer la UI.
 */
export function ExportButton({ label }: { label: string }) {
  return (
    <button
      type="button"
      disabled
      title="Disponible próximamente (fase F6 de la migración)"
      className="cursor-not-allowed rounded-md border border-slate-200 bg-slate-100 px-3 py-1.5 text-sm text-slate-400"
    >
      {label}
    </button>
  );
}

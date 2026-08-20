import type { ReactNode } from "react";

/** Equivalente de `st.metric`: label + valor, tarjeta compacta. */
export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="card elev-sm px-4 py-3 text-center">
      <p className="text-muted m-0 text-[11px] font-medium uppercase tracking-wide">{label}</p>
      <p className="m-0 mt-1.5 text-2xl">{value}</p>
      {hint && <p className="text-muted m-0 mt-1 text-xs">{hint}</p>}
    </div>
  );
}

export function StatCardRow({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">{children}</div>;
}

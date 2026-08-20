import type { StreakKind } from "@/lib/format";

/**
 * Hot/Cold/Normal con emoji + color + fondo tintado — triple codificación
 * para accesibilidad (ver readme del sistema Nocturne, doc/diseno/gaps.md).
 */
export function StreakBadge({ kind }: { kind: StreakKind }) {
  if (kind === "hot") return <span className="badge badge-hot">🔥 Hot</span>;
  if (kind === "cold") return <span className="badge badge-cold">❄️ Cold</span>;
  return <span className="badge badge-neutral">➖ Normal</span>;
}

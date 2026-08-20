/**
 * Píldora V/D del sistema de diseño Nocturne + Baskonia: victoria en rojo
 * sólido, derrota en contorno neutro — la distinción de forma comunica el
 * resultado sin depender solo del color (ver doc/diseno/gaps.md).
 */
export function ResultBadge({ result }: { result: "W" | "L" | string | null | undefined }) {
  if (result === "W") return <span className="badge badge-win">V</span>;
  if (result === "L") return <span className="badge badge-loss">D</span>;
  return <span className="text-muted text-xs">-</span>;
}

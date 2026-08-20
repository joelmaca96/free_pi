/**
 * Helpers de formato, traducción directa de los equivalentes en `app.py`
 * (paridad F5, ver doc/arquitectura/02_migration.md §F5, punto "lib/format.ts").
 */

const WEEKDAYS_ES = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MONTHS_ES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

/** "2026-05-18" -> "lunes, 18 de mayo de 2026". Equivalente de `format_date_es`, app.py:134-139. */
export function formatDateEs(isoDate: string | null | undefined): string {
  if (!isoDate) return "-";
  const dt = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(dt.getTime())) return isoDate;
  return `${WEEKDAYS_ES[dt.getDay()]}, ${dt.getDate()} de ${MONTHS_ES[dt.getMonth()]} de ${dt.getFullYear()}`;
}

/** Equivalente de `_fmt`, app.py:142-143. */
export function fmt(value: number | null | undefined): string {
  return value != null ? value.toFixed(1) : "-";
}

/** Equivalente de `_fmt_pct`, app.py:146-147 (sin espacio antes de "%"). */
export function fmtPct(value: number | null | undefined): string {
  return value != null ? `${(value * 100).toFixed(1)}%` : "-";
}

/** Equivalente de `season_label`, packages/baskonia_core/insights.py:65-69. */
export function seasonLabel(season: number | null | undefined): string {
  if (season == null) return "-";
  return `${season}-${String(season + 1).slice(-2)}`;
}

export type StreakKind = "hot" | "cold" | "neutral";

/** Icono/etiqueta de racha; el umbral ya lo resuelve el backend (StreakItem.label). */
export function streakBadge(label: string | null | undefined): string {
  if (label === "hot") return "🔥 en racha";
  if (label === "cold") return "❄️ bajo forma";
  return "➖";
}

/** Normaliza `StreakItem.label` al tipo que consume `<StreakBadge>`. */
export function streakKind(label: string | null | undefined): StreakKind {
  if (label === "hot") return "hot";
  if (label === "cold") return "cold";
  return "neutral";
}

/** "W" -> verde, "L" -> rojo, null -> "-". */
export function resultLabel(result: string | null | undefined): string {
  return result ?? "-";
}

export function resultColorClass(result: string | null | undefined): string {
  if (result === "W") return "text-brand";
  if (result === "L") return "text-muted";
  return "text-muted";
}

export function scoreLabel(
  teamScore: number | null | undefined,
  opponentScore: number | null | undefined
): string {
  if (teamScore == null || opponentScore == null) return "-";
  return `${teamScore}-${opponentScore}`;
}

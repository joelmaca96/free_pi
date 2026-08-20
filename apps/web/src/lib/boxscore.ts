/** Traducción de `parse_minutes`/`per_36`, packages/baskonia_core/insights.py:173-190. */

export function parseMinutes(value: string | null | undefined): number | null {
  if (!value) return null;
  const parts = value.split(":");
  if (parts.length === 2) {
    const mins = Number(parts[0]);
    const secs = Number(parts[1]);
    if (Number.isNaN(mins) || Number.isNaN(secs)) return null;
    return mins + secs / 60;
  }
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
}

export function per36(value: number | null | undefined, minutes: number | null): number | null {
  if (value == null || !minutes) return null;
  return (value * 36) / minutes;
}

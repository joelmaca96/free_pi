import { seasonLabel } from "@/lib/format";

/** Los 3 controles del header global (app.py:1049-1069), controlados por query string. */

export function SeasonPicker({
  seasons,
  value,
  onChange,
}: {
  seasons: number[];
  value: number | null;
  onChange: (season: number) => void;
}) {
  return (
    <label className="flex flex-col text-xs font-medium text-slate-500">
      Temporada
      <select
        className="mt-0.5 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {seasons.map((s) => (
          <option key={s} value={s}>
            {seasonLabel(s)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function LeagueSelect({
  leagues,
  value,
  onChange,
}: {
  leagues: { code: string; label: string }[];
  value: string | null;
  onChange: (league: string | null) => void;
}) {
  return (
    <label className="flex flex-col text-xs font-medium text-slate-500">
      Competición
      <select
        className="mt-0.5 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      >
        <option value="">Todas</option>
        {leagues.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function LastNInput({
  value,
  onChange,
  label = "Últimos N partidos (forma)",
  min = 1,
  max = 20,
}: {
  value: number;
  onChange: (n: number) => void;
  label?: string;
  min?: number;
  max?: number;
}) {
  return (
    <label className="flex flex-col text-xs font-medium text-slate-500">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (n >= min && n <= max) onChange(n);
        }}
        className="mt-0.5 w-20 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
      />
    </label>
  );
}

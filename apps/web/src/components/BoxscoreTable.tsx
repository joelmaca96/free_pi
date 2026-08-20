import type { ColumnDef } from "@tanstack/react-table";
import { StatTable } from "./StatTable";
import { QueryPanel } from "./PanelState";
import { TeamLogo } from "./TeamLogo";
import { fmt, fmtPct } from "@/lib/format";
import { parseMinutes, per36 } from "@/lib/boxscore";
import { useBoxscore } from "@/api/hooks";

interface BoxscoreRow {
  player: string;
  minutes: string;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  ptsPer36: number | null;
  efg: number | null;
  ts: number | null;
}

const columns: ColumnDef<BoxscoreRow, any>[] = [
  { accessorKey: "player", header: "Jugador" },
  { accessorKey: "minutes", header: "MIN" },
  { accessorKey: "pts", header: "PTS", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "reb", header: "REB", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ast", header: "AST", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ptsPer36", header: "PTS/36", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "efg", header: "eFG%", cell: (c) => fmtPct(c.getValue() as number | null) },
  { accessorKey: "ts", header: "TS%", cell: (c) => fmtPct(c.getValue() as number | null) },
];

/** Box score de un equipo en un partido — replica `boxscore_df`, app.py:327-346. */
export function BoxscoreTable({ gameId, teamSlug }: { gameId: number; teamSlug: string }) {
  const query = useBoxscore(gameId, teamSlug);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <TeamLogo slug={teamSlug} size={28} />
        <span className="font-medium text-slate-800">{teamSlug}</span>
      </div>
      <QueryPanel
        query={query}
        isEmpty={(d) => d.rows.length === 0}
        emptyMessage="Sin box score disponible para este partido."
      >
        {(data) => {
          const rows: BoxscoreRow[] = data.rows.map((r) => {
            const minutes = parseMinutes(r.minutes);
            return {
              player: r.player_name,
              minutes: r.minutes ?? "-",
              pts: r.points ?? null,
              reb: r.rebounds ?? null,
              ast: r.assists ?? null,
              ptsPer36: per36(r.points, minutes),
              efg: r.efg_pct ?? null,
              ts: r.ts_pct ?? null,
            };
          });
          return <StatTable data={rows} columns={columns} />;
        }}
      </QueryPanel>
    </div>
  );
}

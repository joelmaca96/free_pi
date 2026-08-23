import type { ColumnDef } from "@tanstack/react-table";
import { StatTable } from "./StatTable";
import { QueryPanel } from "./PanelState";
import { RefreshButton } from "./RefreshButton";
import { TeamLogo } from "./TeamLogo";
import { fmt, fmtPct } from "@/lib/format";
import { parseMinutes, per36 } from "@/lib/boxscore";
import { useBoxscore } from "@/api/hooks";

interface BoxscoreRow {
  player: string;
  minutes: string | number;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  ptsPer36: number | null;
  efg: number | null;
}

const columns: ColumnDef<BoxscoreRow, any>[] = [
  { accessorKey: "player", header: "Jugador" },
  { accessorKey: "minutes", header: "MIN" },
  { accessorKey: "pts", header: "PTS", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "reb", header: "REB", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ast", header: "AST", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ptsPer36", header: "PTS/36", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "efg", header: "eFG%", cell: (c) => fmtPct(c.getValue() as number | null) },
];

/**
 * Box score de un equipo en un partido. El endpoint devuelve filas de ambos
 * equipos con `team_id`; este componente filtra por el `teamId` recibido.
 */
export function BoxscoreTable({
  gameId,
  teamId,
  teamName,
}: {
  gameId: string;
  teamId: string;
  teamName?: string;
}) {
  const query = useBoxscore(gameId);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <TeamLogo teamId={teamId} size={28} />
        <span className="font-medium text-slate-800">{teamName ?? teamId}</span>
      </div>
      <QueryPanel
        query={query}
        isEmpty={(d) => d.rows.filter((r) => r.team_id === teamId).length === 0}
        emptyMessage="Sin box score disponible para este partido."
        emptyAction={<RefreshButton gameId={gameId} />}
      >
        {(data) => {
          const rows: BoxscoreRow[] = data.rows
            .filter((r) => r.team_id === teamId)
            .map((r) => {
              const minutes = parseMinutes(r.minutes);
              return {
                player: r.name,
                minutes: r.minutes ?? "-",
                pts: r.pts ?? null,
                reb: r.reb ?? null,
                ast: r.ast ?? null,
                ptsPer36: per36(r.pts, minutes),
                efg: r.efg_pct ?? null,
              };
            });
          return <StatTable data={rows} columns={columns} />;
        }}
      </QueryPanel>
    </div>
  );
}

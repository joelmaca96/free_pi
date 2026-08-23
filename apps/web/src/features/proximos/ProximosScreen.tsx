import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import {
  useTeam,
  useTeamGames,
  useScheduleDifficulty,
  useProjection,
  useHeadToHead,
} from "@/api/hooks";
import { QueryPanel } from "@/components/PanelState";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { StatTable } from "@/components/StatTable";
import { GameDetail } from "@/components/GameDetail";
import { TeamLogo } from "@/components/TeamLogo";
import { fmt, formatDateEs } from "@/lib/format";
import { TeamOverviewPanel } from "@/features/resumen/TeamOverviewPanel";

const H2H_LAST_N = 2; // app.py:125

interface DifficultyRow {
  date: string;
  opponentName: string;
  netRating: number | null;
}

const difficultyColumns: ColumnDef<DifficultyRow, any>[] = [
  { accessorKey: "date", header: "Fecha", cell: (c) => formatDateEs(c.getValue() as string) },
  { accessorKey: "opponentName", header: "Rival" },
  { accessorKey: "netRating", header: "Net Rating", cell: (c) => fmt(c.getValue() as number | null) },
];

/** `/{teamId}/proximos` — replica `render_upcoming_tab`, app.py:634-700 (sin scraping bajo demanda). */
export function ProximosScreen() {
  const { teamId = "" } = useParams();
  const filters = useGlobalFilters(teamId);
  const filter = { seasonLabel: filters.seasonLabel };
  const teamQuery = useTeam(teamId);
  const gamesQuery = useTeamGames(teamId, filter);
  const [nextN, setNextN] = useState(5);
  const difficultyQuery = useScheduleDifficulty(teamId, nextN);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const upcoming = useMemo(
    () => (gamesQuery.data?.items ?? []).filter((g) => g.team_score == null),
    [gamesQuery.data]
  );

  const game = upcoming.find((g) => g.id === selectedId) ?? upcoming[0];
  const rivalId = game?.opponent.id;

  const projectionQuery = useProjection(teamId, rivalId, filter);
  const h2hQuery = useHeadToHead(teamId, rivalId, filter);

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-800">
            Dificultad del próximo tramo de calendario
          </h2>
          <label className="flex flex-col text-xs font-medium text-slate-500">
            Próximos N partidos
            <input
              type="number"
              min={1}
              max={15}
              value={nextN}
              onChange={(e) => setNextN(Number(e.target.value))}
              className="mt-0.5 w-20 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
            />
          </label>
        </div>
        <QueryPanel
          query={difficultyQuery}
          isEmpty={(d) => d.games_considered === 0}
          emptyMessage="Sin partidos pendientes en el calendario descargado."
        >
          {(data) => {
            const rows: DifficultyRow[] = data.opponents.map((o) => ({
              date: o.match_date,
              opponentName: o.opponent_name,
              netRating: o.predicted_net_rating ?? null,
            }));
            return (
              <div className="space-y-3">
                <StatCardRow>
                  <StatCard label="Partidos considerados" value={data.games_considered} />
                  <StatCard
                    label="Rivales con datos"
                    value={`${data.opponents_scouted}/${data.games_considered}`}
                  />
                  <StatCard label="Net Rating medio del rival" value={fmt(data.avg_opponent_net_rating)} />
                </StatCardRow>
                <StatTable data={rows} columns={difficultyColumns} />
              </div>
            );
          }}
        </QueryPanel>
      </section>

      <QueryPanel
        query={gamesQuery}
        isEmpty={() => upcoming.length === 0}
        emptyMessage="No hay partidos pendientes en el calendario descargado."
      >
        {() =>
          !game ? null : (
            <div className="space-y-8">
              <section>
                <label className="flex flex-col text-xs font-medium text-slate-500">
                  Próximo enfrentamiento
                  <select
                    className="mt-0.5 max-w-md rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
                    value={game.id}
                    onChange={(e) => setSelectedId(e.target.value)}
                  >
                    {upcoming.map((g) => (
                      <option key={g.id} value={g.id}>
                        {formatDateEs(g.date)} — {g.opponent.name}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="mt-3 flex items-center gap-3">
                  <TeamLogo teamId={game.opponent.id} size={48} />
                  <h3 className="text-lg font-semibold text-slate-900">
                    {formatDateEs(game.date)} — {game.opponent.name} ({game.is_home ? "en casa" : "fuera"})
                  </h3>
                </div>
              </section>

              <section>
                <h2 className="mb-3 text-lg font-semibold text-slate-800">Proyección del partido</h2>
                <QueryPanel
                  query={projectionQuery}
                  isEmpty={(d) => d.projection == null}
                  emptyMessage="Datos insuficientes para proyectar el marcador: falta pace/ORtg/DRtg de alguno de los dos equipos en la temporada y competición seleccionadas."
                >
                  {(data) =>
                    data.projection && (
                      <StatCardRow>
                        <StatCard
                          label="Pace proyectado"
                          value={fmt(data.projection.predicted_pace)}
                        />
                        <StatCard
                          label="ORtg proyectado"
                          value={fmt(data.projection.predicted_ortg)}
                        />
                        <StatCard
                          label="Net Rating proyectado"
                          value={fmt(data.projection.predicted_net_rating)}
                        />
                        <StatCard
                          label="Margen esperado"
                          value={fmt(data.projection.expected_margin)}
                        />
                      </StatCardRow>
                    )
                  }
                </QueryPanel>
              </section>

              <section>
                <h2 className="mb-3 text-lg font-semibold text-slate-800">Scouting: {game.opponent.name}</h2>
                <TeamOverviewPanel teamId={game.opponent.id} filter={filter} lastN={filters.lastN} />
              </section>

              <section>
                <h2 className="mb-3 text-lg font-semibold text-slate-800">
                  Últimos {H2H_LAST_N} enfrentamientos directos: {teamQuery.data?.name ?? teamId} vs{" "}
                  {game.opponent.name}
                </h2>
                <QueryPanel
                  query={h2hQuery}
                  isEmpty={(d) => d.items.length === 0}
                  emptyMessage="Sin enfrentamientos directos guardados todavía entre estos dos equipos."
                >
                  {(data) => {
                    const recent = data.items.slice(-H2H_LAST_N);
                    return (
                      <div className="space-y-6">
                        {recent.map((h) => (
                          <GameDetail
                            key={h.id}
                            game={{
                              id: h.id,
                              date: h.date,
                              isHome: true,
                              opponentId: game.opponent.id,
                              opponentName: game.opponent.name,
                              teamScore: h.team_score ?? null,
                              opponentScore: h.opponent_score ?? null,
                              pace: null,
                              netRating: null,
                            }}
                            selfId={teamId}
                            selfName={teamQuery.data?.name ?? teamId}
                          />
                        ))}
                      </div>
                    );
                  }}
                </QueryPanel>
              </section>
            </div>
          )
        }
      </QueryPanel>
    </div>
  );
}

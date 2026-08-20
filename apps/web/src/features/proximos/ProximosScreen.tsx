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
  usePlayerForm,
} from "@/api/hooks";
import { QueryPanel } from "@/components/PanelState";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { StatTable } from "@/components/StatTable";
import { GameDetail } from "@/components/GameDetail";
import { TeamLogo } from "@/components/TeamLogo";
import { ExportButton } from "@/components/ExportButton";
import { ScoutRivalPanel } from "@/components/ScoutRivalPanel";
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

/** `/{teamSlug}/proximos` — replica `render_upcoming_tab`, app.py:634-700 (sin scraping bajo demanda). */
export function ProximosScreen() {
  const { teamSlug = "" } = useParams();
  const filters = useGlobalFilters(teamSlug);
  const filter = { season: filters.season, league: filters.league };
  const teamQuery = useTeam(teamSlug);
  const gamesQuery = useTeamGames(teamSlug, filter);
  const [nextN, setNextN] = useState(5);
  const difficultyQuery = useScheduleDifficulty(teamSlug, filter, nextN);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const upcoming = useMemo(
    () => (gamesQuery.data?.items ?? []).filter((g) => g.team_score == null),
    [gamesQuery.data]
  );

  const game = upcoming.find((g) => g.id === selectedId) ?? upcoming[0];
  const rivalSlug = game?.opponent.slug;

  const projectionQuery = useProjection(teamSlug, rivalSlug, filter.season ?? 0, filter.league);
  const h2hQuery = useHeadToHead(teamSlug, rivalSlug, filter);
  // Señal de "¿tiene datos este rival?": no puede ser el roster (`/roster` solo
  // incluye jugadores con `photo_url`, que solo rellena el scraper oficial de
  // baskonia.com para el equipo propio — un rival scouteado vía BBR nunca
  // tendría roster, aunque el scouting haya funcionado). `players/form` sí
  // refleja directamente lo que escribe `fetch_opponent_scouting`.
  const rivalDataQuery = usePlayerForm(rivalSlug ?? "", filter, filters.lastN);

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg">
            Dificultad del próximo tramo de calendario
          </h2>
          <label className="field w-20">
            <span>Próximos N partidos</span>
            <input
              type="number"
              min={1}
              max={15}
              value={nextN}
              onChange={(e) => setNextN(Number(e.target.value))}
              className="input"
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
              date: o.date,
              opponentName: o.opponent_name,
              netRating: o.net_rating ?? null,
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
                <label className="field max-w-md">
                  <span>Próximo enfrentamiento</span>
                  <select
                    className="input"
                    value={game.id}
                    onChange={(e) => setSelectedId(Number(e.target.value))}
                  >
                    {upcoming.map((g) => (
                      <option key={g.id} value={g.id}>
                        {formatDateEs(g.date)} — {g.opponent.name}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="mt-3 flex items-center gap-3">
                  <TeamLogo slug={game.opponent.slug} size={48} />
                  <h3 className="text-lg">
                    {formatDateEs(game.date)} — {game.opponent.name} ({game.is_home ? "en casa" : "fuera"})
                  </h3>
                </div>
              </section>

              <section>
                <h2 className="mb-3 text-lg">Proyección del partido</h2>
                <QueryPanel
                  query={projectionQuery}
                  isEmpty={(d) => d.projection == null}
                  emptyMessage="Datos insuficientes para proyectar el marcador: falta pace/ORtg/DRtg de alguno de los dos equipos en la temporada y competición seleccionadas."
                >
                  {(data) =>
                    data.projection && (
                      <StatCardRow>
                        <StatCard
                          label="Posesiones proyectadas"
                          value={fmt(data.projection.projected_possessions)}
                        />
                        <StatCard
                          label={`${teamQuery.data?.name ?? teamSlug} (proyección)`}
                          value={fmt(data.projection.team_projected_score)}
                        />
                        <StatCard
                          label={`${game.opponent.name} (proyección)`}
                          value={fmt(data.projection.opp_projected_score)}
                        />
                      </StatCardRow>
                    )
                  }
                </QueryPanel>
              </section>

              <section>
                <h2 className="mb-3 text-lg">Scouting: {game.opponent.name}</h2>
                <QueryPanel query={rivalDataQuery}>
                  {(data) =>
                    data.items.length === 0 ? (
                      <ScoutRivalPanel
                        teamSlug={game.opponent.slug}
                        teamName={game.opponent.name}
                        lastN={filters.lastN}
                      />
                    ) : (
                      <TeamOverviewPanel teamSlug={game.opponent.slug} filter={filter} lastN={filters.lastN} />
                    )
                  }
                </QueryPanel>
              </section>

              <section>
                <h2 className="mb-3 text-lg">
                  Últimos {H2H_LAST_N} enfrentamientos directos: {teamQuery.data?.name ?? teamSlug} vs{" "}
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
                              opponentSlug: game.opponent.slug,
                              opponentName: game.opponent.name,
                              teamScore: h.team_score ?? null,
                              opponentScore: h.opponent_score ?? null,
                              pace: null,
                              netRating: null,
                            }}
                            selfSlug={teamSlug}
                            selfName={teamQuery.data?.name ?? teamSlug}
                          />
                        ))}
                      </div>
                    );
                  }}
                </QueryPanel>
              </section>

              <ExportButton label="📄 Informe de scouting en PDF" />
            </div>
          )
        }
      </QueryPanel>
    </div>
  );
}

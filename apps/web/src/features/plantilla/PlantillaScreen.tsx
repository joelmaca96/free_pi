import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { useRoster, usePlayerForm } from "@/api/hooks";
import { QueryPanel } from "@/components/PanelState";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { ExportButton } from "@/components/ExportButton";
import { fmt, fmtPct } from "@/lib/format";

const SEASON_STATS_LAST_N = 1000; // igual que app.py:897 (last_n grande = "toda la temporada")

/** `/{teamSlug}/plantilla` — replica `render_roster_tab` + `render_player_card`, app.py:867-904, 703-748. */
export function PlantillaScreen() {
  const { teamSlug = "" } = useParams();
  const filters = useGlobalFilters(teamSlug);
  const filter = { season: filters.season, league: filters.league };
  const rosterQuery = useRoster(teamSlug, filter);
  const seasonFormQuery = usePlayerForm(teamSlug, filter, SEASON_STATS_LAST_N);
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const seasonStatsByPlayer = useMemo(() => {
    const map = new Map<string, NonNullable<typeof seasonFormQuery.data>["items"][number]>();
    for (const row of seasonFormQuery.data?.items ?? []) map.set(row.player_name, row);
    return map;
  }, [seasonFormQuery.data]);

  return (
    <QueryPanel
      query={rosterQuery}
      isEmpty={(d) => d.players.length === 0}
      emptyMessage="Sin plantilla descargada todavía."
    >
      {(data) => {
        const player = data.players.find((p) => p.name === selectedName) ?? data.players[0];
        const seasonStats = player ? seasonStatsByPlayer.get(player.name) : undefined;
        const recentForm = player?.form as
          | {
              games: number;
              avg_minutes: number | null;
              avg_pts: number | null;
              avg_efg_pct: number | null;
              avg_ts_pct: number | null;
              fg3a_rate: number | null;
              ft_rate: number | null;
            }
          | null
          | undefined;

        return (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg">
                Plantilla actual ({data.players.length} jugadores)
              </h2>
              <ExportButton label="🎬 Generar ppt para Paolo" />
            </div>

            <div
              className="grid gap-4"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))" }}
            >
              {data.players.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  onClick={() => setSelectedName(p.name)}
                  className={`card elev-sm flex flex-col items-center gap-1 border-none p-2 text-center hover:bg-white/5 ${
                    player?.name === p.name ? "ring-2 ring-accent" : ""
                  }`}
                >
                  {p.photo_url ? (
                    <img
                      src={p.photo_url}
                      alt={p.name}
                      width={100}
                      height={100}
                      className="rounded-md object-cover"
                    />
                  ) : (
                    <div className="flex h-[100px] w-[100px] items-center justify-center rounded-md bg-neutral-800 text-3xl">
                      🏀
                    </div>
                  )}
                  <span className="text-muted text-xs">
                    #{p.number ?? "-"} {p.name}
                  </span>
                </button>
              ))}
            </div>

            <hr className="border-divider" />

            {player && (
              <div className="space-y-6">
                <div className="flex items-center gap-4">
                  {player.photo_url ? (
                    <img
                      src={player.photo_url}
                      alt={player.name}
                      width={200}
                      height={200}
                      className="rounded-md object-cover"
                    />
                  ) : (
                    <div className="flex h-[200px] w-[200px] items-center justify-center rounded-md bg-neutral-800 text-6xl">
                      🏀
                    </div>
                  )}
                  <div>
                    <h3 className="text-xl">{player.name}</h3>
                    <p className="text-muted text-sm">
                      <strong className="text-text">Posición:</strong> {player.position ?? "-"}
                    </p>
                    <p className="text-muted text-sm">
                      <strong className="text-text">Dorsal:</strong> {player.number ?? "-"}
                    </p>
                  </div>
                </div>

                <section>
                  <h4 className="text-muted mb-2 text-sm font-semibold uppercase tracking-wide">
                    Forma reciente (últimos {filters.lastN} partidos jugados)
                  </h4>
                  <StatCardRow>
                    <StatCard label="Partidos" value={recentForm?.games ?? "-"} />
                    <StatCard label="MIN" value={fmt(recentForm?.avg_minutes)} />
                    <StatCard label="PTS" value={fmt(recentForm?.avg_pts)} />
                    <StatCard label="eFG%" value={fmtPct(recentForm?.avg_efg_pct)} />
                    <StatCard label="TS%" value={fmtPct(recentForm?.avg_ts_pct)} />
                    <StatCard label="3PA%" value={fmtPct(recentForm?.fg3a_rate)} />
                    <StatCard label="FTr" value={fmt(recentForm?.ft_rate)} />
                  </StatCardRow>
                </section>

                <section>
                  <h4 className="text-muted mb-2 text-sm font-semibold uppercase tracking-wide">
                    Estadísticas de la temporada
                  </h4>
                  <QueryPanel
                    query={seasonFormQuery}
                    isEmpty={() => !seasonStats}
                    emptyMessage="Sin datos suficientes."
                  >
                    {() => (
                      <StatCardRow>
                        <StatCard label="Partidos" value={seasonStats?.games ?? "-"} />
                        <StatCard label="MIN" value={fmt(seasonStats?.avg_minutes)} />
                        <StatCard label="PTS" value={fmt(seasonStats?.avg_pts)} />
                        <StatCard label="eFG%" value={fmtPct(seasonStats?.avg_efg_pct)} />
                        <StatCard label="TS%" value={fmtPct(seasonStats?.avg_ts_pct)} />
                        <StatCard label="3PA%" value={fmtPct(seasonStats?.fg3a_rate)} />
                        <StatCard label="FTr" value={fmt(seasonStats?.ft_rate)} />
                      </StatCardRow>
                    )}
                  </QueryPanel>
                </section>
              </div>
            )}
          </div>
        );
      }}
    </QueryPanel>
  );
}

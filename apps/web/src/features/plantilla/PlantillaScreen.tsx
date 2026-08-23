import { useState } from "react";
import { useParams } from "react-router-dom";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { useRoster } from "@/api/hooks";
import { QueryPanel } from "@/components/PanelState";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { fmt, fmtPct } from "@/lib/format";

/** `/{teamId}/plantilla` — replica `render_roster_tab` + `render_player_card`, app.py:867-904, 703-748. */
export function PlantillaScreen() {
  const { teamId = "" } = useParams();
  const filters = useGlobalFilters(teamId);
  const filter = { seasonLabel: filters.seasonLabel };
  const rosterQuery = useRoster(teamId, filter);
  const [selectedName, setSelectedName] = useState<string | null>(null);

  return (
    <QueryPanel
      query={rosterQuery}
      isEmpty={(d) => d.players.length === 0}
      emptyMessage="Sin plantilla descargada todavía."
    >
      {(data) => {
        const player = data.players.find((p) => p.name === selectedName) ?? data.players[0];

        return (
          <div className="space-y-6">
            <h2 className="text-lg font-semibold text-slate-800">
              Plantilla actual ({data.players.length} jugadores)
            </h2>

            <div
              className="grid gap-4"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))" }}
            >
              {data.players.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  onClick={() => setSelectedName(p.name)}
                  className={`flex flex-col items-center gap-1 rounded-md p-2 text-center hover:bg-slate-100 ${
                    player?.name === p.name ? "ring-2 ring-slate-900" : ""
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
                    <div className="flex h-[100px] w-[100px] items-center justify-center rounded-md bg-slate-100 text-3xl">
                      🏀
                    </div>
                  )}
                  <span className="text-xs text-slate-600">
                    #{p.number ?? "-"} {p.name}
                  </span>
                </button>
              ))}
            </div>

            <hr className="border-slate-200" />

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
                    <div className="flex h-[200px] w-[200px] items-center justify-center rounded-md bg-slate-100 text-6xl">
                      🏀
                    </div>
                  )}
                  <div>
                    <h3 className="text-xl font-semibold text-slate-900">{player.name}</h3>
                    <p className="text-sm text-slate-600">
                      <strong>Posición:</strong> {player.position ?? "-"}
                    </p>
                    <p className="text-sm text-slate-600">
                      <strong>Dorsal:</strong> {player.number ?? "-"}
                    </p>
                  </div>
                </div>

                <section>
                  <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
                    Estadísticas de la temporada
                  </h4>
                  <StatCardRow>
                    <StatCard label="Partidos" value={player.gp ?? "-"} />
                    <StatCard label="MIN" value={fmt(player.min_avg)} />
                    <StatCard label="PTS" value={fmt(player.pts_avg)} />
                    <StatCard label="REB" value={fmt(player.reb_avg)} />
                    <StatCard label="AST" value={fmt(player.ast_avg)} />
                    <StatCard label="eFG%" value={fmtPct(player.efg_pct)} />
                  </StatCardRow>
                </section>
              </div>
            )}
          </div>
        );
      }}
    </QueryPanel>
  );
}

import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { useTeam, useTeamGames } from "@/api/hooks";
import { QueryPanel } from "@/components/PanelState";
import { GameDetail } from "@/components/GameDetail";
import { ExportButton } from "@/components/ExportButton";
import { formatDateEs, resultLabel } from "@/lib/format";

/** `/{teamSlug}/anteriores` — replica `render_past_games_tab`, app.py:523-571. */
export function AnterioresScreen() {
  const { teamSlug = "" } = useParams();
  const filters = useGlobalFilters(teamSlug);
  const teamQuery = useTeam(teamSlug);
  const gamesQuery = useTeamGames(teamSlug, { season: filters.season, league: filters.league });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const played = useMemo(
    () => (gamesQuery.data?.items ?? []).filter((g) => g.result != null),
    [gamesQuery.data]
  );

  return (
    <QueryPanel
      query={gamesQuery}
      isEmpty={() => played.length === 0}
      emptyMessage="Sin partidos guardados todavía."
    >
      {() => {
        const game = played.find((g) => g.id === selectedId) ?? played[played.length - 1];
        return (
          <div className="space-y-6">
            <label className="field max-w-md">
              <span>Partido</span>
              <select
                className="input"
                value={game?.id ?? ""}
                onChange={(e) => setSelectedId(Number(e.target.value))}
              >
                {played.map((g) => (
                  <option key={g.id} value={g.id}>
                    {formatDateEs(g.date)} — {g.opponent.name} ({resultLabel(g.result)})
                  </option>
                ))}
              </select>
            </label>

            {game && (
              <>
                <GameDetail
                  game={{
                    id: game.id,
                    date: game.date,
                    isHome: game.is_home,
                    opponentSlug: game.opponent.slug,
                    opponentName: game.opponent.name,
                    teamScore: game.team_score ?? null,
                    opponentScore: game.opponent_score ?? null,
                    pace: game.advanced?.pace ?? null,
                    netRating: game.advanced?.net_rating ?? null,
                  }}
                  selfSlug={teamSlug}
                  selfName={teamQuery.data?.name ?? teamSlug}
                />
                <ExportButton label="📄 Informe de este partido en PDF" />
              </>
            )}
          </div>
        );
      }}
    </QueryPanel>
  );
}

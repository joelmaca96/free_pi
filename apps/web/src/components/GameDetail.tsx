import { useMemo } from "react";
import { useTeamGames } from "@/api/hooks";
import { TeamLogo } from "./TeamLogo";
import { StatCard, StatCardRow } from "./StatCard";
import { BoxscoreTable } from "./BoxscoreTable";
import { formatDateEs, fmt } from "@/lib/format";

export interface GameDetailGame {
  id: number;
  date: string;
  isHome: boolean;
  opponentSlug: string;
  opponentName: string;
  teamScore: number | null;
  opponentScore: number | null;
  pace: number | null;
  netRating: number | null;
}

/**
 * Cabecera + 3 StatCard (Pace, Net Rating local, Net Rating rival) + 2 box
 * scores lado a lado. Replica el bloque compartido de `render_past_games_tab`
 * (app.py:537-562) y `render_head_to_head_tab` (app.py:494-519).
 */
export function GameDetail({
  game,
  selfSlug,
  selfName,
}: {
  game: GameDetailGame;
  selfSlug: string;
  selfName: string;
}) {
  // Net Rating del rival para este partido concreto: no viene en el `GameItem`
  // del equipo consultado (solo trae su propio punto de vista), así que se
  // busca en la lista de partidos del rival.
  const opponentGamesQuery = useTeamGames(game.opponentSlug, { season: null, league: null });
  const opponentAdvanced = useMemo(() => {
    const match = opponentGamesQuery.data?.items.find((g) => g.id === game.id);
    return match?.advanced ?? null;
  }, [opponentGamesQuery.data, game.id]);

  const homeSlug = game.isHome ? selfSlug : game.opponentSlug;
  const awaySlug = game.isHome ? game.opponentSlug : selfSlug;
  const homeName = game.isHome ? selfName : game.opponentName;
  const awayName = game.isHome ? game.opponentName : selfName;
  const homeScore = game.isHome ? game.teamScore : game.opponentScore;
  const awayScore = game.isHome ? game.opponentScore : game.teamScore;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <TeamLogo slug={game.opponentSlug} size={48} />
        <h3 className="text-lg font-semibold text-slate-900">
          {formatDateEs(game.date)} — {homeName} {homeScore ?? "-"} - {awayScore ?? "-"} {awayName}
        </h3>
      </div>

      <StatCardRow>
        <StatCard label="Pace" value={fmt(game.pace)} />
        <StatCard label={`Net Rating ${selfName}`} value={fmt(game.netRating)} />
        <StatCard label={`Net Rating ${game.opponentName}`} value={fmt(opponentAdvanced?.net_rating)} />
      </StatCardRow>

      <div className="grid gap-4 md:grid-cols-2">
        <BoxscoreTable gameId={game.id} teamSlug={homeSlug} />
        <BoxscoreTable gameId={game.id} teamSlug={awaySlug} />
      </div>
    </div>
  );
}

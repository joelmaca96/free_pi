import { useMemo } from "react";
import { useTeamGames } from "@/api/hooks";
import { TeamLogo } from "./TeamLogo";
import { StatCard, StatCardRow } from "./StatCard";
import { BoxscoreTable } from "./BoxscoreTable";
import { EmptyState } from "./PanelState";
import { RefreshButton } from "./RefreshButton";
import { formatDateEs, fmt } from "@/lib/format";

export interface GameDetailGame {
  id: string;
  date: string;
  isHome: boolean;
  opponentId: string;
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
  selfId,
  selfName,
}: {
  game: GameDetailGame;
  selfId: string;
  selfName: string;
}) {
  // Net Rating del rival para este partido concreto: no viene en el `GameItem`
  // del equipo consultado (solo trae su propio punto de vista), así que se
  // busca en la lista de partidos del rival.
  const opponentGamesQuery = useTeamGames(game.opponentId, { seasonLabel: null });
  const opponentAdvanced = useMemo(() => {
    const match = opponentGamesQuery.data?.items.find((g) => g.id === game.id);
    return match?.advanced ?? null;
  }, [opponentGamesQuery.data, game.id]);

  // Sin pace ni net rating de ninguno de los dos equipos, `game_advanced_stats`
  // está vacío para este partido: se ofrece intentar traerlo de la fuente.
  const missingAdvanced =
    game.pace == null && game.netRating == null && opponentAdvanced?.net_rating == null;

  const homeId = game.isHome ? selfId : game.opponentId;
  const awayId = game.isHome ? game.opponentId : selfId;
  const homeName = game.isHome ? selfName : game.opponentName;
  const awayName = game.isHome ? game.opponentName : selfName;
  const homeScore = game.isHome ? game.teamScore : game.opponentScore;
  const awayScore = game.isHome ? game.opponentScore : game.teamScore;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <TeamLogo slug={game.opponentSlug} size={48} />
        <h3 className="m-0 text-lg">
          {formatDateEs(game.date)} — {homeName} {homeScore ?? "-"} - {awayScore ?? "-"} {awayName}
        </h3>
      </div>

      <StatCardRow>
        <StatCard label="Pace" value={fmt(game.pace)} />
        <StatCard label={`Net Rating ${selfName}`} value={fmt(game.netRating)} />
        <StatCard label={`Net Rating ${game.opponentName}`} value={fmt(opponentAdvanced?.net_rating)} />
      </StatCardRow>

      {missingAdvanced && (
        <EmptyState
          message="Sin estadísticas avanzadas ni quintetos para este partido."
          action={<RefreshButton gameId={game.id} />}
        />
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <BoxscoreTable gameId={game.id} teamId={homeId} teamName={homeName} />
        <BoxscoreTable gameId={game.id} teamId={awayId} teamName={awayName} />
      </div>
    </div>
  );
}

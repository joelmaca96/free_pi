import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError, type ProblemDetails } from "./client";

/** staleTime generoso: la API ya cachea con ETag + Cache-Control (apps/api/middleware.py). */
const STALE_TIME_MS = 60_000;

async function unwrap<T>(promise: Promise<{ data?: T; error?: unknown }>): Promise<T> {
  const { data, error } = await promise;
  if (error) {
    throw new ApiError(error as ProblemDetails);
  }
  return data as T;
}

export function useTeams() {
  return useQuery({
    queryKey: ["teams"],
    queryFn: () => unwrap(apiClient.GET("/api/v1/teams")),
    staleTime: STALE_TIME_MS,
  });
}

export function useTeam(teamId: string) {
  return useQuery({
    queryKey: ["team", teamId],
    queryFn: () =>
      unwrap(apiClient.GET("/api/v1/teams/{team_id}", { params: { path: { team_id: teamId } } })),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useFilters(teamId: string) {
  return useQuery({
    queryKey: ["filters", teamId],
    queryFn: () =>
      unwrap(apiClient.GET("/api/v1/teams/{team_id}/filters", { params: { path: { team_id: teamId } } })),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export interface SeasonFilter {
  seasonLabel: string | null;
}

export function useTeamSummary(teamId: string, filter: SeasonFilter) {
  return useQuery({
    queryKey: ["summary", teamId, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/summary", {
          params: { path: { team_id: teamId }, query: { season_label: filter.seasonLabel } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useTeamGames(
  teamId: string,
  filter: SeasonFilter,
  opts: { limit?: number; offset?: number } = {}
) {
  return useQuery({
    queryKey: ["games", teamId, filter, opts],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/games", {
          params: {
            path: { team_id: teamId },
            query: {
              season_label: filter.seasonLabel,
              limit: opts.limit ?? 200,
              offset: opts.offset ?? 0,
            },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useRoster(teamId: string, filter: SeasonFilter) {
  return useQuery({
    queryKey: ["roster", teamId, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/roster", {
          params: { path: { team_id: teamId }, query: { season_label: filter.seasonLabel } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function usePlayerForm(teamId: string, filter: SeasonFilter, lastN: number) {
  return useQuery({
    queryKey: ["playerForm", teamId, filter, lastN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/players/form", {
          params: {
            path: { team_id: teamId },
            query: { last_n: lastN },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function usePlayerLoad(teamId: string, windowDays: number) {
  return useQuery({
    queryKey: ["playerLoad", teamId, windowDays],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/players/load", {
          params: { path: { team_id: teamId }, query: { window_days: windowDays } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useNarrative(teamId: string) {
  return useQuery({
    queryKey: ["narrative", teamId],
    queryFn: () =>
      unwrap(apiClient.GET("/api/v1/teams/{team_id}/narrative", { params: { path: { team_id: teamId } } })),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useScheduleDifficulty(teamId: string, nextN: number) {
  return useQuery({
    queryKey: ["scheduleDifficulty", teamId, nextN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/schedule-difficulty", {
          params: { path: { team_id: teamId }, query: { next_n: nextN } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId,
  });
}

export function useProjection(
  teamId: string,
  opponentId: string | undefined,
  filter: SeasonFilter
) {
  return useQuery({
    queryKey: ["projection", teamId, opponentId, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/matchups/{opponent_id}/projection", {
          params: {
            path: { team_id: teamId, opponent_id: opponentId as string },
            query: { season_label: filter.seasonLabel },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId && !!opponentId,
  });
}

export function useHeadToHead(teamId: string, opponentId: string | undefined, filter: SeasonFilter) {
  return useQuery({
    queryKey: ["headToHead", teamId, opponentId, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{team_id}/matchups/{opponent_id}/head-to-head", {
          params: {
            path: { team_id: teamId, opponent_id: opponentId as string },
            query: { season_label: filter.seasonLabel },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!teamId && !!opponentId,
  });
}

export function useBoxscore(gameId: string | undefined) {
  return useQuery({
    queryKey: ["boxscore", gameId],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/games/{game_id}/boxscore", {
          params: { path: { game_id: gameId as string } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: gameId != null,
  });
}

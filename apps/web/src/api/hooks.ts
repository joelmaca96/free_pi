import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, ApiError, type ProblemDetails } from "./client";
import type { components } from "./schema";

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

export function useTeam(slug: string) {
  return useQuery({
    queryKey: ["team", slug],
    queryFn: () => unwrap(apiClient.GET("/api/v1/teams/{slug}", { params: { path: { slug } } })),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useFilters(slug: string) {
  return useQuery({
    queryKey: ["filters", slug],
    queryFn: () =>
      unwrap(apiClient.GET("/api/v1/teams/{slug}/filters", { params: { path: { slug } } })),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export interface SeasonLeagueFilter {
  season: number | null;
  league: string | null;
}

export function useTeamSummary(slug: string, filter: SeasonLeagueFilter) {
  return useQuery({
    queryKey: ["summary", slug, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/summary", {
          params: { path: { slug }, query: { season: filter.season, league: filter.league } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useTeamGames(
  slug: string,
  filter: SeasonLeagueFilter,
  opts: { limit?: number; offset?: number } = {}
) {
  return useQuery({
    queryKey: ["games", slug, filter, opts],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/games", {
          params: {
            path: { slug },
            query: {
              season: filter.season,
              league: filter.league,
              limit: opts.limit ?? 200,
              offset: opts.offset ?? 0,
            },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useRoster(slug: string, filter: SeasonLeagueFilter) {
  return useQuery({
    queryKey: ["roster", slug, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/roster", {
          params: { path: { slug }, query: { season: filter.season, league: filter.league } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function usePlayerForm(
  slug: string,
  filter: SeasonLeagueFilter,
  lastN: number
) {
  return useQuery({
    queryKey: ["playerForm", slug, filter, lastN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/players/form", {
          params: {
            path: { slug },
            query: { season: filter.season, league: filter.league, last_n: lastN },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useStreaks(
  slug: string,
  season: number,
  league: string | null,
  recentN: number
) {
  return useQuery({
    queryKey: ["streaks", slug, season, league, recentN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/players/streaks", {
          params: { path: { slug }, query: { season, league, recent_n: recentN } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug && season != null,
  });
}

export function usePlayerLoad(slug: string, windowDays: number) {
  return useQuery({
    queryKey: ["playerLoad", slug, windowDays],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/players/load", {
          params: { path: { slug }, query: { window_days: windowDays } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useNarrative(
  slug: string,
  season: number,
  league: string | null,
  recentN: number
) {
  return useQuery({
    queryKey: ["narrative", slug, season, league, recentN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/narrative", {
          params: { path: { slug }, query: { season, league, recent_n: recentN } },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug && season != null,
  });
}

export function useScheduleDifficulty(
  slug: string,
  filter: SeasonLeagueFilter,
  nextN: number
) {
  return useQuery({
    queryKey: ["scheduleDifficulty", slug, filter, nextN],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/schedule-difficulty", {
          params: {
            path: { slug },
            query: { season: filter.season, league: filter.league, next_n: nextN },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug,
  });
}

export function useProjection(
  slug: string,
  opponentSlug: string | undefined,
  season: number,
  league: string | null
) {
  return useQuery({
    queryKey: ["projection", slug, opponentSlug, season, league],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/matchups/{opponent_slug}/projection", {
          params: {
            path: { slug, opponent_slug: opponentSlug as string },
            query: { season, league },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug && !!opponentSlug && season != null,
  });
}

export function useHeadToHead(
  slug: string,
  opponentSlug: string | undefined,
  filter: SeasonLeagueFilter
) {
  return useQuery({
    queryKey: ["headToHead", slug, opponentSlug, filter],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/teams/{slug}/matchups/{opponent_slug}/head-to-head", {
          params: {
            path: { slug, opponent_slug: opponentSlug as string },
            query: { season: filter.season, league: filter.league },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: !!slug && !!opponentSlug,
  });
}

export function useBoxscore(gameId: number | undefined, teamSlug: string | undefined) {
  return useQuery({
    queryKey: ["boxscore", gameId, teamSlug],
    queryFn: () =>
      unwrap(
        apiClient.GET("/api/v1/games/{game_id}/boxscore", {
          params: {
            path: { game_id: gameId as number },
            query: { team_slug: teamSlug as string },
          },
        })
      ),
    staleTime: STALE_TIME_MS,
    enabled: gameId != null && !!teamSlug,
  });
}

export type JobResponse = components["schemas"]["JobResponse"];

const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

/** Último job de scouting de `teamSlug`; hace polling mientras esté activo. */
export function useScoutStatus(teamSlug: string) {
  return useQuery({
    queryKey: ["scoutStatus", teamSlug],
    queryFn: () =>
      unwrap(apiClient.GET("/api/v1/teams/{slug}/scout", { params: { path: { slug: teamSlug } } })),
    enabled: !!teamSlug,
    staleTime: 0,
    refetchInterval: (query) => {
      const job = query.state.data as JobResponse | null | undefined;
      return job && ACTIVE_JOB_STATUSES.has(job.status) ? 2000 : false;
    },
  });
}

/** Encola el scouting de `teamSlug` (idempotente: reutiliza un job activo si ya existe). */
export function useEnqueueScout(teamSlug: string, lastN: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        apiClient.POST("/api/v1/teams/{slug}/scout", {
          params: { path: { slug: teamSlug }, query: { last_n: lastN } },
        })
      ),
    onSuccess: (job) => {
      queryClient.setQueryData(["scoutStatus", teamSlug], job);
    },
  });
}

/**
 * Al completarse un job de scouting, invalida las queries de ese equipo para
 * que el panel "Scouting: {rival}" se repinte con los datos recién llegados.
 */
export function invalidateTeamData(queryClient: ReturnType<typeof useQueryClient>, teamSlug: string) {
  for (const key of ["roster", "filters", "summary", "games", "playerForm"]) {
    queryClient.invalidateQueries({ queryKey: [key, teamSlug], exact: false });
  }
}

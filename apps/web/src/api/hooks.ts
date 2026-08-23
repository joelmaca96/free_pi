import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiClient, ApiError, type ProblemDetails } from "./client";

/** staleTime generoso: la API ya cachea con ETag + Cache-Control (apps/api/middleware.py). */
const STALE_TIME_MS = 60_000;

/**
 * Polling acotado tras un refresco (diseño §6.1, paso 4): el endpoint responde
 * 202 sin esperar al fetch real, así que hay que reconsultar la lectura unas
 * cuantas veces. Acotado a propósito: si el fetch a la fuente falló, la lectura
 * sigue vacía y no se reintenta indefinidamente (nunca se fabrica un dato).
 */
const REFRESH_POLL_INTERVAL_MS = 4_000;
const REFRESH_POLL_ATTEMPTS = 6;

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

/**
 * Programa `REFRESH_POLL_ATTEMPTS` invalidaciones espaciadas de las lecturas
 * afectadas por un refresco y devuelve los ids de temporizador para poder
 * cancelarlos al desmontar.
 */
function scheduleBoundedRefetch(queryClient: QueryClient, keys: unknown[][]): number[] {
  const timers: number[] = [];
  for (let attempt = 1; attempt <= REFRESH_POLL_ATTEMPTS; attempt += 1) {
    timers.push(
      window.setTimeout(() => {
        for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey });
      }, attempt * REFRESH_POLL_INTERVAL_MS)
    );
  }
  return timers;
}

/** Variables de {@link useRefreshGame}; `seasonLabel` solo para partidos aún no cargados. */
export interface RefreshGameVars {
  gameId: string;
  seasonLabel?: string | null;
}

/**
 * Dispara el refresco bajo demanda de un partido (`POST .../refresh`, 202
 * fire-and-forget) y reconsulta de forma acotada las lecturas que dependen de
 * él (`boxscore` del partido y listas de partidos, que llevan `advanced`).
 *
 * El partido va en las variables de `mutate` (no en un argumento del hook) para
 * que el valor sea siempre el del momento del clic y para poder reusar una sola
 * mutación desde una lista de partidos.
 */
export function useRefreshGame() {
  const queryClient = useQueryClient();
  const timers = useRef<number[]>([]);

  useEffect(
    () => () => {
      timers.current.forEach((id) => window.clearTimeout(id));
      timers.current = [];
    },
    []
  );

  return useMutation({
    mutationFn: (vars: RefreshGameVars) =>
      unwrap(
        apiClient.POST("/api/v1/games/{game_id}/refresh", {
          params: {
            path: { game_id: vars.gameId },
            query: { season_label: vars.seasonLabel ?? null },
          },
        })
      ),
    onSuccess: (data, vars) => {
      // `already_in_progress` también deja trabajo en vuelo: conviene
      // reconsultar igual. `rejected_busy` no ha encolado nada.
      if (data.status === "rejected_busy") return;
      timers.current = scheduleBoundedRefetch(queryClient, [
        ["boxscore", vars.gameId],
        ["games"],
      ]);
    },
  });
}

/**
 * Pregunta qué partidos jugados de una fuente/temporada faltan por cargar
 * (`POST /discovery/missing-games`). Solo reporta: la carga de cada partido se
 * dispara luego con {@link useRefreshGame}, uno a uno.
 */
export function useDiscoverMissingGames() {
  return useMutation({
    mutationFn: (vars: { source: "acb" | "euroleague"; seasonLabel: string }) =>
      unwrap(
        apiClient.POST("/api/v1/discovery/missing-games", {
          params: { query: { source: vars.source, season_label: vars.seasonLabel } },
        })
      ),
  });
}

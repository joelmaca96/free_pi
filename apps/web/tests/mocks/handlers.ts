import { http, HttpResponse } from "msw";
import * as f from "./fixtures";

const BASE = "/api/v1";

/** Handlers por defecto: camino feliz para "vitoria", vacío/insuficiente para "bilbao". */
export const handlers = [
  http.get(`${BASE}/teams`, () => HttpResponse.json(f.teams)),

  http.get(`${BASE}/teams/:slug`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? { ...f.teamDetail, slug: "bilbao", name: "Bilbao Basket" } : f.teamDetail)
  ),

  http.get(`${BASE}/teams/:slug/filters`, () => HttpResponse.json(f.filters)),

  http.get(`${BASE}/teams/:slug/summary`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.summaryEmpty : f.summary)
  ),

  http.get(`${BASE}/teams/:slug/games`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.gamesEmpty : f.games)
  ),

  http.get(`${BASE}/teams/:slug/roster`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.rosterEmpty : f.roster)
  ),

  http.get(`${BASE}/teams/:slug/players/form`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.playerFormEmpty : f.playerForm)
  ),

  http.get(`${BASE}/teams/:slug/players/streaks`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.streaksEmpty : f.streaks)
  ),

  http.get(`${BASE}/teams/:slug/players/load`, () => HttpResponse.json(f.load)),

  http.get(`${BASE}/teams/:slug/narrative`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.narrativeEmpty : f.narrative)
  ),

  http.get(`${BASE}/teams/:slug/schedule-difficulty`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.scheduleDifficultyEmpty : f.scheduleDifficulty)
  ),

  http.get(`${BASE}/teams/:slug/matchups/:opponentSlug/projection`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.projectionEmpty : f.projection)
  ),

  http.get(`${BASE}/teams/:slug/matchups/:opponentSlug/head-to-head`, ({ params }) =>
    HttpResponse.json(params.slug === "bilbao" ? f.headToHeadEmpty : f.headToHead)
  ),

  http.get(`${BASE}/games/:gameId/boxscore`, () => HttpResponse.json(f.boxscore)),
];

export const notFoundHandler = (path: string) =>
  http.get(`${BASE}${path}`, () => HttpResponse.json(f.problem, { status: 404 }));

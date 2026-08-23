import { http, HttpResponse } from "msw";
import * as f from "./fixtures";

const BASE = "/api/v1";

/** Handlers por defecto: camino feliz para "bas", vacío/insuficiente para "bilbao". */
export const handlers = [
  http.get(`${BASE}/teams`, () => HttpResponse.json(f.teams)),

  http.get(`${BASE}/teams/:teamId`, ({ params }) =>
    HttpResponse.json(
      params.teamId === "bilbao"
        ? { ...f.teamDetail, id: "bilbao", name: "Bilbao Basket", is_own_team: false }
        : f.teamDetail
    )
  ),

  http.get(`${BASE}/teams/:teamId/filters`, () => HttpResponse.json(f.filters)),

  http.get(`${BASE}/teams/:teamId/summary`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.summaryEmpty : f.summary)
  ),

  http.get(`${BASE}/teams/:teamId/games`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.gamesEmpty : f.games)
  ),

  http.get(`${BASE}/teams/:teamId/roster`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.rosterEmpty : f.roster)
  ),

  http.get(`${BASE}/teams/:teamId/players/form`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.playerFormEmpty : f.playerForm)
  ),

  http.get(`${BASE}/teams/:teamId/players/load`, () => HttpResponse.json(f.load)),

  http.get(`${BASE}/teams/:teamId/narrative`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.narrativeEmpty : f.narrative)
  ),

  http.get(`${BASE}/teams/:teamId/schedule-difficulty`, ({ params }) =>
    HttpResponse.json(
      params.teamId === "bilbao" ? f.scheduleDifficultyEmpty : f.scheduleDifficulty
    )
  ),

  http.get(`${BASE}/teams/:teamId/matchups/:opponentId/projection`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.projectionEmpty : f.projection)
  ),

  http.get(`${BASE}/teams/:teamId/matchups/:opponentId/head-to-head`, ({ params }) =>
    HttpResponse.json(params.teamId === "bilbao" ? f.headToHeadEmpty : f.headToHead)
  ),

  http.get(`${BASE}/games/:gameId/boxscore`, () => HttpResponse.json(f.boxscore)),
];

export const notFoundHandler = (path: string) =>
  http.get(`${BASE}${path}`, () => HttpResponse.json(f.problem, { status: 404 }));

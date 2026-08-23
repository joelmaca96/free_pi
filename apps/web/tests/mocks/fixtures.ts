/** Fixtures del contrato nuevo (schema.d.ts regenerado en la feature 013). */

export const teams = [
  { id: "bas", name: "Baskonia", is_own_team: true },
  { id: "bilbao", name: "Bilbao Basket", is_own_team: false },
];

export const teamDetail = { id: "bas", name: "Baskonia", is_own_team: true };

export const filters = {
  seasons: ["2025-2026", "2024-2025", "2023-2024"],
  default_season: "2025-2026",
  competitions: [
    { id: 1, name: "ACB" },
    { id: 2, name: "Euroliga" },
    { id: 3, name: "Supercopa" },
  ],
};

export const summary = {
  team: { id: "bas", name: "Baskonia" },
  filters: {},
  advanced: {
    avg_ortg: 112.4,
    avg_drtg: 108.9,
    avg_net_rating: 3.5,
    avg_efg_pct: 53.12,
    avg_ts_pct: 56.87,
    avg_tov_pct: 14,
    avg_orb_pct: 28,
    avg_ast_pct: 60,
    avg_stl_pct: 8,
    avg_blk_pct: 5,
    avg_ft_rate: 22,
    avg_ast_to_ratio: 1.6,
  },
  games_played: 38,
  games_upcoming: 4,
};

export const summaryEmpty = {
  team: { id: "bas", name: "Baskonia" },
  filters: {},
  advanced: {
    avg_ortg: null,
    avg_drtg: null,
    avg_net_rating: null,
    avg_efg_pct: null,
    avg_ts_pct: null,
    avg_tov_pct: null,
    avg_orb_pct: null,
    avg_ast_pct: null,
    avg_stl_pct: null,
    avg_blk_pct: null,
    avg_ft_rate: null,
    avg_ast_to_ratio: null,
  },
  games_played: 0,
  games_upcoming: 0,
};

export const games = {
  items: [
    {
      id: "412",
      date: "2026-05-18",
      competition_name: "ACB",
      is_home: true,
      opponent: { id: "rm", name: "Real Madrid" },
      team_score: 88,
      opponent_score: 79,
      result: "W",
      pace: 72.1,
      advanced: { ortg: 118.3, drtg: 106.2, net_rating: 12.1 },
    },
    {
      id: "413",
      date: "2026-06-01",
      competition_name: "ACB",
      is_home: false,
      opponent: { id: "bilbao", name: "Bilbao Basket" },
      team_score: null,
      opponent_score: null,
      result: null,
      pace: null,
      advanced: null,
    },
  ],
  total: 2,
  limit: 200,
  offset: 0,
};

export const gamesEmpty = { items: [], total: 0, limit: 200, offset: 0 };

export const roster = {
  team: { id: "bas", name: "Baskonia" },
  season_label: "2025-2026",
  players: [
    {
      id: "p1",
      name: "Markus Howard",
      number: 0,
      position: "PG",
      team_id: "bas",
      active: true,
      photo_url: "https://example.com/howard.png",
      height_cm: 178,
      birth_date: "1999-03-03",
      nationality: "USA",
      gp: 38,
      min_avg: 27.4,
      pts_avg: 19.6,
      reb_avg: 2.8,
      ast_avg: 3.1,
      efg_pct: 56.1,
    },
  ],
};

export const rosterEmpty = {
  team: { id: "bilbao", name: "Bilbao Basket" },
  season_label: "2025-2026",
  players: [],
};

export const playerForm = {
  player_id: "p1",
  last_n: 5,
  items: [
    {
      game_id: "412",
      game_date: "2026-05-18",
      pts: 24,
      reb: 3,
      ast: 5,
      efg_pct: 66.7,
    },
  ],
};

export const playerFormEmpty = { player_id: "p1", last_n: 5, items: [] };

export const load = {
  window_days: 14,
  as_of: "2026-08-21",
  items: [{ player_id: "p1", name: "Chima Moneke", total_minutes: 148.5 }],
};

export const narrative = {
  team_id: "bas",
  narrative: "El Baskonia juega a un ritmo alto.",
};

export const narrativeEmpty = { team_id: "bilbao", narrative: null };

export const scheduleDifficulty = {
  games_considered: 5,
  opponents_scouted: 3,
  avg_opponent_net_rating: 2.4,
  opponents: [
    {
      opponent_id: "pan",
      opponent_name: "Panathinaikos",
      match_date: "2026-09-30",
      is_home: true,
      predicted_net_rating: 6.8,
      predicted_pace: 74.0,
      predicted_ortg: 115.2,
      has_scouting_data: true,
      key_player_note: null,
      h2h_wins: 1,
      h2h_losses: 0,
      h2h_last_result: "W",
    },
    {
      opponent_id: "baxi",
      opponent_name: "Baxi Manresa",
      match_date: "2026-10-04",
      is_home: false,
      predicted_net_rating: null,
      predicted_pace: null,
      predicted_ortg: null,
      has_scouting_data: false,
      key_player_note: null,
      h2h_wins: null,
      h2h_losses: null,
      h2h_last_result: null,
    },
  ],
};

export const scheduleDifficultyEmpty = {
  games_considered: 0,
  opponents_scouted: 0,
  avg_opponent_net_rating: null,
  opponents: [],
};

export const projection = {
  team: { id: "bas", name: "Baskonia" },
  opponent: { id: "bilbao", name: "Bilbao Basket" },
  projection: {
    predicted_net_rating: 4.6,
    predicted_pace: 73.4,
    predicted_ortg: 112.0,
    expected_margin: 4.6,
  },
};

export const projectionEmpty = {
  team: { id: "bas", name: "Baskonia" },
  opponent: { id: "bilbao", name: "Bilbao Basket" },
  projection: null,
};

export const headToHead = {
  team: { id: "bas", name: "Baskonia" },
  opponent: { id: "bilbao", name: "Bilbao Basket" },
  items: [
    {
      id: "301",
      date: "2025-11-02",
      competition_name: "ACB",
      team_score: 90,
      opponent_score: 85,
      result: "W",
    },
  ],
};

export const headToHeadEmpty = {
  team: { id: "bas", name: "Baskonia" },
  opponent: { id: "bilbao", name: "Bilbao Basket" },
  items: [],
};

export const boxscore = {
  game_id: "412",
  rows: [
    {
      game_id: "412",
      player_id: "p1",
      name: "Markus Howard",
      team_id: "bas",
      minutes: 32,
      pts: 24,
      reb: 3,
      ast: 5,
      efg_pct: 66.7,
    },
    {
      game_id: "412",
      player_id: "p2",
      name: "Facundo Campazzo",
      team_id: "rm",
      minutes: 30,
      pts: 18,
      reb: 2,
      ast: 7,
      efg_pct: 55,
    },
  ],
};

export const problem = {
  type: "https://baskonia.local/errors/team-not-found",
  title: "Equipo no encontrado",
  status: 404,
  detail: "No existe ningún equipo con id 'bas' en la base de datos.",
  instance: "/api/v1/teams/bas/summary",
  request_id: "01J9F3K2QW8ZC4M7",
};

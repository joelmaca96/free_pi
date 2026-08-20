/** Fixtures derivadas de los payloads de ejemplo de doc/arquitectura/01_design.md §5.2. */

export const teams = [
  { slug: "vitoria", name: "Baskonia", league: "acb" },
  { slug: "bilbao", name: "Bilbao Basket", league: "acb" },
];

export const teamDetail = { slug: "vitoria", name: "Baskonia", league: "acb" };

export const filters = {
  seasons: [2026, 2025, 2024],
  default_season: 2025,
  leagues: [
    { code: "acb", label: "ACB" },
    { code: "euroleague", label: "Euroliga" },
    { code: "supercopa", label: "Supercopa" },
  ],
};

export const summary = {
  team: { slug: "vitoria", name: "Baskonia" },
  filters: { season: 2025, league: null },
  advanced: {
    avg_pace: 74.8,
    avg_off_rating: 112.4,
    avg_def_rating: 108.9,
    avg_net_rating: 3.5,
    avg_efg_pct: 0.5312,
    avg_ts_pct: 0.5687,
  },
  games_played: 38,
  games_upcoming: 4,
};

export const summaryEmpty = {
  team: { slug: "vitoria", name: "Baskonia" },
  filters: { season: 2026, league: null },
  advanced: {
    avg_pace: null,
    avg_off_rating: null,
    avg_def_rating: null,
    avg_net_rating: null,
    avg_efg_pct: null,
    avg_ts_pct: null,
  },
  games_played: 0,
  games_upcoming: 0,
};

export const games = {
  items: [
    {
      id: 412,
      date: "2026-05-18",
      league: "acb",
      is_home: true,
      opponent: { slug: "real-madrid", name: "Real Madrid" },
      team_score: 88,
      opponent_score: 79,
      result: "W",
      notes: null,
      advanced: { pace: 72.1, off_rating: 118.3, def_rating: 106.2, net_rating: 12.1 },
      has_boxscore: true,
    },
    {
      id: 413,
      date: "2026-06-01",
      league: "acb",
      is_home: false,
      opponent: { slug: "bilbao", name: "Bilbao Basket" },
      team_score: null,
      opponent_score: null,
      result: null,
      notes: null,
      advanced: null,
      has_boxscore: false,
    },
  ],
  total: 2,
  limit: 200,
  offset: 0,
};

export const gamesEmpty = { items: [], total: 0, limit: 200, offset: 0 };

export const roster = {
  team: { slug: "vitoria", name: "Baskonia" },
  players: [
    {
      name: "Markus Howard",
      number: "0",
      position: "PG",
      photo_url: "https://example.com/howard.png",
      form: {
        player_name: "Markus Howard",
        games: 5,
        avg_minutes: 27.4,
        avg_pts: 19.6,
        avg_pts_per36: 25.8,
        avg_efg_pct: 0.561,
        avg_ts_pct: 0.6012,
        avg_plus_minus: 4.2,
        avg_turnovers: 1.8,
        fg3a_rate: 0.641,
        ft_rate: 0.287,
      },
    },
  ],
};

export const rosterEmpty = { team: { slug: "bilbao", name: "Bilbao Basket" }, players: [] };

export const playerForm = {
  last_n: 5,
  items: [
    {
      player_name: "Markus Howard",
      games: 5,
      avg_minutes: 27.4,
      avg_pts: 19.6,
      avg_pts_per36: 25.8,
      avg_efg_pct: 0.561,
      avg_ts_pct: 0.6012,
      avg_plus_minus: 4.2,
      avg_turnovers: 1.8,
      fg3a_rate: 0.641,
      ft_rate: 0.287,
    },
  ],
};

export const playerFormEmpty = { last_n: 5, items: [] };

export const streaks = {
  season: 2025,
  recent_n: 5,
  min_season_games: 6,
  items: [
    {
      player_name: "Chima Moneke",
      games_season: 31,
      recent_avg_pts: 17.2,
      season_avg_pts: 12.1,
      season_std_pts: 4.3,
      z_score_pts: 1.19,
      recent_avg_ts_pct: 0.642,
      season_avg_ts_pct: 0.581,
      season_std_ts_pct: 0.0712,
      z_score_ts: 0.86,
      label: "hot",
    },
  ],
};

export const streaksEmpty = { season: 2026, recent_n: 5, min_season_games: 6, items: [] };

export const load = {
  window_days: 14,
  games_in_window: 5,
  note: "Carga transversal a temporada/competición (ventana de días).",
  items: [{ player_name: "Chima Moneke", games: 5, total_minutes: 148.5, avg_minutes: 29.7 }],
};

export const narrative = {
  season: 2025,
  league: null,
  recent_n: 5,
  narrative: "El Baskonia juega a un ritmo alto (74,8 posesiones).",
};

export const narrativeEmpty = { season: 2026, league: null, recent_n: 5, narrative: null };

export const scheduleDifficulty = {
  games_considered: 5,
  opponents_scouted: 3,
  avg_opponent_net_rating: 2.4,
  league: null,
  opponents: [
    { opponent_name: "Panathinaikos", date: "2026-09-30", net_rating: 6.8 },
    { opponent_name: "Baxi Manresa", date: "2026-10-04", net_rating: null },
  ],
};

export const scheduleDifficultyEmpty = {
  games_considered: 0,
  opponents_scouted: 0,
  avg_opponent_net_rating: null,
  league: null,
  opponents: [],
};

export const projection = {
  team: { slug: "vitoria", name: "Baskonia" },
  opponent: { slug: "bilbao", name: "Bilbao Basket" },
  season: 2025,
  projection: {
    projected_possessions: 73.4,
    team_projected_rating: 112.0,
    opp_projected_rating: 107.0,
    team_projected_score: 84.2,
    opp_projected_score: 79.6,
    expected_margin: 4.6,
  },
};

export const projectionEmpty = {
  team: { slug: "vitoria", name: "Baskonia" },
  opponent: { slug: "bilbao", name: "Bilbao Basket" },
  season: 2025,
  projection: null,
};

export const headToHead = {
  team: { slug: "vitoria", name: "Baskonia" },
  opponent: { slug: "bilbao", name: "Bilbao Basket" },
  items: [
    { id: 301, date: "2025-11-02", league: "acb", team_score: 90, opponent_score: 85, result: "W" },
  ],
};

export const headToHeadEmpty = {
  team: { slug: "vitoria", name: "Baskonia" },
  opponent: { slug: "bilbao", name: "Bilbao Basket" },
  items: [],
};

export const boxscore = {
  game_id: 412,
  team: { slug: "vitoria", name: "Baskonia" },
  opponent: { slug: "real-madrid", name: "Real Madrid" },
  date: "2026-05-18",
  league: "acb",
  team_score: 88,
  opponent_score: 79,
  result: "W",
  rows: [
    {
      player_name: "Markus Howard",
      minutes: "32:15",
      points: 24,
      rebounds: 3,
      assists: 5,
      steals: 1,
      blocks: 0,
      turnovers: 2,
      fg_made: 8,
      fg_attempted: 15,
      fg3_made: 4,
      fg3_attempted: 8,
      ft_made: 4,
      ft_attempted: 4,
      efg_pct: 0.667,
      ts_pct: 0.71,
    },
  ],
};

export const problem = {
  type: "https://baskonia.local/errors/team-not-found",
  title: "Equipo no encontrado",
  status: 404,
  detail: "No existe ningún equipo con slug 'valencia' en la base de datos.",
  instance: "/api/v1/teams/valencia/summary",
  request_id: "01J9F3K2QW8ZC4M7",
};

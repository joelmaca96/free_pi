-- ============================================================
-- Scouting Baskonia — modelo de base de datos
-- Dialecto: SQLite (portable a PostgreSQL con cambios menores:
-- AUTOINCREMENT -> GENERATED ALWAYS AS IDENTITY, BOOLEAN nativo, etc.)
--
-- Copia versionada de local/features/scouting_baskonia_schema.sql (fuente
-- original del diseño). Esta es la copia canónica que ejecuta el código.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ---------- Catálogos ----------

CREATE TABLE seasons (
  id    INTEGER PRIMARY KEY,
  label TEXT UNIQUE NOT NULL          -- '2025-2026'
);

CREATE TABLE competitions (
  id   INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL           -- 'ACB', 'Euroliga'
);

CREATE TABLE teams (
  id          TEXT PRIMARY KEY,       -- 'bas', 'rm', 'fcb'...
  name        TEXT NOT NULL,
  is_own_team INTEGER NOT NULL DEFAULT 0 CHECK (is_own_team IN (0,1))
);

-- Puente de identidad: un mismo equipo real (p.ej. Valencia Basket) aparece
-- con id/nombre distinto en cada fuente (ACB, Euroliga) - sin esto se crean
-- equipos duplicados al cargar competiciones distintas del mismo club.
CREATE TABLE team_external_ids (
  team_id     TEXT NOT NULL REFERENCES teams(id),
  source      TEXT NOT NULL CHECK (source IN ('baskonia_web','acb','euroleague')),
  external_id TEXT NOT NULL,
  PRIMARY KEY (source, external_id)
);
CREATE INDEX idx_team_external_team ON team_external_ids(team_id);

CREATE TABLE court_zones (
  id    INTEGER PRIMARY KEY,
  label TEXT UNIQUE NOT NULL,         -- 'Pintura', 'Triple exterior'...
  x_min REAL NOT NULL, x_max REAL NOT NULL,
  y_min REAL NOT NULL, y_max REAL NOT NULL
);

-- ---------- Plantilla ----------

CREATE TABLE players (
  id          TEXT PRIMARY KEY,       -- 'howard', 'moneke'...
  team_id     TEXT NOT NULL REFERENCES teams(id),
  name        TEXT NOT NULL,
  number      INTEGER NOT NULL,
  position    TEXT NOT NULL,          -- 'Base', 'Alero', 'Ala-pívot', 'Pívot'
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  -- Bio/foto: solo se rellena de forma fiable para la plantilla propia,
  -- vía scraper de baskonia.com. Rivales pueden quedar NULL.
  photo_url   TEXT,
  height_cm   INTEGER,
  birth_date  DATE,
  nationality TEXT
);

-- Puente de identidad: el mismo jugador tiene un id distinto en cada fuente
-- (web oficial del Baskonia, scraper ACB tipo OpenACB, euroleague_api).
-- Sin esto, el import se rompe en cuanto dos fuentes escriben el nombre
-- de forma distinta (acentos, orden de apellidos, apodos).
CREATE TABLE player_external_ids (
  player_id   TEXT NOT NULL REFERENCES players(id),
  source      TEXT NOT NULL CHECK (source IN ('baskonia_web','acb','euroleague')),
  external_id TEXT NOT NULL,
  PRIMARY KEY (source, external_id)
);
CREATE INDEX idx_player_external_player ON player_external_ids(player_id);

-- ---------- Partidos ----------

-- Neutral (home/away), no solo partidos del Baskonia: permite almacenar
-- también partidos entre otros equipos (calendario completo de un rival).
CREATE TABLE games (
  id              TEXT PRIMARY KEY,        -- 'g1'..'g5'
  season_id       INTEGER NOT NULL REFERENCES seasons(id),
  competition_id  INTEGER NOT NULL REFERENCES competitions(id),
  home_team_id    TEXT NOT NULL REFERENCES teams(id),
  away_team_id    TEXT NOT NULL REFERENCES teams(id),
  game_date       DATE NOT NULL,
  home_score      INTEGER NOT NULL,
  away_score      INTEGER NOT NULL,
  pace            REAL NOT NULL,
  narrative       TEXT,
  UNIQUE (season_id, competition_id, game_date, home_team_id, away_team_id)
);

-- Una fila por equipo y partido (antes solo la del Baskonia; un partido
-- entre dos rivales puede tener las estadísticas de ambos, o de uno solo).
-- ortg/drtg quedan nullable: en la app de origen viven en un array TREND
-- desacoplado de los partidos concretos (dato de ejemplo inconsistente);
-- aquí si se registran de verdad van ligados al partido.
CREATE TABLE game_advanced_stats (
  game_id     TEXT NOT NULL REFERENCES games(id),
  team_id     TEXT NOT NULL REFERENCES teams(id),
  ortg        REAL,
  drtg        REAL,
  net_rating  REAL,
  efg_pct     REAL NOT NULL,
  ts_pct      REAL NOT NULL,
  tov_pct     REAL NOT NULL,
  orb_pct     REAL NOT NULL,
  PRIMARY KEY (game_id, team_id)
);

-- Boxscore real por partido (hoy generado con ruido aleatorio en el
-- frontend vía boxscoreFor() — aquí sería el dato real cargado tras cada
-- partido).
CREATE TABLE player_game_stats (
  game_id   TEXT NOT NULL REFERENCES games(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  minutes   REAL NOT NULL,
  pts       INTEGER NOT NULL,
  reb       INTEGER NOT NULL,
  ast       INTEGER NOT NULL,
  efg_pct   REAL NOT NULL,
  PRIMARY KEY (game_id, player_id)
);

-- Quintetos utilizados (lineups) y su composición.
CREATE TABLE lineups (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT NOT NULL REFERENCES games(id),
  minutes     REAL NOT NULL,
  plus_minus  INTEGER NOT NULL
);

CREATE TABLE lineup_players (
  lineup_id INTEGER NOT NULL REFERENCES lineups(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  PRIMARY KEY (lineup_id, player_id)
);

-- Mapa de calor por zona de tiro (heatzones en el origen), por equipo: un
-- partido puede registrar el mapa propio y/o el del rival.
CREATE TABLE game_zone_stats (
  game_id  TEXT NOT NULL REFERENCES games(id),
  team_id  TEXT NOT NULL REFERENCES teams(id),
  zone_id  INTEGER NOT NULL REFERENCES court_zones(id),
  fg_pct   REAL NOT NULL,
  volume   INTEGER NOT NULL,
  PRIMARY KEY (game_id, team_id, zone_id)
);

-- Tiros individuales (hoy simulados con PRNG a partir de game_zone_stats;
-- en producción vendrían de un tracker real de tiro).
CREATE TABLE shots (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id   TEXT NOT NULL REFERENCES games(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  zone_id   INTEGER REFERENCES court_zones(id),
  pos_x     REAL NOT NULL,
  pos_y     REAL NOT NULL,
  made      INTEGER NOT NULL CHECK (made IN (0,1))
);

CREATE TABLE key_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT NOT NULL REFERENCES games(id),
  team_id     TEXT NOT NULL REFERENCES teams(id),  -- equipo protagonista del evento
  quarter     TEXT NOT NULL,          -- 'Q1'..'Q4'
  game_clock  TEXT NOT NULL,          -- '08:24'
  label       TEXT NOT NULL
);

-- Progresión de marcador (hoy interpolada de forma sintética entre 0 y el
-- resultado final; en producción, marcador real por posesión/minuto).
CREATE TABLE score_progression (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id      TEXT NOT NULL REFERENCES games(id),
  step_index   INTEGER NOT NULL,
  home_score   INTEGER NOT NULL,
  away_score   INTEGER NOT NULL
);

-- ---------- Próximos rivales / scouting ----------

CREATE TABLE upcoming_matchups (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  opponent_team_id      TEXT NOT NULL REFERENCES teams(id),
  competition_id        INTEGER NOT NULL REFERENCES competitions(id),
  match_date            DATE NOT NULL,
  is_home               INTEGER NOT NULL CHECK (is_home IN (0,1)),
  predicted_net_rating  REAL,
  predicted_pace        REAL,
  predicted_ortg        REAL,
  has_scouting_data     INTEGER NOT NULL DEFAULT 0 CHECK (has_scouting_data IN (0,1)),
  key_player_note       TEXT,
  h2h_wins              INTEGER,
  h2h_losses            INTEGER,
  h2h_last_result       TEXT
);

-- ---------- Medias por competición y combinadas ----------
-- En lugar de tablas manuales que se desincronizan del dato real, se
-- calculan como VISTAS sobre player_game_stats/games. Esto da automáticamente:
--   - medias por competición (para separar ACB / Euroliga / Copa del Rey / Supercopa)
--   - medias combinadas (todas las competiciones)
-- sin duplicar ni mantener nada a mano. z-scores y "streak" (forma
-- hot/cold) se calculan en la capa de aplicación a partir de estas vistas,
-- no se almacenan.

CREATE VIEW player_stats_by_competition AS
SELECT
  pgs.player_id,
  g.season_id,
  g.competition_id,
  COUNT(*)                    AS gp,
  AVG(pgs.minutes)            AS min_avg,
  AVG(pgs.pts)                AS pts_avg,
  AVG(pgs.reb)                AS reb_avg,
  AVG(pgs.ast)                AS ast_avg,
  AVG(pgs.efg_pct)            AS efg_pct
FROM player_game_stats pgs
JOIN games g ON g.id = pgs.game_id
GROUP BY pgs.player_id, g.season_id, g.competition_id;

CREATE VIEW player_stats_combined AS
SELECT
  pgs.player_id,
  g.season_id,
  COUNT(*)                    AS gp,
  AVG(pgs.minutes)            AS min_avg,
  AVG(pgs.pts)                AS pts_avg,
  AVG(pgs.reb)                AS reb_avg,
  AVG(pgs.ast)                AS ast_avg,
  AVG(pgs.efg_pct)            AS efg_pct
FROM player_game_stats pgs
JOIN games g ON g.id = pgs.game_id
GROUP BY pgs.player_id, g.season_id;

CREATE VIEW team_stats_by_competition AS
SELECT
  gas.team_id,
  g.season_id,
  g.competition_id,
  COUNT(*)                              AS gp,
  AVG(g.pace)                           AS pace,
  AVG(gas.net_rating)                   AS net_rating,
  AVG(gas.efg_pct)                      AS efg_pct,
  AVG(gas.ts_pct)                       AS ts_pct,
  AVG(gas.ortg)                         AS ortg,
  AVG(gas.drtg)                         AS drtg
FROM game_advanced_stats gas
JOIN games g ON g.id = gas.game_id
GROUP BY gas.team_id, g.season_id, g.competition_id;

CREATE VIEW team_stats_combined AS
SELECT
  gas.team_id,
  g.season_id,
  COUNT(*)                              AS gp,
  AVG(g.pace)                           AS pace,
  AVG(gas.net_rating)                   AS net_rating,
  AVG(gas.efg_pct)                      AS efg_pct,
  AVG(gas.ts_pct)                       AS ts_pct,
  AVG(gas.ortg)                         AS ortg,
  AVG(gas.drtg)                         AS drtg
FROM game_advanced_stats gas
JOIN games g ON g.id = gas.game_id
GROUP BY gas.team_id, g.season_id;

-- ---------- Índices útiles ----------

CREATE INDEX idx_games_date            ON games(game_date);
CREATE INDEX idx_games_home_team       ON games(home_team_id);
CREATE INDEX idx_games_away_team       ON games(away_team_id);
CREATE INDEX idx_pgs_player            ON player_game_stats(player_id);
CREATE INDEX idx_shots_game_player     ON shots(game_id, player_id);
CREATE INDEX idx_upcoming_date         ON upcoming_matchups(match_date);

-- ============================================================
-- DATOS SEMILLA (extraídos de la página tal cual)
-- ============================================================

INSERT INTO seasons VALUES (1, '2025-2026');

INSERT INTO competitions VALUES (1, 'ACB'), (2, 'Euroliga'), (3, 'Copa del Rey'), (4, 'Supercopa');

INSERT INTO teams (id, name, is_own_team) VALUES
  ('bas',  'Baskonia',           1),
  ('rm',   'Real Madrid',        0),
  ('fcb',  'FC Barcelona',       0),
  ('bay',  'Bayern Múnich',      0),
  ('baxi', 'BAXI Manresa',       0),
  ('jb',   'Joventut Badalona',  0),
  ('val',  'Valencia Basket',    0),
  ('gc',   'Gran Canaria',       0),
  ('uni',  'Unicaja Málaga',     0);

INSERT INTO court_zones (id, label, x_min, x_max, y_min, y_max) VALUES
  (1, 'Pintura',              195, 305, 300, 455),
  (2, 'Media dist. izq.',      60, 185, 210, 330),
  (3, 'Media dist. der.',     315, 440, 210, 330),
  (4, 'Triple esquina izq.',   15,  55, 380, 460),
  (5, 'Triple esquina der.',  445, 485, 380, 460),
  (6, 'Triple exterior',      160, 340,  50, 170);

INSERT INTO players (id, team_id, name, number, position) VALUES
  ('howard',       'bas', 'Marcus Howard',           0,  'Base'),
  ('moneke',       'bas', 'Chima Moneke',            95, 'Ala-pívot'),
  ('codi',         'bas', 'Codi Miller-McIntyre',    2,  'Base'),
  ('nikos',        'bas', 'Nikos Rogkavopoulos',     10, 'Alero'),
  ('kotsar',       'bas', 'Maik Kotsar',             21, 'Pívot'),
  ('sedekerskis',  'bas', 'Tadas Sedekerskis',       8,  'Ala-pívot'),
  ('costello',     'bas', 'Matt Costello',           33, 'Pívot'),
  ('lutse',        'bas', 'Tadas Lutse',             12, 'Alero');

-- Nota: ya no se siembran medias de jugador/equipo a mano — se obtienen
-- consultando las vistas player_stats_by_competition / player_stats_combined
-- / team_stats_by_competition / team_stats_combined una vez cargado
-- player_game_stats más abajo.

-- ---- Partidos (orden cronológico) ----
-- Todos son partidos del Baskonia en este seed (home/away según quién jugó
-- en casa), pero el esquema admite igualmente partidos entre dos rivales.

INSERT INTO games (id, season_id, competition_id, home_team_id, away_team_id, game_date, home_score, away_score, pace, narrative) VALUES
  ('g1', 1, 2, 'bas', 'rm',  '2025-12-29', 88, 82, 71.2, 'Triunfo de prestigio ante el Real Madrid. El Baskonia defendió bien el perímetro (28% de triples permitidos) y Howard cerró el partido con un parcial personal de 8 puntos en el último cuarto para sellar la victoria.'),
  ('g2', 1, 1, 'uni', 'bas', '2026-01-05', 85, 91, 73.9, 'Victoria a domicilio con un tercer cuarto decisivo (28-18). Codi Miller-McIntyre repartió 6 asistencias para dirigir el ataque y el equipo mantuvo la ventaja gracias a un 40% en triples en la segunda mitad.'),
  ('g3', 1, 2, 'bas', 'fcb', '2026-01-10', 79, 86, 70.5, 'Partido tenso decidido en los últimos cinco minutos. El Baskonia perdió el control del rebote defensivo (67.5% DREB%) y encajó un parcial de 10-0 en el tercer cuarto que no pudo remontar pese al esfuerzo de Kotsar (9 puntos, 7 rebotes) en los minutos finales.'),
  ('g4', 1, 1, 'gc',  'bas', '2026-01-15', 77, 95, 78.3, 'Paliza clara a domicilio: el Baskonia impuso su ritmo (78.3 posesiones) y anotó con comodidad desde la pintura y el contraataque. Howard y Moneke combinaron 30 puntos y el banco aportó 28, dejando el partido resuelto ya en el descanso (52-34).'),
  ('g5', 1, 1, 'bas', 'val', '2026-01-18', 84, 79, 75.1, 'El Baskonia controló el ritmo desde el segundo cuarto apoyado en un 57.2% eFG de Marcus Howard y el trabajo interior de Kotsar (11 puntos, 7 rebotes). Valencia Basket apretó en el último cuarto con triples de Puerto pero el quinteto titular sostuvo la ventaja hasta el final.');

INSERT INTO game_advanced_stats (game_id, team_id, net_rating, efg_pct, ts_pct, tov_pct, orb_pct) VALUES
  ('g1', 'bas',  7.8, 53.8, 56.7, 10.9, 22.0),
  ('g2', 'bas',  5.1, 55.6, 58.9, 12.4, 23.1),
  ('g3', 'bas', -9.4, 48.9, 51.5, 15.2, 19.8),
  ('g4', 'bas', 16.2, 61.8, 64.2,  9.5, 31.0),
  ('g5', 'bas',  8.9, 57.4, 60.1, 11.8, 24.3);

-- ---- Boxscores reales por partido (alimentan las vistas de medias) ----

INSERT INTO player_game_stats (game_id, player_id, minutes, pts, reb, ast, efg_pct) VALUES
  ('g5', 'howard', 31.2, 19, 4, 5, 57.2),
  ('g5', 'moneke', 25.9, 11, 7, 2, 56.9),
  ('g5', 'codi', 25.8, 12, 4, 6, 51.1),
  ('g5', 'nikos', 16.5, 3, 2, 1, 50.0),
  ('g5', 'kotsar', 23.3, 11, 7, 1, 62.2),
  ('g5', 'sedekerskis', 18.1, 5, 3, 1, 46.4),
  ('g5', 'costello', 16.2, 5, 5, 1, 57.8),
  ('g5', 'lutse', 15.2, 8, 3, 2, 49.6),
  ('g4', 'howard', 30.0, 17, 3, 4, 55.2),
  ('g4', 'moneke', 24.7, 9, 7, 1, 54.9),
  ('g4', 'codi', 24.6, 10, 3, 5, 49.1),
  ('g4', 'nikos', 20.7, 10, 4, 2, 57.0),
  ('g4', 'kotsar', 21.1, 8, 6, 1, 59.2),
  ('g4', 'sedekerskis', 21.7, 11, 5, 2, 52.4),
  ('g4', 'costello', 18.6, 8, 6, 1, 60.8),
  ('g4', 'lutse', 15.2, 7, 3, 2, 48.6),
  ('g3', 'howard', 25.8, 12, 1, 3, 50.2),
  ('g3', 'moneke', 30.1, 17, 10, 3, 62.9),
  ('g3', 'codi', 21.0, 7, 1, 3, 44.1),
  ('g3', 'nikos', 15.3, 3, 0, 0, 49.0),
  ('g3', 'kotsar', 25.9, 12, 9, 2, 64.2),
  ('g3', 'sedekerskis', 23.3, 11, 6, 3, 53.4),
  ('g3', 'costello', 13.8, 2, 3, 0, 54.8),
  ('g3', 'lutse', 15.2, 8, 3, 2, 49.6),
  ('g2', 'howard', 30.6, 17, 3, 5, 56.2),
  ('g2', 'moneke', 23.5, 10, 6, 1, 55.9),
  ('g2', 'codi', 27.0, 13, 4, 7, 52.1),
  ('g2', 'nikos', 15.9, 2, 1, 0, 49.0),
  ('g2', 'kotsar', 25.9, 12, 9, 2, 64.2),
  ('g2', 'sedekerskis', 17.3, 6, 2, 0, 47.4),
  ('g2', 'costello', 18.6, 8, 6, 1, 60.8),
  ('g2', 'lutse', 9.2, 0, 0, 0, 42.6),
  ('g1', 'howard', 32.4, 20, 5, 6, 58.2),
  ('g1', 'moneke', 26.5, 12, 8, 2, 57.9),
  ('g1', 'codi', 22.2, 8, 1, 4, 45.1),
  ('g1', 'nikos', 21.9, 9, 4, 2, 56.0),
  ('g1', 'kotsar', 19.1, 6, 3, 0, 54.2),
  ('g1', 'sedekerskis', 23.9, 12, 6, 3, 53.4),
  ('g1', 'costello', 19.8, 8, 6, 1, 60.8),
  ('g1', 'lutse', 9.2, 0, 0, 0, 42.6);

-- ---- Lineups ----

INSERT INTO lineups (id, game_id, minutes, plus_minus) VALUES
  (1,  'g1', 15.1,  9), (2,  'g1', 7.3,  4), (3,  'g1', 6.0,  1),
  (4,  'g2', 13.5, 10), (5,  'g2', 9.0,  5), (6,  'g2', 5.4,  2),
  (7,  'g3', 16.0, -2), (8,  'g3', 7.8, -6), (9,  'g3', 6.5, -4),
  (10, 'g4', 12.9, 18), (11, 'g4', 11.4, 14), (12, 'g4', 6.2,  9),
  (13, 'g5', 14.2, 12), (14, 'g5', 8.6,  6), (15, 'g5', 5.1, -3);

INSERT INTO lineup_players (lineup_id, player_id) VALUES
  (1,'howard'),(1,'moneke'),(1,'codi'),(1,'sedekerskis'),(1,'kotsar'),
  (2,'howard'),(2,'nikos'),(2,'moneke'),(2,'lutse'),(2,'kotsar'),
  (3,'codi'),(3,'nikos'),(3,'sedekerskis'),(3,'costello'),(3,'kotsar'),
  (4,'howard'),(4,'moneke'),(4,'codi'),(4,'sedekerskis'),(4,'kotsar'),
  (5,'howard'),(5,'nikos'),(5,'moneke'),(5,'lutse'),(5,'costello'),
  (6,'codi'),(6,'nikos'),(6,'sedekerskis'),(6,'lutse'),(6,'kotsar'),
  (7,'howard'),(7,'moneke'),(7,'codi'),(7,'sedekerskis'),(7,'kotsar'),
  (8,'howard'),(8,'nikos'),(8,'moneke'),(8,'lutse'),(8,'costello'),
  (9,'codi'),(9,'nikos'),(9,'sedekerskis'),(9,'lutse'),(9,'kotsar'),
  (10,'howard'),(10,'moneke'),(10,'codi'),(10,'sedekerskis'),(10,'kotsar'),
  (11,'codi'),(11,'nikos'),(11,'sedekerskis'),(11,'lutse'),(11,'costello'),
  (12,'howard'),(12,'nikos'),(12,'moneke'),(12,'lutse'),(12,'kotsar'),
  (13,'howard'),(13,'moneke'),(13,'codi'),(13,'sedekerskis'),(13,'kotsar'),
  (14,'howard'),(14,'nikos'),(14,'moneke'),(14,'lutse'),(14,'costello'),
  (15,'codi'),(15,'nikos'),(15,'sedekerskis'),(15,'lutse'),(15,'costello');

-- ---- Zonas de tiro por partido ----

INSERT INTO game_zone_stats (game_id, team_id, zone_id, fg_pct, volume) VALUES
  ('g1', 'bas', 1, 58, 19), ('g1', 'bas', 2, 39, 8), ('g1', 'bas', 3, 36, 7), ('g1', 'bas', 4, 41, 5), ('g1', 'bas', 5, 30, 5), ('g1', 'bas', 6, 34, 10),
  ('g2', 'bas', 1, 60, 20), ('g2', 'bas', 2, 44, 8), ('g2', 'bas', 3, 40, 7), ('g2', 'bas', 4, 45, 6), ('g2', 'bas', 5, 38, 5), ('g2', 'bas', 6, 39, 11),
  ('g3', 'bas', 1, 55, 18), ('g3', 'bas', 2, 33, 8), ('g3', 'bas', 3, 29, 9), ('g3', 'bas', 4, 25, 6), ('g3', 'bas', 5, 22, 5), ('g3', 'bas', 6, 28, 11),
  ('g4', 'bas', 1, 69, 26), ('g4', 'bas', 2, 48, 7), ('g4', 'bas', 3, 52, 6), ('g4', 'bas', 4, 50, 6), ('g4', 'bas', 5, 44, 5), ('g4', 'bas', 6, 41, 10),
  ('g5', 'bas', 1, 64, 22), ('g5', 'bas', 2, 41, 9), ('g5', 'bas', 3, 38, 8), ('g5', 'bas', 4, 47, 6), ('g5', 'bas', 5, 33, 5), ('g5', 'bas', 6, 36, 12);

-- ---- Eventos clave ----

INSERT INTO key_events (game_id, team_id, quarter, game_clock, label) VALUES
  ('g1','bas','Q1','08:00','Buen arranque defensivo'),
  ('g1','rm','Q2','05:15','Triple de Campazzo'),
  ('g1','bas','Q3','03:40','Defensa perimetral sólida'),
  ('g1','bas','Q4','01:05','8 puntos seguidos de Howard'),
  ('g2','bas','Q1','06:33','Salida ajustada 14-13'),
  ('g2','bas','Q2','03:50','6 asistencias de Codi en el cuarto'),
  ('g2','bas','Q3','01:20','Parcial decisivo de 10-2'),
  ('g2','uni','Q4','02:05','Triple de Unicaja recorta distancia'),
  ('g3','fcb','Q1','09:00','Igualado 18-18'),
  ('g3','fcb','Q2','04:12','Triple sobre la bocina del Barça'),
  ('g3','fcb','Q3','02:30','Parcial de 10-0 del Barça'),
  ('g3','bas','Q4','00:45','Tiro libre fallado en el último ataque'),
  ('g4','bas','Q1','07:10','Parcial inicial 12-2'),
  ('g4','bas','Q2','02:45','Triple de Moneke'),
  ('g4','bas','Q3','06:02','Mate de Kotsar'),
  ('g4','bas','Q4','04:20','El banco amplía la renta a +18'),
  ('g5','bas','Q1','08:24','Salida rápida 8-2'),
  ('g5','bas','Q2','03:12','Triple de Howard'),
  ('g5','val','Q3','05:40','Racha de Valencia 0-7'),
  ('g5','bas','Q4','01:14','Tapón decisivo de Kotsar');

-- ---- Próximos rivales ----

INSERT INTO upcoming_matchups (opponent_team_id, competition_id, match_date, is_home, predicted_net_rating, predicted_pace, predicted_ortg, has_scouting_data, key_player_note, h2h_wins, h2h_losses, h2h_last_result) VALUES
  ('rm',   2, '2026-08-27', 0,  9.8, 72.1, 121.5, 1, 'Facundo Campazzo — en racha (TS% +2.1z)',  2, 3, 'Real Madrid 91-84'),
  ('fcb',  2, '2026-09-03', 1, 11.2, 69.8, 119.0, 1, 'Jabari Parker — en racha (PTS +1.7z)',     1, 4, 'FC Barcelona 86-79'),
  ('bay',  2, '2026-09-10', 0,  3.4, 75.0, 114.2, 1, 'Carsen Edwards — en racha (PTS +1.4z)',    3, 1, 'Baskonia 89-83'),
  ('baxi', 1, '2026-09-17', 1, NULL, NULL, NULL,   0, NULL,                                       NULL, NULL, NULL),
  ('jb',   1, '2026-09-24', 0,  1.1, 76.4, 112.0, 1, 'Sergi Martínez — en racha (eFG% +1.3z)',   2, 2, 'Baskonia 82-80');

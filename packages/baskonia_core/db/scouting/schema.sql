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
  is_own_team INTEGER NOT NULL DEFAULT 0 CHECK (is_own_team IN (0,1)),
  -- Escudo: nullable a propósito, igual que players.photo_url. Ningún módulo
  -- de ingest/ lo puebla todavía (ver local/features/003-vista-plantilla/
  -- 01_design.md §9) — la UI debe degradar a un badge con iniciales, no a un
  -- hueco vacío, mientras esta columna esté en NULL para todos los equipos.
  logo_url    TEXT
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

-- Fila = un rectángulo del mapa de tiros, en la escala 0-500 de
-- `ingest/common/zones.py::to_court_coords`. Filas 1/4/5/6 (Pintura, las dos
-- esquinas de triple, Triple exterior) son ANCLAS geométricas: sus límites
-- exactos los reutiliza `app/components/court.py::_court_line_layers` para
-- dibujar la pintura, el aro, el círculo de tiro libre y la línea de triple
-- (arco elíptico incluido) — moverlas descuadra el dibujo de la cancha, no
-- solo la clasificación. El resto de filas se pueden reajustar libremente.
--
-- RETESELADO (2026-08-27): hasta entonces solo había 6 rectángulos con
-- huecos grandes entre ellos — verificado en vivo que dejaban entre el 21%
-- (Euroliga) y el 48% (Supercopa) de los tiros de la BD sin zona
-- (`zone_id = NULL`, fuera de `game_zone_stats`), ver
-- `local/features/006-zonas-cancha/00_request.md`. Se añaden las filas 7-9
-- para cubrir el resto de la zona ofensiva realista (x:15-485, y:50-460) casi
-- sin huecos — se dejan sin cubrir a propósito las franjas pegadas a banda
-- (x<15 o x>485 a media distancia de profundidad) y la línea de fondo detrás
-- del aro (y>455 en la columna de la pintura): son zonas fuera de banda o
-- pegadas al tablero, sin volumen real de tiro. Las filas nuevas siguen
-- siendo una aproximación de rectángulo -igual que las 6 originales-: "Ala
-- izq./der." mezclan tiros de 2 largos y triples de ala (la línea de triple
-- real es un arco, no cabe en un único corte recto sin multiplicar zonas);
-- se advierte igual que ya hacía `app/pages/proximo_rival.py` con las zonas
-- originales.
--
-- Fila 10 ("Mate") es la excepción: no es geometría real, es una etiqueta
-- para los tiros SIN coordenadas medidas (`shots.located = 0`, hoy solo los
-- mates de ACB, que llegan con el centinela `posX=posY=0` reescalado
-- exactamente al aro — ver `ingest/acb/adapter.py`). Su rectángulo es un
-- único punto que cae DENTRO de "Pintura", así que nunca se le asigna por
-- geometría (`classify_zone` no lo alcanzaría de forma fiable — sin
-- `ORDER BY`, el rectángulo que "gana" en un solape no está garantizado):
-- `ingest/common/raw_game.py` asigna este `zone_id` directamente para todo
-- tiro `located=False`, sin pasar por `classify_zone`. Existe como fila real
-- (no una constante suelta en Python) para que tenga volumen/acierto propios
-- en `game_zone_stats`, en vez de seguir apilados dentro de "Pintura" e
-- inflando su acierto con mates (~100%) mezclados con tiros de media/corta
-- distancia de verdad.
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
  -- Bio/foto: de forma AUTORIZADA (bio completa + descarga a disco) solo
  -- para la plantilla propia, vía scraper de baskonia.com
  -- (`ingest/baskonia_web`). Para un rival, `ingest/acb` rellena `photo_url`
  -- (nunca `photo_local_path`) como HOTLINK con la foto real del boxscore
  -- de acb.com si el hueco está vacío — nunca pisa la de baskonia_web. Puede
  -- seguir siendo NULL si el jugador no ha aparecido aún en ningún boxscore
  -- de ACB, o si es de Euroliga (esa fuente no expone fotos todavía).
  photo_url   TEXT,
  -- Copia local del binario de `photo_url`, descargada por
  -- `ingest/baskonia_web/scraper.py::download_player_photos` (ruta relativa
  -- a `data/`, p.ej. 'player_photos/145.jpg') — evita depender de que
  -- baskonia.com esté arriba/accesible para pintar la plantilla. NULL si no
  -- hay foto real (silueta genérica) o la descarga falló ese jugador
  -- concreto; `photo_url` sigue siendo la fuente de verdad del origen.
  photo_local_path TEXT,
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
  game_id       TEXT NOT NULL REFERENCES games(id),
  team_id       TEXT NOT NULL REFERENCES teams(id),
  ortg          REAL,
  drtg          REAL,
  net_rating    REAL,
  efg_pct       REAL NOT NULL,
  ts_pct        REAL NOT NULL,
  tov_pct       REAL NOT NULL,
  orb_pct       REAL NOT NULL,
  -- Extras fieles a los "four factors"/tasas de 04_team_stats.R de OpenACB
  -- (S_assist/S_steal/S_blocks/FT_rate/ast_to_ratio); nullable porque
  -- ast_to_ratio es indefinido si el equipo no tuvo pérdidas.
  ast_pct       REAL,
  stl_pct       REAL,
  blk_pct       REAL,
  ft_rate       REAL,
  ast_to_ratio  REAL,
  -- Tiros libres del equipo en bruto (convertidos/intentados). `ft_rate` ya
  -- estaba, pero es una TASA (FTM/FGA) y no permite reconstruir ni el volumen
  -- ni el acierto desde la línea. Nullable porque son columnas añadidas
  -- (2026-08-24) sobre bases de datos ya cargadas: los partidos ingeridos
  -- antes quedan en NULL hasta que se recargan (ver `engine.py::
  -- _ADDITIVE_COLUMN_MIGRATIONS` y `_refresh_views`). Los tiros libres del
  -- RIVAL de un partido son la fila de este mismo `game_id` con el otro
  -- `team_id` — no hay columnas "concedidas" duplicadas; las vistas de
  -- equipo los agregan con un self-join (`opp_*`).
  ftm           INTEGER,
  fta           INTEGER,
  PRIMARY KEY (game_id, team_id)
);

-- Puntos anotados/encajados por cuarto (fiel a la parte "quarters" de
-- 09_team_pace.R; los segmentos de 2 minutos y la eficiencia post-tiempo
-- muerto de ese script necesitan play-by-play con marcas de tiempo, que
-- ninguna fuente ofrece hoy - fuera de alcance, ver ingest/acb/client.py).
CREATE TABLE game_team_quarter_stats (
  game_id         TEXT NOT NULL REFERENCES games(id),
  team_id         TEXT NOT NULL REFERENCES teams(id),
  quarter         INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  points_for      INTEGER NOT NULL,
  points_against  INTEGER NOT NULL,
  PRIMARY KEY (game_id, team_id, quarter)
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
  -- Tiros libres convertidos/intentados. `efg_pct` los excluye por
  -- definición (mide solo tiro de campo), así que sin estas dos columnas no
  -- había forma de leer el juego desde la línea de ningún jugador — ni propio
  -- ni rival (los boxscores de ACB/Euroliga se cargan de LOS DOS equipos de
  -- cada partido, así que un rival tiene sus propias filas aquí igual que un
  -- jugador del Baskonia). Nullable por el mismo motivo que en
  -- `game_advanced_stats`: son columnas añadidas (2026-08-24) sobre BDs ya
  -- cargadas; NULL = partido ingerido antes de que existieran, no "0 tiros
  -- libres" — por eso las vistas cuentan aparte `gp_ft`.
  ftm       INTEGER,
  fta       INTEGER,
  PRIMARY KEY (game_id, player_id)
);

-- Quintetos utilizados (lineups) y su composición.
CREATE TABLE lineups (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id     TEXT NOT NULL REFERENCES games(id),
  minutes     REAL NOT NULL,
  plus_minus  INTEGER NOT NULL,
  -- Equipo del quinteto, explícito. Antes no existía y había que inferirlo
  -- por el `players.team_id` de sus cinco jugadores — que es el equipo ACTUAL
  -- del jugador, no el que tenía en ese partido, así que un traspaso ensuciaba
  -- retroactivamente todo agregado histórico (ver la vista `lineup_team`, que
  -- sigue existiendo como respaldo para las filas anteriores a esta columna).
  -- Nullable porque es una columna añadida (2026-08-24) sobre bases de datos
  -- ya cargadas, igual que ftm/fta: NULL = quinteto ingerido antes de que
  -- existiera, no "sin equipo". Ver `engine.py::_ADDITIVE_COLUMN_MIGRATIONS`.
  team_id     TEXT REFERENCES teams(id)
);

CREATE TABLE lineup_players (
  lineup_id INTEGER NOT NULL REFERENCES lineups(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  PRIMARY KEY (lineup_id, player_id)
);

-- Tramos (stints): un quinteto continuo en pista, con principio, fin y
-- marcador. `lineups` agrega el partido entero por combinación de cinco, así
-- que "los últimos minutos" no se podía recortar: los tramos se fundían al
-- persistir aunque `ingest/common/lineups.py` ya trabajaba en segundos de
-- partido y los adaptadores de ACB y Euroliga ya entregaban cuarto y reloj
-- por jugada. Esta tabla es ese dato que se tiraba a la basura
-- (`local/features/005-chatbot/01_design.md` §2.3 y §10.2).
--
-- `margin_start` es lo que convierte "últimos minutos" en "clutch": sin el
-- marcador al abrir el tramo, un +25 y un empate a falta de tres minutos son
-- el mismo dato, y no lo son.
CREATE TABLE lineup_stints (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id        TEXT NOT NULL REFERENCES games(id),
  team_id        TEXT NOT NULL REFERENCES teams(id),
  start_seconds  REAL NOT NULL,     -- desde el inicio del partido (game_clock_to_seconds)
  end_seconds    REAL NOT NULL,
  points_for     INTEGER NOT NULL,
  points_against INTEGER NOT NULL,
  margin_start   INTEGER NOT NULL   -- marcador (a favor - en contra) al abrir el tramo
);

CREATE TABLE lineup_stint_players (
  stint_id  INTEGER NOT NULL REFERENCES lineup_stints(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  PRIMARY KEY (stint_id, player_id)
);
CREATE INDEX idx_stints_game_team ON lineup_stints(game_id, team_id);

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

-- Tiros individuales, con las coordenadas de la fuente reescaladas a la escala
-- de `court_zones` (ver `ingest/common/zones.py::to_court_coords`).
CREATE TABLE shots (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  game_id   TEXT NOT NULL REFERENCES games(id),
  player_id TEXT NOT NULL REFERENCES players(id),
  zone_id   INTEGER REFERENCES court_zones(id),
  pos_x     REAL NOT NULL,
  pos_y     REAL NOT NULL,
  made      INTEGER NOT NULL CHECK (made IN (0,1)),
  -- 1 = `pos_x`/`pos_y` son las coordenadas MEDIDAS que dio la fuente.
  -- 0 = la fuente no las dio y son una posición inferida del tipo de tiro:
  --     hoy solo los mates de ACB, que vienen con `posX=posY=0` (el mismo
  --     centinela que los tiros libres) y se colocan en el aro. Sin esta
  --     columna, el mapa de tiros los pintaba como un tiro localizado más y
  --     no había forma de distinguirlos aguas abajo.
  -- NULL = fila anterior a esta columna (ver `engine.py::
  --     _ADDITIVE_COLUMN_MIGRATIONS`); la interfaz la trata como 1.
  located   INTEGER CHECK (located IN (0,1))
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
  -- Temporada del partido — columna añadida (2026-08-24): esta tabla era
  -- contenido de ejemplo sin ingesta real ni columna de temporada (ver
  -- `local/features/002-ajustes-interfaz/00_request.md`); ahora la puebla
  -- de verdad `ingest/acb/pipeline.py::run_upcoming` (calendario real de
  -- ACB), y necesita saber de qué temporada es cada fila para que
  -- `next_matchup` no mezcle calendarios de temporadas distintas.
  season_id             INTEGER REFERENCES seasons(id),
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
--
-- TIROS LIBRES (2026-08-24): `ftm_avg`/`fta_avg`/`ft_pct` salen de las
-- columnas nuevas `player_game_stats.ftm/fta` y `game_advanced_stats.ftm/fta`.
-- `ft_pct` es el acierto PONDERADO POR VOLUMEN (SUM(ftm)/SUM(fta)), no la
-- media de los porcentajes de cada partido — un 1/1 no puede pesar lo mismo
-- que un 8/12. `gp_ft` (partidos con dato de tiros libres) va aparte de `gp`
-- a propósito: los partidos ingeridos ANTES de que existieran esas columnas
-- quedan en NULL, y `AVG`/`SUM` los ignoran — sin `gp_ft` no habría forma de
-- distinguir "no tiró un solo libre" de "ese partido no trae el dato".
-- Las vistas de equipo añaden además la misma familia con prefijo `opp_`
-- (tiros libres del RIVAL en esos mismos partidos, vía self-join sobre
-- `game_advanced_stats`): es lo que hace falta para leer cuántos tiros
-- libres CONCEDE un equipo, no solo cuántos lanza.

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
  AVG(pgs.efg_pct)            AS efg_pct,
  COUNT(pgs.fta)              AS gp_ft,
  SUM(pgs.ftm)                AS ftm,
  SUM(pgs.fta)                AS fta,
  AVG(pgs.ftm)                AS ftm_avg,
  AVG(pgs.fta)                AS fta_avg,
  100.0 * SUM(pgs.ftm) / NULLIF(SUM(pgs.fta), 0) AS ft_pct
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
  AVG(pgs.efg_pct)            AS efg_pct,
  COUNT(pgs.fta)              AS gp_ft,
  SUM(pgs.ftm)                AS ftm,
  SUM(pgs.fta)                AS fta,
  AVG(pgs.ftm)                AS ftm_avg,
  AVG(pgs.fta)                AS fta_avg,
  100.0 * SUM(pgs.ftm) / NULLIF(SUM(pgs.fta), 0) AS ft_pct
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
  AVG(gas.drtg)                         AS drtg,
  COUNT(gas.fta)                        AS gp_ft,
  SUM(gas.ftm)                          AS ftm,
  SUM(gas.fta)                          AS fta,
  AVG(gas.ftm)                          AS ftm_avg,
  AVG(gas.fta)                          AS fta_avg,
  100.0 * SUM(gas.ftm) / NULLIF(SUM(gas.fta), 0) AS ft_pct,
  COUNT(opp.fta)                        AS gp_opp_ft,
  SUM(opp.ftm)                          AS opp_ftm,
  SUM(opp.fta)                          AS opp_fta,
  AVG(opp.ftm)                          AS opp_ftm_avg,
  AVG(opp.fta)                          AS opp_fta_avg,
  100.0 * SUM(opp.ftm) / NULLIF(SUM(opp.fta), 0) AS opp_ft_pct
FROM game_advanced_stats gas
JOIN games g ON g.id = gas.game_id
LEFT JOIN game_advanced_stats opp
       ON opp.game_id = gas.game_id AND opp.team_id <> gas.team_id
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
  AVG(gas.drtg)                         AS drtg,
  COUNT(gas.fta)                        AS gp_ft,
  SUM(gas.ftm)                          AS ftm,
  SUM(gas.fta)                          AS fta,
  AVG(gas.ftm)                          AS ftm_avg,
  AVG(gas.fta)                          AS fta_avg,
  100.0 * SUM(gas.ftm) / NULLIF(SUM(gas.fta), 0) AS ft_pct,
  COUNT(opp.fta)                        AS gp_opp_ft,
  SUM(opp.ftm)                          AS opp_ftm,
  SUM(opp.fta)                          AS opp_fta,
  AVG(opp.ftm)                          AS opp_ftm_avg,
  AVG(opp.fta)                          AS opp_fta_avg,
  100.0 * SUM(opp.ftm) / NULLIF(SUM(opp.fta), 0) AS opp_ft_pct
FROM game_advanced_stats gas
JOIN games g ON g.id = gas.game_id
LEFT JOIN game_advanced_stats opp
       ON opp.game_id = gas.game_id AND opp.team_id <> gas.team_id
GROUP BY gas.team_id, g.season_id;

-- ---------- Contexto de liga: percentiles ----------
-- "Estilo de juego" no es una columna: es una LECTURA RELATIVA. 73.6
-- posesiones no significan nada sueltas — significan algo comparadas con la
-- media de su liga. Sin este contexto, cualquier asistente que describa a un
-- equipo solo puede recitar números o, peor, adjetivarlos a ojo, que es
-- exactamente la alucinación que hay que impedir
-- (`local/features/005-chatbot/01_design.md` §6).
--
-- El `PARTITION BY` incluye la COMPETICIÓN a propósito: ACB y Euroliga son
-- ligas con ritmos distintos, y un percentil calculado mezclándolas no dice
-- nada de ninguna de las dos. `gp >= 5` evita que lidere el ranking quien ha
-- jugado un partido. (Funciones de ventana: SQLite >= 3.25.)
CREATE VIEW team_style_percentiles AS
SELECT t.team_id, t.season_id, t.competition_id, t.gp,
       t.pace, t.ortg, t.drtg, t.net_rating, t.efg_pct, t.ts_pct,
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY t.pace)       AS pace_pct,
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY t.ortg)       AS ortg_pct,
       -- DRtg se ordena INVERTIDO: menos encajado es mejor, así que p90 tiene
       -- que significar "gran defensa", no "la peor defensa de la liga".
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY -t.drtg)      AS drtg_pct,
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY t.net_rating) AS net_rating_pct,
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY t.efg_pct)    AS efg_pct_pct,
       PERCENT_RANK() OVER (PARTITION BY t.season_id, t.competition_id ORDER BY t.ts_pct)     AS ts_pct_pct,
       COUNT(*)      OVER (PARTITION BY t.season_id, t.competition_id)                        AS league_teams
FROM team_stats_by_competition t
WHERE t.gp >= 5;

-- Lo mismo para jugadores. Mismo mínimo de partidos y por el mismo motivo.
CREATE VIEW player_percentiles AS
SELECT p.player_id, p.season_id, p.competition_id, p.gp,
       p.min_avg, p.pts_avg, p.reb_avg, p.ast_avg, p.efg_pct,
       PERCENT_RANK() OVER (PARTITION BY p.season_id, p.competition_id ORDER BY p.pts_avg) AS pts_pct,
       PERCENT_RANK() OVER (PARTITION BY p.season_id, p.competition_id ORDER BY p.reb_avg) AS reb_pct,
       PERCENT_RANK() OVER (PARTITION BY p.season_id, p.competition_id ORDER BY p.ast_avg) AS ast_pct,
       PERCENT_RANK() OVER (PARTITION BY p.season_id, p.competition_id ORDER BY p.efg_pct) AS efg_pct_pct,
       PERCENT_RANK() OVER (PARTITION BY p.season_id, p.competition_id ORDER BY p.min_avg) AS min_pct,
       COUNT(*)      OVER (PARTITION BY p.season_id, p.competition_id)                     AS league_players
FROM player_stats_by_competition p
WHERE p.gp >= 5;

-- Equipo de cada quinteto, resuelto en UN solo sitio con su advertencia.
-- Prefiere `lineups.team_id` (el dato explícito, poblado por la ingesta desde
-- 2026-08-24) y solo cuando falta cae a la mayoría de `players.team_id` de
-- sus cinco jugadores — que es el equipo ACTUAL del jugador y por tanto una
-- aproximación que un traspaso estropea hacia atrás. `is_inferred` viaja con
-- la fila para que quien la use pueda decirlo en voz alta en vez de
-- disimularlo.
CREATE VIEW lineup_team AS
SELECT l.id AS lineup_id,
       l.game_id,
       l.minutes,
       l.plus_minus,
       COALESCE(l.team_id, (
         SELECT p.team_id
         FROM lineup_players lp
         JOIN players p ON p.id = lp.player_id
         WHERE lp.lineup_id = l.id
         GROUP BY p.team_id
         ORDER BY COUNT(*) DESC, p.team_id
         LIMIT 1
       )) AS team_id,
       CASE WHEN l.team_id IS NULL THEN 1 ELSE 0 END AS is_inferred
FROM lineups l;

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

-- Ver el comentario sobre `CREATE TABLE court_zones` para la geometría completa
-- (anclas 1/4/5/6 fijas, resto reteselado 2026-08-27) y qué es la fila 10.
INSERT INTO court_zones (id, label, x_min, x_max, y_min, y_max) VALUES
  (1,  'Pintura',              195, 305, 300, 455),
  (2,  'Ala izq.',              56, 194, 171, 459),
  (3,  'Ala der.',             306, 444, 171, 459),
  (4,  'Triple esquina izq.',   15,  55, 380, 460),
  (5,  'Triple esquina der.',  445, 485, 380, 460),
  (6,  'Triple exterior',      160, 340,  50, 170),
  (7,  'Media dist. central',  195, 305, 171, 299),
  (8,  'Triple ala izq.',       16, 159,  50, 170),
  (9,  'Triple ala der.',      341, 484,  50, 170),
  (10, 'Mate',                 250, 250, 455, 455);

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

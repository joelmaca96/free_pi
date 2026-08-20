import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import {
  useTeamSummary,
  useTeamGames,
  usePlayerForm,
  useStreaks,
  useNarrative,
  usePlayerLoad,
  type SeasonLeagueFilter,
} from "@/api/hooks";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { StatTable } from "@/components/StatTable";
import { QueryPanel } from "@/components/PanelState";
import { BarChart } from "@/components/charts/BarChart";
import { LastNInput } from "@/components/Filters";
import { StreakBadge } from "@/components/StreakBadge";
import { fmt, fmtPct, formatDateEs, scoreLabel, streakKind, type StreakKind } from "@/lib/format";

interface GameRow {
  id: number;
  date: string;
  opponentName: string;
  score: string;
  pace: number | null;
  offRating: number | null;
  defRating: number | null;
  netRating: number | null;
}

const gameColumns: ColumnDef<GameRow, any>[] = [
  { accessorKey: "date", header: "Fecha", cell: (c) => formatDateEs(c.getValue() as string) },
  { accessorKey: "opponentName", header: "Rival" },
  { accessorKey: "score", header: "Resultado" },
  { accessorKey: "pace", header: "Pace", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "offRating", header: "ORtg", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "defRating", header: "DRtg", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "netRating", header: "Net", cell: (c) => fmt(c.getValue() as number | null) },
];

interface FormRow {
  player: string;
  games: number;
  minutes: number | null;
  pts: number | null;
  ptsPer36: number | null;
  efg: number | null;
  ts: number | null;
  fg3aRate: number | null;
  ftRate: number | null;
}

const formColumns: ColumnDef<FormRow, any>[] = [
  { accessorKey: "player", header: "Jugador" },
  { accessorKey: "games", header: "PJ" },
  { accessorKey: "minutes", header: "MIN", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "pts", header: "PTS", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ptsPer36", header: "PTS/36", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "efg", header: "eFG%", cell: (c) => fmtPct(c.getValue() as number | null) },
  { accessorKey: "ts", header: "TS%", cell: (c) => fmtPct(c.getValue() as number | null) },
  { accessorKey: "fg3aRate", header: "3PA%", cell: (c) => fmtPct(c.getValue() as number | null) },
  { accessorKey: "ftRate", header: "FTr", cell: (c) => fmt(c.getValue() as number | null) },
];

interface StreakRow {
  player: string;
  gamesSeason: number;
  recentPts: number | null;
  seasonPts: number | null;
  zPts: number | null;
  labelPts: StreakKind;
  recentTs: number | null;
  seasonTs: number | null;
  zTs: number | null;
}

function streakColumns(recentN: number): ColumnDef<StreakRow, any>[] {
  return [
    { accessorKey: "player", header: "Jugador" },
    { accessorKey: "gamesSeason", header: "PJ temporada" },
    { accessorKey: "recentPts", header: `PTS últimos ${recentN}`, cell: (c) => fmt(c.getValue() as number | null) },
    { accessorKey: "seasonPts", header: "PTS temporada", cell: (c) => fmt(c.getValue() as number | null) },
    { accessorKey: "zPts", header: "z-score PTS", cell: (c) => fmt(c.getValue() as number | null) },
    {
      accessorKey: "labelPts",
      header: "Racha PTS",
      cell: (c) => <StreakBadge kind={c.getValue() as StreakKind} />,
    },
    { accessorKey: "recentTs", header: `TS% últimos ${recentN}`, cell: (c) => fmtPct(c.getValue() as number | null) },
    { accessorKey: "seasonTs", header: "TS% temporada", cell: (c) => fmtPct(c.getValue() as number | null) },
    { accessorKey: "zTs", header: "z-score TS%", cell: (c) => fmt(c.getValue() as number | null) },
  ];
}

interface LoadRow {
  player: string;
  games: number;
  totalMinutes: number;
  avgMinutes: number;
}

const loadColumns: ColumnDef<LoadRow, any>[] = [
  { accessorKey: "player", header: "Jugador" },
  { accessorKey: "games", header: "PJ ventana" },
  { accessorKey: "totalMinutes", header: "MIN totales", cell: (c) => fmt(c.getValue() as number) },
  { accessorKey: "avgMinutes", header: "MIN/partido", cell: (c) => fmt(c.getValue() as number) },
];

/**
 * Contenido reutilizable de la pantalla Resumen (`render_team_tab`, app.py:404-471).
 * Se usa tanto en `/{team}/resumen` como en el bloque "Scouting: {rival}" de
 * la pantalla Próximos, parametrizado por `teamSlug`.
 */
export function TeamOverviewPanel({
  teamSlug,
  filter,
  lastN,
}: {
  teamSlug: string;
  filter: SeasonLeagueFilter;
  lastN: number;
}) {
  const [windowDays, setWindowDays] = useState(14);

  const summaryQuery = useTeamSummary(teamSlug, filter);
  const gamesQuery = useTeamGames(teamSlug, filter);
  const formQuery = usePlayerForm(teamSlug, filter, lastN);
  const streaksQuery = useStreaks(teamSlug, filter.season ?? 0, filter.league, lastN);
  const narrativeQuery = useNarrative(teamSlug, filter.season ?? 0, filter.league, lastN);
  const loadQuery = usePlayerLoad(teamSlug, windowDays);

  const recentPlayed = useMemo(() => {
    const items = gamesQuery.data?.items ?? [];
    return items
      .filter((g) => g.result != null)
      .sort((a, b) => a.date.localeCompare(b.date))
      .slice(-lastN)
      .map(
        (g): GameRow => ({
          id: g.id,
          date: g.date,
          opponentName: g.opponent.name,
          score: scoreLabel(g.team_score, g.opponent_score),
          pace: g.advanced?.pace ?? null,
          offRating: g.advanced?.off_rating ?? null,
          defRating: g.advanced?.def_rating ?? null,
          netRating: g.advanced?.net_rating ?? null,
        })
      );
  }, [gamesQuery.data, lastN]);

  // "Enfrentamientos directos": rivales jugados más de una vez en el filtro
  // actual (aproximación a "equipos de interés" — la API no expone config.TEAMS).
  const recurringRivals = useMemo(() => {
    const played = (gamesQuery.data?.items ?? []).filter((g) => g.result != null);
    const counts = new Map<string, number>();
    for (const g of played) counts.set(g.opponent.slug, (counts.get(g.opponent.slug) ?? 0) + 1);
    return played
      .filter((g) => (counts.get(g.opponent.slug) ?? 0) > 1)
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(
        (g): GameRow => ({
          id: g.id,
          date: g.date,
          opponentName: g.opponent.name,
          score: scoreLabel(g.team_score, g.opponent_score),
          pace: g.advanced?.pace ?? null,
          offRating: g.advanced?.off_rating ?? null,
          defRating: g.advanced?.def_rating ?? null,
          netRating: g.advanced?.net_rating ?? null,
        })
      );
  }, [gamesQuery.data]);

  return (
    <div className="space-y-8">
      <QueryPanel
        query={narrativeQuery}
      >
        {(data) =>
          data.narrative ? (
            <section>
              <h2 className="mb-2 text-lg">Resumen automático</h2>
              <p className="text-sm">{data.narrative}</p>
            </section>
          ) : null
        }
      </QueryPanel>

      <section>
        <h2 className="mb-3 text-lg">Estadísticas avanzadas (medias)</h2>
        <QueryPanel query={summaryQuery} emptyMessage="Sin datos suficientes.">
          {(data) => (
            <StatCardRow>
              <StatCard label="Pace" value={fmt(data.advanced.avg_pace)} />
              <StatCard label="ORtg" value={fmt(data.advanced.avg_off_rating)} />
              <StatCard label="DRtg" value={fmt(data.advanced.avg_def_rating)} />
              <StatCard label="Net Rating" value={fmt(data.advanced.avg_net_rating)} />
              <StatCard label="eFG%" value={fmtPct(data.advanced.avg_efg_pct)} />
              <StatCard label="TS%" value={fmtPct(data.advanced.avg_ts_pct)} />
            </StatCardRow>
          )}
        </QueryPanel>
      </section>

      <section>
        <h2 className="mb-3 text-lg">Últimos {lastN} partidos jugados</h2>
        <QueryPanel
          query={gamesQuery}
          isEmpty={() => recentPlayed.length === 0}
          emptyMessage="Sin partidos guardados todavía."
        >
          {() => (
            <div className="space-y-4">
              <BarChart
                categories={recentPlayed.map((g) => formatDateEs(g.date))}
                series={[
                  { name: "ORtg", values: recentPlayed.map((g) => g.offRating) },
                  { name: "DRtg", values: recentPlayed.map((g) => g.defRating) },
                ]}
              />
              <StatTable data={recentPlayed} columns={gameColumns} />
            </div>
          )}
        </QueryPanel>
      </section>

      <section>
        <h2 className="mb-2 text-lg">Enfrentamientos directos</h2>
        <p className="mb-3 text-muted text-xs">
          Rivales con más de un partido jugado en el filtro actual.
        </p>
        <QueryPanel
          query={gamesQuery}
          isEmpty={() => recurringRivals.length === 0}
          emptyMessage="Sin enfrentamientos directos jugados todavía."
        >
          {() => <StatTable data={recurringRivals} columns={gameColumns} />}
        </QueryPanel>
      </section>

      <section>
        <h2 className="mb-3 text-lg">
          Forma reciente (últimos {lastN} partidos jugados)
        </h2>
        <QueryPanel
          query={formQuery}
          isEmpty={(d) => d.items.length === 0}
          emptyMessage="Sin datos suficientes."
        >
          {(data) => {
            const rows: FormRow[] = data.items.map((r) => ({
              player: r.player_name,
              games: r.games,
              minutes: r.avg_minutes ?? null,
              pts: r.avg_pts ?? null,
              ptsPer36: r.avg_pts_per36 ?? null,
              efg: r.avg_efg_pct ?? null,
              ts: r.avg_ts_pct ?? null,
              fg3aRate: r.fg3a_rate ?? null,
              ftRate: r.ft_rate ?? null,
            }));
            return (
              <div className="space-y-4">
                <BarChart
                  categories={rows.map((r) => r.player)}
                  series={[{ name: "PTS", values: rows.map((r) => r.pts) }]}
                />
                <StatTable data={rows} columns={formColumns} />
              </div>
            );
          }}
        </QueryPanel>
      </section>

      <section>
        <h2 className="mb-1 text-lg">Rachas (hot/cold)</h2>
        <QueryPanel
          query={streaksQuery}
          isEmpty={(d) => d.items.length === 0}
          emptyMessage={`Sin jugadores con partidos suficientes para calcular racha todavía.`}
        >
          {(data) => {
            const rows: StreakRow[] = data.items.map((r) => ({
              player: r.player_name,
              gamesSeason: r.games_season,
              recentPts: r.recent_avg_pts ?? null,
              seasonPts: r.season_avg_pts ?? null,
              zPts: r.z_score_pts ?? null,
              labelPts: streakKind(r.label),
              recentTs: r.recent_avg_ts_pct ?? null,
              seasonTs: r.season_avg_ts_pct ?? null,
              zTs: r.z_score_ts ?? null,
            }));
            return <StatTable data={rows} columns={streakColumns(lastN)} />;
          }}
        </QueryPanel>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg">Carga de minutos (gestión de fatiga)</h2>
          <LastNInput label="Ventana de días" value={windowDays} onChange={setWindowDays} min={1} max={30} />
        </div>
        <QueryPanel
          query={loadQuery}
          isEmpty={(d) => d.items.length === 0}
          emptyMessage={`Sin partidos jugados con minutos registrados en los últimos ${windowDays} días.`}
        >
          {(data) => {
            const rows: LoadRow[] = data.items.map((r) => ({
              player: r.player_name,
              games: r.games,
              totalMinutes: r.total_minutes,
              avgMinutes: r.avg_minutes,
            }));
            return <StatTable data={rows} columns={loadColumns} />;
          }}
        </QueryPanel>
      </section>
    </div>
  );
}

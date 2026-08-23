import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import {
  useTeamSummary,
  useTeamGames,
  usePlayerForm,
  useNarrative,
  usePlayerLoad,
  useDiscoverMissingGames,
  type SeasonFilter,
} from "@/api/hooks";
import { StatCard, StatCardRow } from "@/components/StatCard";
import { StatTable } from "@/components/StatTable";
import { EmptyState, ErrorState, QueryPanel } from "@/components/PanelState";
import { RefreshButton } from "@/components/RefreshButton";
import { BarChart } from "@/components/charts/BarChart";
import { LastNInput } from "@/components/Filters";
<<<<<<< HEAD
import { fmt, fmtPct, formatDateEs, scoreLabel } from "@/lib/format";
=======
import { StreakBadge } from "@/components/StreakBadge";
import { fmt, fmtPct, formatDateEs, scoreLabel, streakKind, type StreakKind } from "@/lib/format";
>>>>>>> 20f6eef86e1d512f27931afff85c9db2bb3ae92a

interface GameRow {
  id: string;
  date: string;
  opponentName: string;
  score: string;
  pace: number | null;
  ortg: number | null;
  drtg: number | null;
  netRating: number | null;
}

const gameColumns: ColumnDef<GameRow, any>[] = [
  { accessorKey: "date", header: "Fecha", cell: (c) => formatDateEs(c.getValue() as string) },
  { accessorKey: "opponentName", header: "Rival" },
  { accessorKey: "score", header: "Resultado" },
  { accessorKey: "pace", header: "Pace", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ortg", header: "ORtg", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "drtg", header: "DRtg", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "netRating", header: "Net", cell: (c) => fmt(c.getValue() as number | null) },
  {
    // Solo en las filas sin estadísticas avanzadas: el partido está en `games`
    // pero sus hijos no se cargaron, así que se puede reintentar la descarga.
    id: "refresh",
    header: "",
    cell: (c) =>
      c.row.original.ortg == null && c.row.original.netRating == null ? (
        <RefreshButton gameId={c.row.original.id} />
      ) : null,
  },
];

/** Fuentes de ingesta que admiten discovery/refresco bajo demanda. */
const DISCOVERY_SOURCES = [
  { value: "acb", label: "ACB" },
  { value: "euroleague", label: "Euroliga" },
] as const;

interface FormRow {
  gameDate: string;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  efg: number | null;
}

const formColumns: ColumnDef<FormRow, any>[] = [
  { accessorKey: "gameDate", header: "Fecha", cell: (c) => formatDateEs(c.getValue() as string) },
  { accessorKey: "pts", header: "PTS", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "reb", header: "REB", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "ast", header: "AST", cell: (c) => fmt(c.getValue() as number | null) },
  { accessorKey: "efg", header: "eFG%", cell: (c) => fmtPct(c.getValue() as number | null) },
];

<<<<<<< HEAD
=======
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

>>>>>>> 20f6eef86e1d512f27931afff85c9db2bb3ae92a
interface LoadRow {
  player: string;
  totalMinutes: number;
}

const loadColumns: ColumnDef<LoadRow, any>[] = [
  { accessorKey: "player", header: "Jugador" },
  { accessorKey: "totalMinutes", header: "MIN totales", cell: (c) => fmt(c.getValue() as number) },
];

/**
 * Contenido reutilizable de la pantalla Resumen (`render_team_tab`, app.py:404-471).
 * Se usa tanto en `/{team}/resumen` como en el bloque "Scouting: {rival}" de
 * la pantalla Próximos, parametrizado por `teamId`.
 */
export function TeamOverviewPanel({
  teamId,
  filter,
  lastN,
}: {
  teamId: string;
  filter: SeasonFilter;
  lastN: number;
}) {
  const [windowDays, setWindowDays] = useState(14);
  const [discoverySource, setDiscoverySource] = useState<"acb" | "euroleague">("acb");
  const discovery = useDiscoverMissingGames();

  const summaryQuery = useTeamSummary(teamId, filter);
  const gamesQuery = useTeamGames(teamId, filter);
  const formQuery = usePlayerForm(teamId, filter, lastN);
  const narrativeQuery = useNarrative(teamId);
  const loadQuery = usePlayerLoad(teamId, windowDays);

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
          pace: g.pace ?? null,
          ortg: g.advanced?.ortg ?? null,
          drtg: g.advanced?.drtg ?? null,
          netRating: g.advanced?.net_rating ?? null,
        })
      );
  }, [gamesQuery.data, lastN]);

  // "Enfrentamientos directos": rivales jugados más de una vez en el filtro
  // actual (aproximación a "equipos de interés" — la API no expone config.TEAMS).
  const recurringRivals = useMemo(() => {
    const played = (gamesQuery.data?.items ?? []).filter((g) => g.result != null);
    const counts = new Map<string, number>();
    for (const g of played) counts.set(g.opponent.id, (counts.get(g.opponent.id) ?? 0) + 1);
    return played
      .filter((g) => (counts.get(g.opponent.id) ?? 0) > 1)
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(
        (g): GameRow => ({
          id: g.id,
          date: g.date,
          opponentName: g.opponent.name,
          score: scoreLabel(g.team_score, g.opponent_score),
          pace: g.pace ?? null,
          ortg: g.advanced?.ortg ?? null,
          drtg: g.advanced?.drtg ?? null,
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
              <StatCard label="ORtg" value={fmt(data.advanced.avg_ortg)} />
              <StatCard label="DRtg" value={fmt(data.advanced.avg_drtg)} />
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
                  { name: "ORtg", values: recentPlayed.map((g) => g.ortg) },
                  { name: "DRtg", values: recentPlayed.map((g) => g.drtg) },
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
              gameDate: r.game_date,
              pts: r.pts ?? null,
              reb: r.reb ?? null,
              ast: r.ast ?? null,
              efg: r.efg_pct ?? null,
            }));
            return (
              <div className="space-y-4">
                <BarChart
                  categories={rows.map((r) => formatDateEs(r.gameDate))}
                  series={[{ name: "PTS", values: rows.map((r) => r.pts) }]}
                />
                <StatTable data={rows} columns={formColumns} />
              </div>
            );
          }}
        </QueryPanel>
      </section>

      <section>
<<<<<<< HEAD
=======
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
>>>>>>> 20f6eef86e1d512f27931afff85c9db2bb3ae92a
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
              player: r.name,
              totalMinutes: r.total_minutes,
            }));
            return <StatTable data={rows} columns={loadColumns} />;
          }}
        </QueryPanel>
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold text-slate-800">Partidos ausentes del calendario</h2>
        <p className="mb-3 text-xs text-slate-400">
          Pregunta a la fuente qué partidos jugados de la temporada seleccionada no están
          cargados todavía. Solo informa: cada partido se carga después con un clic.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs font-medium text-slate-500">
            Fuente
            <select
              className="mt-0.5 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={discoverySource}
              onChange={(e) => setDiscoverySource(e.target.value as "acb" | "euroleague")}
            >
              {DISCOVERY_SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() =>
              filter.seasonLabel != null &&
              discovery.mutate({ source: discoverySource, seasonLabel: filter.seasonLabel })
            }
            disabled={discovery.isPending || filter.seasonLabel == null}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {discovery.isPending ? "Consultando la fuente…" : "Descubrir partidos ausentes"}
          </button>
          {filter.seasonLabel == null && (
            <span className="text-xs text-slate-400">Selecciona una temporada primero.</span>
          )}
        </div>

        {discovery.isError && (
          <div className="mt-3">
            <ErrorState error={discovery.error} />
          </div>
        )}

        {discovery.data && (
          <div className="mt-3">
            {discovery.data.missing_game_ids.length === 0 ? (
              <EmptyState
                message={`No falta ningún partido de ${discoverySource} en ${discovery.data.season_label}.`}
              />
            ) : (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                {discovery.data.missing_game_ids.map((gameId) => (
                  <li key={gameId} className="flex items-center justify-between px-3 py-2 text-sm">
                    <span className="text-slate-700">{gameId}</span>
                    <RefreshButton
                      gameId={gameId}
                      seasonLabel={discovery.data.season_label}
                      label="Cargar"
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

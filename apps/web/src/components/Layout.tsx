import { NavLink, Outlet, useParams, useSearchParams } from "react-router-dom";
import { useTeam } from "@/api/hooks";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { TeamLogo } from "./TeamLogo";
import { SeasonPicker, LeagueSelect, LastNInput } from "./Filters";

const TABS = [
  { to: "resumen", label: "Resumen" },
  { to: "anteriores", label: "Partidos anteriores" },
  { to: "proximos", label: "Próximos enfrentamientos" },
  { to: "plantilla", label: "Plantilla" },
];

export function Layout() {
  const { teamSlug = "" } = useParams();
  const [searchParams] = useSearchParams();
  const teamQuery = useTeam(teamSlug);
  const filters = useGlobalFilters(teamSlug);
  const query = searchParams.toString();

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex flex-col gap-4 border-b border-slate-200 pb-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <TeamLogo slug={teamSlug} size={56} />
          <h1 className="text-2xl font-semibold text-slate-900">
            {teamQuery.data?.name ?? teamSlug}
          </h1>
        </div>
        <div className="flex flex-wrap gap-3">
          <LastNInput value={filters.lastN} onChange={filters.setLastN} />
          <SeasonPicker
            seasons={filters.filtersQuery.data?.seasons ?? []}
            value={filters.season}
            onChange={filters.setSeason}
          />
          <LeagueSelect
            leagues={filters.filtersQuery.data?.leagues ?? []}
            value={filters.league}
            onChange={filters.setLeague}
          />
        </div>
      </header>

      <nav className="mb-6 flex gap-1 border-b border-slate-200">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={{ pathname: `/${teamSlug}/${tab.to}`, search: query ? `?${query}` : "" }}
            className={({ isActive }) =>
              `rounded-t-md px-4 py-2 text-sm font-medium ${
                isActive
                  ? "border-b-2 border-slate-900 text-slate-900"
                  : "text-slate-500 hover:text-slate-700"
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <main>
        <Outlet />
      </main>
    </div>
  );
}

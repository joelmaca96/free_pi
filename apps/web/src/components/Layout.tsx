import type { ReactNode } from "react";
import { NavLink, Outlet, useParams, useSearchParams } from "react-router-dom";
import { useTeam } from "@/api/hooks";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { TeamLogo } from "./TeamLogo";
import { SeasonPicker, LeagueSelect, LastNInput } from "./Filters";

const TABS: { to: string; label: string; icon: ReactNode }[] = [
  {
    to: "resumen",
    label: "Resumen",
    icon: (
      <svg width="17" height="17" viewBox="0 0 256 256" fill="currentColor">
        <path d="M224,200h-8V40a8,8,0,0,0-8-8H152a8,8,0,0,0-8,8V80H96a8,8,0,0,0-8,8v40H48a8,8,0,0,0-8,8v64H32a8,8,0,0,0,0,16H224a8,8,0,0,0,0-16ZM160,48h40V200H160Zm-56,48h40V200H104ZM56,144H88v56H56Z" />
      </svg>
    ),
  },
  {
    to: "anteriores",
    label: "Partidos anteriores",
    icon: (
      <svg width="17" height="17" viewBox="0 0 256 256" fill="currentColor">
        <path d="M208,32H184V24a8,8,0,0,0-16,0v8H88V24a8,8,0,0,0-16,0v8H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32Zm0,176H48V96H208V208ZM208,80H48V48H72v8a8,8,0,0,0,16,0V48h80v8a8,8,0,0,0,16,0V48h24Z" />
      </svg>
    ),
  },
  {
    to: "proximos",
    label: "Próximos enfrentamientos",
    icon: (
      <svg width="17" height="17" viewBox="0 0 256 256" fill="currentColor">
        <path d="M221.66,133.66l-72,72a8,8,0,0,1-11.32-11.32L196.69,136H40a8,8,0,0,1,0-16H196.69L138.34,61.66a8,8,0,0,1,11.32-11.32l72,72A8,8,0,0,1,221.66,133.66Z" />
      </svg>
    ),
  },
  {
    to: "plantilla",
    label: "Plantilla",
    icon: (
      <svg width="17" height="17" viewBox="0 0 256 256" fill="currentColor">
        <path d="M117.25,157.92a60,60,0,1,0-66.5,0A95.83,95.83,0,0,0,3.53,195.63a8,8,0,1,0,13.4,8.74,80,80,0,0,1,134.14,0,8,8,0,0,0,13.4-8.74A95.83,95.83,0,0,0,117.25,157.92ZM40,108a44,44,0,1,1,44,44A44.05,44.05,0,0,1,40,108Zm210.14,98.7a8,8,0,0,1-11.07-2.33A79.83,79.83,0,0,0,172,168a8,8,0,0,1,0-16,44,44,0,1,0-16.34-84.87,8,8,0,1,1-5.94-14.85,60,60,0,0,1,55.53,105.64,95.83,95.83,0,0,1,47.22,37.71A8,8,0,0,1,250.14,206.7Z" />
      </svg>
    ),
  },
];

export function Layout() {
  const { teamSlug = "" } = useParams();
  const [searchParams] = useSearchParams();
  const teamQuery = useTeam(teamSlug);
  const filters = useGlobalFilters(teamSlug);
  const query = searchParams.toString();

  return (
    <div className="flex min-h-screen bg-bg text-text">
      <aside className="sticky top-0 flex h-screen w-[232px] shrink-0 flex-col gap-8 border-r border-divider bg-surface p-4 py-6">
        <div className="flex flex-col gap-2">
          <div className="h-1 w-7 rounded-full bg-brand" />
          <div className="flex items-center gap-2">
            <TeamLogo slug={teamSlug} size={24} />
            <span className="text-[19px] font-semibold tracking-tight">
              {teamQuery.data?.name ?? teamSlug}
            </span>
          </div>
          <div className="text-muted text-xs uppercase tracking-wide">
            Scouting · Cuerpo técnico
          </div>
        </div>

        <nav className="flex flex-col gap-1">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={{ pathname: `/${teamSlug}/${tab.to}`, search: query ? `?${query}` : "" }}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-accent/10 text-accent"
                    : "text-muted hover:bg-white/5 hover:text-text"
                }`
              }
            >
              {tab.icon}
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex flex-wrap items-end justify-between gap-6 border-b border-divider bg-bg px-6 py-6">
          <div>
            <h1 className="m-0 text-[26px]">{teamQuery.data?.name ?? teamSlug}</h1>
            <p className="text-muted m-0 text-sm">Panel de scouting</p>
          </div>
          <div className="flex flex-wrap items-end gap-4">
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

        <div className="flex flex-col gap-6 p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

import { useParams } from "react-router-dom";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { ExportButton } from "@/components/ExportButton";
import { TeamOverviewPanel } from "./TeamOverviewPanel";

/** `/{teamSlug}/resumen` — replica `render_team_tab`, app.py:404-471. */
export function ResumenScreen() {
  const { teamSlug = "" } = useParams();
  const filters = useGlobalFilters(teamSlug);

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <ExportButton label="📄 Generar informe en PDF" />
      </div>
      <TeamOverviewPanel
        teamSlug={teamSlug}
        filter={{ season: filters.season, league: filters.league }}
        lastN={filters.lastN}
      />
    </div>
  );
}

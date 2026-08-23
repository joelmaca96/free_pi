import { useParams } from "react-router-dom";
import { useGlobalFilters } from "@/lib/useGlobalFilters";
import { TeamOverviewPanel } from "./TeamOverviewPanel";

/** `/{teamId}/resumen` — replica `render_team_tab`, app.py:404-471. */
export function ResumenScreen() {
  const { teamId = "" } = useParams();
  const filters = useGlobalFilters(teamId);

  return (
    <div className="space-y-6">
      <TeamOverviewPanel
        teamId={teamId}
        filter={{ seasonLabel: filters.seasonLabel }}
        lastN={filters.lastN}
      />
    </div>
  );
}

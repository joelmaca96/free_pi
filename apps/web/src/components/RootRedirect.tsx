import { Navigate } from "react-router-dom";
import { useTeams } from "@/api/hooks";
import { LoadingState, ErrorState } from "./PanelState";

/** `/` -> `/{primer equipo}/resumen` (hoy siempre "vitoria", config.TEAMS[0]). */
export function RootRedirect() {
  const teamsQuery = useTeams();

  if (teamsQuery.isLoading) return <LoadingState label="Cargando equipos…" />;
  if (teamsQuery.isError) return <ErrorState error={teamsQuery.error} />;

  const first = teamsQuery.data?.[0];
  if (!first) return <ErrorState error={new Error("No hay equipos en la base de datos.")} />;

  return <Navigate to={`/${first.slug}/resumen`} replace />;
}

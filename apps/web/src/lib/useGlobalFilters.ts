import { useSearchParams } from "react-router-dom";
import { useEffect } from "react";
import { useFilters } from "@/api/hooks";

/**
 * Filtros globales (`season_label`, `competition`, `lastN`) en la query string —
 * única fuente de verdad, persisten al navegar entre pestañas y al recargar en frío.
 */
export function useGlobalFilters(teamId: string) {
  const [searchParams, setSearchParams] = useSearchParams();
  const filtersQuery = useFilters(teamId);

  const seasonLabel = searchParams.get("season_label");
  const competition = searchParams.get("competition");
  const lastNParam = searchParams.get("lastN");
  const lastN = lastNParam != null ? Number(lastNParam) : 5;

  // Si no hay `season_label` en la URL, se preselecciona `default_season` en
  // cuanto se conoce.
  useEffect(() => {
    if (seasonLabel == null && filtersQuery.data?.default_season != null) {
      const next = new URLSearchParams(searchParams);
      next.set("season_label", filtersQuery.data.default_season);
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seasonLabel, filtersQuery.data?.default_season]);

  function update(patch: Record<string, string | number | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value == null) next.delete(key);
      else next.set(key, String(value));
    }
    setSearchParams(next);
  }

  return {
    seasonLabel,
    competition,
    lastN,
    setSeason: (s: string) => update({ season_label: s }),
    setCompetition: (c: string | null) => update({ competition: c }),
    setLastN: (n: number) => update({ lastN: n }),
    filtersQuery,
  };
}

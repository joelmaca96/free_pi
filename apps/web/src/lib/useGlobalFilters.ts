import { useSearchParams } from "react-router-dom";
import { useEffect } from "react";
import { useFilters } from "@/api/hooks";

/**
 * Filtros globales (`season`, `league`, `lastN`) en la query string — única
 * fuente de verdad, persisten al navegar entre pestañas y al recargar en frío
 * (gate de salida F5). Replica los defaults del header de app.py:1049-1069.
 */
export function useGlobalFilters(teamSlug: string) {
  const [searchParams, setSearchParams] = useSearchParams();
  const filtersQuery = useFilters(teamSlug);

  const seasonParam = searchParams.get("season");
  const season = seasonParam != null ? Number(seasonParam) : null;
  const league = searchParams.get("league");
  const lastNParam = searchParams.get("lastN");
  const lastN = lastNParam != null ? Number(lastNParam) : 5;

  // Si no hay `season` en la URL, se preselecciona `default_season` en cuanto
  // se conoce (igual que `current_season()` en el header de Streamlit).
  useEffect(() => {
    if (seasonParam == null && filtersQuery.data?.default_season != null) {
      const next = new URLSearchParams(searchParams);
      next.set("season", String(filtersQuery.data.default_season));
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seasonParam, filtersQuery.data?.default_season]);

  function update(patch: Record<string, string | number | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value == null) next.delete(key);
      else next.set(key, String(value));
    }
    setSearchParams(next);
  }

  return {
    season,
    league,
    lastN,
    setSeason: (s: number) => update({ season: s }),
    setLeague: (l: string | null) => update({ league: l }),
    setLastN: (n: number) => update({ lastN: n }),
    filtersQuery,
  };
}

import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText, waitForCondition } from "./testUtils";

describe("Layout / routing", () => {
  it("recargar en frío con filtros en la URL reconstruye la vista", async () => {
    renderAt("/vitoria/resumen?season=2025&league=euroleague&lastN=5");

    await waitForText("Baskonia");
    const seasonSelect = (await screen.findByLabelText(/Temporada/i)) as HTMLSelectElement;
    const leagueSelect = screen.getByLabelText(/Competición/i) as HTMLSelectElement;
    await waitForCondition(() => seasonSelect.value === "2025");
    expect(seasonSelect.value).toBe("2025");
    expect(leagueSelect.value).toBe("euroleague");
  });

  it("preselecciona la temporada por defecto cuando no viene en la URL", async () => {
    renderAt("/vitoria/resumen");

    const seasonSelect = (await screen.findByLabelText(/Temporada/i)) as HTMLSelectElement;
    await waitForCondition(() => seasonSelect.value === "2025"); // default_season del fixture de /filters
    expect(seasonSelect.value).toBe("2025");
  });

  it("la navegación entre pestañas preserva la query string", async () => {
    renderAt("/vitoria/resumen?season=2025&league=acb&lastN=8");

    const tab = (await screen.findByRole("link", { name: "Plantilla" })) as HTMLAnchorElement;
    await waitForCondition(
      () => tab.getAttribute("href") === "/vitoria/plantilla?season=2025&league=acb&lastN=8"
    );
    expect(tab.getAttribute("href")).toBe("/vitoria/plantilla?season=2025&league=acb&lastN=8");
  });
});

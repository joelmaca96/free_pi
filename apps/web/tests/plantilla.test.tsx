import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText, waitForAllText } from "./testUtils";
import { server } from "./mocks/server";
import { notFoundHandler } from "./mocks/handlers";

describe("PlantillaScreen", () => {
  it("renderiza el mosaico y la ficha del jugador seleccionado (camino feliz)", async () => {
    renderAt("/vitoria/plantilla");

    expect(await waitForText(/Plantilla actual \(1 jugadores\)/i)).toBeInTheDocument();
    await waitForAllText(/Markus Howard/i);
    expect(await waitForText(/Posición:/i)).toBeInTheDocument();

    const exportButton = screen.getByRole("button", { name: /Generar ppt para Paolo/i });
    expect(exportButton).toBeDisabled();
  });

  it('muestra "sin plantilla" cuando el roster está vacío', async () => {
    renderAt("/bilbao/plantilla");

    expect(await waitForText(/Sin plantilla descargada todavía/i)).toBeInTheDocument();
  });

  it("muestra el estado de error con request_id cuando /roster falla", async () => {
    server.use(notFoundHandler("/teams/vitoria/roster"));
    renderAt("/vitoria/plantilla");

    expect(await waitForText(/Equipo no encontrado/i)).toBeInTheDocument();
    expect(await waitForText(/request_id/i)).toBeInTheDocument();
  });
});

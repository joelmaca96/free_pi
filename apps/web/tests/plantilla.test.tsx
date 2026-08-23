import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText, waitForAllText } from "./testUtils";
import { server } from "./mocks/server";
import { notFoundHandler } from "./mocks/handlers";

describe("PlantillaScreen", () => {
  it("renderiza el mosaico y la ficha del jugador seleccionado (camino feliz)", async () => {
    renderAt("/bas/plantilla");

    expect(await waitForText(/Plantilla actual \(1 jugadores\)/i)).toBeInTheDocument();
    await waitForAllText(/Markus Howard/i);
    expect(await waitForText(/Posición:/i)).toBeInTheDocument();
    // La tarjeta muestra las medias de temporada del roster (sin ExportButton).
    expect(await waitForText(/Estadísticas de la temporada/i)).toBeInTheDocument();
    await waitForAllText("19.6"); // pts_avg
  });

  it('muestra "sin plantilla" cuando el roster está vacío', async () => {
    renderAt("/bilbao/plantilla");

    expect(await waitForText(/Sin plantilla descargada todavía/i)).toBeInTheDocument();
  });

  it("muestra el estado de error con request_id cuando /roster falla", async () => {
    server.use(notFoundHandler("/teams/bas/roster"));
    renderAt("/bas/plantilla");

    expect(await waitForText(/Equipo no encontrado/i)).toBeInTheDocument();
    expect(await waitForText(/request_id/i)).toBeInTheDocument();
  });
});

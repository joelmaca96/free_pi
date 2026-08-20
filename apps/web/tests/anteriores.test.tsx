import { describe, expect, it } from "vitest";
import { renderAt, waitForText, waitForAllText } from "./testUtils";
import { server } from "./mocks/server";
import { notFoundHandler } from "./mocks/handlers";

describe("AnterioresScreen", () => {
  it("renderiza el partido seleccionado con su box score (camino feliz)", async () => {
    renderAt("/vitoria/anteriores");

    await waitForAllText(/Real Madrid/i);
    await waitForAllText("Markus Howard");
  });

  it('muestra "sin partidos" cuando no hay jugados', async () => {
    renderAt("/bilbao/anteriores");

    expect(await waitForText(/Sin partidos guardados todavía/i)).toBeInTheDocument();
  });

  it("muestra el estado de error con request_id cuando /games falla", async () => {
    server.use(notFoundHandler("/teams/vitoria/games"));
    renderAt("/vitoria/anteriores");

    expect(await waitForText(/Equipo no encontrado/i)).toBeInTheDocument();
    expect(await waitForText(/request_id/i)).toBeInTheDocument();
  });
});

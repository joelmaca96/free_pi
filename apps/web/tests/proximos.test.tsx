import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText } from "./testUtils";
import { server } from "./mocks/server";
import { http, HttpResponse } from "msw";
import * as f from "./mocks/fixtures";

describe("ProximosScreen", () => {
  it("renderiza dificultad de calendario, proyección y próximo rival (camino feliz)", async () => {
    renderAt("/bas/proximos");

    expect(await waitForText(/Dificultad del próximo tramo/i)).toBeInTheDocument();
    expect(await waitForText("Panathinaikos")).toBeInTheDocument();
    expect(await waitForText(/Proyección del partido/i)).toBeInTheDocument();
    expect(await waitForText(/Pace proyectado/i)).toBeInTheDocument();
  });

  it('muestra "sin datos suficientes" cuando la proyección es null', async () => {
    server.use(
      http.get("/api/v1/teams/:teamId/matchups/:opponentId/projection", () =>
        HttpResponse.json(f.projectionEmpty)
      )
    );
    renderAt("/bas/proximos");

    expect(
      await waitForText(/Datos insuficientes para proyectar el marcador/i)
    ).toBeInTheDocument();
  });

  it("muestra el panel de scouting del rival (TeamOverviewPanel) con sus datos vacíos", async () => {
    renderAt("/bas/proximos");

    // La sección "Scouting: {rival}" muestra directamente el TeamOverviewPanel
    // del rival (bilbao), que con datos vacíos pinta "Sin datos suficientes.".
    expect(await waitForText(/Scouting: Bilbao Basket/i)).toBeInTheDocument();
    expect(await waitForText(/Sin datos suficientes\./i)).toBeInTheDocument();
    // No hay botón de descarga bajo demanda (scout eliminado).
    expect(
      screen.queryByRole("button", { name: /Descargar datos de Bilbao Basket/i })
    ).not.toBeInTheDocument();
  });
});

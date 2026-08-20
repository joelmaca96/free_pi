import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText } from "./testUtils";
import { server } from "./mocks/server";
import { http, HttpResponse } from "msw";
import * as f from "./mocks/fixtures";

describe("ProximosScreen", () => {
  it("renderiza dificultad de calendario, proyección y próximo rival (camino feliz)", async () => {
    renderAt("/vitoria/proximos");

    expect(await waitForText(/Dificultad del próximo tramo/i)).toBeInTheDocument();
    expect(await waitForText("Panathinaikos")).toBeInTheDocument();
    expect(await waitForText(/Proyección del partido/i)).toBeInTheDocument();
  });

  it('muestra "sin datos suficientes" cuando la proyección es null', async () => {
    server.use(
      http.get("/api/v1/teams/:slug/matchups/:opponentSlug/projection", () =>
        HttpResponse.json(f.projectionEmpty)
      )
    );
    renderAt("/vitoria/proximos");

    expect(
      await waitForText(/Datos insuficientes para proyectar el marcador/i)
    ).toBeInTheDocument();
  });

  it("muestra estado vacío del rival cuando no tiene roster (sin botón de descarga)", async () => {
    renderAt("/vitoria/proximos");

    expect(await waitForText(/Todavía no hay datos de Bilbao Basket/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Descargar datos/i })).not.toBeInTheDocument();
  });
});

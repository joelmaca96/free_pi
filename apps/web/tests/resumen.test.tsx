import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText, waitForAllText } from "./testUtils";
import { server } from "./mocks/server";
import { notFoundHandler } from "./mocks/handlers";

describe("ResumenScreen", () => {
  it("renderiza con datos completos (camino feliz)", async () => {
    renderAt("/vitoria/resumen");

    expect(await waitForText(/Estadísticas avanzadas/i)).toBeInTheDocument();
    await waitForAllText("74.8");
    expect(await waitForText(/El Baskonia juega a un ritmo alto/i)).toBeInTheDocument();
    expect(await waitForText("Markus Howard")).toBeInTheDocument();
  });

  it('muestra "sin datos suficientes" cuando rachas/narrativa/forma están vacías', async () => {
    renderAt("/bilbao/resumen");

    expect(await waitForText(/Sin jugadores con partidos suficientes/i)).toBeInTheDocument();
    await waitForAllText("Sin datos suficientes.");
    // La narrativa (null) no debe pintar la sección "Resumen automático".
    expect(screen.queryByText("Resumen automático")).not.toBeInTheDocument();
  });

  it("muestra el estado de error con request_id cuando el resumen falla", async () => {
    server.use(notFoundHandler("/teams/vitoria/summary"));
    renderAt("/vitoria/resumen");

    expect(await waitForText(/Equipo no encontrado/i)).toBeInTheDocument();
    expect(await waitForText(/request_id/i)).toBeInTheDocument();
  });
});

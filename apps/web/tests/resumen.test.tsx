import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderAt, waitForText, waitForAllText } from "./testUtils";
import { server } from "./mocks/server";
import { notFoundHandler } from "./mocks/handlers";

describe("ResumenScreen", () => {
  it("renderiza con datos completos (camino feliz)", async () => {
    renderAt("/bas/resumen");

    expect(await waitForText(/Estadísticas avanzadas \(medias\)/i)).toBeInTheDocument();
    await waitForAllText("112.4"); // avg_ortg
    expect(await waitForText(/El Baskonia juega a un ritmo alto/i)).toBeInTheDocument();
    expect(await waitForText("Chima Moneke")).toBeInTheDocument(); // carga de minutos
  });

  it('muestra "sin datos suficientes" cuando narrativa/forma están vacías', async () => {
    renderAt("/bilbao/resumen");

    await waitForAllText("Sin datos suficientes.");
    // La narrativa (null) no debe pintar la sección "Resumen automático".
    expect(screen.queryByText("Resumen automático")).not.toBeInTheDocument();
  });

  it("muestra el estado de error con request_id cuando el resumen falla", async () => {
    server.use(notFoundHandler("/teams/bas/summary"));
    renderAt("/bas/resumen");

    expect(await waitForText(/Equipo no encontrado/i)).toBeInTheDocument();
    expect(await waitForText(/request_id/i)).toBeInTheDocument();
  });
});

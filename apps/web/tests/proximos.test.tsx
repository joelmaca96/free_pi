import { describe, expect, it } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
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

  it("muestra estado vacío del rival con botón para encolar la descarga", async () => {
    renderAt("/vitoria/proximos");

    expect(await waitForText(/Todavía no hay datos de Bilbao Basket/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Descargar datos de Bilbao Basket/i })
    ).toBeInTheDocument();
  });

  it(
    "al pulsar descargar, encola el job, hace polling y repuebla el panel al completarse",
    async () => {
      // Simula la progresión queued -> running -> done: null hasta que se
      // encola (POST), y luego un estado más avanzado en cada GET sucesivo
      // de polling. players/form deja de estar vacío cuando el job termina
      // (lo que en producción hace el worker escribiendo box scores en BD) —
      // no el roster, que solo se rellena para el equipo propio (ver
      // comentario en ProximosScreen.tsx sobre por qué no vale como señal).
      let enqueued = false;
      let pollsAfterEnqueue = 0;
      server.use(
        http.post("/api/v1/teams/:slug/scout", () => {
          enqueued = true;
          return HttpResponse.json(f.scoutJob({ status: "queued" }), { status: 202 });
        }),
        http.get("/api/v1/teams/:slug/scout", () => {
          if (!enqueued) return HttpResponse.json(null);
          pollsAfterEnqueue += 1;
          if (pollsAfterEnqueue === 1) return HttpResponse.json(f.scoutJob({ status: "running" }));
          return HttpResponse.json(f.scoutJob({ status: "done" }));
        }),
        http.get("/api/v1/teams/:slug/players/form", ({ params }) => {
          if (params.slug === "bilbao" && pollsAfterEnqueue >= 2) return HttpResponse.json(f.playerForm);
          return HttpResponse.json(f.playerFormEmpty);
        })
      );

      renderAt("/vitoria/proximos");

      const button = await waitForText(/Descargar datos de Bilbao Basket/i);
      fireEvent.click(button);

      await waitForText(/Descargando: datos de Bilbao Basket/i, 15000);
      // Al completarse el job, el roster deja de estar vacío y el panel de
      // scouting sustituye al botón de descarga por las secciones de datos.
      await waitForText(/Estadísticas avanzadas \(medias\)/i, 15000);
      expect(
        screen.queryByRole("button", { name: /Descargar datos de Bilbao Basket/i })
      ).not.toBeInTheDocument();
    },
    40000
  );
});

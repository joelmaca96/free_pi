import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen } from "@testing-library/react";
import { renderAt, waitForAllText, waitForCondition, waitForText } from "./testUtils";
import { server } from "./mocks/server";

const BASE = "/api/v1";

describe("Refresco bajo demanda (feature 014)", () => {
  it("ofrece 'Refrescar' sobre un box score vacío y dispara el POST", async () => {
    const refreshCalls: string[] = [];
    server.use(
      http.get(`${BASE}/games/:gameId/boxscore`, ({ params }) =>
        HttpResponse.json({ game_id: params.gameId, rows: [] })
      ),
      http.post(`${BASE}/games/:gameId/refresh`, ({ params, request }) => {
        refreshCalls.push(`${params.gameId}?${new URL(request.url).searchParams}`);
        return HttpResponse.json({ game_id: params.gameId, status: "triggered" }, { status: 202 });
      })
    );

    renderAt("/bas/anteriores");

    // Dos box scores por partido (local y visitante), los dos vacíos.
    await waitForAllText(/Sin box score disponible/i);
    const buttons = screen.getAllByRole("button", { name: "Refrescar" });
    fireEvent.click(buttons[0]);

    await waitForText(/Descargando de la fuente/i);
    expect(refreshCalls.length).toBe(1);
  });

  it("descubre partidos ausentes y los carga uno a uno con su season_label", async () => {
    const refreshCalls: string[] = [];
    server.use(
      http.post(`${BASE}/discovery/missing-games`, ({ request }) => {
        const query = new URL(request.url).searchParams;
        return HttpResponse.json({
          source: query.get("source"),
          season_label: query.get("season_label"),
          missing_game_ids: ["acb-105370"],
        });
      }),
      http.post(`${BASE}/games/:gameId/refresh`, ({ params, request }) => {
        refreshCalls.push(
          `${params.gameId}?${new URL(request.url).searchParams.get("season_label")}`
        );
        return HttpResponse.json({ game_id: params.gameId, status: "triggered" }, { status: 202 });
      })
    );

    renderAt("/bas/resumen");

    // El botón está deshabilitado hasta que `useGlobalFilters` resuelve la
    // temporada por defecto (el endpoint de discovery exige `season_label`).
    const findButton = () =>
      screen.queryByRole("button", { name: "Descubrir partidos ausentes" }) as
        | HTMLButtonElement
        | null;
    await waitForCondition(() => {
      const button = findButton();
      return button != null && !button.disabled;
    });
    fireEvent.click(findButton() as HTMLButtonElement);

    // Solo reporta: el partido aparece listado, pero nada se ha cargado aún.
    expect(await waitForText("acb-105370")).toBeInTheDocument();
    expect(refreshCalls).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Cargar" }));

    await waitForText(/Descargando de la fuente/i);
    expect(refreshCalls).toEqual(["acb-105370?2025-2026"]);
  });
});

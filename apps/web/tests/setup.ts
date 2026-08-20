import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/dom";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { server } from "./mocks/server";

// Margen generoso para findBy*/waitFor: el entorno de test (happy-dom + MSW)
// puede ser más lento que el navegador real, sobre todo al ejecutar varios
// ficheros de test seguidos.
configure({ asyncUtilTimeout: 10000 });

// jsdom no implementa canvas: ECharts se sustituye por un placeholder en tests.
vi.mock("echarts-for-react", () => ({
  default: () => null,
}));

// `listen`/`close` en cada test (no `beforeAll`/`afterAll`): happy-dom puede
// sustituir `globalThis.fetch` entre tests, dejando obsoleto el parche de
// MSW si el servidor solo arranca una vez por fichero.
beforeEach(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  server.close();
});

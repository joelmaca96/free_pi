import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider, Navigate } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { Layout } from "@/components/Layout";
import { ResumenScreen } from "@/features/resumen/ResumenScreen";
import { AnterioresScreen } from "@/features/anteriores/AnterioresScreen";
import { ProximosScreen } from "@/features/proximos/ProximosScreen";
import { PlantillaScreen } from "@/features/plantilla/PlantillaScreen";

/** Árbol de rutas de test, réplica de src/routes.tsx pero con `initialEntries`. */
export function renderAt(initialPath: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const router = createMemoryRouter(
    [
      {
        path: "/:teamSlug",
        element: <Layout />,
        children: [
          { index: true, element: <Navigate to="resumen" replace /> },
          { path: "resumen", element: <ResumenScreen /> },
          { path: "anteriores", element: <AnterioresScreen /> },
          { path: "proximos", element: <ProximosScreen /> },
          { path: "plantilla", element: <PlantillaScreen /> },
        ],
      },
    ],
    { initialEntries: [initialPath] }
  );

  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    ),
    router,
  };
}

/**
 * Espera un texto por polling manual con `setTimeout` en vez de
 * `findByText`/`waitFor` de Testing Library: en este entorno (happy-dom +
 * MSW) el mecanismo de reintento de `waitFor` no siempre detecta la
 * actualización del DOM tras una respuesta ya resuelta, aunque el contenido
 * llega a renderizarse correctamente (verificado con `screen.debug()`).
 */
export async function waitForText(matcher: RegExp | string, timeoutMs = 8000): Promise<Element> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const found = screen.queryByText(matcher);
    if (found) return found;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`waitForText: no se encontró "${matcher}" en ${timeoutMs}ms`);
}

/** Variante de {@link waitForText} para cuando se espera más de una coincidencia. */
export async function waitForAllText(matcher: RegExp | string, timeoutMs = 8000): Promise<Element[]> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const found = screen.queryAllByText(matcher);
    if (found.length > 0) return found;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`waitForAllText: no se encontró "${matcher}" en ${timeoutMs}ms`);
}

/** Poll genérico para condiciones arbitrarias (p.ej. sobre `queryClient`). */
export async function waitForCondition(check: () => boolean, timeoutMs = 8000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`waitForCondition: condición no cumplida en ${timeoutMs}ms`);
}

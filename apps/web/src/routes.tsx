import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { RootRedirect } from "@/components/RootRedirect";
import { ResumenScreen } from "@/features/resumen/ResumenScreen";
import { AnterioresScreen } from "@/features/anteriores/AnterioresScreen";
import { ProximosScreen } from "@/features/proximos/ProximosScreen";
import { PlantillaScreen } from "@/features/plantilla/PlantillaScreen";

export const router = createBrowserRouter([
  { path: "/", element: <RootRedirect /> },
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
]);

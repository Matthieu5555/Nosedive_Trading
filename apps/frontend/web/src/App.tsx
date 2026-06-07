import { Route, Routes } from "react-router-dom";

import { AppLayout } from "./components/AppLayout";
import { BasketPage } from "./pages/Basket";
import { ConfigPage } from "./pages/Config";
import { HealthPage } from "./pages/Health";
import { HomePage } from "./pages/Home";
import { NotFoundPage } from "./pages/NotFound";
import { RiskPage } from "./pages/Risk";
import { RunPage } from "./pages/Run";
import { StressPage } from "./pages/Stress";
import { SurfacesPage } from "./pages/Surfaces";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="health" element={<HealthPage />} />
        <Route path="surfaces" element={<SurfacesPage />} />
        <Route path="risk" element={<RiskPage />} />
        <Route path="basket" element={<BasketPage />} />
        <Route path="stress" element={<StressPage />} />
        <Route path="run" element={<RunPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

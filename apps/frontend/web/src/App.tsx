import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { Badge } from "@/ui/badge";

import { AssistantProvider } from "./components/Assistant/AssistantContext";
import { FloatingAssistant } from "./components/Assistant/FloatingAssistant";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BasketPage } from "./pages/Basket";
import { MarketPage } from "./pages/Market";
import { OperationsPage } from "./pages/Operations";
import { PositionsPage } from "./pages/Positions";
import { RiskScenariosPage } from "./pages/RiskScenarios";
import { SignalsPage } from "./pages/Signals";
import { StrategyPage } from "./pages/Strategy";
import { ROUTES } from "./routes";

function Guarded({ label, children }: { label: string; children: ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>;
}

// Inert map from route path to its guided-tour anchor id, so the nav .map can place a stable
// data-tour-id per NavLink without a per-link literal. Adds no behavior; the tour loop relies on
// these ids being present on every page.
const NAV_TOUR_ID: Record<string, string> = {
  "/": "nav.market",
  "/basket": "nav.basket",
  "/signals": "nav.signals",
  "/strategy": "nav.strategy",
  "/risk": "nav.risk",
  "/positions": "nav.positions",
  "/operations": "nav.operations",
};

const PAGES: Record<string, ReactNode> = {
  "/": <MarketPage />,
  "/basket": <BasketPage />,
  "/signals": <SignalsPage />,
  "/strategy": <StrategyPage />,
  "/risk": <RiskScenariosPage />,
  "/positions": <PositionsPage />,
  "/operations": <OperationsPage />,
};

function AppShell() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <span>AlgoTrading</span>
        </div>
        <nav className="nav" aria-label="Main">
          {ROUTES.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.end}
              data-tour-id={NAV_TOUR_ID[item.path]}
              className={({ isActive }) => (isActive ? "nav-button active" : "nav-button")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <Badge className="session-pill">Paper</Badge>
      </header>
      <main className="main">
        <Routes>
          {ROUTES.map((item) => (
            <Route
              key={item.path}
              path={item.path}
              element={<Guarded label={item.label}>{PAGES[item.path]}</Guarded>}
            />
          ))}
          {/* The short-lived 3-tab consolidation used French paths; forward them to their 7-tab
              homes so any open bookmark still lands somewhere sensible. Orders folded into Strategy. */}
          <Route path="/risque" element={<Navigate to="/basket" replace />} />
          <Route path="/ordres" element={<Navigate to="/strategy" replace />} />
          <Route path="/orders" element={<Navigate to="/strategy" replace />} />
          <Route path="/market" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {/* Mounted outside <Routes> so it persists across navigation and floats on every page. */}
      <FloatingAssistant />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AssistantProvider>
        <AppShell />
      </AssistantProvider>
    </BrowserRouter>
  );
}

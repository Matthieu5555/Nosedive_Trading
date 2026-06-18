import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { Badge } from "@/ui/badge";

import { AssistantProvider } from "./components/Assistant/AssistantContext";
import { FloatingAssistant } from "./components/Assistant/FloatingAssistant";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { featureStatus, WipPlaceholder, WipTag } from "./components/wip";
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
          {ROUTES.map((item) => {
            // A wip tab is shown but not navigable: a greyed, aria-disabled label with a WIP tag,
            // not a NavLink. The route below still resolves by URL to a placeholder.
            const flag = featureStatus(item.path);
            if (flag.status === "wip") {
              return (
                <span
                  key={item.path}
                  className="nav-button is-wip-nav"
                  aria-disabled="true"
                  data-tour-id={NAV_TOUR_ID[item.path]}
                  title={flag.reason ?? "Work in progress"}
                >
                  {item.label}
                  <WipTag reason={flag.reason} />
                </span>
              );
            }
            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.end}
                data-tour-id={NAV_TOUR_ID[item.path]}
                className={({ isActive }) => (isActive ? "nav-button active" : "nav-button")}
              >
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <Badge className="session-pill">Paper</Badge>
      </header>
      <main className="main">
        <Routes>
          {ROUTES.map((item) => {
            const flag = featureStatus(item.path);
            const page =
              flag.status === "wip" ? (
                <WipPlaceholder title={item.heading} reason={flag.reason} />
              ) : (
                PAGES[item.path]
              );
            return (
              <Route
                key={item.path}
                path={item.path}
                element={<Guarded label={item.label}>{page}</Guarded>}
              />
            );
          })}
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

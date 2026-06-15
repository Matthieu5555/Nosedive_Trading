// The operator console — Antho's tab shell over real routes. The tabs map to the roadmap's
// structure: Market = Tab 1 data foundation (index → constituents → ticker analytics), Basket =
// 2A basket Greeks + the single, store-backed order ticket (the booking chain's one home),
// Risk Scenarios = 2B stress surface (on-demand + persisted). Each page is wired to the real BFF.
//
// There is deliberately NO Orders tab: the booking chain lives entirely on Basket (compose →
// price/stress → ticket → confirm), so there is exactly one booking surface, not a duplicate
// sketch beside it (frontend-orders-booking-reconcile, ruling (b)). The legacy /orders path
// redirects to /basket so an old bookmark still lands on the real flow rather than a dead link.

import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

// Phase-2 hardening: the shell now reaches for shadcn primitives from src/ui where it does not
// disturb the hand-tuned topbar layout (guarded by e2e/layout.spec.ts). The session pill renders
// through the Badge primitive but keeps its `.session-pill` class so the existing grid placement
// CSS still governs it — Tailwind utilities and legacy CSS coexisting on one element.
import { Badge } from "@/ui/badge";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { BasketPage } from "./pages/Basket";
import { MarketPage } from "./pages/Market";
import { RiskScenariosPage } from "./pages/RiskScenarios";
import { ROUTES } from "./routes";

// Each route renders inside its own boundary, so a render error on one tab degrades to a
// labelled tile on that tab instead of unwinding the whole console to a blank screen.
function Guarded({ label, children }: { label: string; children: ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>;
}

// The route → page-component map. The route list (path/label/heading) lives in src/routes.ts —
// the single source the e2e collision net also reads — and the page component for each path is
// bound here, the one place that may import React pages. A new page = a routes.ts entry + a line
// here, and the nav, the <Routes> table and the layout/collision suite all pick it up.
const PAGES: Record<string, ReactNode> = {
  "/": <MarketPage />,
  "/basket": <BasketPage />,
  "/risk": <RiskScenariosPage />,
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
          <Route path="/market" element={<Navigate to="/" replace />} />
          {/* The Orders sketch is retired; its path redirects to the real booking home on Basket. */}
          <Route path="/orders" element={<Navigate to="/basket" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

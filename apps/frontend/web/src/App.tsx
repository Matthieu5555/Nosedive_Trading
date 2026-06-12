// The operator console — Antho's tab shell over real routes. The tabs map to the roadmap's
// structure: Market = Tab 1 data foundation (index → constituents → ticker analytics), Basket =
// 2A basket Greeks, Risk Scenarios = 2B stress surface (on-demand + persisted), Orders = the
// execution sketch (read-only, roadmap Phase 3). Each page is wired to the real BFF.

import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { BasketPage } from "./pages/Basket";
import { MarketPage } from "./pages/Market";
import { OrdersPage } from "./pages/Orders";
import { RiskScenariosPage } from "./pages/RiskScenarios";

// Each route renders inside its own boundary, so a render error on one tab degrades to a
// labelled tile on that tab instead of unwinding the whole console to a blank screen.
function Guarded({ label, children }: { label: string; children: ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>;
}

const pages: { path: string; label: string; end?: boolean }[] = [
  { path: "/", label: "Market", end: true },
  { path: "/basket", label: "Basket" },
  { path: "/risk", label: "Risk Scenarios" },
  { path: "/orders", label: "Orders" },
];

function AppShell() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <span>AlgoTrading</span>
        </div>
        <nav className="nav" aria-label="Main">
          {pages.map((item) => (
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
        <div className="session-pill">Paper</div>
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Guarded label="Market"><MarketPage /></Guarded>} />
          <Route path="/market" element={<Navigate to="/" replace />} />
          <Route path="/basket" element={<Guarded label="Basket"><BasketPage /></Guarded>} />
          <Route path="/risk" element={<Guarded label="Risk Scenarios"><RiskScenariosPage /></Guarded>} />
          <Route path="/orders" element={<Guarded label="Orders"><OrdersPage /></Guarded>} />
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

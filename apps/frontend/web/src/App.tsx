import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { Badge } from "@/ui/badge";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { BasketPage } from "./pages/Basket";
import { MarketPage } from "./pages/Market";
import { RiskScenariosPage } from "./pages/RiskScenarios";
import { ROUTES } from "./routes";

function Guarded({ label, children }: { label: string; children: ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>;
}

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
          {}
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

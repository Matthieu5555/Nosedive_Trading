import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { Badge } from "@/ui/badge";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { BasketPage } from "./pages/Basket";
import { MarketPage } from "./pages/Market";
import { OperationsPage } from "./pages/Operations";
import { OrdresPage } from "./pages/Ordres";
import { ROUTES } from "./routes";

function Guarded({ label, children }: { label: string; children: ReactNode }) {
  return <ErrorBoundary label={label}>{children}</ErrorBoundary>;
}

const PAGES: Record<string, ReactNode> = {
  "/": <MarketPage />,
  "/risque": <BasketPage />,
  "/ordres": <OrdresPage />,
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
        <div className="topbar-utility">
          {/* Operations is a secondary utility (backend observability), not a product onglet — a
              quiet link, kept addressable, deliberately outside the three top-level tabs. */}
          <NavLink
            to="/operations"
            className={({ isActive }) => (isActive ? "nav-utility active" : "nav-utility")}
          >
            Operations
          </NavLink>
          <Badge className="session-pill">Paper</Badge>
        </div>
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
          <Route
            path="/operations"
            element={<Guarded label="Operations">{<OperationsPage />}</Guarded>}
          />
          {/* Legacy paths from the 7-tab era. Risque absorbs Basket + Risk Scenarios + Positions;
              Ordres absorbs Orders + Strategy; Signals is dropped (its content lives in Données). */}
          <Route path="/market" element={<Navigate to="/" replace />} />
          <Route path="/basket" element={<Navigate to="/risque" replace />} />
          <Route path="/risk" element={<Navigate to="/risque" replace />} />
          <Route path="/positions" element={<Navigate to="/risque" replace />} />
          <Route path="/orders" element={<Navigate to="/ordres" replace />} />
          <Route path="/strategy" element={<Navigate to="/ordres" replace />} />
          <Route path="/signals" element={<Navigate to="/" replace />} />
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

import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";

import { Badge } from "@/ui/badge";

import { AssistantProvider } from "./components/Assistant/AssistantContext";
import { FloatingAssistant } from "./components/Assistant/FloatingAssistant";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { featureStatus, WipPlaceholder, WipTag } from "./components/wip";
import { tourAnchor } from "./lib/tour";
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

// The guided-tour anchor for each nav tab, declared here where the nav is rendered (the AI-first
// single source: see lib/tour/anchor.ts). The nav tabs are mounted on every page, so they are always
// in the catalog the assistant reads off the DOM, which is exactly why expect:"navigate" steps point
// at them. Spread onto both the live NavLink and the greyed wip span so either form carries the
// anchor's id, label and description.
const NAV_ANCHORS: Record<string, { id: string; label: string; description: string }> = {
  "/": {
    id: "nav.operations",
    label: "Operations tab",
    description: "Opens the Operations page, system health and whether today's data is in.",
  },
  "/market": {
    id: "nav.market",
    label: "Market tab",
    description: "Opens the Market page, where you read what the market is pricing today.",
  },
  "/basket": {
    id: "nav.basket",
    label: "Basket tab",
    description: "Opens the Basket Builder, where you compose a book of option legs and shock it.",
  },
  "/signals": {
    id: "nav.signals",
    label: "Signals tab",
    description: "Opens the Signals page, the strategy signal readings taken at the close.",
  },
  "/strategy": {
    id: "nav.strategy",
    label: "Strategy tab",
    description: "Opens the Strategy page, where you backtest a trading line over captured days.",
  },
  "/risk": {
    id: "nav.risk",
    label: "Risk Scenarios tab",
    description:
      "Opens Risk Scenarios, where you shock the book and reconcile it against the broker.",
  },
  "/positions": {
    id: "nav.positions",
    label: "Positions tab",
    description: "Opens the Positions page, what you own, what it is worth, and your risk.",
  },
};

function navAnchorProps(path: string) {
  const anchor = NAV_ANCHORS[path];
  return anchor ? tourAnchor(anchor.id, anchor.label, anchor.description) : undefined;
}

const PAGES: Record<string, ReactNode> = {
  "/": <OperationsPage />,
  "/market": <MarketPage />,
  "/basket": <BasketPage />,
  "/signals": <SignalsPage />,
  "/strategy": <StrategyPage />,
  "/risk": <RiskScenariosPage />,
  "/positions": <PositionsPage />,
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
                  {...navAnchorProps(item.path)}
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
                {...navAnchorProps(item.path)}
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
              homes so any open bookmark still lands somewhere sensible. Orders folded into Strategy.
              "/operations" forwards to "/" since Operations now owns the index route. */}
          <Route path="/risque" element={<Navigate to="/basket" replace />} />
          <Route path="/ordres" element={<Navigate to="/strategy" replace />} />
          <Route path="/orders" element={<Navigate to="/strategy" replace />} />
          <Route path="/operations" element={<Navigate to="/" replace />} />
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

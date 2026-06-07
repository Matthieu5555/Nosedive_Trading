// The operator console — Antho's three-tab shell (in-page useState nav, no router), restored
// verbatim. The tabs map to the roadmap's structure: Market = Tab 1 data foundation (index →
// constituents → ticker analytics), Risk Scenarios = Tab 2 stress surface, Orders = the
// execution sketch (read-only, roadmap Phase 3). Each page is wired to the real BFF.

import { useState } from "react";

import { MarketPage } from "./pages/Market";
import { OrdersPage } from "./pages/Orders";
import { RiskScenariosPage } from "./pages/RiskScenarios";

type Page = "market" | "risk" | "orders";

const pages: { id: Page; label: string }[] = [
  { id: "market", label: "Market" },
  { id: "risk", label: "Risk Scenarios" },
  { id: "orders", label: "Orders" },
];

export function App() {
  const [page, setPage] = useState<Page>("market");

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <span>AlgoTrading</span>
        </div>
        <nav className="nav" aria-label="Main">
          {pages.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === page ? "nav-button active" : "nav-button"}
              onClick={() => setPage(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="session-pill">Paper</div>
      </header>
      <main className="main">
        {page === "market" && <MarketPage />}
        {page === "risk" && <RiskScenariosPage />}
        {page === "orders" && <OrdersPage />}
      </main>
    </div>
  );
}

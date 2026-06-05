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

import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "Home", end: true },
  { to: "/health", label: "Health", end: false },
  { to: "/surfaces", label: "Surfaces", end: false },
  { to: "/risk", label: "Risk", end: false },
  { to: "/run", label: "Run", end: false },
  { to: "/config", label: "Config", end: false },
];

export function AppLayout() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">AlgoTrading</span>
        <nav>
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}

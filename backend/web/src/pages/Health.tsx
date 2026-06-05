import type { HealthResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { useFetch } from "../hooks/useFetch";

const FLAGS: { key: keyof HealthResponse; label: string; ok: string }[] = [
  { key: "data_flowing", label: "Data flowing", ok: "ok" },
  { key: "surfaces_building", label: "Surfaces building", ok: "ok" },
  { key: "qc_status", label: "QC", ok: "passing" },
  { key: "scenarios_current", label: "Scenarios", ok: "current" },
];

export function HealthPage() {
  const state = useFetch<HealthResponse>("/api/health");

  return (
    <section>
      <h1>System Health</h1>
      <AsyncBlock state={state}>
        {(data) => (
          <div>
            <p>
              Trade date <strong>{data.trade_date}</strong> —{" "}
              <span className={data.is_healthy ? "ok" : "bad"}>
                {data.is_healthy ? "healthy" : "attention needed"}
              </span>
            </p>
            <ul>
              {FLAGS.map((flag) => {
                const value = String(data[flag.key]);
                const good = value === flag.ok;
                return (
                  <li key={flag.key}>
                    <span className={good ? "ok" : "bad"}>{good ? "●" : "○"}</span>{" "}
                    {flag.label}: <code>{value}</code>
                  </li>
                );
              })}
            </ul>
            {data.backlog.length > 0 && (
              <p>Backlog: {data.backlog.join(", ")}</p>
            )}
          </div>
        )}
      </AsyncBlock>
    </section>
  );
}

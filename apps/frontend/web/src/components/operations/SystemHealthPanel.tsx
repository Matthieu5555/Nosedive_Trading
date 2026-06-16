import type { HealthResponse } from "../../api";
import { number, statusLabel } from "../../lib/format";
import { Metric } from "../Metric";

type Tone = "ok" | "warn" | "bad";

const TONE_CLASS: Record<Tone, string> = {
  ok: "ops-pill--ok",
  warn: "ops-pill--warn",
  bad: "ops-pill--bad",
};

function toneFor(value: string): Tone {
  const v = value.toLowerCase();
  if (["ok", "passing", "current", "healthy", "pass"].includes(v)) return "ok";
  if (["no_data", "missing", "stale", "failing", "fail", "error"].includes(v)) return "bad";
  return "warn";
}

function StatusPill({ value }: { value: string }) {
  const tone = toneFor(value);
  return (
    <span className={`ops-pill ${TONE_CLASS[tone]}`} aria-label={`status ${value}`}>
      {statusLabel(value)}
    </span>
  );
}

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <StatusPill value={value} />
    </div>
  );
}

export function SystemHealthPanel({ health }: { health: HealthResponse }) {
  const headline = health.is_healthy ? "Healthy" : "Needs attention";
  const headlineTone: Tone = health.is_healthy ? "ok" : "bad";
  return (
    <div className="ops-health">
      <div className="ops-health__headline">
        <span className={`ops-light ops-light--${headlineTone}`} aria-hidden="true" />
        <div>
          <p className="ops-headline-status">{headline}</p>
          <p className="panel-note">
            As of trade date {health.trade_date}
            {health.last_healthy_trade_date && health.last_healthy_trade_date !== health.trade_date
              ? ` · last fully-healthy day ${health.last_healthy_trade_date}`
              : ""}
          </p>
        </div>
      </div>

      <div className="metric-grid">
        <StatusMetric label="Data flowing in" value={health.data_flowing} />
        <StatusMetric label="Surfaces building" value={health.surfaces_building} />
        <StatusMetric label="Quality control" value={health.qc_status} />
        <StatusMetric label="Stress scenarios" value={health.scenarios_current} />
        <Metric
          label="Market events stored"
          value={`${number(health.events_total, 0)} events`}
        />
      </div>

      {health.backlog.length > 0 && (
        <p className="ops-backlog" role="status">
          Waiting to compute: {health.backlog.map(statusLabel).join(", ")}.
        </p>
      )}
    </div>
  );
}

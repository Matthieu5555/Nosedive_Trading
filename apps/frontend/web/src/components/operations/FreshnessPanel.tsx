import type { AvailableDate, QcVerdict, RecordedDatesResponse } from "../../api";
import { number } from "../../lib/format";
import { Scroll, Stack } from "../layout";
import { Metric } from "../Metric";

function QcBadge({ qc }: { qc: QcVerdict }) {
  const text = qc === "pass" ? "QC pass" : qc === "fail" ? "QC fail" : "QC n/a";
  return (
    <span className={`qc-badge qc-badge--${qc}`} aria-label={`QC ${qc}`}>
      {text}
    </span>
  );
}

function fetchTime(recordedTs: string | null): string {
  if (!recordedTs) return "time unknown";
  const match = recordedTs.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : recordedTs;
}

function FetchRow({ fetch }: { fetch: AvailableDate }) {
  return (
    <tr>
      <td>{fetch.date}</td>
      <td>{fetchTime(fetch.recorded_ts)}</td>
      <td>
        <QcBadge qc={fetch.qc} />
      </td>
    </tr>
  );
}

export function FreshnessPanel({ recorded }: { recorded: RecordedDatesResponse }) {
  const available = recorded.available ?? [];
  const latest = available[0] ?? null;

  if (latest === null) {
    return (
      <p className="panel-note" role="status">
        No recorded analytics for {recorded.index} yet, nothing has computed risk on this index.
      </p>
    );
  }

  const recent = available.slice(0, 5);
  return (
    <Stack className="ops-freshness" gap="md">
      <div className="metric-grid">
        <Metric label="Risk last computed for" value={latest.date} />
        <Metric label="Latest fetch landed at" value={fetchTime(latest.recorded_ts)} />
        <Metric label="Clean, gap-free days recorded" value={`${number(recorded.count, 0)} days`} />
      </div>
      <p className="panel-note">
        Latest snapshot quality: <QcBadge qc={latest.qc} />, a failing badge means the day landed
        but did not pass quality control.
      </p>
      <Scroll className="ops-table-wrap" label="Recorded analytics dates">
        <table className="ops-table">
          <thead>
            <tr>
              <th>Trade date</th>
              <th>Recorded at</th>
              <th>Quality</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((fetch) => (
              <FetchRow key={fetch.run_id ?? fetch.date} fetch={fetch} />
            ))}
          </tbody>
        </table>
      </Scroll>
    </Stack>
  );
}

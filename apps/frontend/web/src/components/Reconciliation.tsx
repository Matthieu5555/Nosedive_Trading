import type {
  ReconCashLine,
  ReconciliationResponse,
  ReconCounts,
  ReconFillLine,
  ReconPositionLine,
} from "../api";
import { sci } from "../lib/format";
import { Metric } from "./Metric";

const STATUS_LABEL: Record<string, string> = {
  match: "Match",
  break: "Break",
  broker_only: "Broker only",
  book_only: "Book only",
};

function CountStrip({ counts }: { counts: ReconCounts }) {
  return (
    <div className="quote-strip">
      <Metric label="Match" value={sci(counts.match)} />
      <Metric label="Break" value={sci(counts.break)} />
      <Metric label="Broker only" value={sci(counts.broker_only)} />
      <Metric label="Book only" value={sci(counts.book_only)} />
    </div>
  );
}

function qty(value: number | null): string {
  return value === null ? "—" : sci(value);
}

export function Reconciliation({ report }: { report: ReconciliationResponse }) {
  const positionBreaks = report.positions.lines.filter((line) => line.status !== "match");
  const fillBreaks = report.fills.lines.filter((line) => line.status !== "match");

  return (
    <article className="panel reconciliation" aria-label="Broker reconciliation">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Account {report.account_id}</p>
          <h2>Does the broker agree with our book?</h2>
        </div>
        <span className={report.ok ? "status" : "status negative"}>
          {report.ok ? "In agreement" : "Breaks found"}
        </span>
      </div>
      <p>
        We diff the broker&apos;s latest account snapshot against our own fills-based book. A{" "}
        <strong>match</strong> means they agree; a <strong>break</strong> means the quantities
        disagree; <strong>broker only</strong> / <strong>book only</strong> means one side has a
        line the other does not. Snapshot as of {report.as_of_ts}.
      </p>

      <section aria-label="Position reconciliation">
        <h3>Positions</h3>
        <CountStrip counts={report.positions.counts} />
        {positionBreaks.length === 0 ? (
          <p role="status">Every broker position matches a book position.</p>
        ) : (
          <div className="table-wrap">
          <table aria-label="Position breaks">
            <thead>
              <tr>
                <th scope="col">Contract</th>
                <th scope="col">Broker qty</th>
                <th scope="col">Book qty</th>
                <th scope="col">Difference</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {positionBreaks.map((line: ReconPositionLine) => (
                <tr key={`pos-${line.join_key}`}>
                  <th scope="row">
                    {line.broker_contract_key ?? line.book_contract_key ?? line.join_key}
                  </th>
                  <td>{qty(line.broker_quantity)}</td>
                  <td>{qty(line.book_quantity)}</td>
                  <td className="negative">{qty(line.quantity_diff)}</td>
                  <td>{STATUS_LABEL[line.status] ?? line.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>

      <section aria-label="Fill reconciliation">
        <h3>Fills</h3>
        <CountStrip counts={report.fills.counts} />
        {fillBreaks.length === 0 ? (
          <p role="status">Every broker fill matches a booked fill.</p>
        ) : (
          <div className="table-wrap">
          <table aria-label="Fill breaks">
            <thead>
              <tr>
                <th scope="col">Contract</th>
                <th scope="col">Broker qty</th>
                <th scope="col">Book qty</th>
                <th scope="col">Difference</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {fillBreaks.map((line: ReconFillLine) => (
                <tr key={`fill-${line.join_key}`}>
                  <th scope="row">
                    {line.broker_contract_key ?? line.book_contract_key ?? line.join_key}
                  </th>
                  <td>{qty(line.broker_signed_quantity)}</td>
                  <td>{qty(line.book_signed_quantity)}</td>
                  <td className="negative">{qty(line.quantity_diff)}</td>
                  <td>{STATUS_LABEL[line.status] ?? line.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>

      <section aria-label="Cash reconciliation">
        <h3>Cash (broker only)</h3>
        <p>
          Cash is informational — our fills-based book carries no cash leg, so every line is
          broker-only.
        </p>
        {report.cash.lines.length === 0 ? (
          <p role="status">No broker cash balances captured.</p>
        ) : (
          <div className="table-wrap">
          <table aria-label="Broker cash balances">
            <thead>
              <tr>
                <th scope="col">Currency</th>
                <th scope="col">Cash balance</th>
                <th scope="col">Settled cash</th>
                <th scope="col">Net liquidation</th>
              </tr>
            </thead>
            <tbody>
              {report.cash.lines.map((line: ReconCashLine) => (
                <tr key={`cash-${line.currency}`}>
                  <th scope="row">{line.currency}</th>
                  <td>{qty(line.broker_cash_balance)}</td>
                  <td>{qty(line.broker_settled_cash)}</td>
                  <td>{qty(line.broker_net_liquidation)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </article>
  );
}

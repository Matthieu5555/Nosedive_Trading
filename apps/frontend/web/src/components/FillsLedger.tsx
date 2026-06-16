import type { Fill } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";

function contractLabel(fill: Fill): string {
  const parts = fill.contract_key.split("|");
  const right = parts[8] || "";
  const strike = parts[7] || "";
  const expiry = parts[6] || "";
  if (!right && !strike) return fill.underlying;
  return `${fill.underlying} ${right} ${strike} ${expiry}`.trim();
}

export function FillsLedger({ fills, currency = "$" }: { fills: Fill[]; currency?: string }) {
  const label = "Fills ledger — the append-only execution blotter";
  if (fills.length === 0) {
    return (
      <div className="state-panel" role="status">
        No fills booked for this selection.
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <caption>{label}, with each fill's venue timestamp</caption>
        <thead>
          <tr>
            <th>Venue time</th>
            <th>Contract</th>
            <th>signed qty</th>
            <th>price</th>
            <th>mode</th>
            <th>booking</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((fill) => (
            <tr key={fill.fill_id} aria-label={`fill ${fill.fill_id}`}>
              <td>{fill.fill_ts}</td>
              <td>{contractLabel(fill)}</td>
              <td>{sci(Number(fill.signed_qty))}</td>
              <td>{sciUnit(fill.price, withCurrency("$", currency))}</td>
              <td>{fill.mode}</td>
              <td>{fill.booking_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

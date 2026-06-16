import type { PositionLine } from "../api";
import { POSITION_GREEK_ORDER } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";

function contractLabel(line: PositionLine): string {
  if (line.strike === null && line.option_right === null) return line.underlying;
  const right = line.option_right ?? "?";
  const strike = line.strike === null ? "?" : sci(line.strike);
  const expiry = line.expiry ?? "?";
  return `${line.underlying} ${right} ${strike} ${expiry}`;
}

export function PositionsTable({
  lines,
  currency = "$",
}: {
  lines: PositionLine[];
  currency?: string;
}) {
  const label = "Open positions — one row per live contract";
  if (lines.length === 0) {
    return (
      <div className="state-panel" role="status">
        No open positions in the booked book for this selection.
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <caption>{label}</caption>
        <thead>
          <tr>
            <th>Contract</th>
            <th>qty</th>
            <th>mark</th>
            <th>market value</th>
            {POSITION_GREEK_ORDER.map((name) => (
              <th key={name}>{name} $</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.contract_key} aria-label={contractLabel(line)}>
              <td>{contractLabel(line)}</td>
              <td>{sci(line.quantity)}</td>
              <td>{sciUnit(line.mark_price, withCurrency("$", currency))}</td>
              <td>{sciUnit(line.market_value, withCurrency("$", currency))}</td>
              {POSITION_GREEK_ORDER.map((name) => {
                const greek = line.greeks[name];
                return (
                  <td key={name}>{sciUnit(greek.dollar, withCurrency(greek.unit, currency))}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

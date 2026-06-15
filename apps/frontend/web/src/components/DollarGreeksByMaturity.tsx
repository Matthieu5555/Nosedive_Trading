// The per-maturity Greeks transpose (owner directive 2026-06-15): ONE maturity in view at a
// time via a selector, the Greeks as COLUMNS — each Greek a (raw, currency) pair side by side —
// and the DELTAS as ROWS, in a horizontally-scrollable table.
//
// This replaces both the per-band term-structure-adjacent matrix AND the wide strike table that
// overflowed the page far to the right: one maturity's grid is narrow enough to read, and the
// selector keeps the whole Greeks block to a single bounded table no matter how many tenors or
// delta bands the capture carries.
//
// Render-layer honesty: a row seated on a railed slice (its implied vol outside the sane band)
// is flagged inline (⚠) and rendered with its served values intact — we never mutate the data,
// we only mark it so a reader does not mistake a railed-slice Greek for a clean one. The
// scientific-notation + currency helpers are reused verbatim (no per-component formatting).

import { useEffect, useState } from "react";

import type { AnalyticsMaturity, AnalyticsPoint } from "../api";
import { sci, UNITS, withCurrency } from "../lib/format";
import { isSaneIv } from "../lib/volRobust";

// The Greek columns, in the owner's reading order, each carrying its raw mathematical unit. The
// $-Greek (currency) unit travels from the backend on the metric and is rendered per cell.
const GREEKS: ReadonlyArray<{ name: keyof AnalyticsPoint["metrics"]; rawUnit: string }> = [
  { name: "delta", rawUnit: UNITS.delta },
  { name: "gamma", rawUnit: UNITS.gamma },
  { name: "vega", rawUnit: UNITS.vega },
  { name: "theta", rawUnit: UNITS.theta },
  { name: "rho", rawUnit: UNITS.rho },
];

// The first non-null currency-unit string a Greek carries across the maturity's points, in the
// index's quote currency — shown once in the column header, never re-derived per cell.
function currencyUnitFor(
  points: AnalyticsPoint[],
  name: keyof AnalyticsPoint["metrics"],
  currency: string,
): string {
  const unit = points.map((p) => p.metrics[name].unit).find((u) => u !== null && u !== undefined);
  return withCurrency(unit ?? null, currency) ?? "n/a";
}

export function DollarGreeksByMaturity({
  maturities,
  currency = "$",
}: {
  maturities: AnalyticsMaturity[];
  currency?: string;
}) {
  const label = "Per-maturity dollar Greeks (Greeks as columns, deltas as rows)";

  // The selected maturity, by its stable label. Keep the selection valid if the maturity set
  // changes under it (index/date switch) by snapping back to the first available one.
  const [selected, setSelected] = useState<string>(() => maturities[0]?.label ?? "");
  useEffect(() => {
    if (maturities.length === 0) return;
    if (!maturities.some((m) => m.label === selected)) {
      setSelected(maturities[0].label);
    }
  }, [maturities, selected]);

  if (maturities.length === 0) {
    return (
      <section aria-label={label} className="greeks-by-maturity">
        <h3>{label}</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </section>
    );
  }

  const maturity = maturities.find((m) => m.label === selected) ?? maturities[0];
  // Delta bands as ROWS, ordered put → ATM → call (signed target delta — the smile's reading
  // order), so the table reads left wing → ATM → right wing top-to-bottom.
  const rows = [...maturity.points].sort((a, b) => a.target_delta - b.target_delta);

  return (
    <section aria-label={label} className="greeks-by-maturity">
      <h3>{label}</h3>
      <div className="greeks-by-maturity-controls">
        <label>
          Maturity{" "}
          <select
            aria-label="Greeks maturity"
            value={maturity.label}
            onChange={(event) => setSelected(event.target.value)}
          >
            {maturities.map((m) => (
              <option key={m.label} value={m.label}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {rows.length === 0 ? (
        <p>No projected analytics for {maturity.label} yet.</p>
      ) : (
        <div className="greeks-by-maturity-scroll">
          <table aria-label={`Dollar Greeks — ${maturity.label}`}>
            <caption>
              Dollar Greeks — {maturity.label} (each Greek: raw and {currency} value; rows are
              delta bands)
            </caption>
            <thead>
              <tr>
                <th rowSpan={2} scope="col">
                  delta band
                </th>
                {GREEKS.map((greek) => (
                  <th key={greek.name} colSpan={2} scope="colgroup" className="greek-group">
                    {greek.name}
                  </th>
                ))}
              </tr>
              <tr>
                {GREEKS.map((greek) => [
                  <th key={`${greek.name}-raw`} scope="col">
                    raw <span className="unit">{withCurrency(greek.rawUnit, currency)}</span>
                  </th>,
                  <th key={`${greek.name}-ccy`} scope="col">
                    {currency} value
                    <span className="unit">{currencyUnitFor(rows, greek.name, currency)}</span>
                  </th>,
                ])}
              </tr>
            </thead>
            <tbody>
              {rows.map((point) => {
                // A row on a railed slice (IV outside the sane band) is rendered with its served
                // values intact but flagged, so a reader doesn't mistake it for a clean fit.
                const flagged = !isSaneIv(point.implied_vol);
                return (
                  <tr key={point.delta_band} className={flagged ? "flagged-row" : undefined}>
                    <th scope="row">
                      {point.delta_band}
                      {flagged ? <span title="railed slice — IV outside sane band"> ⚠</span> : null}
                    </th>
                    {GREEKS.map((greek) => {
                      const metric = point.metrics[greek.name];
                      return [
                        <td key={`${greek.name}-raw`}>{sci(metric.raw)}</td>,
                        <td key={`${greek.name}-ccy`}>{sci(metric.dollar)}</td>,
                      ];
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

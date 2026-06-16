import { ALL_MATURITIES, type AnalyticsMaturity, type AnalyticsPoint, type OptionSide } from "../api";
import { sci, UNITS, withCurrency } from "../lib/format";
import { isSaneIv } from "../lib/volRobust";

const GREEKS: ReadonlyArray<{ name: keyof AnalyticsPoint["metrics"]; rawUnit: string }> = [
  { name: "delta", rawUnit: UNITS.delta },
  { name: "gamma", rawUnit: UNITS.gamma },
  { name: "vega", rawUnit: UNITS.vega },
  { name: "theta", rawUnit: UNITS.theta },
  { name: "rho", rawUnit: UNITS.rho },
];

function currencyUnitFor(
  points: AnalyticsPoint[],
  name: keyof AnalyticsPoint["metrics"],
  currency: string,
): string {
  const unit = points.map((p) => p.metrics[name].unit).find((u) => u !== null && u !== undefined);
  return withCurrency(unit ?? null, currency) ?? "n/a";
}

// A row is at-the-money when its band names ATM (the structurally important row — the spot pivot
// the eye should land on first). The convention is "ATM"; we match it case-insensitively.
function isAtmBand(band: string): boolean {
  return band.toUpperCase().includes("ATM");
}

// The sign of a Greek's raw value at a row, ignoring ~zero (which is neither + nor −). Used to
// mark the row where a Greek flips sign as you walk down the delta bands — the put→call crossover,
// which is the other place an operator's eye should be drawn.
function rawSign(value: number): -1 | 0 | 1 {
  if (!Number.isFinite(value) || Math.abs(value) < 1e-12) return 0;
  return value > 0 ? 1 : -1;
}

export function DollarGreeksByMaturity({
  maturities,
  maturityLabel,
  side,
  currency = "$",
}: {
  maturities: AnalyticsMaturity[];
  // The maturity in view, driven by the shared selector strip (no longer a per-panel dropdown).
  maturityLabel?: string;
  // The put/call switch keeps only the matching delta bands (ATM shared).
  side?: OptionSide;
  currency?: string;
}) {
  const label = "Dollar Greeks by delta band (Greeks as columns, delta bands as rows)";

  if (maturities.length === 0) {
    return (
      <section aria-label={label} className="greeks-by-maturity">
        <h3>Dollar Greeks by delta band</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </section>
    );
  }

  // The table is inherently one tenor. "All maturities" has no single column, so it reads the
  // front (shortest) tenor and says so — the term-structure curves above already span every tenor.
  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;
  const frontMaturity = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years)[0];
  const maturity = isAll
    ? frontMaturity
    : (maturities.find((m) => m.label === maturityLabel) ?? frontMaturity);

  const rows = [...maturity.points]
    .filter((p) => {
      if (side === "put") return p.target_delta <= 0;
      if (side === "call") return p.target_delta >= 0;
      return true;
    })
    .sort((a, b) => a.target_delta - b.target_delta);

  // Pre-compute the sign-flip rows: walking down the sorted bands, a row flips when any Greek's
  // sign differs from the last non-zero sign seen for that Greek above it.
  const lastSign: Partial<Record<keyof AnalyticsPoint["metrics"], -1 | 1>> = {};
  const flipRows = new Set<string>();
  for (const point of rows) {
    let flipped = false;
    for (const { name } of GREEKS) {
      const sign = rawSign(point.metrics[name].raw);
      if (sign === 0) continue;
      const prev = lastSign[name];
      if (prev !== undefined && prev !== sign) flipped = true;
      lastSign[name] = sign;
    }
    if (flipped) flipRows.add(point.delta_band);
  }

  return (
    <section aria-label={label} className="greeks-by-maturity">
      <div className="greeks-by-maturity-heading">
        <h3>
          Dollar Greeks — {maturity.label}
          {isAll ? " (front month)" : ""}
        </h3>
        <p className="panel-note">
          Greeks across, delta bands down · raw and {currency} value · ATM and sign-flip rows
          highlighted
        </p>
      </div>
      {rows.length === 0 ? (
        <p>No projected analytics for {maturity.label} yet.</p>
      ) : (
        <div className="greeks-by-maturity-scroll">
          <table aria-label={`Dollar Greeks — ${maturity.label}`}>
            <caption className="visually-hidden">
              Dollar Greeks for {maturity.label}: each Greek as raw and {currency} value, one row per
              delta band.
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
                const atm = isAtmBand(point.delta_band);
                const flip = flipRows.has(point.delta_band);
                const className = [
                  flagged ? "flagged-row" : "",
                  atm ? "greeks-row--atm" : "",
                  flip ? "greeks-row--flip" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <tr key={point.delta_band} className={className || undefined}>
                    <th scope="row">
                      {point.delta_band}
                      {atm ? <span title="at-the-money"> ●</span> : null}
                      {flip ? <span title="sign flip"> ±</span> : null}
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

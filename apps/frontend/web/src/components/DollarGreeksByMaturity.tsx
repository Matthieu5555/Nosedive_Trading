import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type OptionSide,
} from "../api";
import { sci, UNITS, withCurrency } from "../lib/format";
import { isSaneIv } from "../lib/volRobust";
import { Scroll, Stack } from "./layout";

// The always-present first-order Greeks (the second-order set below is additive-nullable and is
// rendered by its own block, so it is deliberately excluded from this generic first-order indexing).
type FirstOrderGreek = "delta" | "gamma" | "vega" | "theta" | "rho";

const GREEKS: ReadonlyArray<{ name: FirstOrderGreek; rawUnit: string }> = [
  { name: "delta", rawUnit: UNITS.delta },
  { name: "gamma", rawUnit: UNITS.gamma },
  { name: "vega", rawUnit: UNITS.vega },
  { name: "theta", rawUnit: UNITS.theta },
  { name: "rho", rawUnit: UNITS.rho },
];

// The second-order set — how the first-order Greeks themselves move. Vanna (delta's vol-sensitivity),
// Volga (vega's vol-sensitivity), Charm (delta's time-decay). Banked on the same projected cell as
// the first-order Greeks (additive-nullable: a close projected before the field existed serves them
// null). Charm is a display Greek only — it is deliberately absent from the attribution decomposition.
const SECOND_ORDER: ReadonlyArray<{
  name: "vanna" | "volga" | "charm";
  label: string;
  rawUnit: string;
}> = [
  { name: "vanna", label: "vanna", rawUnit: UNITS.vanna },
  { name: "volga", label: "volga", rawUnit: UNITS.volga },
  { name: "charm", label: "charm", rawUnit: UNITS.charm },
];

function currencyUnitFor(
  points: AnalyticsPoint[],
  name: FirstOrderGreek,
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
      <Stack as="section" aria-label={label} className="greeks-by-maturity" gap="sm">
        <h3>Dollar Greeks by delta band</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </Stack>
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
  const lastSign: Partial<Record<FirstOrderGreek, -1 | 1>> = {};
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
    <Stack as="section" aria-label={label} className="greeks-by-maturity" gap="sm">
      <div className="greeks-by-maturity-heading">
        <h3>
          Dollar Greeks, {maturity.label}
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
        <Scroll className="greeks-by-maturity-scroll" label={`Dollar Greeks, ${maturity.label}`}>
          <table aria-label={`Dollar Greeks, ${maturity.label}`}>
            <caption className="visually-hidden">
              Dollar Greeks for {maturity.label}: each Greek as raw and {currency} value, one row
              per delta band.
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
                      {flagged ? <span title="railed slice, IV outside sane band"> ⚠</span> : null}
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
        </Scroll>
      )}
      {rows.length > 0 && (
        <SecondOrderGreeks rows={rows} maturityLabel={maturity.label} currency={currency} />
      )}
    </Stack>
  );
}

// The second-order Greeks (vanna/volga/charm) for the same delta bands, in their own labelled block
// so the first-order table stays readable. A close projected before these were banked serves them
// null, which renders as an explicit "not available for this close" note rather than a blank or a
// fabricated value.
function SecondOrderGreeks({
  rows,
  maturityLabel,
  currency,
}: {
  rows: AnalyticsPoint[];
  maturityLabel: string;
  currency: string;
}) {
  const label = `Second-order Greeks, ${maturityLabel}`;
  const anyPresent = rows.some((point) =>
    SECOND_ORDER.some((greek) => point.metrics[greek.name]?.raw != null),
  );

  if (!anyPresent) {
    return (
      <Stack
        as="section"
        aria-label={label}
        className="greeks-by-maturity greeks-second-order"
        gap="sm"
      >
        <h3>Second-order Greeks</h3>
        <p className="projection-gap" role="status">
          Vanna / Volga / Charm were not banked for this close, nothing to show (older projection).
        </p>
      </Stack>
    );
  }

  return (
    <Stack
      as="section"
      aria-label={label}
      className="greeks-by-maturity greeks-second-order"
      gap="sm"
    >
      <div className="greeks-by-maturity-heading">
        <h3>Second-order Greeks, {maturityLabel}</h3>
        <p className="panel-note">
          How the first-order Greeks themselves move · vanna (Δ vs vol), volga (vega vs vol), charm
          (Δ vs time) · raw and {currency} value per delta band
        </p>
      </div>
      <Scroll className="greeks-by-maturity-scroll" label={label}>
        <table aria-label={label}>
          <caption className="visually-hidden">
            Second-order Greeks for {maturityLabel}: vanna, volga and charm as raw and {currency}{" "}
            value, one row per delta band.
          </caption>
          <thead>
            <tr>
              <th rowSpan={2} scope="col">
                delta band
              </th>
              {SECOND_ORDER.map((greek) => (
                <th key={greek.name} colSpan={2} scope="colgroup" className="greek-group">
                  {greek.label}
                </th>
              ))}
            </tr>
            <tr>
              {SECOND_ORDER.map((greek) => [
                <th key={`${greek.name}-raw`} scope="col">
                  raw <span className="unit">{greek.rawUnit}</span>
                </th>,
                <th key={`${greek.name}-ccy`} scope="col">
                  {currency} value
                  <span className="unit">
                    {secondOrderCurrencyUnit(rows, greek.name, currency)}
                  </span>
                </th>,
              ])}
            </tr>
          </thead>
          <tbody>
            {rows.map((point) => {
              const atm = isAtmBand(point.delta_band);
              return (
                <tr key={point.delta_band} className={atm ? "greeks-row--atm" : undefined}>
                  <th scope="row">
                    {point.delta_band}
                    {atm ? <span title="at-the-money"> ●</span> : null}
                  </th>
                  {SECOND_ORDER.map((greek) => {
                    const metric = point.metrics[greek.name];
                    return [
                      <td key={`${greek.name}-raw`}>{sci(metric?.raw ?? null)}</td>,
                      <td key={`${greek.name}-ccy`}>{sci(metric?.dollar ?? null)}</td>,
                    ];
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Scroll>
    </Stack>
  );
}

function secondOrderCurrencyUnit(
  rows: AnalyticsPoint[],
  name: "vanna" | "volga" | "charm",
  currency: string,
): string {
  const unit = rows
    .map((point) => point.metrics[name]?.unit)
    .find((u) => u !== null && u !== undefined);
  return withCurrency(unit ?? null, currency) ?? "n/a";
}

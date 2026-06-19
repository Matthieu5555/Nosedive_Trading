import { useState } from "react";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type AnalyticsSides,
  type SurfaceSide,
} from "../api";
import { sci, UNITS, withCurrency } from "../lib/format";
import { isSaneIv } from "../lib/volRobust";
import { InfoDot } from "./InfoDot";
import { Cluster, Scroll, Stack } from "./layout";
import { SideToggle } from "./market/SideToggle";
import { TableExpand } from "./TableExpand";

// The first-order Greeks an operator reads first, delta/gamma/vega/theta plus rate-rho. (The
// second-order set below is additive-nullable and rendered by its own block.)
type FirstOrderGreek = "delta" | "gamma" | "vega" | "theta" | "rho";

type GreekColumn = { name: FirstOrderGreek; rawUnit: string };

const GREEKS: ReadonlyArray<GreekColumn> = [
  { name: "delta", rawUnit: UNITS.delta },
  { name: "gamma", rawUnit: UNITS.gamma },
  { name: "vega", rawUnit: UNITS.vega },
  { name: "theta", rawUnit: UNITS.theta },
  { name: "rho", rawUnit: UNITS.rho },
];

// The Greek group the segmented control selects. "first-order" is the four-Greek table the page opens
// on; "second-order" swaps in the vanna/volga/charm block below (the existing additive-nullable
// SecondOrderGreeks, with its "not banked for this close" empty state preserved).
type GreekGroup = "first-order" | "second-order";

const DEFAULT_GROUP: GreekGroup = "first-order";

// The explanatory prose for the higher-order Greeks, lifted out of a permanent panel-note and into
// the InfoDot beside the toggle so it stops taking vertical space.
const HIGHER_ORDER_INFO =
  "How the first-order Greeks themselves move: vanna (delta vs vol), volga (vega vs vol), charm " +
  "(delta vs time). Banked on the same projected cell as the first-order Greeks; a close projected " +
  "before the field existed serves them as a gap, not a value. Each shown raw and as currency value " +
  "per delta band.";

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
  currency = "$",
  sides,
  sidesAvailable = ["combined"],
  perSideServed = false,
}: {
  maturities: AnalyticsMaturity[];
  // The maturity in view, driven by the shared selector strip (no longer a per-panel dropdown).
  maturityLabel?: string;
  currency?: string;
  // The per-side captured maturities (combined / call / put), the SAME dimension the surface reads.
  // When absent (older payload, or BFF not serving per-side), the toggle falls back to combined and
  // Calls / Puts render disabled, never a fabricated split.
  sides?: AnalyticsSides;
  sidesAvailable?: SurfaceSide[];
  perSideServed?: boolean;
}) {
  const label = "Dollar Greeks by delta band (Greeks as columns, delta bands as rows)";

  // Local group selection, mirroring TenorPanel's `tenor` useState pattern. Opens on "first-order"
  // (four Greeks), so the view never mounts all the second-order columns at once.
  const [group, setGroup] = useState<GreekGroup>(DEFAULT_GROUP);
  // Local side selection, owned by this table, mirroring the surface's Combined / Calls / Puts
  // control. Opens on Combined (both wings). A side the close did not capture is offered disabled.
  const [side, setSide] = useState<SurfaceSide>("combined");
  const effectiveSide: SurfaceSide = sidesAvailable.includes(side) ? side : "combined";
  // The maturities for the selected side: the real per-side capture when served, else the combined
  // set passed in (the back-compat top-level `maturities`).
  const sideMaturities: AnalyticsMaturity[] =
    sides && effectiveSide in sides ? sides[effectiveSide] : maturities;

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
  const pickMaturity = (list: AnalyticsMaturity[]): AnalyticsMaturity | null => {
    if (list.length === 0) return null;
    const front = [...list].sort((a, b) => a.maturity_years - b.maturity_years)[0];
    return isAll ? front : (list.find((m) => m.label === maturityLabel) ?? front);
  };
  // The heading tenor follows the combined set so it stays stable as the side toggles; the body
  // reads the selected side's matching maturity.
  const headingMaturity = pickMaturity(maturities) ?? maturities[0];
  const maturity = pickMaturity(sideMaturities);

  // When the per-side views aren't served, a single-side selection falls back to filtering the
  // combined points by the sign of their target delta (puts ≤ 0, calls ≥ 0, ATM shared). With the
  // per-side views served, each side already carries only its own bands, so no sign filter is needed.
  const rawRows = maturity ? maturity.points : [];
  const rows = [...rawRows]
    .filter((p) => {
      if (sides) return true;
      if (effectiveSide === "put") return p.target_delta <= 0;
      if (effectiveSide === "call") return p.target_delta >= 0;
      return true;
    })
    .sort((a, b) => a.target_delta - b.target_delta);

  // The first-order column set in view (delta/gamma/vega/theta).
  const firstOrderColumns = GREEKS;
  const showHigherOrder = group === "second-order";

  // Pre-compute the sign-flip rows: walking down the sorted bands, a row flips when any displayed
  // Greek's sign differs from the last non-zero sign seen for that Greek above it.
  const lastSign: Partial<Record<FirstOrderGreek, -1 | 1>> = {};
  const flipRows = new Set<string>();
  for (const point of rows) {
    let flipped = false;
    for (const { name } of firstOrderColumns) {
      const sign = rawSign(point.metrics[name].raw);
      if (sign === 0) continue;
      const prev = lastSign[name];
      if (prev !== undefined && prev !== sign) flipped = true;
      lastSign[name] = sign;
    }
    if (flipped) flipRows.add(point.delta_band);
  }

  const bodyLabel = maturity?.label ?? headingMaturity.label;

  return (
    <Stack as="section" aria-label={label} className="greeks-by-maturity" gap="sm">
      <div className="greeks-by-maturity-heading">
        <h3>
          Dollar Greeks, {headingMaturity.label}
          {isAll ? " (front month)" : ""}
        </h3>
        <Cluster gap="xs" align="center">
          <SideToggle
            side={side}
            available={sidesAvailable}
            perSideServed={perSideServed}
            onChange={setSide}
            ariaLabel="Dollar Greeks side"
          />
          <div className="mode-toggle" role="group" aria-label="Greek group">
            <button
              type="button"
              className="mode-toggle__option"
              aria-pressed={group === "first-order"}
              title="Delta, gamma, vega, theta, rho, the ones read first"
              onClick={() => setGroup("first-order")}
            >
              First order
            </button>
            <button
              type="button"
              className="mode-toggle__option"
              aria-pressed={group === "second-order"}
              title="Vanna, volga, charm, how the first-order Greeks themselves move"
              onClick={() => setGroup("second-order")}
            >
              Second order
            </button>
          </div>
          <InfoDot label="About second-order Greeks" body={HIGHER_ORDER_INFO} />
          {!showHigherOrder && rows.length > 0 && (
            <TableExpand
              title={`Dollar Greeks, ${bodyLabel}`}
              description={`Every delta band at ${bodyLabel}: delta, gamma, vega, theta and rho as raw and ${currency} value.`}
              triggerLabel="Open the full Dollar Greeks table"
            >
              <FirstOrderGreeksTable
                rows={rows}
                maturityLabel={bodyLabel}
                currency={currency}
                flipRows={flipRows}
              />
            </TableExpand>
          )}
        </Cluster>
      </div>
      {rows.length === 0 ? (
        <p>No projected analytics for {bodyLabel} yet.</p>
      ) : showHigherOrder ? (
        <SecondOrderGreeks rows={rows} maturityLabel={bodyLabel} currency={currency} />
      ) : (
        <Scroll className="greeks-by-maturity-scroll" label={`Dollar Greeks, ${bodyLabel}`}>
          <FirstOrderGreeksTable
            rows={rows}
            maturityLabel={bodyLabel}
            currency={currency}
            flipRows={flipRows}
          />
        </Scroll>
      )}
    </Stack>
  );
}

// The first-order Greeks table proper (delta/gamma/vega/theta, each raw + currency value). Rendered
// both inline and inside the full-screen dialog, so the two reads are byte-for-byte identical.
function FirstOrderGreeksTable({
  rows,
  maturityLabel,
  currency,
  flipRows,
}: {
  rows: AnalyticsPoint[];
  maturityLabel: string;
  currency: string;
  flipRows: Set<string>;
}) {
  const firstOrderColumns = GREEKS;
  return (
    <table aria-label={`Dollar Greeks, ${maturityLabel}`}>
      <caption className="visually-hidden">
        Dollar Greeks for {maturityLabel}: each Greek as raw and {currency} value, one row per delta
        band. ATM and sign-flip rows highlighted.
      </caption>
      <thead>
        <tr>
          <th rowSpan={2} scope="col">
            delta band
          </th>
          {firstOrderColumns.map((greek) => (
            <th key={greek.name} colSpan={2} scope="colgroup" className="greek-group">
              {greek.name}
            </th>
          ))}
        </tr>
        <tr>
          {firstOrderColumns.map((greek) => [
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
              {firstOrderColumns.map((greek) => {
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

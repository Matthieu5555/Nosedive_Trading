import { useState } from "react";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type AnalyticsSides,
  type SurfaceSide,
} from "../api";
import { number, UNITS, withCurrency } from "../lib/format";
import { Cluster, Stack } from "./layout";
import { SideToggle } from "./market/SideToggle";
import { TableExpand } from "./TableExpand";

// Prices and the spread render as plain fixed decimals (the currency lives once in the column
// header, not on every cell) — a strike ladder is read by scanning, which scientific notation
// (4.81 × 10³) defeats. A null quote stays the honest "—", never a fabricated mid.
function priceCell(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return number(value, 2);
}

function volumeCell(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "-";
  return number(value, 0);
}

function spreadCell(bid: number | null | undefined, ask: number | null | undefined): string {
  if (
    bid === null ||
    bid === undefined ||
    ask === null ||
    ask === undefined ||
    !Number.isFinite(bid) ||
    !Number.isFinite(ask)
  ) {
    return "-";
  }
  return number(ask - bid, 2);
}

export function PriceStructure({
  maturities,
  maturityLabel,
  currency = "$",
  sides,
  sidesAvailable = ["combined"],
  perSideServed = false,
}: {
  maturities: AnalyticsMaturity[];
  maturityLabel?: string;
  currency?: string;
  // The per-side captured maturities (combined / call / put), the SAME dimension the surface reads.
  // When absent (older payload, or BFF not serving per-side), the toggle falls back to combined and
  // Calls / Puts render disabled, never a fabricated split.
  sides?: AnalyticsSides;
  sidesAvailable?: SurfaceSide[];
  perSideServed?: boolean;
}) {
  const label = "Price structure by strike (bid / ask / volume + option price)";

  // Local side selection, owned by this table, mirroring the surface's Combined / Calls / Puts
  // control. Opens on Combined (the union read). A side the close did not capture is offered disabled.
  const [side, setSide] = useState<SurfaceSide>("combined");
  const effectiveSide: SurfaceSide = sidesAvailable.includes(side) ? side : "combined";
  // The maturities for the selected side: the real per-side capture when served, else the combined
  // set passed in (the back-compat top-level `maturities`).
  const sideMaturities: AnalyticsMaturity[] =
    sides && effectiveSide in sides ? sides[effectiveSide] : maturities;

  if (maturities.length === 0) {
    return (
      <section aria-label={label} className="price-structure">
        <h3>Price structure</h3>
        <p>No projected analytics for this tenor yet.</p>
      </section>
    );
  }

  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;
  const pickMaturity = (list: AnalyticsMaturity[]): AnalyticsMaturity | null => {
    if (list.length === 0) return null;
    const front = [...list].sort((a, b) => a.maturity_years - b.maturity_years)[0];
    return isAll ? front : (list.find((m) => m.label === maturityLabel) ?? front);
  };
  // The heading tenor follows the combined set, so it stays stable as the side toggles; the body
  // reads the selected side's matching maturity.
  const headingMaturity = pickMaturity(maturities) ?? maturities[0];
  const maturity = pickMaturity(sideMaturities);

  const rows: AnalyticsPoint[] = maturity
    ? [...maturity.points].sort((a, b) => a.strike - b.strike)
    : [];

  return (
    <Stack as="section" aria-label={label} className="price-structure" gap="2xs">
      <div className="price-structure-heading">
        <div>
          <h3>Price structure, {headingMaturity.label}</h3>
          <p className="panel-note">
            Per strike: bid / ask / volume and the option price, read the spread and the traded
            size, never a synthetic mid.
          </p>
        </div>
        <Cluster gap="xs" align="center">
          <SideToggle
            side={side}
            available={sidesAvailable}
            perSideServed={perSideServed}
            onChange={setSide}
            ariaLabel="Price structure side"
          />
          {rows.length > 0 && maturity && (
            <TableExpand
              title={`Price structure, ${maturity.label}`}
              description={`Every strike at ${maturity.label}: bid, ask, spread, volume and the option price.`}
              triggerLabel="Open the full price structure"
            >
              <PriceTable rows={rows} maturityLabel={maturity.label} currency={currency} />
            </TableExpand>
          )}
        </Cluster>
      </div>
      {rows.length === 0 || !maturity ? (
        <p>No strikes for {headingMaturity.label} yet.</p>
      ) : (
        <div className="price-structure-scroll">
          <PriceTable rows={rows} maturityLabel={maturity.label} currency={currency} />
        </div>
      )}
    </Stack>
  );
}

function PriceTable({
  rows,
  maturityLabel,
  currency,
}: {
  rows: AnalyticsPoint[];
  maturityLabel: string;
  currency: string;
}) {
  const priceUnit = withCurrency(UNITS.price, currency) ?? currency;
  return (
    <table aria-label={`Price structure, ${maturityLabel}`}>
      <caption className="visually-hidden">
        Bid, ask, spread, volume and option price for each strike at {maturityLabel}.
      </caption>
      <thead>
        <tr>
          <th scope="col">
            strike <span className="unit">{withCurrency(UNITS.strike, currency)}</span>
          </th>
          <th scope="col">band</th>
          <th scope="col">
            bid <span className="unit">{priceUnit}</span>
          </th>
          <th scope="col">
            ask <span className="unit">{priceUnit}</span>
          </th>
          <th scope="col">
            spread <span className="unit">{priceUnit}</span>
          </th>
          <th scope="col">volume</th>
          <th scope="col">
            price <span className="unit">{priceUnit}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((point) => (
          <tr key={`${point.delta_band}-${point.strike}`}>
            <th scope="row">{number(point.strike, 0)}</th>
            <td>{point.delta_band}</td>
            <td>{priceCell(point.quote?.bid)}</td>
            <td>{priceCell(point.quote?.ask)}</td>
            <td>{spreadCell(point.quote?.bid, point.quote?.ask)}</td>
            <td>{volumeCell(point.quote?.volume)}</td>
            <td>{priceCell(point.price)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

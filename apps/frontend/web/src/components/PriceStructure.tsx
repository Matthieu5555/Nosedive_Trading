import { ALL_MATURITIES, type AnalyticsMaturity, type AnalyticsPoint } from "../api";
import { sci, sciUnit, UNITS, withCurrency } from "../lib/format";

function quoteCell(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return sciUnit(value, unit);
}

function volumeCell(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return sci(value);
}

function spreadCell(
  bid: number | null | undefined,
  ask: number | null | undefined,
  unit: string,
): string {
  if (
    bid === null ||
    bid === undefined ||
    ask === null ||
    ask === undefined ||
    !Number.isFinite(bid) ||
    !Number.isFinite(ask)
  ) {
    return "—";
  }
  return sciUnit(ask - bid, unit);
}

export function PriceStructure({
  maturities,
  maturityLabel,
  currency = "$",
}: {
  maturities: AnalyticsMaturity[];
  maturityLabel?: string;
  currency?: string;
}) {
  const label = "Price structure by strike (bid / ask / volume + option price)";

  if (maturities.length === 0) {
    return (
      <section aria-label={label} className="price-structure">
        <h3>Price structure</h3>
        <p>No projected analytics for this tenor yet.</p>
      </section>
    );
  }

  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;
  const frontMaturity = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years)[0];
  const maturity = isAll
    ? frontMaturity
    : (maturities.find((m) => m.label === maturityLabel) ?? frontMaturity);

  const rows: AnalyticsPoint[] = [...maturity.points].sort((a, b) => a.strike - b.strike);
  const priceUnit = withCurrency(UNITS.price, currency) ?? currency;

  return (
    <section aria-label={label} className="price-structure">
      <div className="price-structure-heading">
        <h3>Price structure — {maturity.label}</h3>
        <p className="panel-note">
          Per strike: bid / ask / volume and the option price — read the spread and the
          traded size, never a synthetic mid.
        </p>
      </div>
      {rows.length === 0 ? (
        <p>No strikes for {maturity.label} yet.</p>
      ) : (
        <div className="price-structure-scroll">
          <table aria-label={`Price structure — ${maturity.label}`}>
            <caption className="visually-hidden">
              Bid, ask, spread, volume and option price for each strike at {maturity.label}.
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
                  <th scope="row">{sci(point.strike)}</th>
                  <td>{point.delta_band}</td>
                  <td>{quoteCell(point.quote?.bid, priceUnit)}</td>
                  <td>{quoteCell(point.quote?.ask, priceUnit)}</td>
                  <td>{spreadCell(point.quote?.bid, point.quote?.ask, priceUnit)}</td>
                  <td>{volumeCell(point.quote?.volume)}</td>
                  <td>{quoteCell(point.price, priceUnit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

import type { BookGreeks, PositionGreekName } from "../api";
import { POSITION_GREEK_ORDER } from "../api";
import { sci, withCurrency } from "../lib/format";

export function BookSummary({ book, currency = "$" }: { book: BookGreeks; currency?: string }) {
  const label = "Book dollar Greeks and total market value";
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <caption>{label} — the additive sum across priced legs</caption>
        <thead>
          <tr>
            <th>Measure</th>
            <th>value</th>
            <th>unit</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>market value</td>
            <td>{sci(book.market_value)}</td>
            <td>{withCurrency("$", currency)}</td>
          </tr>
          {POSITION_GREEK_ORDER.map((name: PositionGreekName) => {
            const greek = book[name];
            return (
              <tr key={name}>
                <td>{name} $</td>
                <td>{sci(greek.dollar)}</td>
                <td>{withCurrency(greek.unit, currency) ?? "n/a"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

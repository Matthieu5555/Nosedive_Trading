import type { BookGreeks, PositionGreekName } from "../api";
import { POSITION_GREEK_ORDER } from "../api";
import { sci, withCurrency } from "../lib/format";
import { signColor } from "./Scorecards";

// The sign-colour law (shared with Scorecards via the exported `signColor`): a signed dollar figure
// reads green when positive, coral when negative, neutral when zero/absent. `signColor` returns
// "positive" | "negative" | null; map the null (no value) to the empty class so the cell stays
// neutral, exactly as the old local helper did.
function signClass(value: number | null | undefined): string {
  return signColor(value) ?? "";
}

export function BookSummary({ book, currency = "$" }: { book: BookGreeks; currency?: string }) {
  const label = "Book dollar Greeks and total market value";
  return (
    <div className="table-wrap">
      <table aria-label={label}>
        {/* The restatement is kept for screen readers but hidden from the visual layout, so it no
            longer competes with the parent card's title + description (progressive disclosure, not
            deletion). */}
        <caption className="visually-hidden">{label}, the additive sum across priced legs</caption>
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
            <td className={signClass(book.market_value)}>{sci(book.market_value)}</td>
            <td>{withCurrency("$", currency)}</td>
          </tr>
          {POSITION_GREEK_ORDER.map((name: PositionGreekName) => {
            const greek = book[name];
            return (
              <tr key={name}>
                <td>{name} $</td>
                <td className={signClass(greek.dollar)}>{sci(greek.dollar)}</td>
                <td>{withCurrency(greek.unit, currency) ?? "n/a"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

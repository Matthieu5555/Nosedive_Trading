import { useMemo, useState } from "react";

import { type AnalyticsMaturity, TENOR_GRID } from "../api";
import { SmileChart } from "./charts";
import { DollarGreeksByMaturity } from "./DollarGreeksByMaturity";

// The reference tenor the page opens on (the blueprint signal tenor). When 3m wasn't captured the
// selector still opens on it and shows the projection gap, so the default is honest rather than
// silently jumping to whatever tenor happens to exist.
const DEFAULT_TENOR = "3m";

// The pinned tenor selector that drives BOTH the smile and the Greeks table below it. The grid is
// the authoritative `tenor_grid` (near → far); a grid tenor the capture didn't reach is offered but
// resolves to a labelled "not captured" gap rather than being hidden (blueprint §4.5). One control,
// one tenor, two panels — the old all-tenor spaghetti / accordion is gone.
export function TenorPanel({
  maturities,
  currency,
}: {
  maturities: AnalyticsMaturity[];
  currency: string;
}) {
  // Which grid tenors actually have a captured maturity, by tenor_label.
  const capturedByTenor = useMemo(() => {
    const map = new Map<string, AnalyticsMaturity>();
    for (const m of maturities) {
      if (m.tenor_label && !map.has(m.tenor_label)) map.set(m.tenor_label, m);
    }
    return map;
  }, [maturities]);

  const [tenor, setTenor] = useState(DEFAULT_TENOR);
  const selected = capturedByTenor.get(tenor) ?? null;

  return (
    <article className="panel tenor-panel" aria-label="Tenor view">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">tenor</p>
          <h2>Smile & Greeks</h2>
        </div>
        <label className="selector-field">
          <span className="visually-hidden">Tenor</span>
          <select
            aria-label="Tenor"
            value={tenor}
            onChange={(event) => setTenor(event.target.value)}
          >
            {TENOR_GRID.map((label) => (
              <option key={label} value={label}>
                {label}
                {capturedByTenor.has(label) ? "" : " (not captured)"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {selected === null ? (
        // A grid tenor beyond the captured span: a labelled projection gap, never a blank or a
        // fabricated curve.
        <p className="projection-gap" role="status">
          {tenor} is not captured for this close — no smile or Greeks to show (projection gap).
        </p>
      ) : (
        <div className="tenor-panel__body">
          <SmileChart maturities={maturities} maturityLabel={selected.label} />
          <DollarGreeksByMaturity
            maturities={maturities}
            maturityLabel={selected.label}
            currency={currency}
          />
        </div>
      )}
    </article>
  );
}

import { useMemo, useState } from "react";

import { type AnalyticsMaturity, type OptionSide, type SurfaceSide, TENOR_GRID } from "../api";
import { atmIv, ivAtDelta, RR_DELTA } from "../lib/scorecards";
import { GreeksShapeCurves, SmileChart, type SurfaceIdentityProps } from "./charts";
import { DollarGreeksByMaturity } from "./DollarGreeksByMaturity";
import { Stack } from "./layout";
import { PriceStructure } from "./PriceStructure";
import { RateDiagnosticsPanel } from "./RateDiagnostics";

// The 25Δ butterfly (curvature) for one slice, in vol points: IV(25Δp) + IV(25Δc) − 2·ATM. Demoted
// out of the headline scorecards into the smile block, where curvature is the natural read
// (blueprint §3.2: niveau/pente/courbure résument le smile). Null where the wings don't bracket.
function ConvexityReadout({ maturity }: { maturity: AnalyticsMaturity }) {
  const atm = atmIv(maturity);
  const ivPut = ivAtDelta(maturity, -RR_DELTA);
  const ivCall = ivAtDelta(maturity, RR_DELTA);
  const convexity =
    atm !== null && ivPut !== null && ivCall !== null ? ivPut + ivCall - 2 * atm : null;
  const value =
    convexity === null
      ? "-"
      : `${convexity * 100 > 0 ? "+" : ""}${(convexity * 100).toFixed(1)} vp`;
  return (
    <p className="smile-convexity" aria-label="Convexity 25Δ">
      <span className="smile-convexity__label">Convexity 25Δ</span>
      <span className="smile-convexity__value">{value}</span>
      <span className="smile-convexity__hint">
        butterfly: IV(25Δp) + IV(25Δc) − 2·ATM (vp = vol point = 0.01 IV)
      </span>
    </p>
  );
}

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
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
  side = "combined",
}: {
  maturities: AnalyticsMaturity[];
  currency: string;
  // The selected surface side (combined / call / put). The Greeks table filters to the matching wing
  // when a single side is selected; combined keeps both wings (ATM shared). The smile/Greeks curves
  // already carry both wings of the side's own maturities, so they need no further filter.
  side?: SurfaceSide;
} & SurfaceIdentityProps) {
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
    <article className="panel tenor-panel" aria-label="Tenor view" data-tour-id="market.smile">
      <Stack gap="md">
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
            {tenor} is not captured for this close, no smile or Greeks to show (projection gap).
          </p>
        ) : (
          <Stack className="tenor-panel__body" gap="lg">
            <Stack className="tenor-panel__smile" gap="2xs">
              <SmileChart
                maturities={maturities}
                maturityLabel={selected.label}
                subject={subject}
                asOf={asOf}
                closeInstant={closeInstant}
                mode={mode}
                coverage={coverage}
                side={side}
              />
              <ConvexityReadout maturity={selected} />
            </Stack>
            <PriceStructure
              maturities={maturities}
              maturityLabel={selected.label}
              currency={currency}
            />
            <RateDiagnosticsPanel
              diagnostics={selected.rate_diagnostics}
              maturityLabel={selected.label}
              currency={currency}
            />
            {/* Two dense panels never share a row: the Greeks line chart and the Dollar Greeks
                table each get the full width, stacked. Chart first (the shape read), then the
                table (the exact per-band numbers underneath it). */}
            <Stack gap="lg">
              <GreeksShapeCurves
                maturities={maturities}
                maturityLabel={selected.label}
                subject={subject}
                asOf={asOf}
                closeInstant={closeInstant}
                mode={mode}
                coverage={coverage}
                currency={currency}
              />
              <DollarGreeksByMaturity
                maturities={maturities}
                maturityLabel={selected.label}
                side={side === "combined" ? undefined : (side as OptionSide)}
                currency={currency}
              />
            </Stack>
          </Stack>
        )}
      </Stack>
    </article>
  );
}

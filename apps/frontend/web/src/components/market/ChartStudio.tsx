import { type ReactNode, useState } from "react";

import { type AnalyticsMaturity, type SurfaceSide } from "../../api";
import { atmIv, ivAtDelta, RR_DELTA } from "../../lib/scorecards";
import { tourAnchor } from "../../lib/tour";
import {
  GREEK_GROUP_INFO,
  GREEK_SPECS,
  GreekFigure,
  type GreekName,
  SmileChart,
  type SurfaceIdentityProps,
} from "../charts";
import { InfoDot } from "../InfoDot";
import { Cluster, Stack } from "../layout";
import { SideToggle } from "./SideToggle";

// The 25Δ butterfly (curvature) for one slice, in vol points: IV(25Δp) + IV(25Δc) − 2·ATM. It lives
// beside the smile, where curvature is the natural read (blueprint §3.2: niveau/pente/courbure
// résument le smile). Null where the wings don't bracket.
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

// The single chart the studio can draw. "smile" is the implied-vol curve across strikes (its own
// put/call wing selector); "first-order" and "second-order" map straight onto the Greek groups, so the
// pill row below the toggle holds that group's Greeks.
type StudioView = "smile" | "first-order" | "second-order";

const STUDIO_INFO =
  "One chart, three reads of the same tenor. Smile is implied vol across strikes (pick a wing, calls " +
  "or puts, or both combined). First order is delta, gamma, vega, theta, the four read first. Second " +
  "order is vanna, volga, charm, how the first-order Greeks themselves move as vol or time changes. " +
  GREEK_GROUP_INFO;

// The Charting studio: ONE panel, below the Volatility surface, that folds the old separate smile chart
// and Greek-vs-strike chart into a single element with a three-way Smile / First order / Second order
// toggle (the page's "important filters become selectors" idiom). The smile read carries its own
// Combined / Calls / Puts wing selector; each Greek read carries that group's pill row. The tenor
// selector that drives every panel on the page lives in this heading (it is the first panel below the
// surface), passed in as `tenorControl`.
export function ChartStudio({
  selected,
  maturities,
  currency,
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
  side = "combined",
  sidesAvailable = ["combined"],
  perSideServed = false,
  tenorControl,
  gap,
}: {
  // The chosen tenor's maturity, or null when the picked grid tenor was not captured (the projection
  // gap), in which case `gap` is rendered in place of the chart.
  selected: AnalyticsMaturity | null;
  maturities: AnalyticsMaturity[];
  currency: string;
  // The page surface side, the default the smile's own wing selector opens on.
  side?: SurfaceSide;
  sidesAvailable?: SurfaceSide[];
  perSideServed?: boolean;
  // The shared tenor selector, owned by the page; rendered in this panel's heading.
  tenorControl: ReactNode;
  // The "this tenor was not captured" projection-gap note, rendered when `selected` is null.
  gap: ReactNode;
} & SurfaceIdentityProps) {
  const [view, setView] = useState<StudioView>("smile");
  // The smile's own wing selector, opening on the page side. Owned here so the studio's smile read is
  // independent of the surface's side, while still defaulting to whatever the surface is showing.
  const [smileSide, setSmileSide] = useState<SurfaceSide>(side);
  // The selected Greek for the active group. Switching group lands it on that group's first Greek, so
  // the chart is never left on a Greek that is no longer in the visible pill row.
  const [greek, setGreek] = useState<GreekName>("delta");

  const changeView = (next: StudioView) => {
    if (next === view) return;
    setView(next);
    if (next === "first-order" || next === "second-order") {
      const first = GREEK_SPECS.find((s) => s.group === next);
      if (first) setGreek(first.name);
    }
  };

  const greeksInView = view === "smile" ? [] : GREEK_SPECS.filter((s) => s.group === view);

  return (
    <article
      className="panel tenor-panel"
      aria-label="Charting studio"
      {...tourAnchor(
        "market.chart-studio",
        "Charting studio",
        "One chart for the selected tenor, switchable between the smile, the first-order Greeks and the second-order Greeks.",
      )}
    >
      <Stack gap="md">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">tenor</p>
            <h2>Charting studio</h2>
          </div>
          {tenorControl}
        </div>

        {selected === null ? (
          gap
        ) : (
          <Stack gap="sm">
            <Cluster className="panel-heading__controls" gap="xs" align="center">
              <div className="mode-toggle" role="group" aria-label="Chart">
                <button
                  type="button"
                  className="mode-toggle__option"
                  aria-pressed={view === "smile"}
                  title="Implied vol across strikes, the smile"
                  onClick={() => changeView("smile")}
                >
                  Smile
                </button>
                <button
                  type="button"
                  className="mode-toggle__option"
                  aria-pressed={view === "first-order"}
                  title="Delta, gamma, vega, theta, the four read first"
                  onClick={() => changeView("first-order")}
                >
                  First order
                </button>
                <button
                  type="button"
                  className="mode-toggle__option"
                  aria-pressed={view === "second-order"}
                  title="Vanna, volga, charm, how the first-order Greeks themselves move"
                  onClick={() => changeView("second-order")}
                >
                  Second order
                </button>
              </div>

              {view === "smile" ? (
                <SideToggle
                  side={smileSide}
                  available={sidesAvailable}
                  perSideServed={perSideServed}
                  onChange={setSmileSide}
                  ariaLabel="Smile side"
                />
              ) : (
                <div className="mode-toggle" role="group" aria-label="Greek">
                  {greeksInView.map((spec) => (
                    <button
                      key={spec.name}
                      type="button"
                      className="mode-toggle__option"
                      aria-pressed={greek === spec.name}
                      title={spec.howToRead}
                      onClick={() => setGreek(spec.name)}
                    >
                      {spec.name}
                    </button>
                  ))}
                </div>
              )}

              <InfoDot label="About the charting studio" body={STUDIO_INFO} />
            </Cluster>

            {view === "smile" ? (
              <Stack className="tenor-panel__smile" gap="2xs">
                <SmileChart
                  maturities={maturities}
                  maturityLabel={selected.label}
                  subject={subject}
                  asOf={asOf}
                  closeInstant={closeInstant}
                  mode={mode}
                  coverage={coverage}
                  side={smileSide}
                />
                <ConvexityReadout maturity={selected} />
              </Stack>
            ) : (
              <GreekFigure
                maturities={maturities}
                maturityLabel={selected.label}
                greek={greek}
                subject={subject}
                asOf={asOf}
                closeInstant={closeInstant}
                mode={mode}
                coverage={coverage}
                currency={currency}
              />
            )}
          </Stack>
        )}
      </Stack>
    </article>
  );
}

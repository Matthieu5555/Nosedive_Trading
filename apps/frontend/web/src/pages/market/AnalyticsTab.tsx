import type { AnalyticsResponse, Constituent, OptionSide, PriceHistoryResponse } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { GreeksTermStructure, PriceChart, SmileChart, VolSurface } from "../../components/charts";
import { DollarGreeksByMaturity } from "../../components/DollarGreeksByMaturity";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { useFetch } from "../../hooks/useFetch";
import { DispersionGap } from "./DispersionGap";

// The selected entity's own daily candlestick — orientation, not edge, so it stays compact at the
// top. One chart, driven by the selector (the old hardcoded second constituent panel is gone).
function EntityPrice({ entity, asOf }: { entity: string; asOf: string }) {
  const price = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(entity)}&end=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={price.loading} error={price.error}>
      {price.data && <PriceChart data={price.data} />}
    </AsyncBlock>
  );
}

// The analytics tab: price → surface block → Greeks block, every panel bound to the shared
// entity / side / maturity context. When the entity is the whole index, the surface block leads
// with the dispersion gap (the on-thesis view) above the index's own surface.
export function AnalyticsTab({
  index,
  entity,
  isIndex,
  asOf,
  analytics,
  side,
  maturityLabel,
  constituents,
  currency,
}: {
  index: string;
  entity: string;
  isIndex: boolean;
  asOf: string;
  analytics: AnalyticsResponse;
  side: OptionSide;
  maturityLabel: string;
  constituents: Constituent[];
  currency: string;
}) {
  const maturities = analytics.maturities;
  const selectedMaturity = maturities.find((m) => m.label === maturityLabel) ?? maturities[0];
  // Members weight-first, so the dispersion fan-out keeps the heaviest names under its cap.
  const members = [...constituents]
    .sort((a, b) => (b.weight ?? -Infinity) - (a.weight ?? -Infinity))
    .map((c) => c.symbol);

  return (
    <div className="analytics-stack">
      <article className="panel history-panel" aria-label={`${entity} daily history`}>
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{entity}</p>
            <h2>Price history</h2>
          </div>
          <span className="status">daily OHLC</span>
        </div>
        <ErrorBoundary label="Price history">
          <EntityPrice entity={entity} asOf={asOf} />
        </ErrorBoundary>
      </article>

      <article className="panel surface-panel" aria-label={`Volatility surface for ${entity}`}>
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{entity}</p>
            <h2>{isIndex ? "Dispersion & volatility surface" : "Volatility surface"}</h2>
          </div>
          <span className="status">{side === "put" ? "puts" : "calls"}</span>
        </div>

        {/* For the index, lead with the gap — the one picture of what the book harvests. */}
        {isIndex && (
          <ErrorBoundary label="Dispersion gap">
            <DispersionGap
              index={index}
              asOf={asOf}
              members={members}
              indexAnalytics={maturities}
            />
          </ErrorBoundary>
        )}

        {/* The 3D surface (kept — the impressive overview) beside the fitted smile for the chosen
            maturity (where you read the number). Both honour the put/call switch. */}
        <div className="surface-two-up">
          <ErrorBoundary label="3D surface">
            <VolSurface surface={analytics.surface} maturities={maturities} side={side} />
          </ErrorBoundary>
          <ErrorBoundary label="Smile">
            {selectedMaturity ? (
              <SmileChart maturity={selectedMaturity} side={side} />
            ) : (
              <figure className="plot">
                <figcaption>Smile</figcaption>
                <p>No maturities captured for {entity} on this date yet.</p>
              </figure>
            )}
          </ErrorBoundary>
        </div>
      </article>

      <article className="panel greeks-panel" aria-label={`Greeks for ${entity}`}>
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{entity}</p>
            <h2>Greeks</h2>
          </div>
          <span className="status">
            {side === "put" ? "puts" : "calls"} · {selectedMaturity?.label ?? "—"}
          </span>
        </div>
        {/* Curves show the shape across maturity; the table shows the exact euro values at the one
            maturity in context, with the ATM row and sign-flips highlighted. */}
        <ErrorBoundary label="Greeks term structure">
          <GreeksTermStructure maturities={maturities} currency={currency} side={side} />
        </ErrorBoundary>
        <ErrorBoundary label="Greeks by delta band">
          <DollarGreeksByMaturity
            maturities={maturities}
            maturityLabel={maturityLabel}
            side={side}
            currency={currency}
          />
        </ErrorBoundary>
      </article>
    </div>
  );
}

import { Metric } from "../../components/Metric";
import { NamedScenarios } from "../../components/NamedScenarios";
import { RateSweep, StressSurface } from "../../components/StressSurface";
import { sciUnit, UNITS, withCurrency } from "../../lib/format";
import type { BasketScenariosResponse, NamedScenario } from "../../stressApi";

type StressTabProps = {
  canStress: boolean;
  loading: boolean;
  error: string | null;
  stress: BasketScenariosResponse | null;
  currency: string;
  onStress: () => void;
  // ③ Choquer shock presets: the named historical crises (2008, COVID, …) replayed against the
  // book, folded in from the standalone Risk Scenarios page. Empty list → labelled empty state.
  namedScenarios: NamedScenario[];
  namedLoading: boolean;
  namedError: string | null;
};

export function StressTab({
  canStress,
  loading,
  error,
  stress,
  currency,
  onStress,
  namedScenarios,
  namedLoading,
  namedError,
}: StressTabProps) {
  return (
    <div className="basket-tab">
      <p className="basket-tab__lead">
        Shock the composed basket across a grid of spot and vol moves — and, on its own axis, a
        parallel rate sweep. This is a full reprice per leg, so it reads the worst-case loss the
        position carries today. Below the grid, replay the named historical crises as shock presets.
      </p>
      <div className="basket-actions">
        <button type="button" onClick={onStress} disabled={loading || !canStress}>
          {loading ? "Stressing…" : "Stress basket"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="error">
          Failed to stress basket: {error}
        </p>
      )}
      {stress !== null && (
        <div className="risk-grid">
          <article className="panel scenario-summary">
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">{stress.underlying}</p>
                <h2>Worst case</h2>
              </div>
              <span className="status negative">
                {stress.n_resolved}/{stress.n_legs} legs repriced
              </span>
            </div>
            <div className="quote-strip">
              <Metric
                label="Worst PnL"
                value={sciUnit(stress.worst_case.pnl, withCurrency(stress.worst_case.unit, currency))}
              />
              <Metric
                label="Spot shock"
                value={sciUnit(stress.worst_case.spot_shock, UNITS.shock)}
              />
              <Metric label="Vol shock" value={sciUnit(stress.worst_case.vol_shock, UNITS.shock)} />
            </div>
            {stress.n_gaps > 0 && (
              <p role="status">
                {stress.n_gaps} leg(s) not repriced:{" "}
                {stress.gaps
                  .map(
                    (gap) =>
                      `${gap.tenor_label ?? gap.underlying}/${gap.delta_band ?? "stock"} (${gap.reason})`,
                  )
                  .join(", ")}
              </p>
            )}
          </article>
          <StressSurface
            surface={stress.surface}
            kicker={`${stress.underlying} ${stress.trade_date}`}
            currency={currency}
          />
          {stress.rate && stress.rate.length > 0 && (
            <RateSweep rates={stress.rate} currency={currency} />
          )}
        </div>
      )}

      <div className="risk-grid">
        <h3>Shock presets — named historical crises</h3>
        {namedError !== null && (
          <p role="alert" className="error">
            Failed to load shock presets: {namedError}
          </p>
        )}
        {namedLoading && <p role="status">Loading shock presets…</p>}
        {!namedLoading && namedError === null && (
          <NamedScenarios scenarios={namedScenarios} currency={currency} />
        )}
      </div>
    </div>
  );
}

import { Metric } from "../../components/Metric";
import { StressSurface } from "../../components/StressSurface";
import { sciUnit, UNITS, withCurrency } from "../../lib/format";
import type { BasketScenariosResponse } from "../../stressApi";

type StressTabProps = {
  canStress: boolean;
  loading: boolean;
  error: string | null;
  stress: BasketScenariosResponse | null;
  currency: string;
  onStress: () => void;
};

export function StressTab({
  canStress,
  loading,
  error,
  stress,
  currency,
  onStress,
}: StressTabProps) {
  return (
    <div className="basket-tab">
      <p className="basket-tab__lead">
        Shock the composed basket across a grid of spot and vol moves. This is a full reprice per
        leg, so it reads the worst-case loss the position carries today.
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
        </div>
      )}
    </div>
  );
}

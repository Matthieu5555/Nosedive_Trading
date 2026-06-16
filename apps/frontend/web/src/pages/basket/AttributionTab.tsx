import type { AttributionResponse } from "../../api";
import { AttributionWaterfall } from "../../components/AttributionWaterfall";

type AttributionTabProps = {
  portfolioId: string;
  onPortfolioId: (value: string) => void;
  tradeDate: string;
  loading: boolean;
  error: string | null;
  attribution: AttributionResponse | null;
  onLoad: () => void;
};

export function AttributionTab({
  portfolioId,
  onPortfolioId,
  tradeDate,
  loading,
  error,
  attribution,
  onLoad,
}: AttributionTabProps) {
  return (
    <div className="basket-tab">
      <p className="basket-tab__lead">
        Read a persisted book's P&amp;L back as the per-Greek contributions that produced it
        (Δ → Γ → Vega → Θ), with the unexplained residual carried as its own bar.
      </p>
      <div className="basket-controls">
        <label>
          Portfolio (attribution){" "}
          <input
            aria-label="portfolio"
            value={portfolioId}
            onChange={(e) => onPortfolioId(e.target.value)}
          />
        </label>
      </div>
      <div className="basket-actions">
        <button type="button" onClick={onLoad} disabled={loading}>
          {loading ? "Loading attribution…" : "P&L attribution"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="error">
          Failed to load attribution: {error}
        </p>
      )}
      {attribution !== null && (
        <div className="risk-grid">
          <AttributionWaterfall
            attribution={attribution}
            kicker={`${portfolioId || "portfolio"} ${tradeDate || "latest"}`}
          />
        </div>
      )}
    </div>
  );
}

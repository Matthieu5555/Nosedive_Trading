import type { AttributionResponse } from "../../api";
import { AttributionWaterfall } from "../../components/AttributionWaterfall";
import { Cluster, Stack } from "../../components/layout";

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
    <Stack gap="md">
      <article className="panel" aria-label="P&L attribution">
        <Stack gap="md">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">Attribution</p>
              <h2>Explain the P&amp;L by Greek</h2>
            </div>
          </div>
          <p className="basket-tab__lead">
            Read a persisted book's P&amp;L back as the per-Greek contributions that produced it (Δ
            → Γ → Vega → Θ), with the unexplained residual carried as its own bar.
          </p>
          <Cluster gap="sm" align="end">
            <label>
              Portfolio (attribution){" "}
              <input
                aria-label="portfolio"
                value={portfolioId}
                onChange={(e) => onPortfolioId(e.target.value)}
              />
            </label>
          </Cluster>
          <Cluster gap="xs">
            <button type="button" onClick={onLoad} disabled={loading}>
              {loading ? "Loading attribution…" : "P&L attribution"}
            </button>
          </Cluster>

          {error !== null && (
            <p role="alert" className="error">
              Failed to load attribution: {error}
            </p>
          )}
        </Stack>
      </article>
      {attribution !== null && (
        <div className="risk-grid">
          <AttributionWaterfall
            attribution={attribution}
            kicker={`${portfolioId || "portfolio"} ${tradeDate || "latest"}`}
          />
        </div>
      )}
    </Stack>
  );
}

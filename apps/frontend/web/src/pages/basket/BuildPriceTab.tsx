import type { BasketRiskResponse } from "../../api";
import { BasketRiskPanel } from "../../components/BasketRiskPanel";
import { Cluster, Stack } from "../../components/layout";

type BuildPriceTabProps = {
  canPrice: boolean;
  loading: boolean;
  error: string | null;
  result: BasketRiskResponse | null;
  currency: string;
  onPrice: () => void;
};

export function BuildPriceTab({
  canPrice,
  loading,
  error,
  result,
  currency,
  onPrice,
}: BuildPriceTabProps) {
  return (
    <Stack gap="md">
      <article className="panel" aria-label="Price the basket">
        <Stack gap="sm">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">Price</p>
              <h2>Price the basket</h2>
            </div>
          </div>
          <p className="basket-tab__lead">
            Price the composed basket off the Market analytics. Every number is the book-additive
            sum of the per-position dollar Greeks, never a fresh pricing pass.
          </p>
          <Cluster gap="xs">
            <button type="button" onClick={onPrice} disabled={loading || !canPrice}>
              {loading ? "Pricing…" : "Price basket"}
            </button>
          </Cluster>

          {error !== null && (
            <p role="alert" className="error">
              Failed to price basket: {error}
            </p>
          )}
        </Stack>
      </article>
      {result !== null && <BasketRiskPanel result={result} currency={currency} />}
    </Stack>
  );
}

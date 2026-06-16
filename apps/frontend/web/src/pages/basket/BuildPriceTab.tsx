import type { BasketRiskResponse } from "../../api";
import { BasketRiskPanel } from "../../components/BasketRiskPanel";

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
    <div className="basket-tab">
      <p className="basket-tab__lead">
        Price the composed basket off the Market analytics. Every number is the book-additive sum
        of the per-position dollar Greeks — never a fresh pricing pass.
      </p>
      <div className="basket-actions">
        <button type="button" onClick={onPrice} disabled={loading || !canPrice}>
          {loading ? "Pricing…" : "Price basket"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="error">
          Failed to price basket: {error}
        </p>
      )}
      {result !== null && <BasketRiskPanel result={result} currency={currency} />}
    </div>
  );
}

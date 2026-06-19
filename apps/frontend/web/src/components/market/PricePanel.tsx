import type { PriceHistoryResponse } from "../../api";
import { tourAnchor } from "../../lib/tour";
import { AsyncBlock } from "../AsyncBlock";
import { PriceChart } from "../charts";
import { ErrorBoundary } from "../ErrorBoundary";
import { Scroll, Stack } from "../layout";

// The Daily price element: the chosen ticker's open/high/low/close history. Self-contained, with its
// own heading, async + error boundary and the empty state PriceChart draws when no bars land. Whatever
// ticker is active drives it; the page passes the matching price payload.
export function PricePanel({
  subject,
  asOfPhrase,
  data,
  loading,
  error,
}: {
  subject: string;
  asOfPhrase: string;
  data: PriceHistoryResponse | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <article
      className="panel"
      aria-label={`${subject} daily history`}
      {...tourAnchor(
        "market.price",
        "Daily price chart",
        "The daily open, high, low and close history for the chosen index.",
      )}
    >
      <Stack gap="md">
        <div className="panel-heading">
          <h2>Daily price, {subject}</h2>
          <span className="status">{asOfPhrase} · OHLC</span>
        </div>
        <ErrorBoundary label="Price">
          <AsyncBlock loading={loading} error={error} height={440} subject={`the ${subject} price`}>
            {data && (
              <Scroll label={`${subject} daily price chart`}>
                <PriceChart data={data} />
              </Scroll>
            )}
          </AsyncBlock>
        </ErrorBoundary>
      </Stack>
    </article>
  );
}

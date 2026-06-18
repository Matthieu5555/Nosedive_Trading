import type { AnalyticsMaturity, Signal } from "../../api";
import { AsyncBlock } from "../AsyncBlock";
import { ErrorBoundary } from "../ErrorBoundary";
import { Scorecards } from "../Scorecards";

// The headline indicators element for the active ticker. A thin bounded wrapper: it owns the async +
// error boundary and the empty/loading state, and hands the resolved signals to Scorecards, which
// renders the band and re-derives no math.
export function ScorecardsPanel({
  maturities,
  ivVsRealized,
  termStructureSlope,
  ivRank,
  impliedCorrelation,
  subject,
  closeInstant,
  asOf,
  runId,
  loading,
  error,
}: {
  maturities: AnalyticsMaturity[] | null;
  ivVsRealized: Signal | null;
  termStructureSlope: Signal | null;
  ivRank: Signal | null;
  impliedCorrelation: Signal | null;
  subject: string;
  closeInstant: string | null;
  asOf: string | null;
  runId: string | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <ErrorBoundary label="Scorecards">
      <AsyncBlock
        loading={loading}
        error={error}
        height={140}
        subject={`the ${subject} indicators`}
      >
        {maturities && (
          <Scorecards
            maturities={maturities}
            ivVsRealized={ivVsRealized}
            termStructureSlope={termStructureSlope}
            ivRank={ivRank}
            impliedCorrelation={impliedCorrelation}
            underlying={subject}
            closeInstant={closeInstant}
            asOf={asOf}
            runId={runId}
          />
        )}
      </AsyncBlock>
    </ErrorBoundary>
  );
}

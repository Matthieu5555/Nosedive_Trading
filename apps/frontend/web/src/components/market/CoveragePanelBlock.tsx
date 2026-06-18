import { useState } from "react";

import { CoveragePanel } from "../CoverageTable";
import { ErrorBoundary } from "../ErrorBoundary";
import { Stack } from "../layout";

// The Capture coverage element: how much of the captured option chain the surface rests on, for the
// active ticker. Mounts collapsed (it is a drilldown, not a headline) and expands on demand; the
// CoveragePanel inside owns its own fetch + empty state. Self-contained with its own heading and
// error boundary.
export function CoveragePanelBlock({
  underlying,
  tradeDate,
  runId,
}: {
  underlying: string;
  tradeDate: string | null;
  runId?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <article className="panel" aria-label="Capture coverage" data-tour-id="market.coverage">
      <Stack gap="md">
        <div className="panel-heading">
          <h2>Capture coverage</h2>
          <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? "Hide" : "Show"}
          </button>
        </div>
        {open && (
          <ErrorBoundary label="Capture coverage">
            <CoveragePanel
              underlying={underlying}
              tradeDate={tradeDate ?? undefined}
              runId={runId}
            />
          </ErrorBoundary>
        )}
      </Stack>
    </article>
  );
}

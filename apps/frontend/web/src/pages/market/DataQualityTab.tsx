import type { Constituent } from "../../api";
import { ConstituentTable } from "../../components/ConstituentTable";
import { CoveragePanel } from "../../components/CoverageTable";
import { ErrorBoundary } from "../../components/ErrorBoundary";

// The data-quality readout, off the main analytics view but one click away: the basket composition
// (weights) and the capture-coverage table that the smooth surface papers over. Picking a member
// here selects it as the analytics entity, so a row is also a shortcut into its surface.
export function DataQualityTab({
  index,
  asOf,
  constituents,
  entity,
  onEntity,
}: {
  index: string;
  asOf: string;
  constituents: Constituent[];
  entity: string;
  onEntity: (symbol: string) => void;
}) {
  return (
    <div className="dataquality-stack">
      <article className="panel" aria-label="Index constituents">
        <div className="panel-heading">
          <h2>Constituents</h2>
          <span className="status">{constituents.length} members</span>
        </div>
        {constituents.length === 0 ? (
          <p>
            No constituents for {index} as of {asOf}.
          </p>
        ) : (
          <ConstituentTable constituents={constituents} selected={entity} onSelect={onEntity} />
        )}
      </article>

      <article className="panel coverage-panel" aria-label={`Capture coverage for ${index}`}>
        <div className="panel-heading">
          <h2>Capture coverage</h2>
          <span className="status">data check</span>
        </div>
        <ErrorBoundary label="Capture coverage">
          <CoveragePanel underlying={index} tradeDate={asOf} />
        </ErrorBoundary>
      </article>
    </div>
  );
}

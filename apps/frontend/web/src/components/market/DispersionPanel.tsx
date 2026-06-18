import type { Signal } from "../../api";
import { AsyncBlock } from "../AsyncBlock";
import { DispersionStrip } from "../DispersionStrip";
import { ErrorBoundary } from "../ErrorBoundary";
import { InfoDot } from "../InfoDot";
import { Cluster, Stack } from "../layout";

// The Avg correlation element. This one reads the INDEX, not the active ticker: dispersion is a
// property of the index against its members, so it stays keyed to the index even when a constituent
// is the active ticker. Self-contained, with its own heading, how-to-read note, async + error
// boundary, and DispersionStrip's own honest "no signal yet" empty state.
export function DispersionPanel({
  index,
  asOfPhrase,
  signal,
  loading,
  error,
}: {
  index: string;
  asOfPhrase: string;
  signal: Signal | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <article className="panel" aria-label="Dispersion" data-tour-id="market.dispersion">
      <Stack gap="md">
        <div className="panel-heading">
          <Cluster gap="2xs" align="center">
            <h2>Avg correlation (ρ), {index}</h2>
            <InfoDot
              label="Dispersion, how to read it"
              body={`How tightly the ${index} members are expected to move together. A high average correlation (ρ near 1) means the index moves as one block, so index vol is dear relative to its members; a low ρ means the members move independently, the case for a dispersion trade. Today a realized-vol diagnostic until constituent implied vols land.`}
            />
          </Cluster>
          <span className="status">{asOfPhrase} · realized-vol diagnostic</span>
        </div>
        <ErrorBoundary label="Dispersion">
          <AsyncBlock loading={loading} error={error}>
            <DispersionStrip index={index} signal={signal} />
          </AsyncBlock>
        </ErrorBoundary>
      </Stack>
    </article>
  );
}

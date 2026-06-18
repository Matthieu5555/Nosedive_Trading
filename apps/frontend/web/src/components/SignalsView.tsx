import { type Signal, SIGNAL_CAPTIONS, type SignalsResponse } from "../api";
import { cleanText, sciUnit, signalLabel } from "../lib/format";
import { InfoDot } from "./InfoDot";
import { Cluster, Scroll, Stack } from "./layout";

interface BarSpec {
  leftPct: number;
  widthPct: number;
  tone: "positive" | "negative" | "neutral";
}

function clamp01(value: number): number {
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

// iv_rank lives in [0,1] and reads left-to-right as a fraction of its 1-year range; the bar fills
// from the left to that fraction.
function rankBar(value: number): BarSpec {
  return { leftPct: 0, widthPct: clamp01(value) * 100, tone: "neutral" };
}

// A signed reading on a symmetric axis (centre = 0): correlation spans a fixed [-1,1], the
// vol-point kinds span ±max within the panel so the relative magnitudes stay legible. Positive
// grows rightward from centre, negative leftward; colour carries the sign.
function signedBar(value: number, scale: number): BarSpec {
  const half = scale <= 0 ? 0 : (Math.min(Math.abs(value), scale) / scale) * 50;
  if (value >= 0) {
    return { leftPct: 50, widthPct: half, tone: "positive" };
  }
  return { leftPct: 50 - half, widthPct: half, tone: "negative" };
}

function isFixedSymmetric(kind: string): boolean {
  return kind === "implied_correlation";
}

function isRank(kind: string): boolean {
  return kind === "iv_rank";
}

// The human-readable value, per kind: iv_rank as a percent of its range, everything else in
// scientific notation with its unit so the magnitude and unit are never lost.
function formatValue(signal: Signal): string {
  if (isRank(signal.signal_kind)) {
    return `${(signal.value * 100).toFixed(1)}%`;
  }
  return cleanText(sciUnit(signal.value, signal.unit));
}

function panelScale(kind: string, rows: Signal[]): number {
  if (isFixedSymmetric(kind)) return 1;
  return rows.reduce((max, row) => Math.max(max, Math.abs(row.value)), 0);
}

function barFor(signal: Signal, scale: number): BarSpec {
  if (isRank(signal.signal_kind)) return rankBar(signal.value);
  return signedBar(signal.value, scale);
}

function SignalRow({ signal, scale }: { signal: Signal; scale: number }) {
  const bar = barFor(signal, scale);
  const showCentre = !isRank(signal.signal_kind);
  return (
    <tr>
      <td className="signal-subject">{signal.subject}</td>
      <td>{signal.tenor_label}</td>
      <td className="signal-value">{formatValue(signal)}</td>
      <td className="signal-bar-cell">
        <div className="signal-bar-track" aria-hidden="true">
          {showCentre && <span className="signal-bar-centre" />}
          <span
            className="signal-bar-fill"
            data-tone={bar.tone}
            style={{ left: `${bar.leftPct}%`, width: `${bar.widthPct}%` }}
          />
        </div>
      </td>
    </tr>
  );
}

// The per-tenor implied-correlation rows sit within this much of each other before we treat them as
// one read worth a single headline row (with the full per-tenor detail still one hover away).
const CORRELATION_FLAT_BAND = 0.02;

function isFlatCorrelation(kind: string, rows: Signal[]): boolean {
  if (!isFixedSymmetric(kind) || rows.length < 2) return false;
  const values = rows.map((row) => row.value);
  return Math.max(...values) - Math.min(...values) <= CORRELATION_FLAT_BAND;
}

function KindPanel({ kind, rows }: { kind: string; rows: Signal[] }) {
  const label = signalLabel(rows[0]?.label ?? kind);
  const unit = rows[0]?.unit ? cleanText(rows[0].unit) : null;
  const caption = SIGNAL_CAPTIONS[kind];
  const scale = panelScale(kind, rows);

  // When the per-tenor implied-correlation reads are all but identical, the table degenerates into
  // ~6 near-duplicate rows. Collapse to one headline row and tuck the full per-tenor breakdown
  // behind the heading's ⓘ, so nothing is lost, only de-cluttered.
  const flat = isFlatCorrelation(kind, rows);
  const shownRows = flat ? rows.slice(0, 1) : rows;
  const tenorDetail = flat ? (
    <span>
      Near-identical across tenors ({rows.map((r) => r.tenor_label).join(", ")}):{" "}
      {rows.map((r) => `${r.tenor_label} ${formatValue(r)}`).join(" · ")}.
    </span>
  ) : null;

  return (
    <article className="panel signal-panel">
      <Stack gap="md">
        <div className="panel-heading">
          <Cluster gap="2xs" align="center">
            <h2>{label}</h2>
            {caption && <InfoDot label={`${label}, how to read it`} body={caption} />}
            {tenorDetail && <InfoDot label={`${label}, per-tenor detail`} body={tenorDetail} />}
          </Cluster>
          {unit && <span className="signal-unit">{unit}</span>}
        </div>
        <Scroll label={`${label} signals`}>
          <table role="table" aria-label={`${label} signals`}>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Tenor</th>
                <th scope="col">Value</th>
                <th scope="col">
                  {isFixedSymmetric(kind) ? "−1 … 0 … +1" : isRank(kind) ? "0 … 100%" : "magnitude"}
                </th>
              </tr>
            </thead>
            <tbody>
              {shownRows.map((signal) => (
                <SignalRow
                  key={`${signal.subject}-${signal.tenor_label}`}
                  signal={signal}
                  scale={scale}
                />
              ))}
            </tbody>
          </table>
        </Scroll>
      </Stack>
    </article>
  );
}

export function SignalsView({ data }: { data: SignalsResponse }) {
  if (data.n_signals === 0) {
    return (
      <div className="state-panel" role="status">
        No signals recorded for {data.underlying}
        {data.trade_date ? ` on ${data.trade_date}` : ""} yet.
      </div>
    );
  }
  return (
    <Stack gap="md">
      <p className="signals-asof">
        {data.n_signals} signal{data.n_signals === 1 ? "" : "s"} for {data.underlying}
        {data.trade_date ? `, as of ${data.trade_date}` : ""}
        {data.snapshot_ts ? ` (snapshot ${data.snapshot_ts})` : ""}
      </p>
      {data.kinds.map((kind) => {
        const rows = data.by_kind[kind] ?? [];
        if (rows.length === 0) return null;
        return <KindPanel key={kind} kind={kind} rows={rows} />;
      })}
    </Stack>
  );
}

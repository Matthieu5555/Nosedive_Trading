// Capture-coverage panel (T-capture-coverage-panel): the captured option chain as a plain quality
// table. The surface view smooths over gaps; this shows them. Two sections, both from data already
// on disk (no recompute): per-expiry capture counts, and per-tenor QC coverage across the WHOLE
// pinned grid — so an empty tenor (1m…3y) shows as a labeled zero-row, never silently omitted.
//
// Types are declared locally (not in ../api) so this panel is a self-contained drop-in.
// `CoverageTable` is presentational (takes data, unit-tested); `CoveragePanel` is the self-fetching
// wrapper a page drops in with one line.

import { useFetch } from "../hooks/useFetch";
import { sci, UNITS } from "../lib/format";
import { AsyncBlock } from "./AsyncBlock";

export type QcStatus = "pass" | "fail" | "unknown";

export interface CoverageExpiry {
  expiry: string;
  tenor: string;
  n_strikes: number;
  n_calls: number;
  n_puts: number;
  strike_min: number | null;
  strike_max: number | null;
}

export interface CoverageTenor {
  tenor: string;
  measured: number | null;
  floor: number | null;
  status: QcStatus;
}

export interface CoverageData {
  underlying: string;
  trade_date: string | null;
  n_expiries: number;
  expiries: CoverageExpiry[];
  tenors: CoverageTenor[];
  qc_status: QcStatus;
  delta_band_status: QcStatus;
}

const STATUS_GLYPH: Record<QcStatus, string> = { pass: "✓", fail: "✗", unknown: "—" };

function StatusBadge({ status, label }: { status: QcStatus; label: string }) {
  return (
    <span data-status={status} title={`${label}: ${status}`}>
      {STATUS_GLYPH[status]} {label}
    </span>
  );
}

// The strike span is two analytics quantities (strikes): each endpoint in scientific notation,
// the "$" unit shown once after the range since both endpoints share it.
function span(min: number | null, max: number | null): string {
  if (min === null || max === null) return "—";
  return `${sci(min)}–${sci(max)} ${UNITS.strike}`;
}

/** Presentational: render the two coverage sections from an already-fetched payload. */
export function CoverageTable({ data }: { data: CoverageData }) {
  return (
    <section aria-label="Capture coverage">
      <header>
        Capture coverage — {data.underlying} {data.trade_date ?? "(no data)"}{" "}
        <StatusBadge status={data.qc_status} label="QC" />{" "}
        <StatusBadge status={data.delta_band_status} label="30Δ band" />
      </header>

      {data.n_expiries === 0 ? (
        <p role="status">No capture for this date.</p>
      ) : (
        <table role="table" aria-label="Captured expiries">
          <thead>
            <tr>
              <th>Expiry</th>
              <th>Tenor</th>
              <th>Strikes</th>
              <th>C / P</th>
              <th>Strike span</th>
            </tr>
          </thead>
          <tbody>
            {data.expiries.map((row) => (
              <tr key={row.expiry}>
                <td>{row.expiry}</td>
                <td>{row.tenor}</td>
                <td>{row.n_strikes}</td>
                <td>
                  {row.n_calls} / {row.n_puts}
                </td>
                <td>{span(row.strike_min, row.strike_max)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <table role="table" aria-label="Per-tenor coverage">
        <thead>
          <tr>
            <th>Tenor</th>
            <th>Measured</th>
            <th>Floor</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {data.tenors.map((row) => (
            <tr key={row.tenor} data-status={row.status}>
              <td>{row.tenor}</td>
              <td>{row.measured ?? "—"}</td>
              <td>{row.floor ?? "—"}</td>
              <td>
                <StatusBadge status={row.status} label={row.tenor} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Self-fetching wrapper: drop `<CoveragePanel underlying=… tradeDate=… />` into a page. */
export function CoveragePanel({
  underlying,
  tradeDate,
}: {
  underlying: string;
  tradeDate?: string;
}) {
  const query =
    `/api/coverage?underlying=${encodeURIComponent(underlying)}` +
    (tradeDate ? `&trade_date=${encodeURIComponent(tradeDate)}` : "");
  const { data, loading, error } = useFetch<CoverageData>(query);
  return (
    <AsyncBlock loading={loading} error={error}>
      {data && <CoverageTable data={data} />}
    </AsyncBlock>
  );
}

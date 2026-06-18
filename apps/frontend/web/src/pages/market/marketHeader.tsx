import type { AvailableDate, QcVerdict, RecordedDatesResponse } from "../../api";

export function QcBadge({ qc }: { qc: QcVerdict }) {
  const text = qc === "pass" ? "QC pass" : qc === "fail" ? "QC fail" : "QC n/a";
  return (
    <span className={`qc-badge qc-badge--${qc}`} aria-label={`QC ${qc}`}>
      {text}
    </span>
  );
}

// The wall-clock time a fetch landed, read straight off the recorded_ts ISO string (HH:MM:SS in
// the zone it was recorded — we slice rather than re-parse so a viewer's timezone never shifts it).
// Null when no timestamp was recorded, so the label can omit the time rather than print a placeholder.
function fetchTime(recordedTs: string | null): string | null {
  if (!recordedTs) return null;
  const match = recordedTs.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : recordedTs;
}

const qcSuffix = (qc: QcVerdict): string =>
  qc === "fail" ? " (QC fail)" : qc === "unknown" ? " (QC n/a)" : "";

// The stable identity of a fetch row. A genuine run-partitioned fetch carries its run_id; a legacy
// flat partition (the BFF doesn't yet emit per-run ids) has none, so we fall back to its trade date.
// Without this, every flat row keys to the same `undefined` and the whole picker collapses to one.
export function fetchKey(fetch: AvailableDate): string {
  return fetch.run_id ?? fetch.date;
}

// Label one fetch as "<date> · <HH:MM:SS>". Two fetches can't share a run_id, but they *could*
// land in the same second — so when a date+time label isn't unique we disambiguate with the
// short key in parens, exactly the tie-break the user asked for.
export function fetchOptionLabels(available: AvailableDate[]): Map<string, string> {
  const base = new Map<string, string>();
  const counts = new Map<string, number>();
  for (const fetch of available) {
    const time = fetchTime(fetch.recorded_ts);
    const label = time ? `${fetch.date} · ${time}` : fetch.date;
    base.set(fetchKey(fetch), label);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const labels = new Map<string, string>();
  for (const fetch of available) {
    const key = fetchKey(fetch);
    const label = base.get(key) ?? key;
    const collides = (counts.get(label) ?? 0) > 1;
    const disambiguated = collides ? `${label} (${key.slice(0, 8)})` : label;
    labels.set(key, `${disambiguated}${qcSuffix(fetch.qc)}`);
  }
  return labels;
}

// The as-of picker, populated from the recorded-dates ``available`` list — one row per *fetch*
// (capture run), newest first, each option valued by its run_id so re-fetching a day adds a row
// instead of replacing one. Each carries its QC verdict; until it loads, a disabled placeholder
// keeps the header layout stable.
export function AsOfSelect({
  recorded,
  value,
  onChange,
}: {
  recorded: RecordedDatesResponse | null;
  value: string | null;
  onChange: (runId: string) => void;
}) {
  const available = recorded?.available ?? [];
  const effective = value ?? (available[0] ? fetchKey(available[0]) : "");
  const labels = fetchOptionLabels(available);
  return (
    <select
      aria-label="As-of fetch"
      data-tour-id="market.as-of"
      value={effective}
      disabled={available.length === 0}
      onChange={(event) => onChange(event.target.value)}
    >
      {available.length === 0 ? (
        <option value="">No recorded fetches</option>
      ) : (
        available.map((fetch) => (
          <option key={fetchKey(fetch)} value={fetchKey(fetch)}>
            {labels.get(fetchKey(fetch))}
          </option>
        ))
      )}
    </select>
  );
}

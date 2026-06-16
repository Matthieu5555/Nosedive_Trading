import type { QcVerdict, RecordedDatesResponse } from "../../api";

export function QcBadge({ qc }: { qc: QcVerdict }) {
  const text = qc === "pass" ? "QC pass" : qc === "fail" ? "QC fail" : "QC n/a";
  return (
    <span className={`qc-badge qc-badge--${qc}`} aria-label={`QC ${qc}`}>
      {text}
    </span>
  );
}

// The as-of date picker, populated from the recorded-dates response's ``available`` list (every
// viewable day, incl. qc-failing ones — each option carries its QC verdict). Until it loads it
// shows a single disabled placeholder so the header layout is stable.
export function AsOfSelect({
  recorded,
  value,
  onChange,
}: {
  recorded: RecordedDatesResponse | null;
  value: string | null;
  onChange: (date: string) => void;
}) {
  const available = recorded?.available ?? [];
  const effective = value ?? available[0]?.date ?? "";
  return (
    <select
      aria-label="As-of date"
      value={effective}
      disabled={available.length === 0}
      onChange={(event) => onChange(event.target.value)}
    >
      {available.length === 0 ? (
        <option value="">No recorded dates</option>
      ) : (
        available.map(({ date, qc }) => (
          <option key={date} value={date}>
            {date}
            {qc === "fail" ? " (QC fail)" : qc === "unknown" ? " (QC n/a)" : ""}
          </option>
        ))
      )}
    </select>
  );
}

import type { Job } from "../../api";

function stagePercent(index: number, total: number): number {
  if (total <= 0) return 0;
  const clamped = Math.max(0, Math.min(index, total));
  return Math.round((clamped / total) * 100);
}

function hasStage(job: Job): job is Job & { stage_index: number; stage_total: number } {
  return (
    typeof job.stage_index === "number" &&
    typeof job.stage_total === "number" &&
    job.stage_total > 0
  );
}

export function JobProgress({ job }: { job: Job }) {
  if (job.state !== "running") return null;

  if (hasStage(job)) {
    const percent = stagePercent(job.stage_index, job.stage_total);
    const stageText = `step ${job.stage_index}/${job.stage_total}`;
    const label = job.stage ? `${stageText} · ${job.stage}` : stageText;
    return (
      <div className="ops-progress">
        <div
          className="ops-progress__bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-label={label}
        >
          <span className="ops-progress__fill" style={{ width: `${percent}%` }} />
        </div>
        <span className="ops-progress__label">
          {label} · {percent}%
        </span>
      </div>
    );
  }

  return (
    <div className="ops-progress">
      <div
        className="ops-progress__bar ops-progress__bar--indeterminate"
        role="progressbar"
        aria-label="in progress…"
      >
        <span className="ops-progress__fill ops-progress__fill--indeterminate" />
      </div>
      <span className="ops-progress__label">in progress…</span>
    </div>
  );
}

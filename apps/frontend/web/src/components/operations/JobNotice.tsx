import { useEffect, useRef, useState } from "react";

import type { Job } from "../../api";

interface Notice {
  job_id: string;
  tone: "done" | "error";
  text: string;
}

function noticeFor(job: Job): Notice {
  if (job.state === "done") {
    return {
      job_id: job.job_id,
      tone: "done",
      text: `${job.underlying} capture complete, surface ready.`,
    };
  }
  return {
    job_id: job.job_id,
    tone: "error",
    text: `${job.underlying} capture failed: ${job.message || "unknown reason"}.`,
  };
}

export function useJobNotice(jobs: Job[]): Notice | null {
  const prevStates = useRef<Map<string, Job["state"]>>(new Map());
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    const prev = prevStates.current;
    const next = new Map<string, Job["state"]>();
    let landed: Notice | null = null;
    for (const job of jobs) {
      const before = prev.get(job.job_id);
      const terminal = job.state === "done" || job.state === "error";
      const wasLive = before === "queued" || before === "running";
      if (terminal && wasLive) {
        landed = noticeFor(job);
      }
      next.set(job.job_id, job.state);
    }
    prevStates.current = next;
    if (landed) setNotice(landed);
  }, [jobs]);

  return notice;
}

export function JobNotice({ jobs }: { jobs: Job[] }) {
  const notice = useJobNotice(jobs);
  if (!notice) return null;
  if (notice.tone === "error") {
    return (
      <p role="alert" className="error ops-notice ops-notice--error">
        {notice.text}
      </p>
    );
  }
  return (
    <p role="status" aria-live="polite" className="ops-notice ops-notice--done">
      {notice.text}
    </p>
  );
}

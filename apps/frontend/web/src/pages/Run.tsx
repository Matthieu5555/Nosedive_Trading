import { useState } from "react";

import type { Job, ProvidersResponse } from "../api";
import { getJson, postJson } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { useFetch } from "../hooks/useFetch";

export function RunPage() {
  const providers = useFetch<ProvidersResponse>("/api/providers");
  const [provider, setProvider] = useState("SAMPLE");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function launch() {
    setError(null);
    try {
      const launched = await postJson<Job>("/api/run", { provider });
      setJob(launched);
      poll(launched.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function poll(jobId: string) {
    const tick = async () => {
      const current = await getJson<Job>(`/api/jobs/${jobId}`);
      setJob(current);
      if (current.state === "queued" || current.state === "running") {
        window.setTimeout(tick, 500);
      }
    };
    window.setTimeout(tick, 500);
  }

  return (
    <section>
      <h1>Run a Pipeline</h1>
      <AsyncBlock state={providers}>
        {(data) => (
          <div>
            <label>
              Provider{" "}
              <select
                aria-label="provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              >
                {data.providers.map((p) => (
                  <option key={p.provider} value={p.provider} disabled={p.status !== "ready"}>
                    {p.provider} ({p.status})
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={launch}>
              Launch
            </button>
          </div>
        )}
      </AsyncBlock>
      {error !== null && <p role="alert" className="error">{error}</p>}
      {job !== null && (
        <p role="status">
          Job {job.job_id}: <strong>{job.state}</strong> — {job.message}
        </p>
      )}
    </section>
  );
}

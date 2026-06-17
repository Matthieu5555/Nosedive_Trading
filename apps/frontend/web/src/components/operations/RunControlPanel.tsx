import { useEffect, useMemo, useState } from "react";

import { Label } from "@/ui/label";

import { ApiError, type Job, type Provider } from "../../api";
import {
  type JobsResponse,
  useJobs,
  useLaunchRun,
  useProviders,
  useRunUnderlyings,
} from "../../hooks/queries";
import { statusLabel } from "../../lib/format";
import { AsyncBlock } from "../AsyncBlock";
import { InfoDot } from "../InfoDot";
import { JobNotice } from "./JobNotice";
import { JobProgress } from "./JobProgress";

const LAUNCH_GLOSS =
  "Replay the last captured day as a new surface — writes nothing to disk until validated.";

const JOB_STATE_CLASS: Record<Job["state"], string> = {
  queued: "ops-pill--warn",
  running: "ops-pill--warn",
  done: "ops-pill--ok",
  error: "ops-pill--bad",
};

function clockTime(ts: string | null): string {
  if (!ts) return "—";
  const match = ts.match(/T(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : ts;
}

function ProviderOption({ provider }: { provider: Provider }) {
  const runnable = provider.status === "ready";
  const suffix = runnable ? "" : ` — ${statusLabel(provider.status)}`;
  return (
    <option value={provider.provider} disabled={!runnable}>
      {provider.provider}
      {suffix}
    </option>
  );
}

function JobRow({ job }: { job: Job }) {
  return (
    <tr>
      <td>
        <span className={`ops-pill ${JOB_STATE_CLASS[job.state]}`}>{statusLabel(job.state)}</span>
      </td>
      <td>{job.provider}</td>
      <td>{job.underlying}</td>
      <td>{clockTime(job.started_at)}</td>
      <td>{clockTime(job.finished_at)}</td>
      <td className="ops-job-message">
        {job.state === "running" ? <JobProgress job={job} /> : job.message || "—"}
      </td>
    </tr>
  );
}

function JobsTable({ data }: { data: JobsResponse }) {
  if (data.jobs.length === 0) {
    return (
      <p className="panel-note" role="status">
        No runs launched this session yet. Launch one above to fetch a fresh surface.
      </p>
    );
  }
  return (
    <div className="ops-table-wrap">
      <table className="ops-table">
        <thead>
          <tr>
            <th>State</th>
            <th>Provider</th>
            <th>Underlying</th>
            <th>Started</th>
            <th>Finished</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {data.jobs.map((job) => (
            <JobRow key={job.job_id} job={job} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function firstRunnable(providers: Provider[]): string {
  return providers.find((p) => p.status === "ready")?.provider ?? providers[0]?.provider ?? "";
}

export function RunControlPanel() {
  const providers = useProviders();
  const underlyings = useRunUnderlyings();
  const jobs = useJobs();
  const launch = useLaunchRun();

  const [provider, setProvider] = useState("");
  const [underlying, setUnderlying] = useState("");

  const providerList = useMemo(() => providers.data?.providers ?? [], [providers.data]);
  const underlyingList = useMemo(() => underlyings.data?.underlyings ?? [], [underlyings.data]);

  useEffect(() => {
    if (providerList.length === 0) return;
    if (!provider || !providerList.some((p) => p.provider === provider)) {
      setProvider(firstRunnable(providerList));
    }
  }, [providerList, provider]);

  useEffect(() => {
    if (underlyingList.length === 0) return;
    if (!underlying || !underlyingList.includes(underlying)) {
      setUnderlying(underlyingList[0]);
    }
  }, [underlyingList, underlying]);

  const selected = providerList.find((p) => p.provider === provider);
  const canLaunch = Boolean(provider) && selected?.status === "ready" && !launch.isPending;
  const launchError =
    launch.error instanceof ApiError
      ? launch.error.detail
      : launch.error instanceof Error
        ? launch.error.message
        : null;

  return (
    <div className="ops-run">
      <AsyncBlock
        loading={providers.isPending || underlyings.isPending}
        error={providers.isError ? providers.error.message : null}
      >
        <div className="ops-run__controls control-row">
          <div className="control-field">
            <Label htmlFor="ops-provider">Data provider</Label>
            <select
              id="ops-provider"
              aria-label="Data provider"
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
            >
              {providerList.map((p) => (
                <ProviderOption key={p.provider} provider={p} />
              ))}
            </select>
          </div>
          <div className="control-field">
            <Label htmlFor="ops-underlying">Underlying</Label>
            <select
              id="ops-underlying"
              aria-label="Underlying"
              value={underlying}
              onChange={(event) => setUnderlying(event.target.value)}
            >
              {underlyingList.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </div>
          <div className="ops-launch-wrap">
            <button
              type="button"
              className="ops-launch"
              disabled={!canLaunch}
              title={LAUNCH_GLOSS}
              onClick={() => launch.mutate({ provider, underlying: underlying || null })}
            >
              {launch.isPending ? "Launching…" : "Launch run"}
            </button>
            <InfoDot label="What does this button do?" body={LAUNCH_GLOSS} />
          </div>
        </div>
      </AsyncBlock>

      {selected && selected.status !== "ready" && <p className="panel-note">{selected.note}</p>}
      {launchError && (
        <p role="alert" className="error">
          Could not launch the run: {launchError}
        </p>
      )}

      <JobNotice jobs={jobs.data?.jobs ?? []} />

      <AsyncBlock loading={jobs.isPending} error={jobs.isError ? jobs.error.message : null}>
        {jobs.data && <JobsTable data={jobs.data} />}
      </AsyncBlock>
    </div>
  );
}

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { HealthResponse, Job, ProvidersResponse, RecordedDatesResponse } from "../api";
import { renderWithClient } from "../test/renderWithClient";
import { jsonGet, notMocked, server } from "../test/server";
import { OperationsPage } from "./Operations";

const HEALTH: HealthResponse = {
  trade_date: "2026-06-01",
  data_flowing: "ok",
  surfaces_building: "ok",
  qc_status: "passing",
  scenarios_current: "current",
  events_total: 81234,
  last_healthy_trade_date: "2026-06-01",
  backlog: [],
  is_healthy: true,
};

const HEALTH_DEGRADED: HealthResponse = {
  trade_date: "2026-06-02",
  data_flowing: "no_data",
  surfaces_building: "missing",
  qc_status: "unknown",
  scenarios_current: "stale",
  events_total: 0,
  last_healthy_trade_date: "2026-06-01",
  backlog: ["analytics", "qc"],
  is_healthy: false,
};

const PROVIDERS: ProvidersResponse = {
  providers: [
    {
      provider: "SAMPLE",
      asset_class: "equity",
      auth_required: false,
      data_latency: "offline",
      status: "ready",
      note: "Offline synthetic chain fixture.",
    },
    {
      provider: "IBKR",
      asset_class: "equity",
      auth_required: false,
      data_latency: "delayed",
      status: "unavailable",
      note: "Live IBKR needs an authenticated CP gateway.",
    },
  ],
};

const RUN_UNDERLYINGS = { underlyings: ["SPX", "SX5E"] };

const QUEUED_JOB: Job = {
  job_id: "job-1",
  provider: "SAMPLE",
  underlying: "SPX",
  state: "queued",
  started_at: null,
  finished_at: null,
  message: "Queued",
  summary: {},
};

const RUNNING_JOB_WITH_STAGE: Job = {
  job_id: "job-2",
  provider: "SAMPLE",
  underlying: "SX5E",
  state: "running",
  started_at: "2026-06-17T17:30:00",
  finished_at: null,
  message: "",
  summary: {},
  stage: "Collecte de la chaîne d'options",
  stage_index: 2,
  stage_total: 4,
};

const RECORDED: RecordedDatesResponse = {
  index: "SPX",
  count: 7,
  dates: ["2026-05-29", "2026-05-28"],
  available: [
    { date: "2026-05-29", run_id: "run-0529", recorded_ts: "2026-05-29T17:30:00", qc: "pass" },
    { date: "2026-05-28", run_id: "run-0528", recorded_ts: "2026-05-28T17:30:00", qc: "fail" },
  ],
};

function mockOps(health: HealthResponse = HEALTH) {
  server.use(
    jsonGet("/api/health", health),
    jsonGet("/api/providers", PROVIDERS),
    jsonGet("/api/run/underlyings", RUN_UNDERLYINGS),
    jsonGet("/api/jobs", { jobs: [] }),
    jsonGet("/api/recorded-dates", RECORDED),
  );
}

test("system health shows a healthy headline and per-stage statuses", async () => {
  mockOps();
  renderWithClient(<OperationsPage />);

  expect(await screen.findByText("Healthy")).toBeInTheDocument();
  expect(screen.getByText("As of trade date 2026-06-01")).toBeInTheDocument();
  // 81234 events rendered with a thousands separator and the unit label.
  expect(screen.getByText("81,234 events")).toBeInTheDocument();
  expect(screen.getAllByText("Ok").length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText("Current")).toBeInTheDocument();
});

test("a degraded system surfaces the backlog and a needs-attention headline", async () => {
  mockOps(HEALTH_DEGRADED);
  renderWithClient(<OperationsPage />);

  expect(await screen.findByText("Needs attention")).toBeInTheDocument();
  expect(screen.getByText(/Waiting to compute: Analytics, Qc/)).toBeInTheDocument();
});

test("run control launches a SAMPLE run and the job appears in the list", async () => {
  mockOps();
  let launched = false;
  server.use(
    http.post("/api/run", () => {
      launched = true;
      return HttpResponse.json(QUEUED_JOB, { status: 202 });
    }),
    // After a launch the BFF's job list carries the new run; before, it is empty.
    http.get("/api/jobs", () => HttpResponse.json({ jobs: launched ? [QUEUED_JOB] : [] })),
  );
  renderWithClient(<OperationsPage />);

  const launch = await screen.findByRole("button", { name: /Launch run/i });
  expect(launch).toBeEnabled();
  await userEvent.click(launch);

  await waitFor(() => expect(launched).toBe(true));
  // The launched job lands in the jobs list (the table headed by "State") as a SAMPLE / Queued row.
  await waitFor(() => {
    const stateHeader = screen.getByRole("columnheader", { name: "State" });
    const jobsTable = stateHeader.closest("table") as HTMLElement;
    expect(within(jobsTable).getByText("SAMPLE")).toBeInTheDocument();
    expect(within(jobsTable).getAllByText("Queued").length).toBeGreaterThanOrEqual(1);
  });
});

test("an unavailable provider is offered disabled, never silently runnable", async () => {
  mockOps();
  renderWithClient(<OperationsPage />);

  const providerSelect = (await screen.findByLabelText("Data provider")) as HTMLSelectElement;
  const ibkr = within(providerSelect).getByRole("option", { name: /IBKR/ }) as HTMLOptionElement;
  expect(ibkr.disabled).toBe(true);
  const sample = within(providerSelect).getByRole("option", {
    name: "SAMPLE",
  }) as HTMLOptionElement;
  expect(sample.disabled).toBe(false);
});

test("freshness reports when risk last computed and the clean-day count", async () => {
  mockOps();
  renderWithClient(<OperationsPage />);

  await waitFor(() => expect(screen.getByText("Risk last computed for")).toBeInTheDocument());
  const metric = screen.getByText("Risk last computed for").closest(".metric") as HTMLElement;
  expect(within(metric).getByText("2026-05-29")).toBeInTheDocument();
  expect(screen.getByText("7 days")).toBeInTheDocument();
});

test("a health fetch error renders through AsyncBlock, not a blank panel", async () => {
  server.use(
    http.get("/api/health", notMocked),
    jsonGet("/api/providers", PROVIDERS),
    jsonGet("/api/run/underlyings", RUN_UNDERLYINGS),
    jsonGet("/api/jobs", { jobs: [] }),
    jsonGet("/api/recorded-dates", RECORDED),
  );
  renderWithClient(<OperationsPage />);

  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});

test("a running job row shows a determinate step tracker, not a frozen pill", async () => {
  server.use(
    jsonGet("/api/health", HEALTH),
    jsonGet("/api/providers", PROVIDERS),
    jsonGet("/api/run/underlyings", RUN_UNDERLYINGS),
    jsonGet("/api/jobs", { jobs: [RUNNING_JOB_WITH_STAGE] }),
    jsonGet("/api/recorded-dates", RECORDED),
  );
  renderWithClient(<OperationsPage />);

  // The running row narrates the engine stage in PM French, with a determinate bar at 50% (2/4).
  expect(await screen.findByText(/step 2\/4/)).toBeInTheDocument();
  expect(screen.getByText(/Collecte de la chaîne d'options/)).toBeInTheDocument();
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveAttribute("aria-valuenow", "50");
});

test("the launch button carries a back-end gloss, reachable via the ⓘ", async () => {
  mockOps();
  renderWithClient(<OperationsPage />);

  const launch = await screen.findByRole("button", { name: /Launch run/i });
  // The intent is legible before the click: the hover title states the back-end action.
  expect(launch).toHaveAttribute("title", expect.stringMatching(/Replay the last captured day/));

  const info = screen.getByRole("button", { name: "What does this button do?" });
  await userEvent.hover(info);
  expect(screen.getByRole("tooltip")).toHaveTextContent(/writes nothing to disk until validated/);
});

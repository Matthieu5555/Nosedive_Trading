import { expect, type Page, test } from "@playwright/test";

import { ANALYTICS_AAA } from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// Principle 4 (action feedback) + Principle 6 (grounded assistant) acceptance criteria from
// tasks/MAT-LEGIBILITY-action-feedback.md, tasks/MAT-LEGIBILITY-assistant.md, and the pair→test
// matrix in tasks/MAT-LEGIBILITY-qa-strategy.md, asserted in a real browser against the mocked BFF.
//
// Every assertion is on the PM's read — visible text, role, and tone — never internal React state
// (template: market-read-flow.spec.ts). Route overrides run AFTER mockBff because Playwright fires
// route handlers most-recent-first, so a per-test "**/api/jobs**" / "**/api/assistant**" wins over
// the shared fixture. Each test asserts pageErrors == [] (a crash is the loudest silent failure).

// A running capture row that DOES report a determinate stage k/N (the BFF stage passthrough). Hand-
// derived expected: stage_index=2, stage_total=4 → "step 2/4" and 2/4 = 50%.
const JOBS_RUNNING_DETERMINATE = {
  jobs: [
    {
      job_id: "job-running-det",
      provider: "SAMPLE",
      underlying: "SX5E",
      state: "running",
      started_at: "2026-06-17T17:30:00",
      finished_at: null,
      message: "Replaying the latest committed day into a surface…",
      summary: {},
      stage: "Collecting the options chain",
      stage_index: 2,
      stage_total: 4,
    },
  ],
};

// A running row whose stage fields are all null — the engine hasn't reported a stage. The contract
// is an HONEST indeterminate "in progress…" with NO fabricated percent (never a CSS-timer fake bar).
const JOBS_RUNNING_INDETERMINATE = {
  jobs: [
    {
      job_id: "job-running-ind",
      provider: "SAMPLE",
      underlying: "SX5E",
      state: "running",
      started_at: "2026-06-17T17:30:00",
      finished_at: null,
      message: "working…",
      summary: {},
      stage: null,
      stage_index: null,
      stage_total: null,
    },
  ],
};

// A back-compat payload: a running row with NO stage keys at all (predates the passthrough). Must
// degrade to the indeterminate bar, never crash (the type is nullable/optional).
const JOBS_RUNNING_LEGACY = {
  jobs: [
    {
      job_id: "job-running-legacy",
      provider: "SAMPLE",
      underlying: "SX5E",
      state: "running",
      started_at: "2026-06-17T17:30:00",
      finished_at: null,
      message: "working…",
      summary: {},
    },
  ],
};

// The active surface frame the BFF echoes back, carrying the resolved close instant (SX5E 17:30 CET,
// OESX settlement — NOT 22:00) and the coverage clause, so the answer wears the same provenance the
// status line shows. Mirrors src/components/Assistant/AssistantPanel.test.tsx FRAME (contract-true).
const ASSISTANT_FRAME = {
  underlying: "SPX",
  trade_date: "2026-05-29",
  run_id: "run-0529",
  mode: "strict" as const,
  close_instant: "2026-05-29 17:30 CET",
  coverage_label: "1 706/2 412 quotes",
};

// A grounded answer: every number it surfaces is a CITATION lifted verbatim from the server facts
// block (already through the house sci/sciUnit idiom), never free-text the model wrote. The panel
// renders the citation value byte-identical and a provenance caption.
const ASSISTANT_GROUNDED = {
  answer: "You are looking at the SPX implied-vol surface at the close.",
  citations: [
    {
      id: "atm_level",
      label: "ATM level",
      value: "1.83 × 10⁻¹ Vol",
      source: "signal enregistré · 3m",
    },
  ],
  grounded: true,
  frame: ASSISTANT_FRAME,
};

// The honest-gap case: the question needed a number the facts block didn't carry, so the assistant
// REFUSES rather than invents — answer is the loud "I won't make it up" copy, citations is empty.
const ASSISTANT_HONEST_GAP = {
  answer:
    "That isn't in what the screen shows for this close — I won't make it up.",
  citations: [],
  grounded: false,
  frame: ASSISTANT_FRAME,
};

async function gotoOperations(page: Page, jobs: unknown): Promise<void> {
  await mockBff(page);
  await page.route("**/api/jobs**", (route) => route.fulfill({ json: jobs }));
  await page.goto("/operations");
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
}

// ── Principle 4: the launch button states what it does in the back end (matrix 4.2) ─────────────

test("the launch button reveals the backend action it fires", async ({ page }) => {
  const { pageErrors } = collectPageErrors(page);
  await gotoOperations(page, { jobs: [] });

  const launch = page.getByRole("button", { name: "Launch run" });
  await expect(launch).toBeVisible();
  // The intent is legible BEFORE the click — the gloss says what fires underneath (no mystery verb),
  // surfaced as the button's title (the future ⓘ-tooltip + assistant read the same string).
  await expect(launch).toHaveAttribute(
    "title",
    /Replay the last captured day.*writes nothing to disk until validated/,
  );
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 4: a running capture narrates DETERMINATE step progress (matrix 4.3) ──────────────

test("a running capture narrates determinate step progress with the stage in PM French", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await gotoOperations(page, JOBS_RUNNING_DETERMINATE);

  // The running row shows a determinate progressbar — a step the eye can follow, not a frozen pill.
  const bar = page.getByRole("progressbar");
  await expect(bar).toBeVisible();
  // "step 2/4" + the real stage name in PM register (never the engine enum like STAGE_COLLECTION).
  await expect(bar).toHaveAttribute("aria-label", /step 2\/4 · Collecting the options chain/);
  // 2 of 4 stages = 50% — a determinate claim that tracks the server's stage count (hand-derived).
  await expect(bar).toHaveAttribute("aria-valuenow", "50");
  await expect(page.getByText("step 2/4 · Collecting the options chain · 50%")).toBeVisible();
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 4: no fabricated percent — null stage degrades to an HONEST indeterminate bar ──────

test("a running capture with no reported stage shows an honest indeterminate bar, never a fake percent", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await gotoOperations(page, JOBS_RUNNING_INDETERMINATE);

  const bar = page.getByRole("progressbar", { name: "in progress…" });
  await expect(bar).toBeVisible();
  await expect(page.getByText("in progress…", { exact: true })).toBeVisible();
  // The honest-gap on progress: when the server can't say which stage, NO percent is claimed.
  await expect(bar).not.toHaveAttribute("aria-valuenow", /.+/);
  await expect(page.getByText(/%/)).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 4 / back-compat: a payload predating the stage passthrough must not crash ─────────

test("a running job payload without stage fields degrades to the indeterminate bar, never a crash", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await gotoOperations(page, JOBS_RUNNING_LEGACY);

  await expect(page.getByRole("progressbar", { name: "in progress…" })).toBeVisible();
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 4: backgroundable — a job that lands announces, at the right altitude, non-blocking ─

test("a capture that lands done announces a non-blocking polite notice, never a silent done-pill", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  // Start the ledger live (running), then flip it to done after one full poll interval (the useJobs
  // refetch is ~4 s while a job is live). The notice fires on the running→done transition observed
  // by useJobNotice — so `running` must genuinely be seen before `done` lands, hence a time gate, not
  // a poll-count gate (a fast double-poll can otherwise skip straight to done). Proves the launch was
  // backgroundable and announces when it lands.
  const t0 = Date.now();
  await page.route("**/api/jobs**", (route) => {
    const body = Date.now() - t0 < 5000 ? JOBS_RUNNING_INDETERMINATE : DONE_LANDED;
    return route.fulfill({ json: body });
  });
  await page.goto("/operations");
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "in progress…" })).toBeVisible({ timeout: 8000 });

  const notice = page.getByText("SX5E capture complete — surface ready.");
  await expect(notice).toBeVisible({ timeout: 15_000 });
  // Announced "polite" (an aria-live status line), never a modal that traps the page (Principle 6:
  // non-blocking). It is a role=status, and the page heading stays interactive behind it.
  await expect(notice).toHaveAttribute("role", "status");
  await expect(page.locator("[role='dialog']")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

const DONE_LANDED = {
  jobs: [
    {
      job_id: "job-running-ind",
      provider: "SAMPLE",
      underlying: "SX5E",
      state: "done",
      started_at: "2026-06-17T17:30:00",
      finished_at: "2026-06-17T17:31:00",
      message: "Built a surface with 6 slices.",
      summary: {},
      stage: null,
      stage_index: null,
      stage_total: null,
    },
  ],
};

// ── Principle 4: a capture that FAILS announces a LOUD red alert (not a silent error pill) ───────

test("a capture that fails announces a loud role=alert notice, never silent", async ({ page }) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  const t0 = Date.now();
  await page.route("**/api/jobs**", (route) => {
    const body = Date.now() - t0 < 5000 ? JOBS_RUNNING_INDETERMINATE : ERROR_LANDED;
    return route.fulfill({ json: body });
  });
  await page.goto("/operations");
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "in progress…" })).toBeVisible({ timeout: 8000 });

  const alert = page.getByText(/SX5E capture failed/);
  await expect(alert).toBeVisible({ timeout: 15_000 });
  await expect(alert).toHaveAttribute("role", "alert");
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

const ERROR_LANDED = {
  jobs: [
    {
      job_id: "job-running-ind",
      provider: "SAMPLE",
      underlying: "SX5E",
      state: "error",
      started_at: "2026-06-17T17:30:00",
      finished_at: "2026-06-17T17:31:00",
      message: "chain fetch timed out",
      summary: {},
      stage: null,
      stage_index: null,
      stage_total: null,
    },
  ],
};

// ── Principle 4 / §3.4: a slow Onglet-1 fetch shows a footprint skeleton past the ~1s floor ──────

test("a slow nappe fetch shows a footprint skeleton in the house idiom, not a bare one-line Loading", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  // Hold /api/analytics open well past SKELETON_DELAY_MS (1000ms) so the skeleton must mount.
  await page.route("**/api/analytics**", async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    return route.fulfill({ json: ANALYTICS_AAA });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();

  // Past the 1s floor the skeleton appears: a status with reserved footprint, in the house French
  // idiom ("Loading…"), NOT the bare one-line English "Loading…" that reflowed the layout (the
  // lie §3.4 forbids). NOTE the Onglet-1 panels do not yet pass a per-panel `subject` to AsyncBlock,
  // so the skeleton names the house idiom but not the specific subject — the ChartSkeleton primitive
  // supports `subject` but Market.tsx does not wire it (open self-describing §2b follow-up).
  const skeleton = page.getByRole("status", { name: /^Loading/ });
  await expect(skeleton.first()).toBeVisible({ timeout: 4000 });
  await expect(skeleton.first()).toHaveAttribute("aria-busy", "true");
  await expect(page.getByText("Loading…", { exact: true })).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 6: the assistant explains the current screen from the ON-SCREEN data (matrix 6.1) ──

test("the assistant explains the current screen and cites a number from the payload", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/assistant**", (route) => route.fulfill({ json: ASSISTANT_GROUNDED }));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();

  // The assistant is a summonable panel, never a wall — closed by default (non-blocking, Principle 6).
  const launch = page.getByRole("button", { name: "Ask the assistant" });
  await expect(launch).toBeVisible();
  await expect(launch).toHaveAttribute("aria-expanded", "false");
  await launch.click();

  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // It answers from the on-screen surface (names the subject), not generically.
  await expect(page.getByText(/nappe de implied vol de SPX/)).toBeVisible();
  // Every number is a CITATION lifted verbatim from the facts block (byte-identical to a scorecard).
  await expect(page.getByRole("list", { name: "Citations" })).toBeVisible();
  await expect(page.getByText("1.83 × 10⁻¹ Vol")).toBeVisible();
  // The provenance caption wears the resolved 17:30 CET close (OESX), NEVER 22:00.
  const caption = page.getByText(/SPX · close 2026-05-29 17:30 CET · strict/);
  await expect(caption).toBeVisible();
  await expect(caption).not.toContainText("22:00");
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 6: the assistant REFUSES an uncited number (matrix 6.2 — grounded in the data layer) ─

test("the assistant refuses to state a number absent from the payload — no hallucinated figure", async ({
  page,
}) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/assistant**", (route) => route.fulfill({ json: ASSISTANT_HONEST_GAP }));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();

  await page.getByRole("button", { name: "Ask the assistant" }).click();
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // The refusal is the contract: it says, plainly, it will not invent the number.
  const gap = page.getByText(/je ne vais pas l'inventer/);
  await expect(gap).toBeVisible();
  await expect(gap).toHaveAttribute("role", "status");
  // The enforcement is in the data layer: no citation list, so no number can leak into the answer.
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0);
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

// ── Principle 6: a BFF/model failure surfaces a LOUD inline error, never a silent dead panel ─────

test("an assistant backend failure surfaces a loud role=alert, never silent", async ({ page }) => {
  const { pageErrors } = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/assistant**", (route) =>
    route.fulfill({
      status: 502,
      json: { error: "assistant_unavailable", detail: "OpenRouter timed out" },
    }),
  );
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();

  await page.getByRole("button", { name: "Ask the assistant" }).click();
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  const alert = page.getByRole("alert").filter({ hasText: /Assistant indisponible/ });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("OpenRouter timed out");
  expect(pageErrors, pageErrors.join("\n")).toEqual([]);
});

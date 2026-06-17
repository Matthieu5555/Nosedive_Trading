import { expect, type Page, type Route, test } from "@playwright/test";

import type { AssistantResponse } from "../src/components/Assistant/assistantApi";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// Real-browser acceptance tests for the AI-first ASSISTANT surface (P6 / MAT-LEGIBILITY-assistant),
// asserting the design-language criteria from tasks/MAT-LEGIBILITY-qa-strategy.md §6 and §3:
//   - every numeric answer carries a CITATION with its unit, and an uncited number is refused
//     (grounded=false → honest-gap copy, never a fabricated value) — the load-bearing guardrail;
//   - the answer wears the active frame's provenance (subject · close instant · mode · coverage),
//     and the close instant is SX5E's 17:30 CET (OESX settlement), never 22:00;
//   - no silent state in the panel: thinking (role=status), answer, and a LOUD error (role=alert)
//     read differently, and the not-ready panel names its subject (empty ≠ error);
//   - switching the index re-writes the frame the assistant posts, so it can never describe a
//     different surface than the page (no stale frame);
//   - no flow leaves an uncaught page error.
//
// The BFF is mocked at the network layer (e2e/mock-bff.ts) exactly like the existing suite; this
// surface's POST /api/assistant is not in the shared mock, so each test overrides it AFTER mockBff
// — Playwright runs route handlers most-recent-first, so the per-test override wins (the pattern
// market-read-flow.spec.ts:50-54 uses). Numbers are hand-derived; nothing reaches a live BFF.

// The active frame the BFF resolves and echoes back. The close instant is the OESX 17:30 CET
// settlement, NOT the XEUR 22:00 futures close — the assistant renders only what the frame carries.
const FRAME_SPX = {
  underlying: "SPX",
  trade_date: "2026-05-29",
  run_id: "run-0529",
  mode: "strict" as const,
  close_instant: "2026-05-29 17:30 CET",
  coverage_label: "1 706/2 412 quotes",
};

const FRAME_SX5E = {
  ...FRAME_SPX,
  underlying: "SX5E",
};

// A GROUNDED answer: the only number it surfaces is a CITATION lifted verbatim from the server-built
// facts block, already run through the house sci/sciUnit idiom (so it carries its unit) — never
// free-text the model wrote. The citation value "1.83 × 10⁻¹ Vol" is the scorecard's ATM vol.
const GROUNDED: AssistantResponse = {
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
  frame: FRAME_SPX,
};

// The HONEST-GAP answer: the question needed a number the facts block didn't carry, so the assistant
// REFUSES to invent it — grounded=false, the loud "I won't invent it" copy, and an EMPTY citation
// list so no number can leak. This is the anti-pattern (a hallucinated number) made impossible.
const HONEST_GAP: AssistantResponse = {
  answer:
    "That isn't in what the screen shows for this close — I won't make it up.",
  citations: [],
  grounded: false,
  frame: FRAME_SPX,
};

// Mount on Onglet 1 (/) with the shared mock; SPX is the default index and RECORDED_TWO_DATES gives
// an effective as-of (2026-05-29), so the panel is "ready" (it gates on underlying + as-of).
async function gotoMarket(page: Page): Promise<void> {
  await mockBff(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
}

// Fulfil POST /api/assistant with a fixed response (mocked OpenRouter via the BFF seam).
async function mockAssistant(page: Page, response: AssistantResponse): Promise<void> {
  await page.route("**/api/assistant**", (route: Route) => route.fulfill({ json: response }));
}

async function openAssistant(page: Page): Promise<void> {
  const panel = page.getByRole("complementary", { name: "Assistant" });
  if (await panel.isVisible().catch(() => false)) return;
  const launch = page.getByRole("button", { name: "Ask the assistant" });
  // The launch button mounts inside the Market status row, which re-mounts while the page settles
  // its fetches; click it and confirm the panel opened, retrying once if the click raced the mount.
  await expect(launch).toBeVisible();
  await expect(launch).toHaveAttribute("aria-expanded", "false");
  await launch.click();
  await expect(panel)
    .toBeVisible({ timeout: 3000 })
    .catch(async () => {
      await launch.click();
      await expect(panel).toBeVisible();
    });
}

test("assistant is non-blocking: closed by default behind a launch button, never a modal wall", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);

  // The page is fully usable with the assistant closed: only a launch button, no panel, no modal.
  await expect(page.getByRole("button", { name: "Ask the assistant" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Assistant" })).toHaveCount(0);
  // The Market read surface is present and unobscured (the self-describing nappe figure renders
  // behind it — its label carries the how-to-read gloss "implied vol vs log-moneyness vs maturity").
  await expect(
    page.getByRole("figure", { name: /implied vol vs log-moneyness vs maturity/ }),
  ).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a grounded answer carries its citation WITH a unit and the provenance frame caption", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);
  await mockAssistant(page, GROUNDED);
  await openAssistant(page);

  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // The answer text appears.
  await expect(page.getByText(/nappe de implied vol de SPX/)).toBeVisible();

  // The numeric claim is a CITATION lifted from the facts block, rendered with its unit via the
  // house sci/sciUnit idiom — the assistant's number is byte-identical to the scorecard's.
  const citations = page.getByRole("list", { name: "Citations" });
  await expect(citations).toBeVisible();
  await expect(citations).toContainText("ATM level");
  await expect(citations).toContainText("1.83 × 10⁻¹ Vol");

  // Provenance caption wears the active frame: subject · 17:30 CET close · mode · coverage — and it
  // states the OESX settlement instant, never the 22:00 futures close.
  const frame = page.getByText(/SPX · close 2026-05-29 17:30 CET · strict · 1 706\/2 412/);
  await expect(frame).toBeVisible();
  await expect(frame).not.toContainText("22:00");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the browser only ever calls the BFF's /api/assistant — never OpenRouter directly", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  // Record every request the page makes during a full assistant interaction. The OpenRouter key
  // lives only in the BFF's environment; if the browser ever reached openrouter.ai directly, the
  // server-side grounding validator would be bypassed and the key would have to ship to the client.
  const requested: string[] = [];
  page.on("request", (req) => requested.push(req.url()));
  await gotoMarket(page);
  await mockAssistant(page, GROUNDED);
  await openAssistant(page);
  await page.getByRole("button", { name: "What am I looking at?" }).click();
  await expect(page.getByText(/nappe de implied vol de SPX/)).toBeVisible();

  // No request ever touches the model host; the assistant call goes to the BFF's own endpoint.
  expect(requested.filter((u) => /openrouter\.ai|\/chat\/completions/.test(u))).toEqual([]);
  expect(requested.some((u) => /\/api\/assistant\b/.test(u))).toBe(true);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant REFUSES an uncited number: grounded=false renders the honest gap, no number", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);
  await mockAssistant(page, HONEST_GAP);
  await openAssistant(page);

  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // The loud honest-gap copy appears, in a quiet status (not an error: it is the designed answer to
  // an out-of-facts question), and it explicitly says it will not invent the number.
  const gap = page.getByText(/je ne vais pas l'inventer/);
  await expect(gap).toBeVisible();
  await expect(gap).toHaveAttribute("role", "status");

  // No citation list exists, so no fabricated number can leak onto the screen — the guardrail.
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a BFF/OpenRouter failure is a LOUD inline alert, never a silent or stale answer", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);
  // The BFF labels a model failure as a non-500 (502 assistant_unavailable), never a bare 500.
  await page.route("**/api/assistant**", (route: Route) =>
    route.fulfill({
      status: 502,
      json: { error: "assistant_unavailable", detail: "OpenRouter timed out" },
    }),
  );
  await openAssistant(page);

  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // Failure surfaces as a role=alert (loud), in alarm words, with the BFF's detail — never silent.
  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(/Assistant indisponible/);
  await expect(alert).toContainText(/OpenRouter timed out/);

  // No fabricated answer or citation slipped in alongside the error.
  await expect(page.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a request in flight shows a thinking indicator (role=status), never a frozen blank box", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);

  // Hold the assistant response open so the thinking state is observable, then release it.
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/assistant**", async (route: Route) => {
    await gate;
    await route.fulfill({ json: GROUNDED });
  });
  await openAssistant(page);

  await page.getByRole("button", { name: "What am I looking at?" }).click();

  // While in flight: an explicit thinking indicator, marked busy and live — not a frozen panel.
  const thinking = page.getByText(/The assistant is thinking…/);
  await expect(thinking).toBeVisible();
  await expect(thinking).toHaveAttribute("aria-busy", "true");
  await expect(thinking).toHaveAttribute("role", "status");

  // Release the response: the thinking state gives way to the grounded answer.
  release();
  await expect(page.getByText(/nappe de implied vol de SPX/)).toBeVisible();
  await expect(page.getByText(/The assistant is thinking…/)).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("switching the index re-writes the frame the assistant grounds on (no stale frame)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);

  // The BFF echoes back a frame matching the underlying the panel posted, so the answer can never
  // describe a different surface than the page. Mirror that: serve the frame keyed off the request.
  await page.route("**/api/assistant**", async (route: Route) => {
    const body = route.request().postDataJSON() as { underlying?: string };
    const frame = body.underlying === "SX5E" ? FRAME_SX5E : FRAME_SPX;
    await route.fulfill({
      json: {
        answer: `You are looking at the ${frame.underlying} implied-vol surface at the close.`,
        citations: [],
        grounded: true,
        frame,
      } satisfies AssistantResponse,
    });
  });

  // Ask on the default index (SPX): the frame caption names SPX.
  await openAssistant(page);
  await page.getByRole("button", { name: "What am I looking at?" }).click();
  await expect(page.getByText(/nappe de implied vol de SPX/)).toBeVisible();
  await expect(page.getByText(/^SPX · close 2026-05-29 17:30 CET · strict/)).toBeVisible();

  // Switch the underlying selector to SX5E. The page re-fetches for the new index and the panel
  // subtree re-mounts (the assistant collapses back to its launch button) — re-open it the way a PM
  // would, then ask again: the new answer's frame caption tracks the NEW index, so the assistant
  // grounds on the live subject the page is showing, never a stale one.
  await page.getByLabel("Index", { exact: true }).selectOption("SX5E");
  await openAssistant(page);
  await page.getByRole("button", { name: "What am I looking at?" }).click();
  await expect(page.getByText(/nappe de implied vol de SX5E/)).toBeVisible();
  await expect(page.getByText(/^SX5E · close 2026-05-29 17:30 CET · strict/)).toBeVisible();
  // The stale SPX frame is gone — no two contradictory frames on one screen.
  await expect(page.getByText(/^SPX · close/)).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("an indicative frame is tagged INDICATIVE and never reads as the stored close", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await gotoMarket(page);

  // When the frame is indicative the caption carries INDICATIVE (not "strict"), so the PM can never
  // mistake an indicative mark for the stored close — the strict/indicative guardrail, surfaced.
  await mockAssistant(page, {
    answer: "Indicative surface — market probably closed.",
    citations: [],
    grounded: true,
    frame: { ...FRAME_SPX, mode: "indicative" },
  });
  await openAssistant(page);
  await page.getByRole("button", { name: "What am I looking at?" }).click();

  const frame = page.getByText(/^SPX · close 2026-05-29 17:30 CET · INDICATIVE/);
  await expect(frame).toBeVisible();
  await expect(frame).not.toContainText("strict");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

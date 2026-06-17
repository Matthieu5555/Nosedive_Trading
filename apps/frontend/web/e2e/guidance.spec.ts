import { expect, type Page, type Route, test } from "@playwright/test";

import { ANALYTICS_AAA } from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// The "guidance" surface — Principles 5 (contextual guidance: hotspot + next-step pulse) and 6 (the
// grounded, screen-aware assistant) of frontend-design-language-2026, with the §2b/§3 self-describing
// + no-silent-state guarantees the assistant rests on. The oracle for every assertion is the owner's
// one line (MAT-LEGIBILITY-qa-strategy): can the PM tell what they're looking at, and would they ever
// be misled? Each test asserts user-visible text + role + tone (the PM read), never internal React
// state, and every test asserts pageErrors == [] (a crash is the loudest silent-state failure).
//
// Real browser, mocked BFF, same contract as the rest of the suite (mock-bff.ts). Per-test route
// overrides for /api/analytics and /api/assistant are registered AFTER mockBff so Playwright's
// most-recent-first handler order lets them win. No live BFF, no data/ store, deterministic.

// One assistant turn the BFF would emit: a GROUNDED answer wears its frame and cites the on-screen
// number verbatim. The value strings are the contract the front renders; the test asserts the panel
// surfaces exactly these, never a number the payload didn't carry.
const ASSISTANT_GROUNDED = {
  answer:
    "Vous regardez la nappe de volatilité SX5E à la clôture du 2026-06-17. L'ATM 3m est à 18,3 %.",
  citations: [
    { id: "atm_level", label: "ATM 3m", value: "1.830e1 %", source: "facts-block" },
  ],
  grounded: true,
  frame: {
    underlying: "SX5E",
    trade_date: "2026-06-01",
    run_id: null,
    mode: "strict",
    close_instant: "2026-06-17 17:30 CET",
    coverage_label: "1 706/2 412 cotations",
  },
};

// The honest-gap turn: the question needed a number the facts block did not carry, so the BFF returns
// grounded=false with the refusal copy and ZERO citations — never a fabricated value. This is the
// anti-pattern guard ("an assistant that hallucinates a number is worse than no assistant").
const ASSISTANT_GAP = {
  answer:
    "Ça n'est pas dans ce que l'écran affiche pour cette clôture — je ne vais pas l'inventer.",
  citations: [] as { id: string; label: string; value: string; source: string }[],
  grounded: false,
  frame: {
    underlying: "SX5E",
    trade_date: "2026-06-01",
    run_id: null,
    mode: "strict",
    close_instant: "2026-06-17 17:30 CET",
    coverage_label: "1 706/2 412 cotations",
  },
};

async function mockGuidance(
  page: Page,
  assistant: unknown = ASSISTANT_GROUNDED,
): Promise<void> {
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  await page.route("**/api/assistant**", (route: Route) => route.fulfill({ json: assistant }));
}

// Land on a fully resolved Market screen (index auto-selected, analytics rendered). Returns once the
// nappe panel heading is on screen, the steady-state from which the guidance affordances are read.
async function gotoMarketResolved(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Market" })).toBeVisible();
  // The index selector auto-selects the first option (SPX) once /api/indices resolves.
  await expect(page.getByLabel("Index", { exact: true })).toHaveValue("SPX");
}

// ---------------------------------------------------------------------------------------------------
// Principle 5 — contextual guidance: a next-step hint that points then gets out of the way, and ⓘ
// hotspots that open a NON-MODAL tooltip (never a front-loaded tour / dialog).
// ---------------------------------------------------------------------------------------------------

test("guidance: the unconfigured index selector flags the next step on first load, then goes quiet once chosen", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  // Hold /api/indices open briefly so the first-load `index === ""` window is observable before the
  // auto-select trips. The cue (data-hint + the role=status prompt) is what a first-time PM sees.
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  let releaseIndices = (): void => {};
  await page.route("**/api/indices**", async (route) => {
    await new Promise<void>((resolve) => {
      releaseIndices = resolve;
    });
    return route.fulfill({
      json: {
        indices: [
          { symbol: "SPX", name: "S&P 500", currency: "USD" },
          { symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" },
        ],
      },
    });
  });

  await page.goto("/");
  const index = page.getByLabel("Index", { exact: true });
  // Before any index is chosen: the selector carries the next-step hint and the prompt cue is shown,
  // in plain PM words, NOT a modal — assert no dialog overlay exists.
  await expect(index).toHaveAttribute("data-hint", "choose-index");
  await expect(page.getByText("Choisissez un indice pour commencer")).toBeVisible();
  expect(await page.getByRole("dialog").count()).toBe(0);

  // The PM acts (the auto-select resolves the index). The hint must die — never re-fire.
  releaseIndices();
  await expect(index).toHaveValue("SPX");
  await expect(index).not.toHaveAttribute("data-hint", "choose-index");
  await expect(page.getByText("Choisissez un indice pour commencer")).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("guidance: an info-dot hotspot opens a non-modal what-is-this tooltip the page stays usable behind", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);

  // The Operations launch ⓘ ("Que fait ce bouton ?") is the LIVE guidance hotspot today: a tier-2
  // marker on the action that fires a real backend capture. It carries the action gloss (what the
  // button does underneath), the §4 "every action explains itself" affordance.
  await page.goto("/operations");
  await expect(page.getByRole("heading", { level: 1, name: "Operations" })).toBeVisible();

  const infoDot = page.getByRole("button", { name: "Que fait ce bouton ?" });
  await expect(infoDot).toBeVisible();
  // Quiet until opened: no tooltip on first paint (a hotspot, not a billboard).
  await expect(page.getByRole("tooltip")).toHaveCount(0);

  // Hover opens a role=tooltip; it is INLINE, not a modal — assert no dialog and the page behind is
  // still interactive (the provider selector is still operable while the tooltip is open).
  await infoDot.hover();
  await expect(page.getByRole("tooltip").first()).toBeVisible();
  expect(await page.getByRole("dialog").count()).toBe(0);
  await expect(page.getByLabel("Data provider").first()).toBeEnabled();

  // Keyboard-reachable + dismissible: focus opens, Escape closes (no trap).
  await infoDot.focus();
  await expect(page.getByRole("tooltip").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------------------------------
// Principle 2b — the self-describing nappe the assistant grounds on: subject · as-of · mode · coverage.
// SX5E close is 17:30 CET (OESX settlement), never 22:00 — assert the caption carries the instant.
// ---------------------------------------------------------------------------------------------------

test("guidance: the nappe heading and caption re-write themselves when the underlying selector changes", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockGuidance(page);
  await gotoMarketResolved(page);

  // On SPX (no close instant configured) the subject names the index; no false instant is invented.
  await expect(page.getByRole("heading", { name: "Nappe de volatilité — SPX" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nappe de volatilité — SX5E" })).toHaveCount(0);

  // Switch the underlying. The subject heading AND the panel aria-label re-write in the same paint —
  // no stale frame. For SX5E the caption must state the 17:30 CET close instant (not 22:00, not bare).
  await page.getByLabel("Index", { exact: true }).selectOption("SX5E");
  const nappe = page.getByRole("heading", { name: "Nappe de volatilité — SX5E" });
  await expect(nappe).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nappe de volatilité — SPX" })).toHaveCount(0);

  // The caption (status line) on the SX5E nappe carries the close instant. Read the panel by its
  // re-written aria-label and assert the instant text is present, never the 22:00 futures close.
  const sx5ePanel = page.getByRole("article", { name: "Nappe de volatilité — SX5E" });
  await expect(sx5ePanel).toContainText("17:30 CET");
  await expect(sx5ePanel).not.toContainText("22:00");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// ---------------------------------------------------------------------------------------------------
// Principle 6 — the grounded assistant: non-blocking, cites provenance, never invents a number.
// ---------------------------------------------------------------------------------------------------

test("guidance: the assistant is summonable and non-blocking — a panel you open, not a wall you must pass", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockGuidance(page);
  await gotoMarketResolved(page);

  // Closed by default: the page is fully usable, only a quiet launch affordance is present.
  const launch = page.getByRole("button", { name: "Demander à l'assistant" });
  await expect(launch).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Assistant" })).toHaveCount(0);

  // Summon it. It opens as an inline panel (aside, NOT a modal dialog); the page behind stays live —
  // the index selector is still operable, proving it is not a blocking wall.
  await launch.click();
  await expect(page.getByRole("complementary", { name: "Assistant" })).toBeVisible();
  expect(await page.getByRole("dialog").count()).toBe(0);
  await expect(page.getByLabel("Index", { exact: true })).toBeEnabled();

  // It is dismissible without losing the page.
  await page.getByRole("button", { name: "Fermer l'assistant" }).click();
  await expect(page.getByRole("complementary", { name: "Assistant" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("guidance: a grounded assistant answer cites the on-screen number and wears its provenance frame", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockGuidance(page, ASSISTANT_GROUNDED);
  await gotoMarketResolved(page);

  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  const panel = page.getByRole("complementary", { name: "Assistant" });
  await panel.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  // The answer renders, and it carries a CITATION with the value lifted verbatim from the payload —
  // provenance made visible. The number on screen is the number the payload carried, byte for byte.
  // The citation's LABEL is resolved from the shared explanation map (explainEntry(cite.id)) — for
  // atm_level that is "Vol à la monnaie" — so the tooltip and the assistant can never disagree on
  // what the number is; the VALUE is the payload's sci-notation string verbatim.
  await expect(panel.getByText(/L'ATM 3m est à 18,3/)).toBeVisible();
  const citations = panel.getByRole("list", { name: "Citations" });
  await expect(citations).toBeVisible();
  await expect(citations).toContainText("Vol à la monnaie");
  await expect(citations).toContainText("1.830e1 %");

  // The answer wears the same provenance frame the page shows: subject · 17:30-CET close · mode ·
  // coverage. The PM sees the assistant and the chart agree.
  await expect(panel).toContainText("SX5E");
  await expect(panel).toContainText("17:30 CET");
  await expect(panel).toContainText("strict");
  await expect(panel).toContainText("1 706/2 412 cotations");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("guidance: when the screen does not hold the number, the assistant refuses to invent one — no uncited figure", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockGuidance(page, ASSISTANT_GAP);
  await gotoMarketResolved(page);

  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  const panel = page.getByRole("complementary", { name: "Assistant" });

  // Ask for a figure the facts block doesn't carry.
  await panel.getByLabel("Votre question").fill("Quel est le vega total du book ?");
  await panel.getByRole("button", { name: "Envoyer" }).click();

  // The honest-gap answer renders in status (quiet, not a confident assertion) and says it won't
  // invent the number — the loud refusal, never a fabricated value.
  const gap = panel.getByText(/je ne vais pas l'inventer/);
  await expect(gap).toBeVisible();
  await expect(gap).toHaveRole("status");

  // The hard guardrail, asserted in the data the panel surfaces: a grounded=false turn carries NO
  // citations list — there is no uncited number presented as an answer.
  await expect(panel.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("guidance: a failed assistant request surfaces a loud alert, never a silent or stale answer", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_AAA }));
  // The BFF returns a labelled error (mirrors app.py's labelled-400 path), not a 200 with junk.
  await page.route("**/api/assistant**", (route) =>
    route.fulfill({
      status: 503,
      json: { error: "assistant_unavailable", detail: "OpenRouter est injoignable." },
    }),
  );
  await gotoMarketResolved(page);

  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  const panel = page.getByRole("complementary", { name: "Assistant" });
  await panel.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  // No silent state in the panel: the failure reads as a loud role=alert, naming the assistant as
  // unavailable — it never silently returns nothing or a stale answer.
  const alert = panel.getByRole("alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(/Assistant indisponible/);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

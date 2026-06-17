import { expect, type Page, test } from "@playwright/test";

import type { AnalyticsResponse, SignalsResponse } from "../src/api";
import { ANALYTICS_SCORECARD, INDICES_SPX_SX5E, SIGNALS_SX5E } from "../src/test/fixtures";
import { collectPageErrors } from "./helpers";
import { mockBff } from "./mock-bff";

// The Données/Market SCORECARD band, end to end in a real browser, asserted against the
// design-language acceptance criteria the MAT-LEGIBILITY specs make law (qa-strategy matrix
// rows 2.1/2.2/2.3, §2b self-describing provenance, §3 no-silent-state, §6 grounded assistant).
//
// Assertions are on the PM read — visible text, role, tone — never internal React state, exactly
// as market-read-flow.spec.ts:56-125 is the template. Every test asserts pageErrors == [] (a crash
// is the loudest silent failure, qa-strategy 3.7). The BFF is mocked at the network layer via the
// shared mock-bff + per-test overrides that win because page.route runs most-recent-first
// (qa-strategy "Route-override order matters"); no test reaches a live BFF or data/.
//
// Independent oracle for the six headline reads, hand-derived from ANALYTICS_SCORECARD (a 3m slice,
// SIGNALS_SX5E (src/test/fixtures.ts), NEVER read back from computeScorecards:
//   ATM level   = smile IV at k nearest 0 = IV(k=0.0) = 0.20            -> "20.0%"
//   Skew 25Δ    = IV(25Δp) − IV(25Δc). Put −0.30(0.32)/−0.20(0.28) interp@−0.25 = 0.30;
//                 call +0.20(0.22)/+0.30(0.24) interp@+0.25 = 0.23; 0.30 − 0.23 = +0.07 -> "+7.0 vp"
//   Term slope  = signal term_structure_slope.value = 0.012            -> "+1.2 vp"
//   IV-rank     = signal iv_rank.value = 0.62                          -> "62.0%"
//   RV − IV     = signal iv_vs_realized.value = −0.018                 -> "−1.8 vp"
//   ρ̄           = signal implied_correlation.value = 0.50              -> "50.0%"

// A signals payload with EVERY signal absent — the band must still render six cards, each "—",
// never a blank cell or a crash (qa-strategy 2.1: a null reads "—", never blank).
const SIGNALS_EMPTY: SignalsResponse = {
  ...SIGNALS_SX5E,
  n_signals: 0,
  kinds: [],
  by_kind: {},
  signals: [],
};

// Analytics with an empty term structure — computeScorecards returns null, so ATM/Skew read "—"
// off a "no surface" tenor note, and the band is still six legible cards, never a blank panel.
const ANALYTICS_NO_SLICE: AnalyticsResponse = {
  ...ANALYTICS_SCORECARD,
  n_maturities: 0,
  maturities: [],
};

interface AssistantBody {
  question: string;
  underlying: string;
  trade_date: string;
  element_id?: string | null;
}

// The grounded contract the assistant data layer enforces: the only number that may reach the
// answer is a CITATION lifted verbatim from the server facts block (here the ATM scorecard value).
// The model never free-texts a figure. A question the facts block can't answer comes back
// grounded=false with EMPTY citations and the honest-gap copy — never a fabricated number.
function assistantHandler(body: AssistantBody) {
  const frame = {
    underlying: body.underlying,
    trade_date: body.trade_date,
    run_id: "run-0529",
    mode: "strict" as const,
    close_instant: `${body.trade_date} 17:30 CET`,
    coverage_label: "1 706/2 412 cotations",
  };
  // A "what number isn't on screen" probe → honest gap (no citation, refuses to invent).
  if (/sharpe|var|drawdown|inventer|99/i.test(body.question)) {
    return {
      answer:
        "Ça n'est pas dans ce que l'écran affiche pour cette clôture — je ne vais pas l'inventer.",
      citations: [],
      grounded: false,
      frame,
    };
  }
  return {
    answer: `Vous regardez la nappe de vol implicite de ${body.underlying} à la clôture.`,
    citations: [
      {
        id: "atm_level",
        label: "Vol à la monnaie",
        value: "2.00 × 10⁻¹ Vol",
        source: "signal enregistré · 3m",
      },
    ],
    grounded: true,
    frame,
  };
}

async function mockScorecards(page: Page): Promise<void> {
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_SCORECARD }));
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: SIGNALS_SX5E }));
}

// The six cards in render order; the band must show all six even when a value is "—".
const CARD_LABELS = ["ATM level", "Term-structure slope", "IV-rank", "Skew 25Δ", "RV − IV", "ρ̄"];

function band(page: Page) {
  return page.getByRole("region", { name: "Volatility scorecards" });
}

test("scorecards band renders all six labelled cards with their values (2.1 label·value·hint)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  await page.goto("/");

  const scorecards = band(page);
  await expect(scorecards).toBeVisible();

  // Every card is present and named — a card is an article labelled by its metric.
  for (const label of CARD_LABELS) {
    await expect(scorecards.getByRole("article", { name: label })).toBeVisible();
  }
  // Six cards, no more, no fewer (the locked headline six, never a stray tile).
  await expect(scorecards.getByRole("article")).toHaveCount(6);

  // Each card carries label + value + hint (three lines), asserted on the hand-derived values.
  const atm = scorecards.getByRole("article", { name: "ATM level" });
  await expect(atm).toContainText("20.0%");
  await expect(atm).toContainText("at-the-money implied vol");

  const skew = scorecards.getByRole("article", { name: "Skew 25Δ" });
  await expect(skew).toContainText("+7.0 vp");

  const slope = scorecards.getByRole("article", { name: "Term-structure slope" });
  await expect(slope).toContainText("+1.2 vp");

  const ivRank = scorecards.getByRole("article", { name: "IV-rank" });
  await expect(ivRank).toContainText("62.0%");

  const rvIv = scorecards.getByRole("article", { name: "RV − IV" });
  // volPoints prepends "+" only for positives; a negative keeps JS's ASCII hyphen-minus.
  await expect(rvIv).toContainText("-1.8 vp");

  const rho = scorecards.getByRole("article", { name: "ρ̄" });
  await expect(rho).toContainText("50.0%");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a missing signal reads as the honest em-dash gap, never a blank cell or a crash (2.1)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_SCORECARD }));
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: SIGNALS_EMPTY }));
  await page.goto("/");

  const scorecards = band(page);
  await expect(scorecards).toBeVisible();
  // All six cards still render — an empty signal is labelled "—", never omitted.
  await expect(scorecards.getByRole("article")).toHaveCount(6);

  // The four signal-fed cards each read the honest gap "—" (and their hint says why).
  for (const label of ["Term-structure slope", "IV-rank", "RV − IV", "ρ̄"]) {
    const card = scorecards.getByRole("article", { name: label });
    await expect(card.locator(".scorecard__value")).toHaveText("—");
    await expect(card).toContainText("not recorded");
  }
  // The surface-projected cards still resolve off the captured smile (not a signal).
  await expect(scorecards.getByRole("article", { name: "ATM level" })).toContainText("20.0%");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("with no captured slice the projected cards read '—' on a 'no surface' note (2.1, never blank)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_NO_SLICE }));
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: SIGNALS_SX5E }));
  await page.goto("/");

  const scorecards = band(page);
  await expect(scorecards).toBeVisible();
  await expect(scorecards.getByRole("article")).toHaveCount(6);
  // ATM/Skew have no slice to read — honest "—", and the hint says "no surface", never a blank.
  const atm = scorecards.getByRole("article", { name: "ATM level" });
  await expect(atm.locator(".scorecard__value")).toHaveText("—");
  await expect(atm).toContainText("no surface");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the sign legend reads the signs in plain PM words, with the vp unit defined (2.2)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  await page.goto("/");

  const legend = page.getByLabel("Sign legend");
  await expect(legend).toBeVisible();
  // The colour is given meaning IN WORDS, not just a coloured number (qa-strategy 2.2).
  await expect(legend).toContainText("RV−IV > 0 = vol cheap (buy)");
  await expect(legend).toContainText("RV−IV < 0 = vol rich (sell)");
  await expect(legend).toContainText("slope < 0 = backwardation = risk imminent");
  // The unit travelling on every "vp" value is defined on the band itself.
  await expect(legend).toContainText("vp = vol point");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// FIXME — the Scorecards component already builds this provenance line + 17:30 CET close instant
// (asOfCloseLine, src/components/Scorecards.tsx:16-23), but Market.tsx mounts <Scorecards> WITHOUT
// the underlying/asOf/runId props (src/pages/Market.tsx:234-243), so the band renders no provenance
// line today. Retired when Market threads those props (the §2/§2b provenance affordance for the band).
test.fixme("the provenance line names subject + as-of, and the SX5E close instant is 17:30 CET (§2b, §2)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  await page.goto("/");

  // Default index is SPX → date-only as-of (no close instant is registered for SPX, never guessed).
  const provSpx = page.getByLabel("Scorecard provenance");
  await expect(provSpx).toContainText("SPX");
  await expect(provSpx).toContainText("as of 2026-05-29");
  await expect(provSpx).not.toContainText("CET");

  // Switch the underlying selector → the provenance line re-writes itself to SX5E and now carries
  // the OESX 17:30 CET close instant (sx5e-close-instant-1730-cet), NOT the 22:00 futures close.
  await page.getByLabel("Index", { exact: true }).selectOption("SX5E");
  const provSx5e = page.getByLabel("Scorecard provenance");
  await expect(provSx5e).toContainText("SX5E");
  await expect(provSx5e).toContainText("17:30 CET (close)");
  await expect(provSx5e).not.toContainText("22:00");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

// FIXME — the InfoDot lives inside the provenance line (Scorecards.tsx:162), which only renders when
// the band receives underlying/asOf (see the provenance FIXME above). Retired by the same Market.tsx
// prop wiring; the assertion is the §2.3 "where-did-this-come-from" affordance, written out in full.
test.fixme("an info dot on the band reveals the where-from provenance gloss, non-modal (2.3, §2)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  await page.goto("/");

  const info = page.getByRole("button", {
    name: "Scorecards — where these numbers come from",
  });
  await expect(info).toBeVisible();
  await expect(info).toHaveAttribute("aria-expanded", "false");

  // Hovering opens a NON-MODAL tooltip (role=tooltip), not a modal wall — the workflow continues.
  await info.hover();
  const tip = page.getByRole("tooltip");
  await expect(tip).toBeVisible();
  // The gloss states what-and-where-from in PM register: which numbers are projected vs persisted.
  await expect(tip).toContainText("captured volatility surface");
  await expect(tip).toContainText("persisted signals computed by the backend");
  // It is a tooltip, never a <dialog>/modal that blocks the band beneath it.
  await expect(page.locator("dialog[open]")).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("switching the index re-writes the scorecard values in one paint — no stale frame (§2b.7)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/analytics**", (route) => route.fulfill({ json: ANALYTICS_SCORECARD }));
  // Different signal payloads per index, so a stale frame would show the WRONG number after a swap.
  await page.route("**/api/signals?**", (route) => {
    const underlying = new URL(route.request().url()).searchParams.get("underlying");
    if (underlying === "SX5E") return route.fulfill({ json: SIGNALS_SX5E });
    return route.fulfill({ json: SIGNALS_EMPTY });
  });
  await page.goto("/");

  const scorecards = band(page);
  // SPX → empty signals → the signal-fed cards read "—".
  await expect(page.getByLabel("Index", { exact: true })).toHaveValue("SPX");
  await expect(
    scorecards.getByRole("article", { name: "IV-rank" }).locator(".scorecard__value"),
  ).toHaveText("—");

  // Switch to SX5E → the band re-writes in the same paint: the SAME card now carries the SX5E
  // signal value, never the stale SPX "—". (The provenance-line agreement is asserted by the
  // provenance FIXME above, once Market threads underlying/asOf into the band.)
  await page.getByLabel("Index", { exact: true }).selectOption("SX5E");
  await expect(
    scorecards.getByRole("article", { name: "IV-rank" }).locator(".scorecard__value"),
  ).toHaveText("62.0%");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("loading the band shows a footprint skeleton (role=status, aria-busy), never a bare blank (§3)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: SIGNALS_SX5E }));
  // Hold analytics open past the SKELETON_DELAY_MS (1 s) floor so the skeleton is shown, not blank.
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/analytics**", async (route) => {
    await gate;
    await route.fulfill({ json: ANALYTICS_SCORECARD });
  });
  await page.goto("/");

  // While the fetch is in flight the surface is a status skeleton (footprint reserved), not a crash
  // and not a silently empty panel.
  const skeleton = page.locator('.chart-skeleton[role="status"][aria-busy="true"]').first();
  await expect(skeleton).toBeVisible({ timeout: 5000 });

  release();
  // Once resolved the real band replaces the skeleton.
  await expect(band(page)).toBeVisible();

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("a failed analytics fetch shows a loud alert, never a silently dead band (§3 no silent state)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockBff(page);
  await page.route("**/api/indices**", (route) => route.fulfill({ json: INDICES_SPX_SX5E }));
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: SIGNALS_SX5E }));
  await page.route("**/api/analytics**", (route) =>
    route.fulfill({ status: 502, json: { error: "surface_unavailable", detail: "boom" } }),
  );
  await page.goto("/");

  // The failure is loud: a role=alert, not a blank/empty band that reads as "fine".
  const alert = page.getByRole("alert").first();
  await expect(alert).toBeVisible();
  // The scorecards band itself must NOT render off a failed fetch (no plausible silent-green band).
  await expect(band(page)).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant explains the band from on-screen data and CITES its number (§6.1/6.2 grounded)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  let posted: AssistantBody | null = null;
  await page.route("**/api/assistant", async (route) => {
    posted = route.request().postDataJSON() as AssistantBody;
    await route.fulfill({ json: assistantHandler(posted) });
  });
  await page.goto("/");
  await expect(band(page)).toBeVisible();

  // Open the (non-blocking) assistant and ask it to explain the current screen.
  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  await page.getByRole("button", { name: "Qu'est-ce que je regarde ?" }).click();

  const assistant = page.getByRole("complementary", { name: "Assistant" });
  // It answers from the on-screen subject (SPX, the active index), not a generic answer.
  await expect(assistant).toContainText("nappe de vol implicite de SPX");
  // The number it surfaces is a CITATION lifted from the facts block — the same ATM scorecard value
  // (2.00 × 10⁻¹ Vol = 0.20 = the band's 20.0%). It is in a labelled Citations list, never inline.
  await expect(assistant.getByRole("list", { name: "Citations" })).toBeVisible();
  await expect(assistant.getByRole("list", { name: "Citations" })).toContainText("2.00 × 10⁻¹ Vol");
  // It posted the active frame (the on-screen subject + as-of), so it can't describe another frame.
  expect(posted).not.toBeNull();
  expect((posted as unknown as AssistantBody).underlying).toBe("SPX");

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

test("the assistant refuses an uncited number — honest gap in a quiet status, no citation (§6.2)", async ({
  page,
}) => {
  const errors = collectPageErrors(page);
  await mockScorecards(page);
  await page.route("**/api/assistant", async (route) => {
    const body = route.request().postDataJSON() as AssistantBody;
    await route.fulfill({ json: assistantHandler(body) });
  });
  await page.goto("/");
  await expect(band(page)).toBeVisible();

  await page.getByRole("button", { name: "Demander à l'assistant" }).click();
  // Ask for a figure the screen doesn't hold (a 99% VaR) — the data layer must REFUSE to invent it.
  await page.getByLabel("Votre question").fill("Quel est le VaR 99% du book ?");
  await page.getByRole("button", { name: "Envoyer" }).click();

  const assistant = page.getByRole("complementary", { name: "Assistant" });
  const gap = assistant.getByText(/je ne vais pas l'inventer/);
  await expect(gap).toBeVisible();
  // The honest gap reads as a quiet status, and carries NO citation list — so no number can leak.
  await expect(gap).toHaveAttribute("role", "status");
  await expect(assistant.getByRole("list", { name: "Citations" })).toHaveCount(0);

  expect(errors.pageErrors, errors.pageErrors.join("\n")).toEqual([]);
});

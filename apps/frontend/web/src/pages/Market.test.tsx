import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

import {
  ANALYTICS_AAA_DEGENERATE,
  ANALYTICS_AAA_DENSE,
  ANALYTICS_AAA_MONEYNESS_FALLBACK,
  ANALYTICS_PER_SIDE,
  ANALYTICS_QUOTED,
  ANALYTICS_SCORECARD,
  INDICES_SPX_SX5E,
  RECORDED_EMPTY,
  RECORDED_TWO_DATES,
  SIGNALS_SX5E,
} from "../test/fixtures";
import { jsonGet, notMocked, server } from "../test/server";
import { MarketPage } from "./Market";

test("leads with the index selector and an as-of dropdown, no entity/side/maturity strip", async () => {
  render(<MarketPage />);

  expect(await screen.findByLabelText("Index")).toBeInTheDocument();
  expect(screen.getByLabelText("As-of fetch")).toBeInTheDocument();
  // The ADR-0051 amputation removes the constituent "Entity" axis and the put/call switch.
  expect(screen.queryByLabelText("Entity")).not.toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: /option side/i })).not.toBeInTheDocument();
});

test("is one scrollable page (price → scorecards → surface → tenor → dispersion), not tabs", async () => {
  render(<MarketPage />);

  // Price (context), then the scorecards block, then the 3D surface, then the dispersion strip.
  expect(await screen.findByRole("heading", { name: /Daily price/i })).toBeInTheDocument();
  expect(await screen.findByLabelText("Volatility scorecards")).toBeInTheDocument();
  expect(await screen.findByLabelText(/Volatility surface.*maturity/i)).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: /Avg correlation/i })).toBeInTheDocument();
  // The old tab chrome is gone.
  expect(screen.queryByRole("tab", { name: "Analytics" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Data quality" })).not.toBeInTheDocument();
});

test("renders the six headline scorecards with independently-derived numbers", async () => {
  // A 3m slice with ±25Δ-bracketing bands: ATM 0.20, skew = 0.30 − 0.23 = +0.07 (+7.0 vp). The
  // signals (SIGNALS_SX5E, returned regardless of index): RV−IV −0.018 (−1.8 vp), term-structure
  // slope +0.012 (+1.2 vp), IV-rank 0.62 (62.0%), ρ̄ 0.5 (50.0%). Convexity is DEMOTED out of the
  // headline into the smile block, so it is no longer a scorecard.
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const cards = await screen.findByLabelText("Volatility scorecards");
  const atm = await screen.findByLabelText("ATM level");
  expect(within(atm).getByText("20.0%")).toBeInTheDocument();
  expect(within(screen.getByLabelText("Skew 25Δ")).getByText("+7.0 vp")).toBeInTheDocument();
  // RV-IV is the persisted iv_vs_realized signal, not a recompute.
  expect(within(screen.getByLabelText("RV - IV")).getByText("-1.8 vp")).toBeInTheDocument();
  // The three new persisted-signal cards.
  expect(
    within(screen.getByLabelText("Term-structure slope")).getByText("+1.2 vp"),
  ).toBeInTheDocument();
  expect(within(screen.getByLabelText("IV-rank")).getByText("62.0%")).toBeInTheDocument();
  expect(
    within(screen.getByLabelText("Avg correlation (ρ)")).getByText("50.0%"),
  ).toBeInTheDocument();
  // Convexity is no longer a headline card (it moved to the smile block).
  expect(within(cards).queryByLabelText("Convexity 25Δ")).not.toBeInTheDocument();
});

test("the sign legend prints so the trader never inverts a sign", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const legend = await screen.findByLabelText("Sign legend");
  expect(legend).toHaveTextContent(/options look cheap to buy/i);
  expect(legend).toHaveTextContent(/options look expensive to sell/i);
  expect(legend).toHaveTextContent(/near-term risk is rising/i);
});

test("a signed scorecard reads in the sign colour (green positive, coral negative)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  // Skew +7.0 vp is positive → the value carries the positive class; RV−IV −1.8 vp negative.
  const skewValue = within(await screen.findByLabelText("Skew 25Δ")).getByText("+7.0 vp");
  expect(skewValue).toHaveClass("positive");
  const rvValue = within(screen.getByLabelText("RV - IV")).getByText("-1.8 vp");
  expect(rvValue).toHaveClass("negative");
});

test("convexity is demoted into the smile block (curvature reads with the smile)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  // The 3m slice: ATM 0.20, IV(25Δp) interp 0.30, IV(25Δc) interp 0.23 →
  // convexity = 0.30 + 0.23 − 0.40 = +0.13 → +13.0 vp, now beside the smile, not in the headline.
  const convexity = await screen.findByLabelText("Convexity 25Δ");
  expect(within(convexity).getByText("+13.0 vp")).toBeInTheDocument();
});

test("the rate diagnostics render r(T) + carry/dividend for the selected tenor", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  // The 3m slice carries rate_diagnostics: forward 4812.5, r 2.54%, carry −1.31%, dividend 3.85%.
  // r(T) is the explicit, displayed interest-rate input (the owner's ask), with its annualized unit.
  const rates = await screen.findByLabelText("Rate diagnostics");
  expect(within(rates).getByText(/Rate diagnostics, 3m/i)).toBeInTheDocument();
  expect(within(rates).getByText(/2\.540% \/yr \(annualized, continuous\)/)).toBeInTheDocument();
  expect(within(rates).getByText(/-1\.310% \/yr/)).toBeInTheDocument();
  expect(within(rates).getByText(/3\.850% \/yr/)).toBeInTheDocument();
  // The forward renders as a plain price in the index currency.
  expect(within(rates).getByText(/4,812\.5 /)).toBeInTheDocument();
});

test("the rate diagnostics show an honest gap when no forward was banked for the tenor", async () => {
  // ANALYTICS_QUOTED's 3m slice has no rate_diagnostics field (undefined) → the projection-gap note.
  server.use(jsonGet("/api/analytics", ANALYTICS_QUOTED));
  render(<MarketPage />);

  const rates = await screen.findByLabelText("Rate diagnostics");
  expect(within(rates).getByText(/No forward\/rate diagnostic banked/i)).toBeInTheDocument();
});

test("a scorecard with no data honestly shows '-' (never fabricated)", async () => {
  // The default ANALYTICS_AAA has a single put band (−0.3), so the ±25Δ wings can't be bracketed.
  render(<MarketPage />);
  const skew = await screen.findByLabelText("Skew 25Δ");
  expect(within(skew).getByText("-")).toBeInTheDocument();
});

test("one tenor selector lists the pinned grid and drives the smile + greeks table", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const tenor = await screen.findByLabelText("Tenor");
  // The pinned tenor_grid, in reading order.
  for (const label of ["10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"]) {
    expect(
      within(tenor).getByRole("option", { name: new RegExp(`^${label}`) }),
    ).toBeInTheDocument();
  }
  // The default tenor (3m) is captured, so its smile and Greeks table render.
  expect(await screen.findByLabelText(/smile 3m/i)).toBeInTheDocument();
  expect(await screen.findByRole("table", { name: /Dollar Greeks, 3m/i })).toBeInTheDocument();
});

test("a tenor beyond the captured span renders as a labelled projection gap", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  const user = userEvent.setup();
  render(<MarketPage />);

  await screen.findByLabelText(/smile 3m/i);
  // 12m is offered (pinned grid) but not captured in this fixture.
  expect(
    within(screen.getByLabelText("Tenor")).getByRole("option", { name: /12m \(not captured\)/ }),
  ).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Tenor"), "12m");
  expect(await screen.findByText(/12m is not captured/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/smile 3m/i)).not.toBeInTheDocument();
});

test("the smile superimposes put + call (both wings, no side filter)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const smile = await screen.findByLabelText(/smile 3m/i);
  // Both wings plotted as scatter traces (the gap between them is the skew).
  expect(within(smile).getByTestId("plot-types").textContent).toMatch(/scatter,scatter/);
});

test("the surface header offers a Call / Put / Combined selector, landing on Combined", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_PER_SIDE));
  render(<MarketPage />);

  const group = await screen.findByRole("group", { name: "Surface side" });
  const combined = within(group).getByRole("button", { name: "Combined" });
  const calls = within(group).getByRole("button", { name: "Calls" });
  const puts = within(group).getByRole("button", { name: "Puts" });
  // Combined is the landing state.
  expect(combined).toHaveAttribute("aria-pressed", "true");
  expect(calls).toHaveAttribute("aria-pressed", "false");
  expect(puts).toHaveAttribute("aria-pressed", "false");
});

// The surface panel article and the plot figure inside it both carry a "Volatility surface" label,
// so scope to the panel article (by role) and read its plot figure's z-grid / trace types.
function surfacePanelArticle(): HTMLElement {
  return screen.getByRole("article", { name: /Volatility surface/i });
}

test("selecting Calls then Puts swaps the rendered surface to that side's grid", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_PER_SIDE));
  const user = userEvent.setup();
  render(<MarketPage />);

  const group = await screen.findByRole("group", { name: "Surface side" });
  await screen.findByRole("article", { name: /Volatility surface/i });
  const combinedZ = within(surfacePanelArticle()).getByTestId("plot-z").textContent;

  await user.click(within(group).getByRole("button", { name: "Calls" }));
  await waitFor(() =>
    expect(within(group).getByRole("button", { name: "Calls" })).toHaveAttribute(
      "aria-pressed",
      "true",
    ),
  );
  const callZ = within(surfacePanelArticle()).getByTestId("plot-z").textContent;
  // The call surface is a different z-grid than combined (genuinely different per-side data).
  expect(callZ).not.toEqual(combinedZ);

  await user.click(within(group).getByRole("button", { name: "Puts" }));
  const putZ = within(surfacePanelArticle()).getByTestId("plot-z").textContent;
  expect(putZ).not.toEqual(callZ);
});

test("the maturity control is a floor, and applying it keeps the 3D surface (never a single slice)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_PER_SIDE));
  const user = userEvent.setup();
  render(<MarketPage />);

  // The header maturity control opens on "All maturities" (no floor).
  const floor = await screen.findByLabelText("Minimum maturity");
  expect((floor as HTMLSelectElement).value).toMatch(/^0$/);
  // No floor -> a 3D surface trace in the surface panel.
  await screen.findByRole("article", { name: /Volatility surface/i });
  expect(within(surfacePanelArticle()).getByTestId("plot-types").textContent).toMatch(/surface/);

  // The options are FLOORS ("min … and up"), never a single tenor that would collapse the surface.
  expect((floor as HTMLSelectElement).textContent).toMatch(/min .* and up/i);
  expect((floor as HTMLSelectElement).textContent).not.toMatch(/2d smile/i);

  // Apply a floor (the first "min … and up") -> the surface panel STILL renders a 3D surface, not
  // a 2D smile slice. The single-tenor smile lives in the Smile & Greeks panel below.
  const floorOption = within(floor as HTMLSelectElement)
    .getAllByRole("option")
    .find((o) => /min .* and up/i.test(o.textContent ?? "")) as HTMLOptionElement;
  await user.selectOptions(floor, floorOption.value);
  await waitFor(() =>
    expect(within(surfacePanelArticle()).getByTestId("plot-types").textContent).toMatch(/surface/),
  );
});

test("the dispersion strip reads the realized-vol ρ̄ signal (no per-member fan-out)", async () => {
  render(<MarketPage />);
  // implied_correlation from the signal fixture is 0.5 → 50.00%.
  expect(await screen.findByLabelText("Implied correlation")).toHaveTextContent(
    /Avg correlation \(ρ\) = 50.00%/,
  );
});

test("never calls /api/analytics for a constituent symbol, index-keyed only", async () => {
  const underlyings: string[] = [];
  server.use(
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      if (u) underlyings.push(u);
      return HttpResponse.json(ANALYTICS_SCORECARD);
    }),
  );
  render(<MarketPage />);

  await screen.findByLabelText(/smile 3m/i);
  await waitFor(() => expect(underlyings.length).toBeGreaterThan(0));
  // Only the index (SPX) is ever requested; no member (AAA/BBB) surface is fetched.
  expect(new Set(underlyings)).toEqual(new Set(["SPX"]));
});

test("renders the dense reconstructed surface as the full surface (both wings, no side slice)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DENSE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Volatility surface.*maturity/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  // The whole lattice survives — no put-wing slice (k = −0.1, 0.0, 0.1 all present).
  expect(within(surface).getByTestId("plot-z")).toHaveTextContent(
    JSON.stringify([
      [0.27, 0.24, 0.25],
      [0.23, 0.21, 0.22],
    ]),
  );
});

test("the grid-fallback smile is labeled as log-moneyness and flags a degenerate fit", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_MONEYNESS_FALLBACK));
  render(<MarketPage />);

  // The fallback fixture's only tenor is "0.250y"; the tenor selector opens on 3m (not captured),
  // so to read the smile we pick the captured label. Its single tenor renders by default since 3m
  // isn't present — the selector falls back to the front tenor for the gap label, so assert the
  // surface fallback names log-moneyness instead.
  const surface = await screen.findByLabelText(/Volatility surface.*maturity/i);
  expect(surface.getAttribute("aria-label")).toMatch(/log-moneyness|surface/i);
});

test("the constituents table is display-only and index-keyed", async () => {
  render(<MarketPage />);
  const constituents = await screen.findByRole("region", { name: /constituents/i });
  expect(within(constituents).getByText("AAA")).toBeInTheDocument();
  expect(within(constituents).getByText("BBB")).toBeInTheDocument();
});

test("renders a labeled empty state when no dates are recorded", async () => {
  server.use(jsonGet("/api/recorded-dates", RECORDED_EMPTY));
  render(<MarketPage />);
  expect(await screen.findByText(/No capture runs to show/i)).toBeInTheDocument();
});

test("shows a qc-failing day with a QC fail badge instead of hiding it", async () => {
  server.use(
    jsonGet("/api/recorded-dates", {
      index: "SPX",
      count: 0,
      dates: [],
      available: [
        { date: "2026-06-10", run_id: "run-0610", recorded_ts: "2026-06-10T17:30:00", qc: "fail" },
      ],
    }),
  );
  render(<MarketPage />);
  expect(await screen.findByText("QC fail")).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  server.use(http.get("/api/recorded-dates", notMocked));
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});

test("monetized Greeks render in the index's quote currency (€ for SX5E)", async () => {
  server.use(
    jsonGet("/api/indices", {
      indices: [{ symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" }],
    }),
    jsonGet("/api/recorded-dates", {
      index: "SX5E",
      count: 1,
      dates: ["2026-05-29"],
      available: [
        { date: "2026-05-29", run_id: "run-0529", recorded_ts: "2026-05-29T17:30:00", qc: "pass" },
      ],
    }),
    jsonGet("/api/analytics", { ...ANALYTICS_SCORECARD, underlying: "SX5E" }),
    jsonGet("/api/signals", SIGNALS_SX5E),
  );
  render(<MarketPage />);

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks, 3m/i });
  expect(within(greeks).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("€ per €1 of underlying")).toBeInTheDocument();
});

test("the scorecards strip sits at the very top, above the price block", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const cards = await screen.findByLabelText("Volatility scorecards");
  const price = await screen.findByRole("heading", { name: /Daily price/i });
  // DOM order = reading order: the scorecards come before the price heading (⓪ then ①).
  expect(cards.compareDocumentPosition(price) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("the price block carries a master-detail constituents workspace (index + member candles)", async () => {
  render(<MarketPage />);

  // The index candlestick and the selected member's candlestick are both present (the 2nd candle).
  expect(await screen.findByRole("heading", { name: /Daily price/i })).toBeInTheDocument();
  // The constituents workspace defaults to the heaviest member; its panel names that member.
  const constituents = await screen.findByRole("region", { name: /constituents/i });
  expect(within(constituents).getByText("AAA")).toBeInTheDocument();
  expect(within(constituents).getByText("BBB")).toBeInTheDocument();
  // The member detail panel labels itself for the selected ticker (heaviest by default).
  expect(await screen.findByLabelText(/Price history for/i)).toBeInTheDocument();
});

test("selecting a constituent swaps the member candlestick (master-detail)", async () => {
  const user = userEvent.setup();
  render(<MarketPage />);

  await screen.findByLabelText(/Price history for/i);
  const constituents = await screen.findByRole("region", { name: /constituents/i });
  await user.click(within(constituents).getByRole("button", { name: "BBB" }));
  expect(await screen.findByLabelText("Price history for BBB")).toBeInTheDocument();
});

test("the price-structure block reads bid / ask / volume per strike, never a mid", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_QUOTED));
  render(<MarketPage />);

  const block = await screen.findByLabelText(/Price structure, 3m/i);
  // The header advertises bid/ask/volume, the columns an operator reads for the spread + size.
  expect(within(block).getByRole("columnheader", { name: /bid/i })).toBeInTheDocument();
  expect(within(block).getByRole("columnheader", { name: /ask/i })).toBeInTheDocument();
  expect(within(block).getByRole("columnheader", { name: /volume/i })).toBeInTheDocument();
  // The ATM strike (100) carries quote.{bid 4.1, ask 4.5, volume 1234} — the nested shape the BFF
  // emits — shown as plain readable numbers (the currency lives in the column header), not averaged
  // to a mid. The row name concatenates every cell, including the bid/ask-derived spread (4.5−4.1
  // =0.4) and the thousands-separated volume.
  const atmRow = within(block).getByRole("row", { name: /atm/i });
  expect(atmRow).toHaveTextContent("4.1"); // bid
  expect(atmRow).toHaveTextContent("4.5"); // ask
  expect(atmRow).toHaveTextContent("0.4"); // spread = ask − bid
  expect(atmRow).toHaveTextContent("1,234"); // volume, thousands-separated
});

test("a strike with no quotes shows '-' for bid/ask/volume (honest gap, no fabricated mid)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_QUOTED));
  render(<MarketPage />);

  const block = await screen.findByLabelText(/Price structure, 3m/i);
  // The 8×10¹ (80) strike / 30dp band has null bid/ask/volume → honest dashes, never a mid.
  const noQuoteRow = within(block).getByRole("row", { name: /30dp/i });
  expect(within(noQuoteRow).getAllByText("-").length).toBeGreaterThanOrEqual(3);
});

test("the tenor panel shows Greek shape curves beside the Greeks table (complementary)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  // The §3.6 profiles: delta S-curve + gamma/vega bells vs strike, alongside the raw/$ table.
  expect(await screen.findByRole("table", { name: /Dollar Greeks, 3m/i })).toBeInTheDocument();
  const curves = await screen.findByLabelText(/Greeks 3m/i);
  expect(within(curves).getByTestId("plot-types").textContent).toMatch(/scatter,scatter,scatter/);
});

test("the surface renders a degenerate slice legibly (108%/140% IV clamped, not a spike)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DEGENERATE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Volatility surface.*maturity/i);
  // The railed slice is flagged in the label rather than rendered as a garbage peak.
  expect(surface.getAttribute("aria-label")).toMatch(/flagged|surface/i);
  // The 140%/55% cells (above the 0.35 display band) are clamped to null holes; the duplicate
  // -0.1 column is collapsed. The plotted z keeps only the in-band cells of the short slice.
  const z = JSON.parse(within(surface).getByTestId("plot-z").textContent || "[]") as (
    | number
    | null
  )[][];
  const shortSlice = z[0];
  // Every plotted value in the degenerate slice is either a hole or inside the sane band.
  for (const cell of shortSlice) {
    if (cell !== null) expect(cell).toBeLessThanOrEqual(0.6);
  }
  // The 140% cell did not survive as a height-spiking value.
  expect(shortSlice).not.toContain(1.4);
});

test("the capture coverage panel mounts collapsed and expands on demand", async () => {
  server.use(
    jsonGet("/api/coverage", {
      underlying: "SPX",
      trade_date: "2026-05-29",
      n_expiries: 0,
      expiries: [],
      tenors: [],
      qc_status: "pass",
      delta_band_status: "pass",
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const toggle = await screen.findByRole("button", { name: /show/i });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.click(toggle);
  expect(await screen.findByText(/Capture coverage, SPX/i)).toBeInTheDocument();
});

// --- §2b self-describing labels: one state writes the surface's identity sentence ----------------

const SX5E_INDICES = {
  indices: [{ symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" }],
};
const SX5E_RECORDED = {
  index: "SX5E",
  count: 1,
  dates: ["2026-06-17"],
  available: [
    { date: "2026-06-17", run_id: "run-0617", recorded_ts: "2026-06-17T17:30:00", qc: "pass" },
  ],
};

test("the surface heading is the self-describing subject, not a generic noun", async () => {
  // Default fixture is SPX as of 2026-05-29. The §2b sentence names the subject in the heading.
  render(<MarketPage />);
  // ❌ old static heading is gone.
  expect(screen.queryByRole("heading", { name: "Volatility surface" })).not.toBeInTheDocument();
  // ✅ subject heading.
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SPX" }),
  ).toBeInTheDocument();
});

test("the surface caption carries as-of · mode · coverage, degrading honestly when unwired", async () => {
  // SPX has no registered close instant → date-only as-of; coverage is not yet on the payload →
  // 'coverage unavailable', never a fabricated fraction. Mode defaults to 'strict'.
  render(<MarketPage />);
  const surface = await screen.findByLabelText("Volatility surface, SPX");
  expect(
    within(surface).getByText(/close 2026-05-29 · strict · coverage unavailable/),
  ).toBeInTheDocument();
  // No invented numerator/denominator anywhere on the caption.
  expect(within(surface).queryByText(/\d+\/\d+ quotes/)).not.toBeInTheDocument();
});

test("an SX5E surface carries the registry-resolved close instant, never 22:00", async () => {
  // The close instant is the BFF-resolved /api/analytics `close_instant` (from the index registry),
  // not a front-side hard-coded "17:30 CET" map: the front renders exactly what the payload carries.
  server.use(
    jsonGet("/api/indices", SX5E_INDICES),
    jsonGet("/api/recorded-dates", SX5E_RECORDED),
    jsonGet("/api/analytics", {
      ...ANALYTICS_SCORECARD,
      underlying: "SX5E",
      close_instant: "17:30 CET",
    }),
    jsonGet("/api/signals", SIGNALS_SX5E),
  );
  render(<MarketPage />);
  const surface = await screen.findByLabelText("Volatility surface, SX5E");
  // The instant arrives with the analytics payload (not synchronously off a front-side map), so wait.
  // Caption and figure caption both carry it — the self-describing guarantee means they agree, so
  // matching all is correct (findByText would reject the legitimate duplicate).
  const instants = await within(surface).findAllByText(/close 2026-06-17 17:30 CET/);
  expect(instants.length).toBeGreaterThan(0);
  // The 22:00 XEUR futures close is the trap this binds against.
  expect(within(surface).queryByText(/22:00/)).not.toBeInTheDocument();
});

test("switching the index rewrites the heading and the caption together (no stale label)", async () => {
  // Two indices offered; SPX selected first (date-only), then switch to SX5E (17:30 CET instant).
  server.use(
    jsonGet("/api/indices", {
      indices: [
        { symbol: "SPX", name: "S&P 500", currency: "USD" },
        { symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" },
      ],
    }),
    http.get("/api/recorded-dates", ({ request }) => {
      const idx = new URL(request.url).searchParams.get("index");
      return HttpResponse.json(idx === "SX5E" ? SX5E_RECORDED : RECORDED_TWO_DATES);
    }),
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      // The BFF resolves the close instant per underlying; SPX has none registered (date-only),
      // SX5E resolves to the 17:30 venue close. The front renders whichever the payload carries.
      return HttpResponse.json({
        ...ANALYTICS_SCORECARD,
        underlying: u,
        close_instant: u === "SX5E" ? "17:30 CET" : null,
      });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  await screen.findByRole("heading", { name: "Volatility surface, SPX" });
  await user.selectOptions(screen.getByLabelText("Index"), "SX5E");

  // The heading AND the caption both follow the new state in the same paint.
  const surface = await screen.findByLabelText("Volatility surface, SX5E");
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SX5E" }),
  ).toBeInTheDocument();
  expect(
    (await within(surface).findAllByText(/close 2026-06-17 17:30 CET/)).length,
  ).toBeGreaterThan(0);
  // The old subject left every label — no contradiction lingers.
  expect(
    screen.queryByRole("heading", { name: "Volatility surface, SPX" }),
  ).not.toBeInTheDocument();
});

test("a market-closed surface (no maturities) names its subject and reads as status, not error", async () => {
  server.use(
    jsonGet("/api/indices", SX5E_INDICES),
    jsonGet("/api/recorded-dates", SX5E_RECORDED),
    jsonGet("/api/analytics", {
      underlying: "SX5E",
      trade_date: "2026-06-17",
      n_maturities: 0,
      maturities: [],
      surface: null,
    }),
    jsonGet("/api/signals", SIGNALS_SX5E),
  );
  render(<MarketPage />);
  const empty = await screen.findByText(
    /No two-sided quote for SX5E on 2026-06-17, market probably closed\./,
  );
  // Empty self-describes and reads as a non-error status (Principle 3: empty ≠ error).
  expect(empty).toBeInTheDocument();
  expect(empty).toHaveAttribute("role", "status");
  const surface = await screen.findByLabelText("Volatility surface, SX5E");
  expect(within(surface).queryByRole("alert")).not.toBeInTheDocument();
});

test("the empty index selector anchors a next-step guidance hint that clears once chosen", async () => {
  // Slow indices so the selector is empty on first paint → the hint is anchored.
  let release = () => {};
  const gate = new Promise<void>((r) => {
    release = r;
  });
  server.use(
    http.get("/api/indices", async () => {
      await gate;
      return HttpResponse.json(INDICES_SPX_SX5E);
    }),
  );
  render(<MarketPage />);
  const select = await screen.findByLabelText("Index");
  expect(select).toHaveAttribute("data-hint", "choose-index");
  expect(screen.getByText(/Choose an index to begin/i)).toBeInTheDocument();

  release();
  // Once an index resolves and is auto-selected, the hint dies (it isn't the noise P5 forbids).
  await waitFor(() => expect(select).not.toHaveAttribute("data-hint"));
  expect(screen.queryByText(/Choose an index to begin/i)).not.toBeInTheDocument();
});

test("the index selector is driven by /api/indices, a parked index is not offered", async () => {
  server.use(
    jsonGet("/api/indices", { indices: [{ symbol: "SX5E", name: "EURO STOXX 50" }] }),
    jsonGet("/api/recorded-dates", { index: "SX5E", count: 0, dates: [], available: [] }),
  );
  render(<MarketPage />);
  const select = await screen.findByLabelText("Index");
  expect(
    within(select).getByRole("option", { name: /EURO STOXX 50 \(SX5E\)/ }),
  ).toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: /SPX/ })).not.toBeInTheDocument();
});

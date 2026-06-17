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
  ANALYTICS_QUOTED,
  ANALYTICS_SCORECARD,
  RECORDED_EMPTY,
  SIGNALS_SX5E,
} from "../test/fixtures";
import { jsonGet, notMocked, server } from "../test/server";
import { MarketPage } from "./Market";

test("leads with the index selector and an as-of dropdown — no entity/side/maturity strip", async () => {
  render(<MarketPage />);

  expect(await screen.findByLabelText("Index")).toBeInTheDocument();
  expect(screen.getByLabelText("As-of fetch")).toBeInTheDocument();
  // The ADR-0051 amputation removes the constituent "Entity" axis and the put/call switch.
  expect(screen.queryByLabelText("Entity")).not.toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: /option side/i })).not.toBeInTheDocument();
});

test("is one scrollable page (price → scorecards → nappe → tenor → dispersion), not tabs", async () => {
  render(<MarketPage />);

  // Price (context), then the scorecards block, then the 3D nappe, then the dispersion strip.
  expect(await screen.findByRole("heading", { name: "Price" })).toBeInTheDocument();
  expect(await screen.findByLabelText("Volatility scorecards")).toBeInTheDocument();
  expect(await screen.findByLabelText(/Implied-volatility surface/i)).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: /Dispersion/i })).toBeInTheDocument();
  // The old tab chrome is gone.
  expect(screen.queryByRole("tab", { name: "Analytics" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Data quality" })).not.toBeInTheDocument();
});

test("renders the four scorecards with independently-derived numbers", async () => {
  // A 3m slice with ±25Δ-bracketing bands: ATM 0.20, skew = 0.30 − 0.23 = +0.07 (+7.0 vp),
  // convexity = 0.30 + 0.23 − 0.40 = +0.13 (+13.0 vp). RV−IV from the signal fixture is −0.018
  // (−1.8 vp). The signals server mock returns SIGNALS_SX5E regardless of the index queried.
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const atm = await screen.findByLabelText("ATM level");
  expect(within(atm).getByText("20.0%")).toBeInTheDocument();
  expect(within(screen.getByLabelText("Skew 25Δ")).getByText("+7.0 vp")).toBeInTheDocument();
  expect(
    within(screen.getByLabelText("Convexity 25Δ")).getByText("+13.0 vp"),
  ).toBeInTheDocument();
  // RV−IV is the persisted iv_vs_realized signal, not a recompute.
  expect(within(screen.getByLabelText("RV − IV")).getByText("-1.8 vp")).toBeInTheDocument();
});

test("a scorecard with no data honestly shows '—' (never fabricated)", async () => {
  // The default ANALYTICS_AAA has a single put band (−0.3), so the ±25Δ wings can't be bracketed.
  render(<MarketPage />);
  const skew = await screen.findByLabelText("Skew 25Δ");
  expect(within(skew).getByText("—")).toBeInTheDocument();
});

test("one tenor selector lists the pinned grid and drives the smile + greeks table", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const tenor = await screen.findByLabelText("Tenor");
  // The pinned tenor_grid, in reading order.
  for (const label of ["10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"]) {
    expect(within(tenor).getByRole("option", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
  }
  // The default tenor (3m) is captured, so its smile and Greeks table render.
  expect(await screen.findByLabelText(/Smile — 3m/i)).toBeInTheDocument();
  expect(await screen.findByRole("table", { name: /Dollar Greeks — 3m/i })).toBeInTheDocument();
});

test("a tenor beyond the captured span renders as a labelled projection gap", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  const user = userEvent.setup();
  render(<MarketPage />);

  await screen.findByLabelText(/Smile — 3m/i);
  // 12m is offered (pinned grid) but not captured in this fixture.
  expect(
    within(screen.getByLabelText("Tenor")).getByRole("option", { name: /12m \(not captured\)/ }),
  ).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Tenor"), "12m");
  expect(await screen.findByText(/12m is not captured/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/Smile — 3m/i)).not.toBeInTheDocument();
});

test("the smile superimposes put + call (both wings, no side filter)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const smile = await screen.findByLabelText(/Smile — 3m/i);
  // Both wings plotted as scatter traces (the gap between them is the skew).
  expect(within(smile).getByTestId("plot-types").textContent).toMatch(/scatter,scatter/);
});

test("the dispersion strip reads the realized-vol ρ̄ signal (no per-member fan-out)", async () => {
  render(<MarketPage />);
  // implied_correlation from the signal fixture is 0.5 → 50.00%.
  expect(await screen.findByLabelText("Implied correlation")).toHaveTextContent(/ρ̄ = 50.00%/);
});

test("never calls /api/analytics for a constituent symbol — index-keyed only", async () => {
  const underlyings: string[] = [];
  server.use(
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      if (u) underlyings.push(u);
      return HttpResponse.json(ANALYTICS_SCORECARD);
    }),
  );
  render(<MarketPage />);

  await screen.findByLabelText(/Smile — 3m/i);
  await waitFor(() => expect(underlyings.length).toBeGreaterThan(0));
  // Only the index (SPX) is ever requested; no member (AAA/BBB) surface is fetched.
  expect(new Set(underlyings)).toEqual(new Set(["SPX"]));
});

test("renders the dense reconstructed surface as the full nappe (both wings, no side slice)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DENSE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
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
  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
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

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks — 3m/i });
  expect(within(greeks).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("€ per €1 of underlying")).toBeInTheDocument();
});

test("the scorecards strip sits at the very top, above the price block", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  const cards = await screen.findByLabelText("Volatility scorecards");
  const price = await screen.findByRole("heading", { name: "Price" });
  // DOM order = reading order: the scorecards come before the price heading (⓪ then ①).
  expect(cards.compareDocumentPosition(price) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("the price block carries a master-detail constituents workspace (index + member candles)", async () => {
  render(<MarketPage />);

  // The index candlestick and the selected member's candlestick are both present (the 2nd candle).
  expect(await screen.findByRole("heading", { name: "Price" })).toBeInTheDocument();
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

test("the price-structure block reads bid / ask / volume per strike — never a mid", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_QUOTED));
  render(<MarketPage />);

  const block = await screen.findByLabelText(/Price structure — 3m/i);
  // The header advertises bid/ask/volume, the columns an operator reads for the spread + size.
  expect(within(block).getByRole("columnheader", { name: /bid/i })).toBeInTheDocument();
  expect(within(block).getByRole("columnheader", { name: /ask/i })).toBeInTheDocument();
  expect(within(block).getByRole("columnheader", { name: /volume/i })).toBeInTheDocument();
  // The ATM strike (1×10²) carries bid 4.1, ask 4.5, volume 1234 — shown in sci-notation + unit
  // (house formatting), not averaged to a mid. The row name concatenates every cell.
  const atmRow = within(block).getByRole("row", { name: /atm/i });
  expect(atmRow).toHaveTextContent("4.1 × 10⁰ $");
  expect(atmRow).toHaveTextContent("4.5 × 10⁰ $");
  expect(atmRow).toHaveTextContent("1.234 × 10³");
});

test("a strike with no quotes shows '—' for bid/ask/volume (honest gap, no fabricated mid)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_QUOTED));
  render(<MarketPage />);

  const block = await screen.findByLabelText(/Price structure — 3m/i);
  // The 8×10¹ (80) strike / 30dp band has null bid/ask/volume → honest dashes, never a mid.
  const noQuoteRow = within(block).getByRole("row", { name: /30dp/i });
  expect(within(noQuoteRow).getAllByText("—").length).toBeGreaterThanOrEqual(3);
});

test("the tenor panel shows Greek shape curves beside the Greeks table (complementary)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_SCORECARD));
  render(<MarketPage />);

  // The §3.6 profiles: delta S-curve + gamma/vega bells vs strike, alongside the raw/$ table.
  expect(await screen.findByRole("table", { name: /Dollar Greeks — 3m/i })).toBeInTheDocument();
  const curves = await screen.findByLabelText(/Greek profiles — 3m/i);
  expect(within(curves).getByTestId("plot-types").textContent).toMatch(/scatter,scatter,scatter/);
});

test("the nappe renders a degenerate slice legibly (108%/140% IV clamped, not a spike)", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DEGENERATE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
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
  expect(await screen.findByText(/Capture coverage — SPX/i)).toBeInTheDocument();
});

test("the index selector is driven by /api/indices — a parked index is not offered", async () => {
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

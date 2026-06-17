import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import type { PlotProps } from "./Plot";

// A local Plot mock that, unlike the shared one, serializes the layout and each trace's
// hovertemplate — so the axis-title units and the per-point provenance tooltip (the two things this
// suite proves) can be asserted from the DOM rather than reaching into Plotly internals.
vi.mock("./Plot", () => ({
  Plot: ({ data, layout, label }: PlotProps) => {
    const types = data.map((t) => (t as { type?: string }).type ?? "unknown").join(",");
    const z = (data[0] as { z?: unknown }).z;
    const templates = data
      .map((t) => (t as { hovertemplate?: string }).hovertemplate ?? "")
      .join("||");
    const texts = data
      .map((t) => JSON.stringify((t as { text?: unknown }).text ?? null))
      .join("||");
    return (
      <figure aria-label={label}>
        <figcaption>{label}</figcaption>
        <div data-testid="plot-types">{types}</div>
        <div data-testid="plot-z">{z === undefined ? "" : JSON.stringify(z)}</div>
        <div data-testid="plot-layout">{JSON.stringify(layout ?? {})}</div>
        <div data-testid="plot-hovertemplates">{templates}</div>
        <div data-testid="plot-texts">{texts}</div>
      </figure>
    );
  },
}));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import type { AnalyticsMaturity, AnalyticsResponse, SurfaceDense } from "../api";
import {
  describeSurface,
  GreeksShapeCurves,
  SmileChart,
  type SurfaceCoverage,
  VolSurface,
} from "./charts";

// The narrow no-break space (U+202F) the descriptor groups thousands with — pinned in the oracle
// so the expected strings are derived from the spec sentence, not copied from the code under test.

// A minimal smooth dense surface so VolSurface takes the DenseVolSurface path and the Plot mock
// renders the figcaption with the descriptor title.
const SMOOTH: SurfaceDense = {
  log_moneyness: [-0.1, 0.0, 0.1],
  maturity_years: [0.083, 0.25],
  implied_vol: [
    [0.2, 0.18, 0.19],
    [0.21, 0.2, 0.205],
  ],
  model_version: "svi-test",
  degenerate_maturity_years: [],
};

// A degenerate-everywhere dense surface: every maturity in the degenerate list → market-closed tone.
const DEGENERATE_CLOSE: SurfaceDense = {
  log_moneyness: [-0.1, 0.0, 0.1],
  maturity_years: [0.083, 0.25],
  implied_vol: [
    [0.2, 0.18, 0.19],
    [0.21, 0.2, 0.205],
  ],
  model_version: "svi-test",
  degenerate_maturity_years: [0.083, 0.25],
};

const SX5E_MATURITY: AnalyticsMaturity = {
  maturity_years: 0.083,
  tenor_label: "1m",
  label: "1m (0.083y)",
  smile: {
    axis_type: "delta",
    deltas: [-0.3, 0.0, 0.3],
    implied_vols: [0.21, 0.18, 0.2],
    log_moneyness: [-0.1, 0.0, 0.1],
  },
  surface_slice: null,
  points: [
    {
      delta_band: "30dp",
      target_delta: -0.3,
      log_moneyness: -0.1,
      strike: 4000,
      forward_price: 4200,
      implied_vol: 0.21,
      total_variance: 0.01,
      price: 12,
      // One-sided quote → "one-sided indicative mark".
      quote: { bid: 11.5, ask: null, volume: 3 },
      metrics: {
        delta: { raw: -0.3, dollar: -1260, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.01, dollar: 4, unit: "$ per 1% move" },
        vega: { raw: 0.5, dollar: 0.5, unit: "$ per 1 vol point" },
        theta: { raw: -0.01, dollar: -0.00002, unit: "$ per calendar day" },
        rho: { raw: 0.08, dollar: 0.001, unit: "$ per 1% rate" },
      },
      provenance: {
        calc_ts: "2026-06-17T15:30:00+00:00",
        code_version: "v",
        config_hash: "c",
        stamp_hash: "s",
        n_sources: 1,
      },
    },
    {
      delta_band: "atm",
      target_delta: 0.0,
      log_moneyness: 0.0,
      strike: 4200,
      forward_price: 4200,
      implied_vol: 0.18,
      total_variance: 0.008,
      price: 30,
      // Two-sided quote → "two-sided".
      quote: { bid: 29.5, ask: 30.5, volume: 120 },
      metrics: {
        delta: { raw: 0.0, dollar: 0, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.02, dollar: 8, unit: "$ per 1% move" },
        vega: { raw: 0.7, dollar: 0.7, unit: "$ per 1 vol point" },
        theta: { raw: -0.02, dollar: -0.00004, unit: "$ per calendar day" },
        rho: { raw: 0.09, dollar: 0.0012, unit: "$ per 1% rate" },
      },
      provenance: {
        calc_ts: "2026-06-17T15:30:00+00:00",
        code_version: "v",
        config_hash: "c",
        stamp_hash: "s",
        n_sources: 1,
      },
    },
  ],
};

const SX5E_ANALYTICS: AnalyticsResponse = {
  underlying: "SX5E",
  trade_date: "2026-06-17",
  n_maturities: 1,
  maturities: [SX5E_MATURITY],
  surface: SMOOTH,
};

const COVERAGE_STRICT: SurfaceCoverage = { resting: 1706, total: 2412 };
const COVERAGE_INDICATIVE: SurfaceCoverage = { resting: 2280, total: 2412, indicative: 574 };

describe("describeSurface, hand-built oracle (subject · as-of · mode · coverage)", () => {
  test("strict names every fact; partial coverage (1706/2412) raises the voice to partial", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      coverage: COVERAGE_STRICT,
    });
    expect(d.title).toBe(
      `Volatility surface, SX5E · close 2026-06-17 17:30 CET · strict · 1,706/2,412 quotes`,
    );
    // 1706 < 2412 is partial coverage: the headline raises its voice (spec: recede only when full).
    expect(d.tone).toBe("partial");
  });

  test("strict + genuinely full coverage (resting === total) recedes to tone full", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      coverage: { resting: 2412, total: 2412 },
    });
    expect(d.title).toBe(
      `Volatility surface, SX5E · close 2026-06-17 17:30 CET · strict · 2,412/2,412 quotes`,
    );
    expect(d.tone).toBe("full");
  });

  test("indicative + partial says INDICATIVE and counts the indicative marks, tone partial", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "indicative",
      coverage: COVERAGE_INDICATIVE,
    });
    expect(d.title).toBe(
      `Volatility surface, SX5E · close 2026-06-17 17:30 CET · INDICATIVE · 2,280/2,412 (574 indicative marks)`,
    );
    expect(d.tone).toBe("partial");
  });

  test("degenerate close goes loud with market probably closed, tone degenerate", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "indicative",
      coverage: COVERAGE_INDICATIVE,
      degenerate: true,
    });
    expect(d.title).toBe(
      "Volatility surface, SX5E · close 2026-06-17 17:30 CET · indicative, market probably closed",
    );
    expect(d.tone).toBe("degenerate");
  });

  test("missing coverage degrades to coverage unavailable, never a fabricated fraction", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      coverage: null,
    });
    expect(d.title).toBe(
      "Volatility surface, SX5E · close 2026-06-17 17:30 CET · strict · coverage unavailable",
    );
    expect(d.title).not.toMatch(/\//);
  });

  test("unknown close instant renders the date only, never 22:00", () => {
    const d = describeSurface({
      subject: "SX5E",
      asOf: "2026-06-17",
      mode: "strict",
      coverage: COVERAGE_STRICT,
    });
    expect(d.asOfPhrase).toBe("close 2026-06-17");
    expect(d.title).not.toMatch(/17:30|22:00|:/);
  });

  test("no mode defaults to strict, never invents indicative", () => {
    const d = describeSurface({ subject: "SX5E", asOf: "2026-06-17", coverage: COVERAGE_STRICT });
    expect(d.modeWord).toBe("strict");
    expect(d.title).toMatch(/· strict ·/);
    expect(d.title).not.toMatch(/INDICATIVE|indicative/);
  });
});

describe("VolSurface, one state drives every label, no contradiction", () => {
  test("dense surface figcaption carries subject · as-of · mode · coverage", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        closeInstant="17:30 CET"
        mode="strict"
        coverage={COVERAGE_STRICT}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    const label = fig.getAttribute("aria-label") || "";
    expect(label).toMatch(/SX5E/);
    expect(label).toMatch(/close 2026-06-17 17:30 CET/);
    expect(label).toMatch(/strict/);
    expect(label).toMatch(new RegExp(`1,706/2,412 quotes`));
  });

  test("switching the subject rewrites the title in the same paint", () => {
    const { rerender } = render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    expect(screen.getByLabelText(/Volatility surface, SX5E/i)).toBeTruthy();

    rerender(
      <VolSurface
        subject="DAX"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    expect(screen.queryByLabelText(/Volatility surface, SX5E/i)).toBeNull();
    expect(screen.getByLabelText(/Volatility surface, DAX/i)).toBeTruthy();
  });

  test("indicative + partial never says strict and never says full coverage", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        closeInstant="17:30 CET"
        mode="indicative"
        coverage={COVERAGE_INDICATIVE}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    const label =
      screen.getByLabelText(/Volatility surface, SX5E/i).getAttribute("aria-label") || "";
    expect(label).toMatch(/INDICATIVE/);
    expect(label).not.toMatch(/· strict ·/);
    expect(label).not.toMatch(/complète/i);
  });

  test("dense surface axes carry their UNITS unit in the house idiom", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    const layout =
      within(screen.getByLabelText(/Volatility surface, SX5E/i)).getByTestId("plot-layout")
        .textContent || "";
    // log-moneyness (ln(K/F)), maturity (y), implied vol (Vol) — the UNITS tokens on the titles.
    expect(layout).toMatch(/log-moneyness \(ln\(K\/F\)\)/);
    expect(layout).toMatch(/maturity \(y\)/);
    expect(layout).toMatch(/implied vol \(Vol\)/);
  });

  test("dense surface point tooltip carries coordinates with units + two-sided provenance", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        surface={SX5E_ANALYTICS.surface}
        maturities={SX5E_ANALYTICS.maturities}
      />,
    );
    const tpl =
      within(screen.getByLabelText(/Volatility surface, SX5E/i)).getByTestId("plot-hovertemplates")
        .textContent || "";
    expect(tpl).toMatch(/log-moneyness %\{x:\.3f\} ln\(K\/F\)/);
    expect(tpl).toMatch(/maturity %\{y:\.2f\} y/);
    expect(tpl).toMatch(/implied vol %\{z:\.1%\} · two-sided/);
  });
});

describe("VolSurface empty state, self-describing, not a generic blank", () => {
  test("empty surface names its subject and as-of and reads as status", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={null}
        surface={null}
        maturities={[]}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    const status = within(fig).getByRole("status");
    expect(status.textContent).toBe("No surface for SX5E on 2026-06-17.");
    expect(status.textContent).not.toMatch(/No surface to plot/i);
  });

  test("degenerate-everywhere close announces market probably closed in title and empty copy", () => {
    render(
      <VolSurface
        subject="SX5E"
        asOf="2026-06-17"
        mode="indicative"
        coverage={COVERAGE_INDICATIVE}
        surface={DEGENERATE_CLOSE}
        maturities={[]}
      />,
    );
    const fig = screen.getByLabelText(/market probably closed/i);
    expect(fig.getAttribute("aria-label")).toMatch(/Volatility surface, SX5E/);
  });
});

describe("SmileChart, shares the identity sentence; tooltip carries provenance", () => {
  test("title names subject·as-of·mode and the selected tenor", () => {
    render(
      <SmileChart
        subject="SX5E"
        asOf="2026-06-17"
        closeInstant="17:30 CET"
        mode="strict"
        coverage={COVERAGE_STRICT}
        maturities={[SX5E_MATURITY]}
        maturityLabel={SX5E_MATURITY.label}
      />,
    );
    const label =
      screen.getByLabelText(/Volatility surface, SX5E/i).getAttribute("aria-label") || "";
    expect(label).toMatch(/SX5E/);
    expect(label).toMatch(/close 2026-06-17 17:30 CET/);
    expect(label).toMatch(/smile 1m/);
  });

  test("empty smile self-describes off the same descriptor", () => {
    render(
      <SmileChart subject="SX5E" asOf="2026-06-17" mode="strict" coverage={null} maturities={[]} />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    expect(within(fig).getByRole("status").textContent).toBe("No surface for SX5E on 2026-06-17.");
  });

  test("legend still names the real series (puts/calls), never Series 1", () => {
    render(
      <SmileChart
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        maturities={[SX5E_MATURITY]}
        maturityLabel={SX5E_MATURITY.label}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    expect(within(fig).getByTestId("plot-types").textContent).toMatch(/scatter/);
    const tpl = within(fig).getByTestId("plot-hovertemplates").textContent || "";
    // The wing series carry their real name in the hovertemplate, never a "Series N".
    expect(tpl).toMatch(/puts ·/);
    expect(tpl).toMatch(/calls ·/);
    expect(tpl).not.toMatch(/Series \d/);
  });

  test("point tooltip carries per-point provenance: one-sided → marque indicative, two-sided → two-sided", () => {
    render(
      <SmileChart
        subject="SX5E"
        asOf="2026-06-17"
        mode="indicative"
        coverage={COVERAGE_INDICATIVE}
        maturities={[SX5E_MATURITY]}
        maturityLabel={SX5E_MATURITY.label}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    // The template feeds per-point provenance through its %{text} slot…
    const tpl = within(fig).getByTestId("plot-hovertemplates").textContent || "";
    expect(tpl).toMatch(/implied vol %\{y:\.1%\} · %\{text\}/);
    // …and the trace `text` array carries the real provenance words: the 30dp point has a
    // one-sided quote (ask null) → indicative; the ATM point is two-sided.
    const texts = within(fig).getByTestId("plot-texts").textContent || "";
    expect(texts).toMatch(/one-sided indicative mark/);
    expect(texts).toMatch(/two-sided/);
  });
});

describe("GreeksShapeCurves, identity sentence + currencied strike axis", () => {
  test("title names subject·as-of and the selected tenor", () => {
    render(
      <GreeksShapeCurves
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={COVERAGE_STRICT}
        currency="EUR"
        maturities={[SX5E_MATURITY]}
        maturityLabel={SX5E_MATURITY.label}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    const label = fig.getAttribute("aria-label") || "";
    expect(label).toMatch(/Greeks 1m/);
    // strike ($) re-currencied to € via withCurrency for an EUR index.
    const layout = within(fig).getByTestId("plot-layout").textContent || "";
    expect(layout).toMatch(/strike \(€\)/);
  });

  test("empty greeks self-describes", () => {
    render(
      <GreeksShapeCurves
        subject="SX5E"
        asOf="2026-06-17"
        mode="strict"
        coverage={null}
        maturities={[]}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    expect(within(fig).getByRole("status").textContent).toBe("No surface for SX5E on 2026-06-17.");
  });
});

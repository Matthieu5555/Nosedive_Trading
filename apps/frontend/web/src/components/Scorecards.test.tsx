import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { AnalyticsMaturity, AnalyticsPoint, Signal } from "../api";
import { asOfCloseLine, Scorecards } from "./Scorecards";

const PROV = {
  calc_ts: "2026-06-17T15:31:00+00:00",
  code_version: "abc",
  config_hash: "cfg",
  stamp_hash: "stamp",
  n_sources: 1,
};

function point(targetDelta: number, iv: number): AnalyticsPoint {
  return {
    delta_band: `${targetDelta}`,
    target_delta: targetDelta,
    log_moneyness: 0,
    strike: 100,
    forward_price: 100,
    implied_vol: iv,
    total_variance: 0,
    price: 0,
    metrics: {
      delta: { raw: 0, dollar: 0, unit: null },
      gamma: { raw: 0, dollar: 0, unit: null },
      vega: { raw: 0, dollar: 0, unit: null },
      theta: { raw: 0, dollar: 0, unit: null },
      rho: { raw: 0, dollar: 0, unit: null },
    },
    provenance: PROV,
  };
}

function maturity(): AnalyticsMaturity {
  return {
    maturity_years: 0.25,
    tenor_label: "3m",
    label: "3m (0.250y)",
    smile: {
      axis_type: "delta",
      deltas: [],
      implied_vols: [0.24, 0.2, 0.22],
      log_moneyness: [-0.1, 0.0, 0.1],
    },
    surface_slice: null,
    points: [point(-0.3, 0.3), point(-0.2, 0.26), point(0.2, 0.22), point(0.3, 0.24)],
  };
}

function signal(value: number, tenor = "3m"): Signal {
  return {
    signal_kind: "term_structure_slope",
    label: "slope",
    subject: "SX5E",
    tenor_label: tenor,
    value,
    unit: null,
    snapshot_ts: "2026-06-17T17:30:00+02:00",
    source_snapshot_ts: "2026-06-17T17:30:00+02:00",
    provenance: PROV,
  };
}

describe("asOfCloseLine — the SX5E close instant is 17:30 CET, not the bare date and not 22:00", () => {
  test("a known index carries its option close instant", () => {
    expect(asOfCloseLine("2026-06-17", "SX5E")).toBe("as of 2026-06-17 17:30 CET (close)");
  });

  test("the instant is 17:30 CET — never the 22:00 XEUR futures close (the original trap)", () => {
    const line = asOfCloseLine("2026-06-17", "SX5E");
    expect(line).toContain("17:30 CET");
    expect(line).not.toContain("22:00");
    expect(line).not.toContain("00:00");
  });

  test("an unknown index degrades to date-only — never a guessed time", () => {
    expect(asOfCloseLine("2026-06-17", "UNKNOWN")).toBe("as of 2026-06-17");
  });

  test("a missing as-of yields no line at all (never a fabricated stamp)", () => {
    expect(asOfCloseLine(null, "SX5E")).toBeNull();
    expect(asOfCloseLine(undefined, undefined)).toBeNull();
  });
});

describe("Scorecards — provenance line binds to live state", () => {
  test("the as-of line states subject + the 17:30 CET close instant", () => {
    render(
      <Scorecards
        maturities={[maturity()]}
        ivVsRealized={null}
        termStructureSlope={signal(0.02)}
        ivRank={null}
        impliedCorrelation={null}
        underlying="SX5E"
        asOf="2026-06-17"
        runId="run-abc"
      />,
    );
    const prov = screen.getByLabelText("Scorecard provenance");
    expect(prov.textContent).toContain("SX5E");
    expect(prov.textContent).toContain("as of 2026-06-17 17:30 CET (close)");
  });

  test("the as-of line rewrites itself when the underlying/date changes (label tracks state)", () => {
    const { rerender } = render(
      <Scorecards
        maturities={[maturity()]}
        ivVsRealized={null}
        termStructureSlope={null}
        ivRank={null}
        impliedCorrelation={null}
        underlying="SX5E"
        asOf="2026-06-17"
      />,
    );
    expect(screen.getByLabelText("Scorecard provenance").textContent).toContain(
      "as of 2026-06-17 17:30 CET (close)",
    );
    rerender(
      <Scorecards
        maturities={[maturity()]}
        ivVsRealized={null}
        termStructureSlope={null}
        ivRank={null}
        impliedCorrelation={null}
        underlying="UNKNOWN"
        asOf="2026-06-10"
      />,
    );
    const prov = screen.getByLabelText("Scorecard provenance").textContent ?? "";
    expect(prov).toContain("UNKNOWN");
    expect(prov).toContain("as of 2026-06-10");
    expect(prov).not.toContain("17:30 CET");
  });

  test("a provenance InfoDot names where each number came from and cites the source capture", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    render(
      <Scorecards
        maturities={[maturity()]}
        ivVsRealized={null}
        termStructureSlope={null}
        ivRank={null}
        impliedCorrelation={null}
        underlying="SX5E"
        asOf="2026-06-17"
        runId="run-abc"
      />,
    );
    await user.hover(screen.getByRole("button", { name: /where these numbers come from/i }));
    const tip = screen.getByRole("tooltip");
    expect(tip.textContent).toContain("persisted signals");
    expect(tip.textContent).toContain("run run-abc");
  });

  test("with no provenance props the band still renders its six cards (degrades cleanly)", () => {
    render(
      <Scorecards
        maturities={[maturity()]}
        ivVsRealized={null}
        termStructureSlope={null}
        ivRank={null}
        impliedCorrelation={null}
      />,
    );
    expect(screen.queryByLabelText("Scorecard provenance")).not.toBeInTheDocument();
    expect(screen.getByLabelText("ATM level")).toBeInTheDocument();
    expect(screen.getByLabelText("ρ̄")).toBeInTheDocument();
  });
});

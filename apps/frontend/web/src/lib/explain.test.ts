import { describe, expect, it } from "vitest";

import {
  EXPLAIN,
  type ExplainContext,
  explainWithContext,
  isMetricId,
  METRIC_IDS,
  UnknownMetricError,
} from "./explain";

const NBSP = "\u202f";

describe("EXPLAIN map", () => {
  it("has one entry per declared metric id and each is fully populated", () => {
    const expected = [
      "atm_level",
      "term_structure_slope",
      "iv_rank",
      "skew_25d",
      "rv_minus_iv",
      "rho_bar",
      "convexity_25d",
      "nappe",
      "smile",
      "greek_profiles",
      "surface_coverage",
    ].sort();
    expect([...METRIC_IDS].sort()).toEqual(expected);
    for (const id of METRIC_IDS) {
      const e = EXPLAIN[id];
      expect(e.label.length).toBeGreaterThan(0);
      expect(e.whatIs.length).toBeGreaterThan(0);
      expect(e.howToRead.length).toBeGreaterThan(0);
      expect(typeof e.whereFrom).toBe("function");
    }
  });

  it("bakes no live reading into the what-is copy (the assistant must cite live data)", () => {
    const renderedValue = /\d+([.,]\d+)?\s*(%|vp|×\s*10)/;
    for (const id of METRIC_IDS) {
      expect(EXPLAIN[id].whatIs).not.toMatch(renderedValue);
    }
  });

  it("moved the smile/greek/convexity copy verbatim from the components", () => {
    expect(EXPLAIN.smile.whatIs).toBe("implied vol vs log-moneyness; puts ◄ ATM ► calls");
    expect(EXPLAIN.greek_profiles.whatIs).toBe(
      "raw Greeks vs strike; gamma/vega bell, delta S-curve (where it peaks)",
    );
    expect(EXPLAIN.convexity_25d.whatIs).toBe("butterfly: IV(25Δp) + IV(25Δc) − 2·ATM");
  });
});

describe("whereFrom is a pure function of context", () => {
  it("prints the SX5E close instant at 17:30 CET for a recorded signal", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "signal",
      tenorLabel: "3m",
      value: 0.012,
    };
    expect(EXPLAIN.term_structure_slope.whereFrom(ctx)).toBe(
      `signal enregistré · 3m · clôture 2026-06-17 17:30 CET`,
    );
  });

  it("projects ATM/skew from the smile, never from a stored signal", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "projected",
      tenorLabel: "3m",
      value: 0.184,
    };
    expect(EXPLAIN.atm_level.whereFrom(ctx)).toBe(
      `projeté depuis le smile · 3m · clôture 2026-06-17 17:30 CET`,
    );
  });

  it("names indicative marks as not the stored close", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "indicative",
      source: "signal",
      tenorLabel: "1m",
      value: 0.02,
    };
    expect(EXPLAIN.iv_rank.whereFrom(ctx)).toBe(
      `signal enregistré · 1m · clôture 2026-06-17 17:30 CET · marque indicative — pas la clôture stockée`,
    );
  });

  it("says non enregistré, never a guess, when the value is absent", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "signal",
      tenorLabel: "3m",
      value: null,
    };
    expect(EXPLAIN.rv_minus_iv.whereFrom(ctx)).toBe("signal non enregistré pour cette clôture");
  });

  it("renders the coverage clause for the nappe in strict mode", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "surface",
      coverage: { twoSided: 1706, total: 2412 },
    };
    expect(EXPLAIN.nappe.whereFrom(ctx)).toBe(
      `SX5E · clôture 2026-06-17 17:30 CET · strict · 1${NBSP}706 / 2${NBSP}412 cotations · 70,7${NBSP}% deux-faces · 706 à une face exclues`,
    );
  });

  it("degrades to couverture indisponible when the coverage block is absent", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "surface",
      coverage: null,
    };
    expect(EXPLAIN.surface_coverage.whereFrom(ctx)).toBe(
      `SX5E · clôture 2026-06-17 17:30 CET · strict · couverture indisponible`,
    );
  });

  it("does not look ahead — it only formats the as-of it is handed", () => {
    const ctx: ExplainContext = {
      underlying: "SX5E",
      asOf: "2026-06-10",
      mode: "strict",
      source: "signal",
      tenorLabel: "3m",
      value: 0.01,
    };
    const where = EXPLAIN.term_structure_slope.whereFrom(ctx);
    expect(where).toContain("2026-06-10");
    expect(where).not.toContain("2026-06-17");
  });
});

describe("explainWithContext — the grounded assistant seam", () => {
  it("assembles label + what + how + where-from + the live value through sciUnit", () => {
    const out = explainWithContext("atm_level", {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      mode: "strict",
      source: "projected",
      tenorLabel: "3m",
      value: 0.184,
      unit: "Vol",
    });
    expect(out.id).toBe("atm_level");
    expect(out.label).toBe("ATM level");
    expect(out.whatIs).toBe(EXPLAIN.atm_level.whatIs);
    expect(out.howToRead).toBe(EXPLAIN.atm_level.howToRead);
    expect(out.whereFrom).toContain("clôture 2026-06-17 17:30 CET");
    expect(out.value).toBe("1.84 × 10⁻¹ Vol");
  });

  it("never emits a numeral when the value is absent (Principle 6 honesty guard)", () => {
    const out = explainWithContext("rv_minus_iv", {
      underlying: "SX5E",
      asOf: "2026-06-17",
      closeInstant: "17:30 CET",
      source: "signal",
      tenorLabel: "3m",
      value: null,
    });
    expect(out.value).toBeNull();
    expect(out.whereFrom).toBe("signal non enregistré pour cette clôture");
    expect(out.whereFrom).not.toMatch(/\d/);
  });

  it("rejects an unknown metric id with a typed error, never free text", () => {
    expect(() => explainWithContext("made_up_metric")).toThrow(UnknownMetricError);
    expect(isMetricId("made_up_metric")).toBe(false);
    expect(isMetricId("nappe")).toBe(true);
  });
});

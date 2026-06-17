import { describe, expect, test } from "vitest";

import {
  ASSISTANT_COPY,
  type ExplainContext,
  explainEntry,
  explainWithContext,
  knownElementIds,
} from "./assistantCopy";

const STRICT_SIGNAL_CTX: ExplainContext = {
  underlying: "SX5E",
  asOf: "2026-06-17",
  closeInstant: "2026-06-17 17:30 CET",
  mode: "strict",
  tenor: "3m",
  source: "signal",
  hasValue: true,
};

describe("assistantCopy whereFrom", () => {
  test("a strict signal names the OESX close instant 17:30 CET, not 22:00", () => {
    const where = ASSISTANT_COPY.atm_level.whereFrom(STRICT_SIGNAL_CTX);
    expect(where).toContain("17:30 CET");
    expect(where).not.toContain("22:00");
    expect(where).toContain("clôture 2026-06-17 17:30 CET");
    expect(where).toContain("signal enregistré · 3m");
    expect(where).toContain("clôture stockée (strict)");
  });

  test("falls back to a 17:30 CET clause when the resolved instant is absent", () => {
    const where = ASSISTANT_COPY.atm_level.whereFrom({
      ...STRICT_SIGNAL_CTX,
      closeInstant: null,
    });
    expect(where).toContain("2026-06-17 17:30 CET");
    expect(where).not.toContain("22:00");
  });

  test("indicative mode is named indicative and explicitly NOT the stored close", () => {
    const where = ASSISTANT_COPY.atm_level.whereFrom({
      ...STRICT_SIGNAL_CTX,
      mode: "indicative",
    });
    expect(where).toContain("INDICATIVE");
    expect(where).toContain("pas la clôture stockée");
    expect(where).not.toContain("(strict)");
  });

  test("a projected read says projeté depuis le smile, not signal enregistré", () => {
    const where = ASSISTANT_COPY.skew_25d.whereFrom({
      ...STRICT_SIGNAL_CTX,
      source: "projected",
    });
    expect(where).toContain("projeté depuis le smile · 3m");
    expect(where).not.toContain("signal enregistré");
  });

  test("an absent value says non enregistré and emits no numeral", () => {
    const where = ASSISTANT_COPY.atm_level.whereFrom({
      ...STRICT_SIGNAL_CTX,
      hasValue: false,
    });
    expect(where).toContain("non enregistré");
    expect(where).toMatch(/non enregistré pour cette clôture \(SX5E\)/);
  });
});

describe("assistantCopy guardrail (no invented number)", () => {
  test("no entry bakes in a FORMATTED live value (sci-notation or a unit-bearing decimal)", () => {
    // The guardrail is that the *number the assistant quotes* always comes from the citation
    // (the server-built facts block), never from this copy. Definitional integers that are part
    // of a metric's definition ("sur 1 an", "de 0 à 1", "2·ATM") are prose, not live values; a
    // formatted analytics value would look like sci-notation ("1.83 × 10⁻¹") or a decimal with a
    // unit ("18.3%", "0.42 Vol") — those must never appear.
    for (const [id, entry] of Object.entries(ASSISTANT_COPY)) {
      // Strip the one definitional scale constant (vp = 0.01 IV) before the decimal check.
      const copy = `${entry.whatIs} ${entry.howToRead}`.replace(/0\.01/g, "");
      expect(copy, `entry ${id} baked in a sci-notation value`).not.toMatch(/×\s*10/);
      expect(copy, `entry ${id} baked in a decimal value`).not.toMatch(/\d+\.\d/);
    }
  });

  test("explainWithContext returns copy + provenance, never a number, for an absent value", () => {
    const text = explainWithContext("atm_level", {
      ...STRICT_SIGNAL_CTX,
      hasValue: false,
    });
    expect(text).not.toBeNull();
    expect(text).toContain("non enregistré");
  });

  test("an unknown id returns null (typed unknown metric), never free text", () => {
    expect(explainEntry("not_a_metric")).toBeNull();
    expect(explainWithContext("not_a_metric", STRICT_SIGNAL_CTX)).toBeNull();
  });
});

describe("assistantCopy vocabulary", () => {
  test("every known id has a label, whatIs, howToRead and whereFrom", () => {
    for (const id of knownElementIds()) {
      const entry = explainEntry(id);
      expect(entry).not.toBeNull();
      expect(entry?.label).toBeTruthy();
      expect(entry?.whatIs).toBeTruthy();
      expect(entry?.howToRead).toBeTruthy();
      expect(typeof entry?.whereFrom).toBe("function");
    }
  });

  test("the closed vocabulary covers the metric ids the build-order spec fixes", () => {
    const ids = new Set(knownElementIds());
    for (const id of [
      "nappe",
      "smile",
      "greek_profiles",
      "atm_level",
      "skew_25d",
      "rv_minus_iv",
      "iv_rank",
      "term_structure_slope",
      "rho_bar",
      "convexity_25d",
      "surface_coverage",
    ]) {
      expect(ids.has(id), `missing copy entry for ${id}`).toBe(true);
    }
  });
});

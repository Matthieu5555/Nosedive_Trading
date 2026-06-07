import { expect, test } from "vitest";

import { buildTemplate } from "./basketTemplates";

// Expected legs are hand-listed here (not read from the template code). The simple scenario is the
// three pillars −30Δ put / ATM / +30Δ call. See basketTemplates.ts for the straddle limitation.

test("straddle is the single ATM leg (interim — full two-leg straddle needs an ATM-put cell)", () => {
  // NOT the ±30Δ pair (that is the strangle) and NOT two ATM calls (delta +1, not a straddle).
  expect(buildTemplate("straddle", "AAA", "1m")).toEqual([
    { instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA", tenor_label: "1m", delta_band: "atm" },
  ]);
});

test("strangle composes the ±30Δ wings (long 30dc + long 30dp)", () => {
  expect(buildTemplate("strangle", "AAA", "3m")).toEqual([
    { instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA", tenor_label: "3m", delta_band: "30dc" },
    { instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA", tenor_label: "3m", delta_band: "30dp" },
  ]);
});

test("risk_reversal is long the +30Δ call, short the −30Δ put (signed +1 / -1)", () => {
  expect(buildTemplate("risk_reversal", "MSFT", "6m")).toEqual([
    { instrument_kind: "option", side: "long", quantity: 1, underlying: "MSFT", tenor_label: "6m", delta_band: "30dc" },
    { instrument_kind: "option", side: "short", quantity: -1, underlying: "MSFT", tenor_label: "6m", delta_band: "30dp" },
  ]);
});

test("straddle and strangle do NOT compose identical legs", () => {
  // The expert's dispositive check: two template buttons that produce the same legs is a bug.
  const straddle = buildTemplate("straddle", "AAA", "1m");
  const strangle = buildTemplate("strangle", "AAA", "1m");
  expect(straddle).not.toEqual(strangle);
});

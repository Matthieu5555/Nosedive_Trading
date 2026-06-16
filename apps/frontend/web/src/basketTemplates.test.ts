import { expect, test } from "vitest";

import { buildTemplate } from "./basketTemplates";

test("straddle composes the two ATM legs (long atm call + long atmp put, same strike)", () => {
  expect(buildTemplate("straddle", "AAA", "1m")).toEqual([
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "AAA",
      tenor_label: "1m",
      delta_band: "atm",
    },
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "AAA",
      tenor_label: "1m",
      delta_band: "atmp",
    },
  ]);
});

test("strangle composes the ±30Δ wings (long 30dc + long 30dp)", () => {
  expect(buildTemplate("strangle", "AAA", "3m")).toEqual([
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "AAA",
      tenor_label: "3m",
      delta_band: "30dc",
    },
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "AAA",
      tenor_label: "3m",
      delta_band: "30dp",
    },
  ]);
});

test("risk_reversal is long the +30Δ call, short the −30Δ put (signed +1 / -1)", () => {
  expect(buildTemplate("risk_reversal", "MSFT", "6m")).toEqual([
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "MSFT",
      tenor_label: "6m",
      delta_band: "30dc",
    },
    {
      instrument_kind: "option",
      side: "short",
      quantity: -1,
      underlying: "MSFT",
      tenor_label: "6m",
      delta_band: "30dp",
    },
  ]);
});

test("straddle and strangle do NOT compose identical legs", () => {
  const straddle = buildTemplate("straddle", "AAA", "1m");
  const strangle = buildTemplate("strangle", "AAA", "1m");
  expect(straddle).not.toEqual(strangle);
});

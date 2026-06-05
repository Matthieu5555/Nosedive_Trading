import { describe, expect, it } from "vitest";

import { money, number, percent, signedMoney, statusLabel, volPercent } from "./format";

describe("number", () => {
  it.each([
    [5312.42, 2, "5,312.42"],
    [1840000, 0, "1,840,000"],
    [0.00763, 5, "0.00763"],
    [-46.44, 2, "-46.44"],
  ])("formats %s with %s digits as %s", (value, digits, expected) => {
    expect(number(value, digits)).toBe(expected);
  });
});

describe("money", () => {
  it.each([
    [5312.42, "USD", "$5,312.42"],
    [5311.9, "USD", "$5,311.90"],
    [1.3, "USD", "$1.30"],
    [196.45, "EUR", "€196.45"],
  ])("formats %s %s as %s", (value, currency, expected) => {
    expect(money(value, currency)).toBe(expected);
  });
});

describe("signedMoney", () => {
  it.each([
    [12224, "+$12,224"],
    [-915, "-$915"],
    [0, "$0"],
  ])("formats %s as %s", (value, expected) => {
    expect(signedMoney(value)).toBe(expected);
  });
});

describe("percent", () => {
  it.each([
    [0.42, "+0.42%"],
    [-3, "-3.00%"],
    [0, "0.00%"],
  ])("formats %s as %s", (value, expected) => {
    expect(percent(value)).toBe(expected);
  });
});

describe("volPercent", () => {
  it("renders fractional vol as a percentage", () => {
    expect(volPercent(0.165)).toBe("16.5%");
  });
});

describe("statusLabel", () => {
  it.each([
    ["paper_accepted", "Paper accepted"],
    ["filled", "Filled"],
    ["pass", "Pass"],
  ])("labels %s as %s", (value, expected) => {
    expect(statusLabel(value)).toBe(expected);
  });
});

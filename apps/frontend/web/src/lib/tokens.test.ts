import { afterEach, describe, expect, test } from "vitest";

import { palette, PALETTE_FALLBACK, radius, token } from "./tokens";

afterEach(() => {
  document.documentElement.removeAttribute("style");
});

describe("token", () => {
  test("reads the live :root CSS variable when one is set", () => {
    document.documentElement.style.setProperty("--positive", "#00ff00");
    expect(token("positive")).toBe("#00ff00");
  });

  test("falls back to the literal palette when no CSS variable is present", () => {
    expect(token("negative")).toBe(PALETTE_FALLBACK.negative);
    expect(token("negative")).toBe("#f08a7e");
  });

  test("a different live value overrides the fallback", () => {
    document.documentElement.style.setProperty("--amber", "#123456");
    expect(token("amber")).toBe("#123456");
    expect(token("amber")).not.toBe(PALETTE_FALLBACK.amber);
  });
});

describe("radius", () => {
  test("falls back to 8px", () => {
    expect(radius()).toBe("8px");
  });

  test("reads the live --radius", () => {
    document.documentElement.style.setProperty("--radius", "12px");
    expect(radius()).toBe("12px");
  });
});

describe("palette", () => {
  test("returns one value per declared token name", () => {
    const p = palette();
    expect(Object.keys(p).sort()).toEqual(Object.keys(PALETTE_FALLBACK).sort());
  });

  test("mixes live and fallback values", () => {
    document.documentElement.style.setProperty("--blue", "#0000ff");
    const p = palette();
    expect(p.blue).toBe("#0000ff");
    expect(p.text).toBe(PALETTE_FALLBACK.text);
  });
});

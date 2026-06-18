import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";

import { useMarketTicker } from "./useMarketTicker";

test("lands on the index as the active ticker", () => {
  const { result } = renderHook(() => useMarketTicker("SX5E"));
  expect(result.current.ticker).toBe("SX5E");
  expect(result.current.kind).toBe("index");
});

test("selecting a constituent makes it the active ticker", () => {
  const { result } = renderHook(() => useMarketTicker("SX5E"));
  act(() => result.current.selectConstituent("ASML"));
  expect(result.current.ticker).toBe("ASML");
  expect(result.current.kind).toBe("constituent");
});

test("selecting the index symbol as a constituent clears back to the index read", () => {
  const { result } = renderHook(() => useMarketTicker("SX5E"));
  act(() => result.current.selectConstituent("ASML"));
  // Picking the index symbol (the ETF chip) returns to the index read, never a phantom member.
  act(() => result.current.selectConstituent("SX5E"));
  expect(result.current.ticker).toBe("SX5E");
  expect(result.current.kind).toBe("index");
});

test("selectIndex returns the active ticker to the index", () => {
  const { result } = renderHook(() => useMarketTicker("SX5E"));
  act(() => result.current.selectConstituent("SIE"));
  act(() => result.current.selectIndex());
  expect(result.current.ticker).toBe("SX5E");
  expect(result.current.kind).toBe("index");
});

test("changing the index re-lands the ticker on the new index, dropping a stale member", () => {
  const { result, rerender } = renderHook(({ index }) => useMarketTicker(index), {
    initialProps: { index: "SX5E" },
  });
  act(() => result.current.selectConstituent("ASML"));
  expect(result.current.ticker).toBe("ASML");

  rerender({ index: "SPX" });
  // The member belonged to SX5E; under SPX the ticker re-lands on the index, never a foreign member.
  expect(result.current.ticker).toBe("SPX");
  expect(result.current.kind).toBe("index");
});

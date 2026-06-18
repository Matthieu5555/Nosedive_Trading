import { useCallback, useMemo, useRef, useState } from "react";

export type MarketTickerKind = "index" | "constituent";

export interface MarketTicker {
  // The active underlying that drives every analytics panel (surface, smile, Greeks, price, signals).
  // It is either the index/ETF itself or one of its constituents.
  ticker: string;
  // Whether the active ticker is the index/ETF or one of its members. Lets a panel caption read
  // "ASML, a EURO STOXX 50 member" without re-deriving membership.
  kind: MarketTickerKind;
  // Set the active ticker to the index/ETF (the page's landing read for a given index).
  selectIndex: () => void;
  // Set the active ticker to a constituent symbol. Selecting the index symbol clears back to the
  // index read.
  selectConstituent: (symbol: string) => void;
}

// The Market page's selected-ticker, page-scoped on purpose. The universe is the index/ETF plus its
// constituents; selecting any of them makes it the active `ticker` every panel below keys off. The
// index argument is the chosen ETF (it defines the universe + the capture runs). When the index
// changes the selection re-lands on the index, so a constituent picked for one index can never
// outlive the index it belonged to (the page used to reset the selected member by hand on every index
// change; that reset now lives here, structurally). Structured so it could later be promoted to an
// app-wide context, but it holds no global state today.
export function useMarketTicker(index: string): MarketTicker {
  const [constituent, setConstituent] = useState<string | null>(null);
  // Re-land on the index the moment the index changes, derived during render (no effect, no extra
  // paint): a stale member is dropped before it can drive a panel for the wrong index.
  const lastIndex = useRef(index);
  if (lastIndex.current !== index) {
    lastIndex.current = index;
    if (constituent !== null) setConstituent(null);
  }

  const activeConstituent = lastIndex.current === index ? constituent : null;
  const ticker = activeConstituent ?? index;
  const kind: MarketTickerKind = ticker === index ? "index" : "constituent";

  const selectIndex = useCallback(() => setConstituent(null), []);
  const selectConstituent = useCallback(
    (symbol: string) => setConstituent(symbol === index ? null : symbol),
    [index],
  );

  return useMemo(
    () => ({ ticker, kind, selectIndex, selectConstituent }),
    [ticker, kind, selectIndex, selectConstituent],
  );
}

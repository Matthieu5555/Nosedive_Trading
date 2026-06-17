import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import type { BasketLegInput } from "../api";

// The shared book the operator carries through the cockpit: the underlying + as-of date + the
// composed legs. It lets the parcours be one pipeline — compose a book on Risk, then send THAT book
// on Orders — instead of three islands that each rebuild state from scratch. Today it is shared
// between Risk and Orders; the Data tab is wired in later (Market.tsx is frozen behind another lane).
export interface BookContextValue {
  underlying: string;
  tradeDate: string;
  legs: BasketLegInput[];
  setUnderlying: (value: string) => void;
  setTradeDate: (value: string) => void;
  setLegs: (update: BasketLegInput[] | ((current: BasketLegInput[]) => BasketLegInput[])) => void;
  addLeg: (leg: BasketLegInput) => void;
  removeLeg: (index: number) => void;
  clearLegs: () => void;
}

// An inert default so a component rendered WITHOUT a provider (e.g. a standalone page-mount test)
// reads an empty book and no-op setters instead of throwing. The real state lives in BookProvider.
const DEFAULT_BOOK: BookContextValue = {
  underlying: "",
  tradeDate: "",
  legs: [],
  setUnderlying: () => {},
  setTradeDate: () => {},
  setLegs: () => {},
  addLeg: () => {},
  removeLeg: () => {},
  clearLegs: () => {},
};

const BookContext = createContext<BookContextValue>(DEFAULT_BOOK);

export function BookProvider({ children }: { children: ReactNode }) {
  const [underlying, setUnderlying] = useState("");
  const [tradeDate, setTradeDate] = useState("");
  const [legs, setLegs] = useState<BasketLegInput[]>([]);

  const value = useMemo<BookContextValue>(
    () => ({
      underlying,
      tradeDate,
      legs,
      setUnderlying,
      setTradeDate,
      setLegs,
      addLeg: (leg) => setLegs((current) => [...current, leg]),
      removeLeg: (index) => setLegs((current) => current.filter((_, i) => i !== index)),
      clearLegs: () => setLegs([]),
    }),
    [underlying, tradeDate, legs],
  );

  return <BookContext.Provider value={value}>{children}</BookContext.Provider>;
}

export function useBook(): BookContextValue {
  return useContext(BookContext);
}

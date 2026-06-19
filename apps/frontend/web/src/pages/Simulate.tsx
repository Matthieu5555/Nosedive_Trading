import { useState } from "react";

import { Stack } from "../components/layout";
import { tourAnchor } from "../lib/tour";
import { BuildBasket } from "./simulate/BuildBasket";
import { PortfolioStress } from "./simulate/PortfolioStress";

// The two what-if questions Simulate answers, sharing one stress engine. "portfolio" stresses the
// real book you hold (the old Risk Scenarios page); "build" stresses a hypothetical basket you
// compose on the spot (the old Basket Builder). The book source is the screen's dominant filter, so
// per the house rule it is a prominent selector, not a buried option.
type BookSource = "portfolio" | "build";

const SOURCES: { value: BookSource; label: string; hint: string }[] = [
  {
    value: "portfolio",
    label: "My book",
    hint: "Stress the portfolio you actually hold.",
  },
  {
    value: "build",
    label: "Build a basket",
    hint: "Compose a hypothetical book on the spot and stress that.",
  },
];

export function SimulatePage() {
  const [source, setSource] = useState<BookSource>("portfolio");

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">
            What if the market moves, against the book I hold or one I invent?
          </p>
          <h1>Simulate</h1>
        </div>
        <div
          className="mode-toggle"
          role="group"
          aria-label="Book source"
          {...tourAnchor(
            "simulate.source",
            "Book source",
            "Choose whether to stress the book you hold or a hypothetical basket you build.",
          )}
        >
          {SOURCES.map((option) => (
            <button
              key={option.value}
              type="button"
              className="mode-toggle__option"
              aria-pressed={source === option.value}
              title={option.hint}
              onClick={() => setSource(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {source === "portfolio" ? <PortfolioStress /> : <BuildBasket />}
    </Stack>
  );
}

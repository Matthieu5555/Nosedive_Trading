import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BarChart } from "./BarChart";
import { LineChart } from "./LineChart";

describe("LineChart", () => {
  it("renders one polyline per series with true data extremes as axis labels", () => {
    // y spans -200..100: the axis labels must show the data min/max,
    // not the padded drawing range.
    render(
      <LineChart
        ariaLabel="Portfolio PnL by spot shock"
        series={[
          {
            id: "pnl",
            points: [
              { x: -10, y: -200 },
              { x: 0, y: 0 },
              { x: 10, y: 100 },
            ],
          },
          {
            id: "delta",
            points: [
              { x: -10, y: -1 },
              { x: 10, y: 1 },
            ],
          },
        ]}
        formatX={(value) => `${value}%`}
      />,
    );

    const chart = screen.getByRole("img", { name: "Portfolio PnL by spot shock" });
    expect(chart.querySelectorAll("polyline.series-line")).toHaveLength(2);
    expect(screen.getByText("-200")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("-10%")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
  });

  it("marks the requested shock when it lies inside the plotted range", () => {
    const points = [
      { x: -10, y: -1 },
      { x: 10, y: 1 },
    ];
    const { rerender } = render(
      <LineChart ariaLabel="ladder" series={[{ id: "pnl", points }]} markerX={-5} />,
    );

    expect(screen.getByRole("img", { name: "ladder" }).querySelector(".chart-marker")).not.toBeNull();

    // A shock outside the ladder range (request allows up to +-50%) draws no marker.
    rerender(<LineChart ariaLabel="ladder" series={[{ id: "pnl", points }]} markerX={25} />);
    expect(screen.getByRole("img", { name: "ladder" }).querySelector(".chart-marker")).toBeNull();
  });
});

describe("BarChart", () => {
  it("renders one bar per bucket with sign-aware classes and visible values", () => {
    render(
      <BarChart
        ariaLabel="Theta by expiry"
        bars={[
          { label: "2026-06-19", value: -12.4 },
          { label: "2026-07-17", value: 3.5 },
        ]}
      />,
    );

    const chart = screen.getByRole("img", { name: "Theta by expiry" });
    expect(chart.querySelectorAll("rect.bar")).toHaveLength(2);
    expect(chart.querySelectorAll("rect.negative-bar")).toHaveLength(1);
    expect(chart.querySelectorAll("rect.positive-bar")).toHaveLength(1);
    expect(screen.getByText("-12.4")).toBeInTheDocument();
    expect(screen.getByText("2026-07-17")).toBeInTheDocument();
  });
});

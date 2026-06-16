import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

import {
  ANALYTICS_AAA_DENSE,
  ANALYTICS_AAA_MONEYNESS_FALLBACK,
  RECORDED_EMPTY,
} from "../test/fixtures";
import { jsonGet, notMocked, server } from "../test/server";
import { MarketPage } from "./Market";

test("leads with the context selector strip (entity / side / maturity) and an as-of dropdown", async () => {
  render(<MarketPage />);

  expect(await screen.findByLabelText("Entity")).toBeInTheDocument();
  const side = screen.getByRole("radiogroup", { name: /option side/i });
  expect(within(side).getByRole("radio", { name: "Puts" })).toBeInTheDocument();
  expect(within(side).getByRole("radio", { name: "Calls" })).toBeInTheDocument();
  expect(screen.getByLabelText("Maturity")).toBeInTheDocument();
  expect(screen.getByLabelText("As-of fetch")).toBeInTheDocument();
});

test("the entity selector lists the index itself and each member", async () => {
  render(<MarketPage />);
  const entity = await screen.findByLabelText("Entity");
  expect(within(entity).getByRole("option", { name: /SPX \(index\)/ })).toBeInTheDocument();
  expect(await within(entity).findByRole("option", { name: "AAA" })).toBeInTheDocument();
  expect(within(entity).getByRole("option", { name: "BBB" })).toBeInTheDocument();
});

test("defaults to the index and shows its price history, surface, and smile", async () => {
  render(<MarketPage />);

  expect(await screen.findByLabelText(/SPX daily history/i)).toBeInTheDocument();
  expect(await screen.findByLabelText(/Implied-volatility surface/i)).toBeInTheDocument();
  // The maturity selector defaults to "all maturities", so the smile overlays every tenor.
  expect(await screen.findByLabelText(/Smile — all maturities/i)).toBeInTheDocument();
});

test("the index view leads with the dispersion gap (index vol vs member vol)", async () => {
  render(<MarketPage />);
  expect(
    await screen.findByLabelText(/Dispersion: index vol vs average member vol/i),
  ).toBeInTheDocument();
});

test("selecting a member repoints the analytics at it and drops the dispersion gap", async () => {
  const user = userEvent.setup();
  render(<MarketPage />);

  await screen.findByLabelText(/Dispersion: index vol/i);
  await user.selectOptions(screen.getByLabelText("Entity"), "AAA");

  expect(await screen.findByLabelText(/AAA daily history/i)).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.queryByLabelText(/Dispersion: index vol/i)).not.toBeInTheDocument(),
  );
});

test("renders the dollar-Greeks term structure and the by-band table (puts by default)", async () => {
  render(<MarketPage />);

  const deltaPanel = await screen.findByLabelText(/Delta \$ term structure/i);
  expect(within(deltaPanel).getByTestId("line-series")).toHaveTextContent("30dp");
  expect(within(deltaPanel).getByTestId("line-unit")).toHaveTextContent("$ per $1 of underlying");

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("30dp")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per 1% move")).toBeInTheDocument();
});

test("the put/call switch filters the Greeks to the chosen wing", async () => {
  const user = userEvent.setup();
  render(<MarketPage />);

  expect((await screen.findAllByText("30dp")).length).toBeGreaterThan(0);

  // The only captured band in the fixture is a put; switching to calls empties the wing.
  await user.click(screen.getByRole("radio", { name: "Calls" }));
  await waitFor(() => expect(screen.queryByText("30dp")).not.toBeInTheDocument());
});

test("renders the dense reconstructed surface, sliced to the put wing", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DENSE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  // Default side is puts → only the log-moneyness ≤ 0 columns survive (k = −0.1, 0.0).
  expect(within(surface).getByTestId("plot-z")).toHaveTextContent(
    JSON.stringify([
      [0.27, 0.24],
      [0.23, 0.21],
    ]),
  );
});

test("the grid-fallback smile is labeled as moneyness and flags a degenerate fit", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_MONEYNESS_FALLBACK));
  render(<MarketPage />);

  // Default "all maturities" overlay still names the log-moneyness axis and flags degenerate tenors.
  const smile = await screen.findByLabelText(/Smile — all maturities/i);
  expect(smile.getAttribute("aria-label")).toMatch(/implied vol vs log-moneyness/i);
  expect(smile.getAttribute("aria-label")).toMatch(/degenerate fit/i);
});

test("the Data quality tab carries the constituents and the coverage table", async () => {
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("tab", { name: "Data quality" }));

  const constituents = await screen.findByRole("region", { name: /constituents/i });
  expect(within(constituents).getByText("AAA")).toBeInTheDocument();
  expect(within(constituents).getByText("BBB")).toBeInTheDocument();
});

test("renders a labeled empty state when no dates are recorded", async () => {
  server.use(jsonGet("/api/recorded-dates", RECORDED_EMPTY));
  render(<MarketPage />);
  expect(await screen.findByText(/No capture runs to show/i)).toBeInTheDocument();
});

test("shows a qc-failing day with a QC fail badge instead of hiding it", async () => {
  server.use(
    jsonGet("/api/recorded-dates", {
      index: "SPX",
      count: 0,
      dates: [],
      available: [
        { date: "2026-06-10", run_id: "run-0610", recorded_ts: "2026-06-10T17:30:00", qc: "fail" },
      ],
    }),
  );
  render(<MarketPage />);
  expect(await screen.findByText("QC fail")).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  server.use(http.get("/api/recorded-dates", notMocked));
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});

test("monetized Greeks render in the index's quote currency (€ for SX5E)", async () => {
  server.use(
    jsonGet("/api/indices", {
      indices: [{ symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" }],
    }),
    jsonGet("/api/recorded-dates", {
      index: "SX5E",
      count: 1,
      dates: ["2026-05-29"],
      available: [
        { date: "2026-05-29", run_id: "run-0529", recorded_ts: "2026-05-29T17:30:00", qc: "pass" },
      ],
    }),
  );
  render(<MarketPage />);

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("€ per €1 of underlying")).toBeInTheDocument();
});

test("the index selector is driven by /api/indices — a parked index is not offered", async () => {
  server.use(
    jsonGet("/api/indices", { indices: [{ symbol: "SX5E", name: "EURO STOXX 50" }] }),
    jsonGet("/api/recorded-dates", { index: "SX5E", count: 0, dates: [], available: [] }),
  );
  render(<MarketPage />);
  const select = await screen.findByLabelText("Index");
  expect(
    within(select).getByRole("option", { name: /EURO STOXX 50 \(SX5E\)/ }),
  ).toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: /SPX/ })).not.toBeInTheDocument();
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { SignalsResponse } from "../api";
import { jsonGet, notMocked, server } from "../test/server";
import { SignalsPage } from "./Signals";

const UNDERLYINGS = { underlyings: ["SX5E"] };

function envelope(over: Partial<SignalsResponse> = {}): SignalsResponse {
  return {
    underlying: "SX5E",
    trade_date: "2026-06-15",
    snapshot_ts: "2026-06-15T17:30:00+00:00",
    n_signals: 2,
    kinds: ["iv_rank", "implied_correlation"],
    by_kind: {
      iv_rank: [
        {
          signal_kind: "iv_rank",
          label: "IV rank",
          subject: "SX5E",
          tenor_label: "1m",
          value: 0.62,
          unit: "fraction [0,1]",
          snapshot_ts: "2026-06-15T17:30:00+00:00",
          source_snapshot_ts: "2026-06-15T17:30:00+00:00",
          provenance: {
            calc_ts: "2026-06-15T17:30:00+00:00",
            code_version: "t",
            config_hashes: { pricing: "c" },
            stamp_hash: "s",
            n_sources: 1,
          },
        },
      ],
      implied_correlation: [
        {
          signal_kind: "implied_correlation",
          label: "Implied correlation ρ̄",
          subject: "SX5E",
          tenor_label: "3m",
          value: 0.5,
          unit: "correlation [-1,1]",
          snapshot_ts: "2026-06-15T17:30:00+00:00",
          source_snapshot_ts: "2026-06-15T17:30:00+00:00",
          provenance: {
            calc_ts: "2026-06-15T17:30:00+00:00",
            code_version: "t",
            config_hashes: { pricing: "c" },
            stamp_hash: "s",
            n_sources: 1,
          },
        },
      ],
    },
    signals: [],
    ...over,
  };
}

test("loads underlyings, default-selects one, and renders the per-kind panels", async () => {
  server.use(jsonGet("/api/signals/underlyings", UNDERLYINGS), jsonGet("/api/signals", envelope()));

  render(<SignalsPage />);

  const select = await screen.findByLabelText("Underlying");
  await waitFor(() => expect((select as HTMLSelectElement).value).toBe("SX5E"));

  expect(await screen.findByRole("heading", { name: "IV rank" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Implied correlation ρ̄" })).toBeInTheDocument();
  expect(screen.getByText("62.0%")).toBeInTheDocument();
});

test("a chosen trade date is sent on the signals query", async () => {
  let lastUrl = "";
  server.use(
    jsonGet("/api/signals/underlyings", UNDERLYINGS),
    http.get("/api/signals", ({ request }) => {
      lastUrl = request.url;
      return HttpResponse.json(envelope());
    }),
  );

  render(<SignalsPage />);
  await screen.findByRole("heading", { name: "IV rank" });

  const date = screen.getByLabelText("Trade date");
  await userEvent.type(date, "2026-06-10");

  await waitFor(() => expect(lastUrl).toContain("trade_date=2026-06-10"));
  expect(lastUrl).toContain("underlying=SX5E");
});

test("a labelled-empty partition shows the no-signals state, not an error", async () => {
  server.use(
    jsonGet("/api/signals/underlyings", UNDERLYINGS),
    jsonGet(
      "/api/signals",
      envelope({ n_signals: 0, kinds: [], by_kind: {}, snapshot_ts: null, signals: [] }),
    ),
  );

  render(<SignalsPage />);
  expect(await screen.findByText(/No signals recorded for SX5E/i)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a failing underlyings list surfaces an error and offers no dead selector data", async () => {
  server.use(http.get("/api/signals/underlyings", () => notMocked()));

  render(<SignalsPage />);

  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText(/not mocked|500/i)).toBeInTheDocument();
});

test("a failing signals fetch surfaces its error inline", async () => {
  server.use(
    jsonGet("/api/signals/underlyings", UNDERLYINGS),
    http.get("/api/signals", () => notMocked()),
  );

  render(<SignalsPage />);
  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText(/not mocked|500/i)).toBeInTheDocument();
});

test("no underlyings yields a plain No data yet state", async () => {
  server.use(jsonGet("/api/signals/underlyings", { underlyings: [] }));

  render(<SignalsPage />);
  expect(await screen.findByText("No data yet")).toBeInTheDocument();
});

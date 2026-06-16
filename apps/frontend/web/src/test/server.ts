import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";

import {
  ANALYTICS_AAA,
  CONSTITUENTS_TWO,
  DELTA_BANDS_32,
  INDICES_SPX_SX5E,
  PORTFOLIOS_ONE,
  PRICE_HISTORY_AAA,
  PRICE_HISTORY_BATCH_TWO,
  RECORDED_TWO_DATES,
  SCENARIOS_EMPTY,
  SIGNAL_UNDERLYINGS,
  SIGNALS_SX5E,
} from "./fixtures";

export function notMocked() {
  return HttpResponse.json({ error: "not mocked" }, { status: 500 });
}

export function jsonGet(path: string, body: JsonBodyType) {
  return http.get(path, () => HttpResponse.json(body));
}

export function jsonPost(path: string, body: JsonBodyType) {
  return http.post(path, () => HttpResponse.json(body));
}

export const server = setupServer(
  jsonGet("/api/indices", INDICES_SPX_SX5E),
  jsonGet("/api/recorded-dates", RECORDED_TWO_DATES),
  jsonGet("/api/constituents", CONSTITUENTS_TWO),
  jsonGet("/api/price-history", PRICE_HISTORY_AAA),
  jsonGet("/api/analytics", ANALYTICS_AAA),
  jsonPost("/api/price-history/batch", PRICE_HISTORY_BATCH_TWO),
  jsonGet("/api/risk/portfolios", PORTFOLIOS_ONE),
  jsonGet("/api/risk/scenarios", SCENARIOS_EMPTY),
  jsonGet("/api/config/delta-bands", { delta_bands: DELTA_BANDS_32 }),
  jsonGet("/api/ticket/options", { brokers: ["ibkr"], time_in_force: ["day", "gtc"] }),
  jsonGet("/api/signals/underlyings", SIGNAL_UNDERLYINGS),
  jsonGet("/api/signals", SIGNALS_SX5E),

  http.all("/api/*", notMocked),
);

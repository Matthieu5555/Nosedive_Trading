import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { BasketRequest } from "./api";
import { ApiError, getJson, postJson, priceBasket } from "./api";
import { server } from "./test/server";

const A_BASKET: BasketRequest = {
  basket_id: "basket-T-latest",
  trade_date: "",
  underlying: "T",
  legs: [
    {
      instrument_kind: "option",
      side: "long",
      quantity: 1,
      underlying: "T",
      tenor_label: "1m",
      delta_band: "atm",
    },
  ],
};

test("getJson returns the parsed payload on 200", async () => {
  server.use(http.get("/api/echo", () => HttpResponse.json({ value: 7 })));
  await expect(getJson<{ value: number }>("/api/echo")).resolves.toEqual({ value: 7 });
});

test("postJson sends the JSON body and returns the parsed payload", async () => {
  let received: unknown = null;
  server.use(
    http.post("/api/echo", async ({ request }) => {
      received = await request.json();
      return HttpResponse.json({ ok: true });
    }),
  );
  await expect(postJson<{ ok: boolean }>("/api/echo", { a: 1 })).resolves.toEqual({ ok: true });
  expect(received).toEqual({ a: 1 });
});

test("postJson surfaces the BFF's typed 400 detail, not a bare status line", async () => {
  server.use(
    http.post("/api/echo", () =>
      HttpResponse.json({ error: "bad_basket", detail: "leg 0: missing side" }, { status: 400 }),
    ),
  );
  const failure = await postJson("/api/echo", {}).catch((err: unknown) => err);
  expect(failure).toBeInstanceOf(ApiError);
  const apiError = failure as ApiError;
  expect(apiError.status).toBe(400);
  expect(apiError.detail).toBe("leg 0: missing side");
  expect(apiError.message).toBe("400 leg 0: missing side");
});

test("an error body without a detail field is surfaced whole as JSON", async () => {
  server.use(
    http.get("/api/echo", () => HttpResponse.json({ error: "store_down" }, { status: 500 })),
  );
  const failure = await getJson("/api/echo").catch((err: unknown) => err);
  expect(failure).toBeInstanceOf(ApiError);
  expect((failure as ApiError).message).toBe('500 {"error":"store_down"}');
});

test("a non-JSON error body falls back to the status text", async () => {
  server.use(
    http.get(
      "/api/echo",
      () => new HttpResponse("<html>gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    ),
  );
  const failure = await getJson("/api/echo").catch((err: unknown) => err);
  expect(failure).toBeInstanceOf(ApiError);
  expect((failure as ApiError).message).toBe("502 Bad Gateway");
});

test("priceBasket rides the same path: a malformed basket's 400 detail reaches the caller", async () => {
  server.use(
    http.post("/api/basket/risk", () =>
      HttpResponse.json({ error: "bad_basket", detail: "unknown tenor 9z" }, { status: 400 }),
    ),
  );
  const failure = await priceBasket(A_BASKET).catch((err: unknown) => err);
  expect(failure).toBeInstanceOf(ApiError);
  expect((failure as ApiError).detail).toBe("unknown tenor 9z");
});

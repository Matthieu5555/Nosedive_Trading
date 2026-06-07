# REP5 — pydantic response models for the BFF API contract

> **READY — coordinate with web fixtures.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> The BFF hand-builds every response dict (`serializers.py`, 267 LOC) — re-implementing
> FastAPI `response_model`. Highest-value *future* play: do it before Phase 2 multiplies endpoints.

- **Owns:** `apps/frontend/src/algotrading/frontend/` — `serializers.py`, the routers
  (`surfaces.py`, `risk.py`, `analytics.py`, `run.py`, `constituents.py`, `price_history.py`,
  `recorded_dates.py`, …), `app.py`. The wire contract mirrored by `web/src/api.ts` and
  pinned by `apps/frontend/web/src/test/fixtures.ts`.
- **Depends on:** nothing hard. Conforms to [ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)
  (typed-config spirit) and the P0.2/OQ-1 unit-carrying mandate.
- **Blocks:** nothing, but **every new Phase 2 endpoint** (1I, 2A–2D) should be built on the
  typed models, so land this first.
- **State going in:** exactly one pydantic model in the whole BFF (`run.py` `RunRequest`).
  No `response_model`, no `Depends`, errors hand-returned as `JSONResponse(..., status_code=4xx)`.

## Objective

Generate the API JSON from typed pydantic response models instead of hand dicts — keeping the
deliberate wire shape, gaining validation + OpenAPI + serialization for free.

## What to do (ordered)

1. **Define pydantic response models** for the BFF contract. **Keep the existing wire shape
   exactly** — the unit-carrying `{raw, dollar, unit}` metric objects, the compact provenance,
   the grouped smile/surface/Greek views. This shape is a deliberate contract decoupled from
   the storage contract; do not flatten it to the storage dataclasses.
2. **Adopt `response_model` + `model_dump(mode="json")`** per route, retiring the `*_to_dict`
   functions in `serializers.py`. Native pydantic ISO datetime encoding replaces `_iso()`.
   Keep genuine view-shaping (e.g. `analytics.py:_group_by_maturity`) — that's BFF logic, not
   serialization, and stays.
3. **`Depends(get_context)`** for the repeated `_context(request)` / `request.app.state.ctx`.
4. **`HTTPException` + one exception handler + typed `date` path params** for the hand-returned
   error payloads (`surfaces.py:48-53`, `analytics.py:105-110`, `run.py:58-70`) and the
   `date.fromisoformat` try/except. **Preserve the typed error bodies** the front consumes
   (`{"error": ..., "trade_date": ...}`) via a custom handler — not the default 422.
5. **Coordinate the shape with `web/src/test/fixtures.ts`** — run `npm test` against the new
   responses; the wire bytes the front expects must not change.

## Done when

Root gate green; `npm test` green against the new responses; `serializers.py` reduced to view
shaping only; every route declares a `response_model`; OpenAPI docs render; the unit-carrying
contract is enforced by types, not hand dicts.

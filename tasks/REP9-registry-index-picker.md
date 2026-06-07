# REP9 — Registry-driven index picker (BFF `/api/indices` over the 1J registry)

> **READY — no blocker.** Dashboard-review follow-up (course transcript + front review;
> [AUDIT-tasks-coherence-2026-06-07.md](AUDIT-tasks-coherence-2026-06-07.md)).
> The front page's index selector is a **free-text `<input>`** the operator must type exactly
> (`pages/Home.tsx`), while the **1J index registry already knows** which indices are enabled,
> their display names, and their calendars. The picker should read that registry, not a text box.

- **Owns:** BFF `apps/frontend/src/algotrading/frontend/` — a new `routers/indices.py`
  (`GET /api/indices`), its serializer, and registration in `app.py`; web
  `apps/frontend/web/src/` — the index control in `pages/Home.tsx` (free-text `<input>` →
  `<select>`) and the `IndicesResponse`/`IndexOption` types in `api.ts`.
- **Depends on:** the **1J index registry** — `enabled_indices()` /
  `index_registry_from_config` (`packages/infra/.../universe/registry_loader.py`,
  `index_registry.py`), which already exist and are loaded from `configs/universe.yaml`.
  Conforms to [ADR 0035](../.agent/decisions/0035-index-registry-and-per-index-capture-schedule.md)
  (the registry is the single source of which indices exist) and
  [ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md).
- **Blocks:** nothing, but should land **before** Phase 2 — Tab-2 pages (2A–2D) inherit the
  same index/underlying selection and should not each hand-roll a free-text box.
- **State going in:** `pages/Home.tsx` holds `useState("SPX")` behind an
  `<input aria-label="index">` that uppercases whatever is typed. Mistype `SX5E` and the
  cascade silently resolves nothing (no partition for the wrong key). The registry seam that
  would feed a dropdown is built (1J) but **not exposed over the BFF** — there is no
  `/api/indices` router today.

## Objective

The operator picks the index from a **registry-driven dropdown** (only enabled indices, shown
by display name, valued by the canonical symbol the partitions use) — never a free-text box
that must match the storage key by hand. One BFF endpoint, one front control.

## What to do (ordered)

1. **`GET /api/indices` (BFF).** Add `routers/indices.py` that reads the read-only
   `PlatformConfig` already on `app.state` (the same context the other routers use), resolves
   the registry via `index_registry_from_config(...)`, and returns the **enabled** entries —
   `symbol` (the canonical key consumers partition by), `name` (display), `calendar`,
   `currency`. Disabled entries (unverified conid, etc.) are excluded. Add an `index_to_dict`
   serializer beside the others; no business logic in the router. Empty registry → an empty
   `indices` list with the labels, never a 500.
2. **Register the router** in `app.py` alongside the existing set (CORS already covers GET).
3. **Typed client (web).** Add `IndicesResponse` / `IndexOption` to `api.ts` mirroring the
   serializer (the `api.ts` header comment makes the HTTP shape the seam — keep them in sync).
4. **`pages/Home.tsx`: free-text `<input>` → `<select>`.** Fetch `/api/indices`, render a
   `<select aria-label="index">` of the enabled options (label = name, value = symbol),
   defaulting to the first enabled index. On change, reset the as-of/selected cascade exactly
   as today. Keep the **value = canonical symbol** so the downstream `recorded-dates` /
   `constituents` / `analytics` queries use the partition key verbatim (no client-side
   normalization). Coordinate with REP3 if it lands first (use `useQuery` for the fetch).
5. **The symbol is the registry's, not the front's.** Do not re-spell or alias the symbol in
   the UI (this is the SP500-vs-SPX class of bug — the registry seeds `SPX`; the picker shows
   "S&P 500" and submits `SPX`).

## Test surface

- **BFF↔infra seam (extend `apps/frontend/tests/test_readback_api.py`):** seed a registry with
  two enabled + one disabled index through config; `GET /api/indices` returns exactly the two
  enabled, by canonical symbol, with display names — `test_indices_lists_enabled_only`. A
  disabled entry is absent — `test_indices_excludes_disabled`.
- **Web component test (`Home.test.tsx`):** the index control renders as a select of the
  registry options (mock `/api/indices`), defaults to the first, and selecting one drives the
  recorded-dates/constituents cascade; no free-text index input remains.

## Done when

`GET /api/indices` is registered and reads the 1J registry read-only (enabled-only, canonical
symbol + display name); `pages/Home.tsx` selects the index from that dropdown with no
free-text box; the submitted value is the registry's canonical symbol; both gates green
(`npm run lint && npm test`; root Python gate).

## Gotchas

- **Read-only serving** — the BFF resolves the registry from the loaded config; it does not
  write `universe.yaml`. Adding/enabling an index is a config edit (1J), not a BFF action.
- **Canonical symbol discipline** — the dropdown `value` is the registry symbol used by every
  downstream partition/resolver; display-name aliasing stays in the `label` only.

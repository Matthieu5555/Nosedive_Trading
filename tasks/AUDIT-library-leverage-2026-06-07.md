# Audit — library leverage & code-reduction (2026-06-07)

**Question asked:** are the proven libraries we depend on (Nautilus, QuantLib,
py-vollib, scipy, duckdb/polars/pyarrow, exchange-calendars, pycryptodome, plotly,
pydantic/fastapi/numpy/pandas) used to their full potential, and can we delete
hand-written code by leaning on them harder? Per the "lean on proven libraries"
mandate (ADR 0023, 0030–0033).

**Method:** nine parallel research passes, one per library/cluster, each grounded in
the actual `pyproject.toml` declarations, `uv.lock` resolutions, and source call
sites (no claim un-cited). The *now* actions are the **REP** backlog at the bottom
(REP0…REP8). The *future* library adoptions live in
[`BIG_PICTURE.md`](../BIG_PICTURE.md) under "Library leverage — forward view".

---

## Headline finding — read this before "simplifying" anything

**The analytics core is hand-rolled on purpose. Leave it alone.** The largest body
of "could a library do this?" code — Black-76 price + Greeks (`pricing/black76.py`),
the implied-vol solver (`iv/solver.py`), raw-SVI fit + closed-form derivatives
(`surfaces/svi.py`), the no-arb checks (`surfaces/arbitrage.py`), the parity
forward/DF fit (`forwards/parity.py`) — is deliberately bespoke and **must stay** for
four reasons that recur across every sub-audit:

1. **Frozen contract & provenance.** `black76.py` is the pinned pricing keystone
   ("nothing else turns a state vector into a price"); `SurfaceParameters` persists
   the raw-SVI `(a,b,ρ,m,σ)` tuple; `PRICER_VERSION`/`canonical_json`→SHA-256
   reproducibility depends on these byte-stable. A library swap changes the numbers.
2. **Convention-pinned Greeks.** The repo's Greeks are forward+carry consistent
   (spot-delta, vega-per-1.00, per-year theta, forward-fixed rho). QuantLib's
   `BlackCalculator` and py-vollib use different conventions — silent drift in the
   most-tested function.
3. **Independent test oracles.** QuantLib and py-vollib are wired in **as cross-check
   oracles, not production code** (QuantLib: 1 real import, the American lattice;
   py-vollib: 1 import, in `test_iv.py` only). Swapping the engine *to* the oracle
   makes the cross-check a tautology and destroys the test's value.
4. **Deliberate diagnostics.** `iv/solver.py` is 329 LOC mostly because of its
   labeled-failure taxonomy (`below_intrinsic`/`above_max`/`non_convergence`) and
   because the same primitive inverts the **American** lattice — which no European-only
   library (vollib, `blackFormulaImpliedStdDev`) can do.

So "1 import of QuantLib" is **not** under-leverage. It reflects correct judgement:
QuantLib owns exactly the early-exercise lattice you should never hand-roll, and
nothing else. Same for py-vollib and scipy.stats (kept as independent oracles).

The genuine leverage gaps are **everywhere else**: config validation, the BFF wire
layer, connectivity, the storage as-of seam, the web shell, and dependency hygiene.

---

## Scorecard — declared vs actually leveraged

| Library (declared) | Real source imports | Verdict |
|---|---|---|
| nautilus-trader ≥1.227 | 3 sites (backtest host, tick model, IBKR config) | **Under-leveraged**, but mostly blocked on live `TradingNode` (1C). ~430–510 LOC of duplicated connectivity removable then. Execution engine unused — correct, not built yet. |
| QuantLib ≥1.42.1 | 1 (`pricing/american.py`) | **Correctly leveraged.** Used exactly where it should be (American lattice). Further swaps are overkill or harmful. |
| py-vollib ≥1.0.12 | 1 (`test_iv.py`, oracle only) | **Correct as oracle.** Not on any production path. Candidate `py_vollib_vectorized` only if full-chain throughput becomes a bottleneck (future). |
| scipy ≥1.17.1 | 3 (brentq, least_squares, stats oracle) | **Well-leveraged.** The two heavy jobs (root-find, NLSQ) already on scipy. ~10–22 LOC of micro-swaps only. |
| duckdb ≥1.5.3 | yes (read engine, ASOF JOIN membership) | **Well-leveraged** where used; the membership ASOF JOIN is the model to copy. |
| **polars ≥1.41.2** | **0** | **Phantom dependency.** Mandated by ADR 0033, comment claims "the core is polars", but no module imports it. Adopt (REP2) or drop (REP0). |
| pyarrow ≥24 | yes (schema + Parquet IO) | Correct as substrate. `pyarrow.dataset` is the one expansion (deferred). |
| exchange-calendars ≥4.13 | 2 (resolver, registry validation) | **Correctly & minimally leveraged.** Zero hand-rolled holiday/session/close math. Nothing to remove. |
| **pycryptodome ≥3.20** (lock 3.23.0) | **0** | **Declared, installed, never imported.** ADR's "built on pycryptodome" is currently false. The OAuth signer is stdlib-only. Decide: remove or write the RSA/DH LST exchange (REP0/REP8). |
| plotly ≥6.8 (web: plotly.js ^3.6) | 2 Python tools + web charts | **Right single-charting call.** One real bug: web 3D uses `mesh3d`-of-cloud, should be `surface` over the gridded data the BFF already returns. |
| **pydantic** | 1 model (`RunRequest`) | **Largest leverage gap.** `reflective.py` reinvents pydantic v2 coercion; `__post_init__`×9 reinvents `Field` constraints; `serializers.py` (267 LOC) reinvents `response_model`. |
| fastapi | BFF spine, shallow | No `response_model`, no `Depends`, hand-returned errors. Leverage gap tied to pydantic. |
| numpy | narrow, correct | **Well-leveraged.** No loop-where-vectorize anti-patterns found. |
| **pandas ≥2.3** | **0** | **Phantom dependency.** Declared, zero source imports (stays transitively via nautilus). Drop as a direct dep (REP0). |

Three declared libraries — **polars, pycryptodome, pandas** — have **zero source
imports**. That is the single most concrete, lowest-risk cleanup surface.

---

## What can actually be deleted/swapped (grouped)

### Big swaps (real LOC, real risk)
- **pydantic for config** — `reflective.py` (116) + `__post_init__` across 9 classes
  (~180) + nested `loader.py` builders (~90) ≈ **~330 LOC** replaceable by pydantic v2
  frozen+strict models. Hard constraint: byte-stable values feeding SHA-256 hashes;
  route `ValidationError`→`ConfigFieldError`; preserve the `indices:` canonicalization. → **REP6**
- **pydantic + FastAPI for the BFF** — `serializers.py` (267) + per-router `JSONResponse`
  shaping + manual `app.state.ctx` wiring + hand-returned errors ≈ **~235 LOC** replaceable
  by `response_model` + `Depends` + `HTTPException`. The wire *shape* is a deliberate
  contract (unit-carrying `{raw,dollar,unit}`) — keep the shape, generate it from typed
  models. Coordinate with `web/src/test/fixtures.ts`. → **REP5**
- **Nautilus connectivity** — `session.py` (288) + `supervisor.py` (240) + `clock.py`
  (72) ≈ **~430–510 LOC** of duplicated reconnect/heartbeat/clock the IBKR adapter +
  `LiveClock` own. Blocked on the live `TradingNode` (1C); keep client-id bands + gap
  seam for Saxo/Deribit. ADR 0023 §3 already marks the `ib_async`-era session for
  retirement. → **REP7**

### Medium swaps
- **Storage as-of consistency** — `snapshots/as_of.py` hand-rolls a per-field as-of
  (Python loop, ~28 LOC) while `membership.py` does the *same shape* as a native
  DuckDB `QUALIFY row_number()`. The two as-of mechanisms are inconsistent and this is
  the single most look-ahead-sensitive function. Unify on duckdb/polars; gives polars
  its first real home. Plus lineage predicate-pushdown (A2) and Arrow `is_in` dedup (A4). → **REP2**
- **Frontend TanStack** — `useFetch`/`AsyncBlock`/manual `setTimeout` polling reinvent
  TanStack Query (never installed despite ADR 0030 framing); `Run.tsx` polling has a real
  unmount bug. Two plain `<table>`s bypass the already-installed TanStack Table. → **REP3**
- **shadcn drift** — ADR 0030 chose shadcn/Tailwind; the code has raw Radix + 69 lines of
  hand CSS. Governance decision: adopt or amend the ADR. → **REP4**

### Small / hygiene
- **scipy micro-swaps** — `_interpolate_sorted`→`numpy.interp` (~10 LOC); Theil-Sen→
  `scipy.stats.theilslopes` (parity break, gate behind fixture re-bless). → **REP1**
- **Dependency hygiene & accuracy** — drop pandas direct dep; resolve polars
  (adopt-or-drop); resolve pycryptodome (remove-or-implement); document py-vollib/QuantLib
  as oracle deps; fix the stale `test_risk.py:6` py_vollib docstring; fix the ADR
  "built on pycryptodome" claim. → **REP0**

### What NOT to do (explicit keeps)
- Do **not** swap `black76.py`, `iv/solver.py`, `surfaces/svi.py`, `surfaces/arbitrage.py`
  to QuantLib/vollib (headline finding).
- Do **not** convert day-count / time-to-expiry to exchange-calendars session counts —
  option time-to-expiry is calendar-day ACT/365 by convention; a trading-day count is a
  *wrong number*, not a simplification.
- Do **not** swap the IBKR HMAC-SHA256 per-request signer to oauthlib — IBKR's variant
  (SHA-256, base64-decoded-LST key) is non-standard; stdlib `hmac` is already the right
  primitive.
- Do **not** push `risk/aggregation.py` onto polars — tiny data, and order-free
  determinism is a tested invariant.

---

## REP backlog — actionable now

Ordered by value/effort. Effort S/M/L, risk low/med/high. "Blocked" = do not start until
the named work lands.

| ID | Title | Effort | Risk | Blocked on | ~LOC delta |
|----|-------|--------|------|-----------|-----------|
| REP0 | Dependency hygiene & accuracy fixes | S | low | — | −2 deps, doc fixes |
| REP1 | scipy micro-swaps (numpy.interp, optional Theil-Sen) | S | low–med | — | −10…−22 |
| REP2 | Unify the storage as-of seam on duckdb/polars (+lineage pushdown, Arrow dedup) | M | med (look-ahead) | — | −40 |
| REP3 | Frontend: adopt TanStack Query/Table, fix 3D surface | M | low | — | −60 |
| REP4 | shadcn decision: adopt per ADR 0030 or amend the ADR | M | med | — | (decision) |
| REP5 | pydantic response models for the BFF API contract | L | med | coordinate web fixtures | −235 |
| REP6 | pydantic v2 for the config layer (retire `reflective.py`) | L | med (determinism) | — | −330 |
| REP7 | Collapse hand-rolled connectivity into the Nautilus IBKR adapter | M | med | live `TradingNode` (task 1C) | −430…−510 |
| REP8 | Implement the IBKR LST exchange (RSA/DH) on pycryptodome | L | high (crypto) | IBKR live auth work | +~150 |

### REP0 — Dependency hygiene & accuracy fixes
Three declared libs have zero source imports, and two doc claims are false. Cheapest,
highest-accuracy-value bundle.
- Drop `pandas` as a **direct** dep in `packages/infra/pyproject.toml` (it stays
  transitively via nautilus; no source module imports it).
- Resolve `polars`: either land REP2 (its first real use) or drop it from
  `packages/infra/pyproject.toml`. Do not leave a phantom dep whose comment claims it is
  "the core".
- Resolve `pycryptodome` in `packages/infra-ibkr/pyproject.toml`: remove it now, **or**
  schedule REP8 and leave it. Either way, fix the ADR 0031 / `cp_rest_oauth.py:6` text
  that says the signer is "built on pycryptodome" — today it is stdlib-only.
- Document `py-vollib` and `quantlib` as **test-oracle** dependencies (a comment in the
  pyproject + a line in the relevant README), so their low import count is not mistaken
  for under-leverage.
- Fix `packages/infra/tests/test_risk.py:6` — the docstring claims a py_vollib cross-check
  the code does not perform (it cross-checks against QuantLib). Correct the docstring or
  add the vollib oracle.

### REP1 — scipy micro-swaps
- `surfaces/fit.py:87-98` `_interpolate_sorted` → `numpy.interp` (flat-extrapolation is
  numpy's default clamp; re-verify the `k<=ks[0]`/`k>=ks[-1]` edges match bit-for-bit).
- *Optional:* `utils/robust.py:99-120` `theil_sen_line` → `scipy.stats.theilslopes`.
  **Parity break** — scipy's intercept convention differs (`median(y)−slope·median(x)` vs
  the repo's `median(yᵢ−slope·xᵢ)`); golden/cross-process-hash fixtures shift. Only do it
  with a fixture re-bless and a re-check of the outlier-rejection path
  (`forwards/estimate.py:402-433`). Low priority; defer unless consolidating regression backends.

### REP2 — Unify the storage as-of seam
- `snapshots/as_of.py:27-54` per-field as-of (Python loop + manual `event_id` tiebreak) →
  DuckDB `QUALIFY row_number() OVER (PARTITION BY field_name ORDER BY canonical_ts DESC,
  event_id DESC)=1` (the idiom already proven in `membership.py:245-252`) **or** the polars
  equivalent. Preserve the inclusive `<=` boundary and the tiebreak **exactly**;
  property-test shuffle-invariance and exact-tie against the current impl; run the
  `check-lookahead-bias` skill after. This is the look-ahead boundary — highest care.
- `adapter.py:356-391` lineage resolution: replace full-table-read + Python filter with a
  DuckDB `WHERE (pk…) IN (…)` predicate pushdown (keep the full composite-key match). Real
  scaling win on the multi-year raw layer.
- `adapter.py:153-163` append dedup: Arrow `is_in` / DuckDB anti-join instead of the
  `zip`+set membership loop. Small, contained.
- Defer A3 (`pyarrow.dataset` for partition discovery, `adapter.py:198-238`) — large blast
  radius (the live/version separation is correctness-critical, and partition cols are
  stored inside files). Note it but don't bundle.
- Landing REP2 closes the polars phantom-dep question in REP0.

### REP3 — Frontend leverage
- Install `@tanstack/react-query`; replace `hooks/useFetch.ts`, `components/AsyncBlock.tsx`,
  and `api.ts:187-211` get/postJson; convert `pages/Run.tsx:25-34` `setTimeout` polling to
  `refetchInterval` + `enabled` (fixes the fire-after-unmount bug). Maps onto the
  index→date→ticker query cascade.
- Extend the existing `ConstituentTable` TanStack-Table pattern to the two plain `<table>`s
  in `pages/Surfaces.tsx:29-59` and `pages/Risk.tsx:16-41` (and `DollarGreeks.tsx`).
- Fix `components/charts.tsx:58-77`: `mesh3d`-of-point-cloud → plotly `surface` over the
  regular (band × maturity) grid the BFF already returns (mirror `plot_live_surface.py:108-129`).
  This is a real readability bug — the current 3D view renders jagged, defeating ADR 0030's
  purpose.
- Trivial: dedup `serializers.py:150-157`/`188-196` (`_metric`/`_analytics_metric` are
  byte-identical).

### REP4 — shadcn decision (governance)
ADR 0030 Decision 2 chose shadcn/ui + Tailwind; the code uses raw Radix + 69 lines of hand
CSS, and `MaturityAccordion.tsx:1` comments claim shadcn. Decide before Tab-2's forms/dialogs
(2A basket builder, 3A order ticket) proliferate: **(a)** adopt shadcn/Tailwind into the Vite
toolchain, or **(b)** amend ADR 0030 to the leaner "Radix + plain CSS" reality. Leaving the
drift is the main frontend governance risk.

### REP5 — pydantic response models for the BFF
Define pydantic response models for the BFF contract and adopt FastAPI `response_model` +
`model_dump(mode="json")`, retiring most of `serializers.py` (267 LOC). Keep the deliberate
wire shape (unit-carrying `{raw,dollar,unit}` metrics, compact provenance) — generate it from
typed models instead of hand dicts. Also: `Depends(get_context)` for the repeated
`_context(request)`; `HTTPException` + one exception handler + typed `date` params for the
hand-returned `JSONResponse(..., status_code=4xx)` and `date.fromisoformat` try/except. **Do
this before Phase 2 multiplies the endpoint count** (1I, 2A–2D) — every new endpoint then gets
validation + OpenAPI + serialization free. Coordinate the shape with `web/src/test/fixtures.ts`.

### REP6 — pydantic v2 for the config layer
Retire `core/config/reflective.py` (116) and the `__post_init__` range checks across the 9
`platform_config.py` dataclasses (~180), replacing them with pydantic v2 **frozen + strict**
models: `Field(gt/ge/lt/le)` constraints, `Literal[...]` for the convention/normalisation
enums, native nested models + `dict[str,int]` (collapsing the `loader.py` escape-hatch
builders, ~90). **Hard determinism constraint:** validated values must stay byte-identical
feeding `canonical_json`→SHA-256 (strict mode so `10.5→int` still rejects); route pydantic
`ValidationError`→the existing `ConfigFieldError(section,field,value,reason)` at one boundary;
preserve the `indices:` block canonicalization that feeds `config_hashes["universe"]`. Largest
and riskiest swap — split into (6a config models, 6b loader, 6c error mapping) and keep the
reproducibility tests green at each step. This is the clearest single "reinventing the library"
case in the repo.

### REP7 — Collapse connectivity into the Nautilus IBKR adapter
**Blocked on the live `TradingNode` landing (task 1C).** Once the node is wired via the
already-built `build_data_client_config` (`nautilus_ibkr.py`), retire `connectivity/session.py`
(288), fold `connectivity/supervisor.py` (240) reconnect/heartbeat/re-subscribe into the
adapter, and drop `connectivity/clock.py` (72) for Nautilus `LiveClock`/`TestClock`. Do all
three together (~430–510 LOC). **Keep** the client-id bands and the gap→`GapInterval`→collector
seam (Saxo/Deribit still need their own lifecycle per ADR 0023 §3). Do **not** touch the
content-addressed dedup / immutability in `collectors/collector.py` — that is load-bearing and
not Nautilus's job. The raw-store-as-`ParquetDataCatalog` question (S5/S6) stays an open design
decision (ADR 0023 catalog-topology) — not part of this task.

### REP8 — Implement the IBKR LST exchange on pycryptodome
**Blocked on IBKR live-auth work.** The genuinely dangerous crypto — the Live Session Token
acquisition (RSA-SHA256 request-token signing, RSA-OAEP/PKCS1 decrypt of the access-token
secret, Diffie-Hellman key exchange) — does not exist yet; this is the *only* place
pycryptodome is actually needed. When written: use `Crypto.PublicKey.RSA`,
`Crypto.Signature.pkcs1_15`, `Crypto.Cipher.PKCS1_OAEP` and `Crypto.Util.number` — never
hand-rolled big-int `pow()`. Wire the `oauth_signer` callable into `CpRestTransport` (currently
unconstructed) with a **CSPRNG nonce** (`secrets.token_hex`/`token_urlsafe`, not `random`/`uuid1`).
Keep the existing stdlib HMAC-SHA256 per-request signer as-is. High risk (crypto) — review under
`/security-review`. This is also the action that makes REP0's "built on pycryptodome" claim true.

---

## Net picture

If REP0–REP6 land (the unblocked set), the repo deletes on the order of **~600–900 LOC** of
hand-written validation/serialization/plumbing in exchange for library declarations, and
removes two-to-three phantom dependencies — **without touching the deterministic analytics
core**, which is correctly bespoke. REP7 adds ~430–510 LOC more once live trading lands. The
single most valuable *architectural* move is REP5+REP6 (pydantic as the typed contract for both
config and the API), because it pays compounding interest as Phase 2 multiplies endpoints and
config blocks. The single most valuable *correctness* move is REP2 (one consistent, engine-native
as-of seam on the look-ahead boundary).

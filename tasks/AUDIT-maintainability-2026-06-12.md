# Audit — maintainability & library leverage, round 2 (2026-06-12)

**Question asked:** beyond the REP0–REP10 backlog, what makes this codebase hard for a solo dev to maintain — duplication, dead code, hand-rolled jobs that proven libraries already own, and ungoverned zones? Same mandate as round 1 (ADR 0023: lean on proven libraries), wider lens.

**Method:** 20 agents — 13 finders, one lens each (BFF, web, broker leaves, storage, tests, logging, validation/config, duplication, scripts, analytics structure, risk/QC, collection runtime, resilience), 6 verifiers who adversarially re-measured every claim against the working tree (re-ran the greps, re-read every cited line, re-counted the LOC, sanity-checked every library), and 1 synthesis pass (this document). Findings that merely restated a REP task or failed honest-impact review were rejected; the appendix lists them so the next auditor does not re-find them.

**Relationship to the 2026-06-07 audit:** that round asked "are the declared libraries used to their potential?" and produced REP0–REP10. This round hunts *beyond* that backlog. Where a finding extends a REP task (REP2, REP3, REP5, REP6, REP7), only the genuinely new part is reported and the dependency is named. The round-1 headline still holds and was re-confirmed by omission: **nothing here touches the deterministic analytics core** (`black76.py`, `iv/solver.py`, `svi.py`, `arbitrage.py`, `parity.py`). Every finding lives in the plumbing around it, and the three findings that could move persisted hashes say so explicitly.

---

## Headline

Three themes dominate the 46 confirmed findings.

**First, the platform validates by hand what pydantic and FastAPI — already dependencies — do natively, at every boundary except the one REP6 already fixed.** The BFF defines `_context(request)` eleven times and uses `Depends` zero times (M3); POST bodies are parsed with `await request.json()` plus isinstance chains next to a working pydantic model in the same package (M19); every broker JSON payload is mined with ~47 isinstance/`.get` guards and four drifting scalar coercers (M4); the index registry hand-rolls 265 lines of exactly what the now-landed pydantic config seam does, justified by a docstring pointing at a retired mechanism (M16); four operational record types each carry a hand-written serialization dialect (M28).

**Second, the same small helper is copy-pasted until it drifts — and hand-encoded conventions are now actively deceiving maintainers, human and agent alike.** The audit's loudest find (M1) turned out to be a false positive *caused by the hazard it named*: a hand-encoded cache key whose `\x1f` delimiter was converted to a raw invisible byte in a refactor, so two independent audit agents — and very nearly the post-audit fix — misread working code as broken (corrected and hardened 2026-06-12; see M1). The same defect class is visible everywhere it hasn't yet detonated: two close-capture value-parsers that disagree with the normalizer they claim to mirror (M4/M12), Saxo tick routing that substring-matches keys and misroutes strikes across expiries (M13), four exponential-backoff engines (M20), eight inlined copies of the canonical-JSON-SHA256 idiom in two silently different conventions (M25), three MAD z-scores with a public name collision (M15), seven copies of one Protocol (M40), and a logging plane split between a hand-rolled JSON formatter and an unconfigured structlog (M8).

**Third, nothing enforces anything.** There is no CI, no task runner, no pre-commit — the four-command gate and a purpose-built offline smoke script exist only as comments and operator memory (M2). That is why ~900 LOC of verified-dead code is still in the tree (M6, M21, M29), why `scripts/` — including the production EOD entrypoint systemd fires — sits outside ruff and mypy (M24), and why at least five READMEs/docstrings now describe behavior that no longer exists (M21, M29, M43, M24). The byte-identical-replay invariant, the project's headline guarantee, is currently checked only when someone remembers to run it.

---

## Ranked findings

| id | title | area | library | effort | risk | impact |
|----|-------|------|---------|--------|------|--------|
| M1 | Invisible `0x1f` byte in batch-preload key — false positive as a bug; representation hazard fixed 2026-06-12 | web | @tanstack/react-query | S | low | 3 |
| M2 | No CI, no task runner — the gate and smoke run on memory | repo | GH Actions + setup-uv + just | S | low | 5 |
| M3 | BFF re-implements DI and query validation 11 times over | bff | fastapi (already a dep) | S | low | 5 |
| M4 | Broker wire payloads hand-mined; four divergent scalar coercers | broker wire | pydantic v2 (already a dep) | M | med | 5 |
| M5 | ParquetStore hand-rolls Hive dataset discovery three times | storage | duckdb (already a dep) | M | med | 5 |
| M6 | BrokerSession is dead twice over — deletable today | connectivity | — | S | low | 4 |
| M7 | Raw-layer reads ignore the store's partition pushdown | storage | duckdb (already a dep) | S | low | 4 |
| M8 | Two parallel logging stacks — unify on structlog in core | logging | structlog (already a dep) | M | low | 4 |
| M9 | TS wire types hand-mirror serializers — generate from OpenAPI | web | openapi-typescript | M | low | 4 |
| M10 | Web tests hand-roll per-file fetch routers — adopt msw | tests | msw | S | low | 4 |
| M11 | Contract-record builders copied across 10 test files | tests | — | M | low | 4 |
| M12 | cp_rest_close_capture.py: 812-line god module, three snapshot impls | broker wire | — | M | low | 4 |
| M13 | Saxo tick routing by substring match misroutes strikes across expiries | broker | — | M | low | 4 |
| M14 | projection.py re-declares PRICER_VERSION and the config hasher | reproducibility | — | S | low | 4 |
| M15 | Robust MAD z-score implemented three times, with a name collision | qc/validation | — | S | low | 4 |
| M16 | index_registry hand-rolls 265 LOC of pydantic-shaped validation | universe config | pydantic v2 (already a dep) | S | low | 4 |
| M17 | Run/profile metadata: three hand SQL backends with schema drift | storage meta | SQLAlchemy Core 2.x | M | low | 4 |
| M18 | Keepalive scripts duplicate CpRestSession with a weaker auth check | scripts/ops | — | M | med | 4 |
| M19 | Hand-parsed POST bodies — pydantic request models (REP5 extension) | bff | pydantic/fastapi | M | low | 4 |
| M20 | Four backoff engines; IBKR RetryConfig clones infra's BackoffSchedule | connectivity | tenacity | M | low | 4 |
| M21 | Delete the superseded ib_async modules the README declares dead | infra-ibkr | — | S | low | 3 |
| M22 | Hand-rolled Saxo OAuth2 lifecycle and .env upsert | infra-saxo | authlib + python-dotenv | M | med | 3 |
| M23 | Two .env parsers, three data-root defaults, nine parents[N] roots | bootstrap | python-dotenv | S | low | 3 |
| M24 | scripts/ (2,004 LOC, incl. the EOD entrypoint) outside ruff and mypy | scripts | — | M | low | 3 |
| M25 | canonical-JSON+sha256 inlined in 8 modules, two divergent conventions | reproducibility | — | S | med | 3 |
| M26 | Two divergent WS-listener lifecycles; neither reconnects | connectivity | websockets (already a dep) | M | med | 3 |
| M27 | SaxoTransport copies one verb body four times | connectivity | httpx (already a dep) | S | low | 3 |
| M28 | Four operational record types hand-roll to_dict/from_dict | storage meta | pydantic v2 | S | low | 3 |
| M29 | Daily close-capture exists twice; the actor twin is a museum piece | orchestration | — | M | low | 3 |
| M30 | projection.py: 664 lines, five concerns; extract the DF resolver | analytics-adjacent | — | M | low | 3 |
| M31 | One-snapshot provenance stamping hand-rolled four times (+1 sibling) | provenance | — | S | low | 3 |
| M32 | Store-read idioms duplicated across routers; basket.py self-copies | bff | — | S | low | 3 |
| M33 | api.ts: three fetch error handlers; postJson drops the typed detail | web | — | S | low | 3 |
| M34 | Chart theme hex-copied 4×; Plot.tsx merge silently discards it | web | plotly layout.template | S | low | 3 |
| M35 | test_readback_api.py: 1465-line god test shadowing per-router files | tests | — | M | low | 3 |
| M36 | Four golden-bless env vars — one regen flag | tests | — | S | low | 3 |
| M37 | QcThresholds: 125-line pure pass-through wrapper | qc | — | M | low | 3 |
| M38 | fit.py interpolant rebuilds arrays per call in the bisection loop | analytics-adjacent | stdlib bisect | S | low | 2 |
| M39 | Six near-identical _FakeTransport variants — one conftest fake | tests | — | S | low | 2 |
| M40 | _SupportsGet protocol copy-pasted seven times | broker wire | — | S | low | 2 |
| M41 | BFF module-global state instead of app-lifetime state | bff | fastapi lifespan | S | low | 2 |
| M42 | Saxo discovery mutates frozen contracts via object.\_\_setattr\_\_ | infra-saxo | — | S | low | 2 |
| M43 | run_end_of_day's skip machinery is vestigial; the docs lie | orchestration | — | S | low | 2 |
| M44 | scenarios/stress_surface copy-paste pair; deferral now actionable | risk | — | S | low | 2 |
| M45 | Quote-QC severity ranking works by lexicographic coincidence | qc | — | S | low | 2 |
| M46 | Two different frozen dataclasses both named RawMarketEvent | storage | — | S | low | 2 |

---

## The headline find that wasn't — and what it proves anyway

### M1 — Invisible `0x1f` delimiter in the batch-preload key (web lens) — **FALSE POSITIVE as a bug; fixed as a hazard, 2026-06-12**

**Post-audit correction (owner-side re-measurement):** the finder and verifier both reported `constituentHistory.ts` joining symbols with `join("")` and splitting with `split("")` — per-character requests, a live regression. Byte-level inspection (`od -c`) shows that is wrong: all three sites contained a **literal, invisible `\x1f` control character** inside the string quotes. Commit e464a66 wrote it as the visible escape `"\u001f"`; the f6fe233 extraction converted it to the raw byte. Behavior on HEAD was correct — the preload requested full tickers all along.

What survives is the maintainability finding, sharpened: a raw control character in source renders as an empty string in every normal viewer, which is how it deceived two independent audit agents and nearly got "fixed" (i.e. broken) a third time on the same misreading. The defect class the original write-up named — hand-encoding structural keys into delimited strings — is real; the failure mode just turned out to be *unreadability* rather than breakage.

**Fixed in this audit session:** the module now passes the symbol array through and keys the cache with `JSON.stringify([asOf, ...symbols])` — no delimiter at all, behavior-identical; and `Market.test.tsx` gained a body-asserting test (`underlyings` must equal `["AAA", "BBB"]`), closing the fixed-payload-mock blind spot that let both the original conversion and this false positive go unchallenged. Web suite 49/49, lint clean.

The stage-2 recommendation stands unchanged: delete the whole 77-line hand-rolled promise-cache module as the first concrete REP3 slice — module-level `Map<string, Promise>`, error eviction, `resetForTests` re-export, cancelled-flag hook are all TanStack Query's core feature set (structural queryKey hashing, dedup across remounts, `staleTime`, per-test `QueryClient`).

Effort S, risk low, no hash exposure. **Lesson for future audits:** a claim that code is wrong must be proven at the byte level or by executing it; two agents reading the same rendered text are one measurement, not two.

---

## The BFF and web client re-implement their own frameworks

### M3 — Routers re-implement FastAPI's DI and query validation 11 times over (bff lens)

Zero uses of `Depends`/`HTTPException`/`exception_handler`/`response_model` in the whole BFF (grep-verified). Every router copy-pastes a private `_context(request)` accessor (11 definitions — `surfaces.py:22-29`, `risk.py:24-25`, `analytics.py:42-49`, `price_history.py:25-32`, `constituents.py:26-33`, `coverage.py:39-40`, `health.py:27-28`, `basket.py:35-36`, `config.py:23-24`, `recorded_dates.py:31-32`, `run.py:30-31`), an ISO date parser (4 definitions), and a try/except-to-400 block (the verifier counted 10 occurrences across 8 sites). Two handlers re-import `timedelta` mid-function (`price_history.py:217`, `constituents.py:98` — the former module already imports it at the top).

Proposal: one `deps.py` with a `CtxDep` annotated dependency and a `trade_date` dependency raising a small `BadDateError`, plus one `app.exception_handler` emitting the exact existing 400 payloads — wire contract byte-identical, existing tests pass unchanged. FastAPI is the declared framework doing its own job. Effort S, risk low, ~−90 LOC. **Verified:** exactly 11 `_context` defs, 4 parser defs, zero `Depends` anywhere — all counts re-measured.

### M19 — Hand-parsed POST bodies become pydantic request models (resilience + bff lenses, merged; extends REP5)

`basket.py:106-115` and again at `basket.py:185-194` (copy-pasted in one file) and `price_history.py:153-179` do `await request.json()` + isinstance checks + per-field guards; `_build_basket` (`basket.py:63-93`) plucks bare `leg["side"]` so a missing field becomes the opaque 400 detail `"'side'"`. Meanwhile `run.py:23-27` already uses a pydantic `RunRequest` — the codebase is split on its own convention. The hand-rolled path even forced a fake `_JsonRequest` ASGI shim in tests (`test_readback_api.py:446-478`).

Proposal: `BasketLegIn`/`BasketIn`/`BatchHistoryIn` models, `model_validate` inside the existing try (preserving the `{"error": "bad_basket"}` 400 shape, not FastAPI's 422), a domain-error handler for `ContractValidationError`, and one shared home for `_QC_FAIL_STATUSES` (byte-identical at `health.py:24` and `coverage.py:34` — the coverage copy's comment admits the duplication). Effort M, risk low, ~−60 LOC. **Verified by two independent lenses;** the verifier narrowed scope: typed date params and the single exception handler are already REP5 step 4 — only the request models, domain-error handler, and the frozenset dedup are new. Fold into the REP5 work order.

### M9 — Generate the TS wire types from OpenAPI instead of hand-mirroring (web lens; depends on REP5)

`api.ts` (390 lines) and `stressApi.ts` (57 lines) mirror the BFF serializers by discipline alone: `api.ts:1`, the WS-1I block at `api.ts:104`, and WS-2A at `api.ts:232` each repeat "keep both sides in lockstep"; `stressApi.ts:1` admits it exists only to avoid merge friction. ~25 interfaces, a discriminated union, nullable policies in comments, no compiler or CI check that the sides agree.

Once REP5's pydantic response models land, FastAPI emits OpenAPI for free; `openapi-typescript` (openapi-ts org, ~6M weekly downloads, types-only so zero runtime cost) generates `schema.d.ts`, and drift fails `tsc` instead of surfacing as runtime undefined. Optionally pair with `openapi-fetch` (same org) for path/params checking. Effort M, risk low, ~−420 hand-written lines. **Verified:** REP5's step 5 keeps the hand-written web side — TS codegen is genuinely outside its scope. Hard dependency: REP5 first.

### M32 — Store-read idioms duplicated across routers (bff lens; precursor to REP2)

Two idioms recur with near-identical comments: "latest partition date for an underlying" (`basket.py:50-60`, `coverage.py:43-50`, `runner.py:101-111`, `health.py:31-32` as the no-underlying variant) and "version-blind read then filter by underlying" (`surfaces.py:54-61`, `analytics.py:178-190` plus twice more at `199-203`, `basket.py:119-128` and `196-205`, `constituents.py:95-97`). basket.py's two POST handlers also duplicate ~25 lines verbatim between themselves (`133-143` vs `208-218`). Proposal: two helpers (`latest_partition_date`, `read_for_underlying`) plus one `_basket_inputs`; the eventual filter pushdown belongs in the store API, which is REP2's territory — this dedup pays now and shrinks to a one-liner then. Effort S, risk low, ~−50. **Verified:** all sites re-read; verbatim-shaped.

### M33 — api.ts carries three divergent fetch error handlers (web lens; precursor to REP3)

`getJson` (`api.ts:325`) JSON-stringifies the error body; `postJson` (`api.ts:339`) throws only `status statusText`, discarding the typed detail the BFF deliberately serves — and the batch preload routes through it, so its error path shows a bare "400 Bad Request"; `priceBasket` (`api.ts:354`) and `stressBasket` (`api.ts:374`) are byte-identical 17-line copies that *do* extract detail. `Basket.tsx:51` hand-rolls the same mutation state machine twice around them. Collapse to one `request<T>` throwing a typed ApiError; under REP3 the Basket blocks become two `useMutation` calls. No new library. Effort S, risk low. **Verified:** all three implementations and the dropped-detail path re-read.

### M34 — Chart theme copied as hex literals into four files, and Plot.tsx's merge silently discards it (web lens)

The dark-panel theme exists five times: CSS custom properties in index.css plus hand-copied hex in `Plot.tsx:29`/`Plot.tsx:88`, `CandleChart.tsx:32`, `LightweightLineChart.tsx:37`, `charts.tsx:99`. Worse, Plot.tsx's hand-rolled merge is broken: `mergedLayout` spreads `...layout` *after* `...defaultLayout`, so any caller-supplied axis (e.g. StressSurface's axis titles) clobbers the themed axis object — gridcolor/tickcolor silently lost on every 2D panel that sets a title. `Plot.tsx:6` also still claims Plotly is the single charting dependency, contradicting `charts.tsx` and the README.

Proposal: one `chartTheme.ts` token module, a shared `baseLightweightOptions()`, and — instead of fixing the bespoke deep merge — Plotly's native `layout.template` (already a dependency; templates merge per-attribute by design, fixing exactly this clobber class). Display-only; no numerical output touched. Effort S, risk low, ~−60. **Verified:** the merge bug was confirmed by tracing the spreads; the verifier notes StressSurface.tsx lives in `components/`, not `pages/`.

### M41 — Module-global mutable state instead of app-lifetime state (bff lens)

`JOB_STORE` and the ThreadPoolExecutor live at module scope (`runner.py:82-86`; the executor is never shut down — no lifespan hook anywhere), the OAuth state store is a module singleton (`oauth_state.py:50-58`), and the SAXO_* env vars are frozen at import time (`oauth.py:25-27`). Tests already pay: `conftest.py:31` must `JOB_STORE.clear()` between tests. Hang all of it on `AppContext`, read env in `create_app`, shut the executor down in a lifespan handler. Effort S, risk low, honestly small (impact 2). **Verified:** no `_EXECUTOR.shutdown` anywhere; the manual clear confirmed.

---

## Broker edges: wire parsing, transports, lifecycles

### M4 — Broker wire payloads are hand-mined with isinstance chains and four divergent coercers (validation-config + infra-ibkr lenses, merged)

Every CP-REST/Saxo/Deribit payload is consumed as untyped `Any` and defensively spelunked — the verifier counted ~47 isinstance guard sites across the cited modules (`cp_rest_close_capture.py:146-221`, `cp_rest_normalize.py:50-70`, `cp_rest_discovery.py:55-105`, `cp_rest_history_normalize.py:53-147`, `saxo_adapter.py:76-171`, `universe/normalization.py:33-50`, `cp_rest_index.py:44-160`, `cp_rest_session.py:40-71`). The "coerce one broker scalar" job exists at least four times with drifting semantics, and the drift is real: `_parse_value` (`cp_rest_normalize.py:50`) strips one leading status flag and drops the −1 sentinel, while close-capture's two in-module copies do `lstrip("CHch")` with no sentinel drop — the docstring claims to mirror the normalizer it disagrees with. `config.py:98-139` separately hand-rolls five `_require_*` validators for one YAML file. pydantic is a core dependency (`packages/core/pyproject.toml:7`) and is imported nowhere in infra-ibkr or infra-saxo (grep-verified).

Proposal: pydantic v2 models per wire shape (CP snapshot row, secdef item, strikes, history bars; Saxo and Deribit frames) with `extra="ignore"`, the existing bespoke parse functions moved *verbatim* into shared `Annotated[..., BeforeValidator(...)]` types so emitted values stay byte-identical, per-row try/skip preserved where rows are skipped today, and the existing `test_cp_rest_equivalence.py` bar as the regression gate. The `config.py` `_require_*` block is REP6-shaped — execute it as a REP6 extension to leaf packages.

Effort M, risk medium, **hash-stability risk flagged** — the shared value-parse surface feeds persisted events, safe only if the parsers move verbatim into validators. **Verified by two independent finders whose findings the verifier ordered merged into one task;** coordinate with M12, which deduplicates the same coercers structurally.

### M12 — cp_rest_close_capture.py is an 812-line god module with three snapshot implementations (infra-ibkr lens)

One module mixes five concerns: snapshot warm-up polling + 414-safe conid batching (`cp_rest_close_capture.py:176-266`), payload coercion, IBKR month-token parsing (`373-481`), discovery-window policy, and basket assembly. The value parse appears twice inside it (lines `201` and `330`), both diverging from the canonical `cp_rest_normalize.py:50-70` — so a sentinel-only row counts as "warm" yet yields zero events. And `cp_rest_adapter.py:94-106` builds the identical snapshot request a *third* time with no warm-up and no 50-conid batching, so the adapter never inherited the 414/cold-snapshot fixes.

Proposal: extract `cp_rest_snapshot.py` (request shaping, batching, warm-up, one exported value parse reusing `_parse_value`) used by both adapter and close-capture, and `cp_rest_chain_window.py` for month tokens + discovery policy; close-capture shrinks to ~300 lines of orchestration. Persisted events still come from `snapshot_to_events`, so replay hashes are untouched; only live warm-up convergence changes, for the better. Effort M, risk low. **Verified:** all five concerns, both in-module parses, and the third snapshot implementation confirmed at the cited lines; complementary to, not duplicated by, M4.

### M13 — Saxo adapter routes ticks by substring-matching keys; misroutes strikes across expiries (brokers lens)

`_keys_for_strike` (`saxo_adapter.py:524-537`) resolves a snapshot strike by scanning subscribed keys for `':C:'` plus `f':{strike_str}:'` — and ignores expiry. With `n_expiries>1` (a mode the class explicitly supports), strike 100 in expiry 2 resolves to expiry 1's key, so expiry-2 ticks are emitted under expiry-1's instrument. The verifier found it *worse* than claimed: the key format ends `...:STRIKE:MULT:EXCH:CCY`, so `':100:'` also false-matches every multiplier-100 key. Meanwhile the canonical `parse_instrument_key` lives one layer down (`contracts.py:149`) and the Deribit adapter already uses it; `saxo_adapter.py:271-285` and `365-376` repeat the ad-hoc splitting.

Proposal: parse each subscribed key once at subscribe() into a `(expiry, Decimal(strike), right) -> key` dict; `_keys_for_strike` becomes an exact lookup taking the expiry. Structural reuse of an in-repo parser, no library. Effort M, risk low, **hash-stability risk flagged** — fixing the routing changes which `instrument_key` persisted events carry in multi-expiry mode, which is the point.

### M20 — Four backoff engines; IBKR's RetryConfig is a verbatim clone of infra's BackoffSchedule (duplication + infra-ibkr lenses, merged)

Exponential-with-cap backoff exists four times: `supervisor.py:55-75` + its loop at `223-236`; `infra_ibkr/config.py:39-52` (`RetryConfig.delay_for` — same formula, same 0-based convention, same validation, near-verbatim) consumed by `cp_rest_history.py:243-280`'s own loop; `session.py:108-112`'s jittered variant; and `cp_rest_transport.py:84-116`'s Retry-After-honoring loop. `supervisor.py`'s docstring claims retry behavior "lives in exactly one place." The history layer also duck-types `exc.__cause__.response.status_code` (`cp_rest_history.py:56-65`) because `CpRestTransportError` swallows the HTTP status.

Two-step proposal: (a) delete the RetryConfig clone by importing BackoffSchedule (infra-ibkr already depends on infra — layering-legal, verified); (b) adopt tenacity (jd/tenacity, actively maintained, the de-facto retry library; supports injected `sleep=` so the deterministic fake-clock tests keep working) for the two CP-REST HTTP loops, and — worth doing regardless — give `CpRestTransportError` a `status_code` field and delete the `__cause__` reach. Leave the supervisor/session loops alone: they dissolve under REP7/M6. Effort M, risk low. **Verifier caveats:** the Retry-After wait needs a small custom callable and the loops' bespoke parts (per-attempt OAuth re-sign, terminal fast-fail) survive as predicates, so the declarative win is smaller than pitched; the part-(a) dedup and the status_code fix are the unambiguous core.

### M22 — Hand-rolled Saxo OAuth2 lifecycle and .env upsert (brokers lens)

~370 LOC implement the full authorization-code flow by hand: authorize-URL + code exchange (`web_oauth.py:17-63`), a 234-line thread-locked refresh-rotation state machine (`token_manager.py:48-234`), and a hand-written .env upserter (`env_tokens.py:11-30` + `token_persist.py:24-43`). Neither authlib nor python-dotenv is a dependency anywhere (grep over all pyprojects + uv.lock). Authlib is the actively maintained standard OAuth2 client — `create_authorization_url`/`fetch_token` replace web_oauth, and its `update_token` hook owns refresh rotation; python-dotenv's `set_key` does exactly the upsert. Keep only a ~30-LOC proactive background refresher (Saxo's 40-min idle refresh-token expiry is the genuinely bespoke part, which the finding honestly concedes). Effort M, risk medium (security-sensitive code, which is precisely why it should be a library), realistic net ~−250. **Verifier corrections:** 4 maintaining test files, not 5; dotenv `set_key` creates a missing .env where `token_persist` deliberately skips — preserve that delta.

### M26 — Two divergent WS-listener lifecycles; neither reconnects (brokers lens; outside REP7 by REP7's own scoping)

Saxo runs its WS loop in an owned daemon thread but breaks on the first recv error (`saxo_adapter.py:378-407`); Deribit calls `asyncio.get_event_loop().create_task()` from sync code (`deribit_adapter.py:242-245`) — deprecated on the workspace's Python 3.13 pin with no running loop, and the task never executes unless a caller happens to own one; `deribit_transport.py:66-96` is a bare `async for` with no reconnect. REP7 explicitly keeps Saxo/Deribit lifecycles out of its scope (`tasks/REP7-nautilus-connectivity-collapse.md`), so this seam is uncovered.

Proposal: one WS-listener runner in `algotrading.infra.collectors` (owned thread + stop event + fault callback) used by both leaves, leaning on the websockets library's built-in reconnect iterator (`async for ws in connect(...)` — already a dependency of both leaf packages, in the library since v10). Effort M, risk medium, net +10 LOC but one lifecycle instead of two and a real defect killed. **Verified:** the get_event_loop fragility, the no-reconnect paths, and the REP7 carve-out all confirmed.

### M27 — SaxoTransport copies one verb body four times (brokers + resilience lenses)

`saxo_transport.py:59-124`: get/post/patch/delete each rebuild the URL by hand, rebuild the Bearer header from `token_fn`, and repeat the same raise_for_status/wrap-into-SaxoTransportError block — ~70 LOC of duplication that httpx (already a hard dep) owns natively: `Client(base_url=...)`, a custom `httpx.Auth` whose `auth_flow` injects the token per request (rotation still works), one ~15-LOC `_request`. `deribit_transport.py:49-64` rebuilds the same idiom; apply the base_url fix there too. Effort S, risk low, wire behavior byte-identical. **Verifier caveat:** the injected `_client` test seam means tests construct bare clients — they need the matching construction change.

### M40 — The transport-seam Protocol is copy-pasted seven times (infra-ibkr lens)

Identical `class _SupportsGet(Protocol)` defined five times in infra-ibkr (`cp_rest_discovery.py:28`, `cp_rest_index.py:34`, `cp_rest_close_capture.py:142`, `cp_rest_history.py:72`, `cp_rest_adapter.py:33`), once in infra-saxo (`saxo_underlying.py:24`), plus the get+post variant at `cp_rest_session.py:35`; `live_capture.py:51-52` falls back to a runtime `callable(getattr(...))` duck-check because no shared type exists. Define `SupportsRestGet`/`SupportsRest` once in `algotrading.infra` next to the contracts seam. Effort S, risk low, honestly small (impact 2) but it also hands REP7 a single named seam to target. **Verified:** grep found exactly the claimed seven.

### M42 — Saxo discovery mutates frozen contracts via object.\_\_setattr\_\_ (brokers lens)

`saxo_discovery.py:142-160` builds each OptionContract through `parse_saxo_option`, then bypasses the frozen dataclass with three `object.__setattr__` calls (the verifier adds: the else branch of the duplicated raw-dict build is dead code, since `parse_saxo_option` always sets raw). This defeats the immutability contract and skips any future `__post_init__` validation on those fields. Pass the values as parameters (or `dataclasses.replace`); field values identical, so keys and stored records are byte-identical — the overwritten fields are `compare=False` metadata, not part of the canonical key. Effort S, risk low.

### M21 — Delete the superseded ib_async modules the README already declares dead (infra-ibkr lens)

`packages/infra-ibkr/README.md:152-161` declares `ibkr_transport.py`, `ibkr_adapter.py`, `ibkr_discovery.py` "superseded ... retained only as dead reference" — 329 src LOC plus three test files that `importorskip("ib_async")` and silently skip in the gate. Grep confirms the only live references outside their own tests are `scripts/ibkr_bootstrap.py:117` (operator script outside the gate) and a cross-broker test import (`test_broker_agnostic.py:156`), which ports cleanly to the CP-REST normalizer's `snapshot_to_events`. ~600 LOC (src+tests) a solo dev mentally rules out on every pass. Effort S, risk low. **Verified:** the deletion also removes a consumer of `session.py`, easing M6.

### M6 — BrokerSession is dead twice over: zero callers AND unloadable from the live config (collection-runtime + duplication lenses, merged; REP7 carve-out)

connectivity carries two full reconnect state machines. The verifiers proved `session.py`'s is dead by two independent measurements: (1) grep across packages/apps/scripts finds zero production constructors of `BrokerSession`, `SessionConfig`, `ReconnectPolicy`, or `next_delay` — only its own 46-line test; (2) `SessionConfig.from_config` reads `data['broker']['reconnect']` (`session.py:80-99`) with field names (`base_delay_seconds`/`multiplier`) that the real `configs/broker.yaml` does not contain — the live config carries only the supervisor schema (`supervisor.py:119-128`), so `BrokerSession.from_config` would KeyError against the actual file. It is dead *and* unloadable, yet a maintainer must still reconcile two backoff formulas and two config schemas whenever they touch reconnect.

REP7 lists retiring session.py behind the live-TradingNode gate — but the gate only applies to code that needs a replacement. Dead code needs none. Delete the state machine and its test now (~250 LOC + 46 test); keep SessionSupervisor as the single home. **One caveat the finding understated, verified:** `TransportError` and the `BrokerTransport` protocol *are* imported by production code (`ibkr_transport.py:14`, `ibkr_bootstrap.py:116-130`) and must be re-homed first. Effort S, risk low. The remainder of REP7 stays blocked as written.

---

## Dead code and docs that lie

### M29 — The daily close-capture exists twice; the actor twin is a museum piece (collection-runtime lens)

`actor/close_capture.py:88-180` (216 lines) documents itself — and `actor/README.md:18-24` claims — as "the seam 1G's schedule wires." Grep: zero production callers; the real path, `eod_stages.py:360-391`, re-implements the same per-index close loop inline and has already drifted ahead (it uses `run_analytics_with_qc` and accumulates QC rows; the twin calls plain `run_analytics` with no QC). Two implementations of the platform's headline daily product, and the docs point new readers at the dead one first. Delete the capture trio, relocate `DEFAULT_PROVIDER`, fix the README. **Verifier caveat narrowing the deletion:** `IndexBasket`, defined in the same file, *is* live (eod_stages and live_capture use it) — relocate it, don't delete. Effort M, risk low.

### M43 — run_end_of_day's idempotent-skip machinery is vestigial and its docs lie (collection-runtime lens)

ADR 0032 made the pipeline overwrite-by-re-run; the inline comment at `pipeline.py:151-158` says so and the code agrees (`skipped` is never appended to; `already_done` at `pipeline.py:119-186` is computed only to log). But the module docstring (`pipeline.py:6-13`), `orchestration/README.md:18-23`, and `completed_stages`' docstring (`run_state.py:194-203`) all still describe the retired skip behavior. A solo dev returning in six months will design a catch-up flow around skip semantics that do not exist. Make the code tell the truth: drop `EodResult.skipped`, rewrite the three docs, optionally collapse the five copy-paste stage blocks into one loop. Effort S, risk low. **Verified:** docs demonstrably contradict measured behavior.

### M46 — Two different frozen dataclasses both named RawMarketEvent (storage lens)

`storage/events.py:33-68` (EAV capture event, Decimal value) vs `contracts/tables.py:44-60` (canonical persisted contract, float value, three timestamps) — same name, same package. `storage/__init__.py:71-73` documents the collision as a hazard; `sample_bridge.py:47` already alias-imports around it. Grep shows the storage-side class is imported only by json_io, sample_bridge, and tests, while 17 sites use the contracts one — so renaming the collector-level one to `CollectorEvent` is small and mechanical, with no persisted bytes involved. Effort S, risk low, impact honestly 2 — but it is a guaranteed future wrong-import.

(M21 and M6 above also belong to this theme; they are filed under broker edges where their content lives.)

---

## Storage and the read path

### M5 — ParquetStore reimplements Hive dataset discovery three times with magic thresholds (storage lens; extends REP2)

`adapter.py` hand-rolls three competing file-discovery strategies for the same partition tree: a per-day stat walk (`adapter.py:290-340`), a recursive glob with post-hoc `trade_date=` string parsing (`adapter.py:342-400`), and a single-partition fast path (`adapter.py:402-460`), switched by hard-coded budgets `0 <= delta.days <= 1826` vs `<= 31`. The `key=value` path parsing repeats in five places; the `duckdb.connect()` + UTC + try/finally + dict(zip) boilerplate is copy-pasted three times in adapter.py alone (`adapter.py:512-537` among them) plus `membership.py` and `as_of.py` (the latter is REP2's).

Proposal: one DuckDB scan over `table_dir/**/*.parquet` with `filename=true`, pushing trade_date/underlying/provider/version predicates as filename filters (DuckDB prunes files on filename predicates before opening them); the live-vs-versioned split becomes a `NOT LIKE '%/version=%'` filter matching today's `_is_live_file`; `list_partitions`/`underlyings_present` become `SELECT DISTINCT` over parsed segments; `ORDER BY filename` preserves today's read order; one shared `_query` context manager. DuckDB is already the read engine and filename pruning is a core stable feature. Effort M, risk medium, ~−150, no persisted-hash exposure (read path only). **Verifier caveats to hold:** benchmark before deleting the day-budget heuristics (a `**` glob still enumerates the tree, so the narrow-range stat-walk advantage on a multi-year store must be measured), and handle the zero-files-glob case explicitly. Tree depth is variable (optional `provider=`/`version=` segments) — use filename pruning, not `hive_partitioning=true`.

### M7 — Raw-layer reads ignore the store's partition pushdown; every cron fire scans all history (collection-runtime lens)

`ParquetStore.read` already supports trade_date pushdown (`adapter.py:462-509`; `eod_stages.py:329-331` uses it correctly). But `replay_day`'s no-underlying branch reads the FULL table and filters in Python (`replay.py:41-49`); `RawCollector._reload_seen_event_ids` scans the entire event history on every construction (`collector.py:147-153`); `build_summary` re-scans on every close (`collector.py:255-259`); and the EOD metrics step inherits the full scan via replay_day (`jobs.py:216-219`). With a daily-close cron the plan of record, every fire's cost grows linearly with total history — the creeping slowdown a solo dev notices months in, on the live box.

Fix: always pass `trade_date=` (the collector only writes events stamped with its own trade_date, so the filtered read provably returns the identical set — pinned by the existing byte-identical replay gate). Effort S, risk low, ~−10 LOC. **Verified:** equivalence argument checked against the write path; explicitly not REP2 (that is the derived-layer as-of seam, this is raw-layer pruning).

### M17 — Run/profile metadata: three hand-written SQL backends with live schema drift (storage lens)

The 4-method RunRepository port is implemented three times (`runs.py:75-111` JSON-file, `sqlite_runs.py:24-89`, `postgres_runs.py:30-117`), bodies near-duplicated, `_connect` contextmanager verbatim-duplicated into `sqlite_profiles.py:26-107`. The backends have already drifted: sqlite stores `ended_at` as TEXT isoformat, postgres as TIMESTAMPTZ — so `ORDER BY ended_at` compares strings on one backend and instants on the other. ~310 LOC of hand SQL for two tiny tables, explicitly never on the deterministic reconstruction path (the safest library-swap surface in storage).

Proposal: one SQLAlchemy Core repository (Core 2.x is the canonical, actively maintained dialect layer; new dependency, but entirely off the hashed paths), one Table definition each, dialect chosen by `create_engine(url)` in the factory (`factory.py:31-66`), `ended_at` as `DateTime(timezone=True)` on both. Keep the JSON-file registry as the zero-dep reference. Effort M, risk low, ~−120. **Verifier caveat:** SQLAlchemy's sqlite datetime TEXT format uses a space separator vs the existing 'T' — pin the ISO-T format or normalize once, or mixed rows could mis-order within a day.

### M28 — Four operational record types hand-roll serialization; Manifest's field list lives in three places (validation-config lens)

Manifest, RunRecord, ProfileVersion, StageRun each carry hand-written to_dict/from_dict dialects: `manifest.py:42-70` spells all 10 fields; `runs.py:43-72` re-spells the Manifest constructor with ad-hoc `.get(..., "unknown")` defaults duplicating the dataclass defaults — adding one manifest field is a three-file edit, funneled through by both SQL repos (`sqlite_runs.py:78-88`, `sqlite_profiles.py:87-106`); `run_state.py:94-136` duplicates the pattern again with its own `_encode/_decode`. Convert to pydantic models keeping the exact `json.dumps(sort_keys, separators)` call sites, so on-disk bytes (registry files, the PIPE_BUF-bounded ledger lines, sqlite payload columns) stay byte-identical; Manifest config_hashes are recomputed from snapshot content (`manifest.py:88-92`), independent of this serialization. Effort S, risk low, ~−100. **Verified:** triple maintenance and hash-independence both confirmed; distinct from REP5 (BFF) and REP6 (config) — the operational-records plane.

---

## Reproducibility and analytics-adjacent plumbing

### M14 — projection.py re-declares the two canonical reproducibility homes (analytics-structure lens)

`projection.py:64-68` hard-codes `PRICER_VERSION = "black76-lr-1.0.0"` as a second copy of `pricing/engine.py:46-51` (the comment admits it "mirrors" it; grep confirms exactly two definitions, and `pricing/__init__.py:27` already exports the canonical one). The engine's own history — the corrected "black76-crr" misnomer at `engine.py:49-50` — proves the double-edit hazard: today that correction would hit two files or persisted `ProjectedOptionAnalytics.pricer_version` silently forks from `PricingResult.pricer_version`. Separately, `projection.py:177-186` and `projection.py:476-498` each inline `hashlib.sha256(canonical_json(...))` with function-local imports, re-implementing core config's private `_sha256` (`platform_config.py:715-741`) — the only two such sites outside core. Import the constant (layering already safe — projection imports from pricing), expose one `object_config_hash` helper in core.config. Byte-identical: same string, same sha256 over the same bytes. Effort S, risk low, impact 4 for ten lines.

### M25 — canonical-JSON + SHA-256 inlined in 8+ modules, with two silently divergent conventions (duplication lens)

The reproducibility-critical idiom `json.dumps(sort_keys=True, separators=(',',':'))` + sha256 is re-typed by hand in `provenance.py:209-210`, `qc/result.py:48`, `risk/scenarios.py:104-105`, `risk/stress_surface.py:148-149`, `universe/service.py:47` (adds `default=str`), `yaml_config.py:73` — while a proper helper pair already exists buried in `platform_config.py:721-730` and reused only by `surfaces/projection.py:186`. Crucially the paths are *not* equivalent: the config `canonical_json` runs a `_canonical()` pre-pass (collapses −0.0, `allow_nan=False`) that the inline copies skip — the repo has two (the verifier says effectively three, counting `default=str`) subtly different definitions of "canonical JSON" feeding content hashes.

Proposal: a `core.hashing` module with two audited primitives — bare `canonical_dumps` (byte-for-byte what the inline copies do today) and `sha256_hex` — plus the −0.0-collapsing variant kept under its explicit name; route every copy through the primitive matching its *current* bytes, pinned by golden-hash tests. No convention changes — that is the REP1 trap, and the finding explicitly avoids it. Effort S, risk medium, **hash-stability risk flagged** (which is why the pinning is mandatory); the win is that the next hash-relevant change happens in one reviewed file instead of eight, and the divergence becomes a documented choice. **Verified** with two minor citation slips noted (yaml_config is line 71; the normalization.py mirror pair lives in `infra/collectors/`); the eight primary citations are accurate. The related smaller pair in `risk/` is M44.

### M31 — The one-snapshot provenance stamp is hand-rolled four times, with a general sibling one layer up (analytics-structure lens)

Four contract-emission sites repeat the identical refs-then-stamp shape: `fit.py:242-260`, `projection.py:449-473`, `estimate.py:489-500`, `solver.py:305-313` — grep on the `source_timestamps` idiom confirms exactly these — while `actor/stamping.py:46-71` already owns the general-case `build_stamp` over the same `core.provenance.stamp` (`provenance.py:213-245`). Every new emitted contract (the index-analytics roadmap adds more) clones the pattern again. Add one `snapshot_stamp` helper next to `stamp()`; stamp() sorts its inputs, so the emitted ProvenanceStamp including `stamp_hash` is byte-identical. Touches solver/forwards only at stamp assembly, not numerics. Effort S, risk low. **Verified**, including a fifth related call site in `snapshots/builder.py` strengthening the case.

### M30 — projection.py is 664 lines holding five concerns; the discount-curve resolver is the seam to cut (analytics-structure lens)

The module holds config validation + hashing (`projection.py:109-201`), the tenor map, a full discount-curve engine (`projection.py:236-311`: label-join precedence, flat-forward interpolation in −ln DF, nearest-knot extrapolation — F-SURF-01), the delta-to-strike bisection (`projection.py:371-430`), cell pricing, and provenance merging. The DF resolver is a curve concern, not a regrid concern — `risk/valuation.py:92` already derives rates from DFs independently, and any Phase-2 consumer must today import it from surfaces.projection. Pure code-move: extract `SnapshotMarketState` into its own module; `surfaces/__init__.py:36-47` already mediates all external imports so callers are untouched. No arithmetic touched, no hash movement. Effort M, risk low.

### M38 — fit.py's scalar interpolant rebuilds NumPy arrays per call inside the innermost bisection loop (analytics-structure lens)

`_interpolate_sorted` (`fit.py:91-111`) calls `np.asarray` on the slice tuples on every scalar evaluation, and it sits under projection's delta inversion (`projection.py:396-430`, up to 100 bisection iterations × 8 tenors × 8 bands) — thousands of tuple-to-array conversions per grid. NumPy is used only for `searchsorted`; the docstring records that `np.interp` was rejected for 1-ULP divergence and the interior arithmetic is deliberately hand-rolled (`fit.py:79-88`). Replace `searchsorted` with stdlib `bisect.bisect_left` — identical index on a sorted float tuple, identical IEEE-754 ops on the interior expression, so byte-identical, gated for free by the regen-gated projection golden test. Explicitly not REP1-shaped: this *removes* a pointless numpy round-trip with exact output preservation, and projection.py already imports bisect. Effort S, risk low.

### M15 — Robust MAD z-score implemented three times; one duplicate collides with the canonical public name (risk-qc lens)

`utils/robust.py:59-75` is the README-declared one home (ADR 0021), correctly used by forwards (`estimate.py:33-34`). But `qc/checks.py:795-823` carries its own `_median` + unsigned `robust_z_score` with 1.4826 inlined, and `validation/anomaly.py:35-64` carries another `_median` plus a second `robust_zscore_vs_baseline` — the *same exported name* as utils', both in their packages' `__all__` (verified). anomaly.py's importability rationale for the duplication (`anomaly.py:10-15`) is moot — it already imports core.config, and utils.robust needs only math + statistics. Make utils the single implementation; qc keeps a 3-line abs-wrapper preserving its EmptyBaselineError. The verifier checked bit-equality operation by operation (statistics.median's `(a+b)/2` vs hand `0.5*(a+b)`, same multiply-then-divide order, `abs(x/y)==abs(x)/y`, degenerate-MAD branches agree) — byte-identical, pinned by the existing hand-computed tests. Not REP1-shaped: intra-repo dedup with provable bit-equality. Effort S, risk low, ~−55.

### M44 — scenarios.py / stress_surface.py copy-paste pair, and a deferral that has come due (risk-qc lens)

`stress_surface.py:67-76` `_dedup_preserving_order` is a verbatim copy of `scenarios.py:73-87`'s helper; both modules re-implement the canonical-JSON-sha256-`[:12]` short hash (`stress_surface.py:133-149`, `scenarios.py:90-105`) that `provenance.py:187-209` owns for stamps. The stress module's own docstring (`stress_surface.py:23-27`) deferred unification "once the 2C claim on scenarios.py clears" — 2C has landed, so the deferral is now pure debt. Shared `short_config_hash` + ordered-dedup helpers; payloads and format strings unchanged, so every persisted `effective_*_version` string is byte-identical. Effort S, risk low, small (impact 2). Coordinate with M25's hashing module — the `[:12]` truncation must be parametrized.

### M45 — Quote-QC severity ranking works by lexicographic coincidence (storage-snapshots lens)

`quote_quality.py:134-139` computes the worst verdict as `max()` over raw severity *strings* — correct today only because "reject" > "caution" alphabetically. The `_SEVERITY_RANK` dict defined for exactly this purpose at `quote_quality.py:21-22` is referenced nowhere (grep-verified). Adding any new severity (e.g. "warn") silently mis-ranks. Rank explicitly via the existing dict, delete the dead `status = worst` alias. **Verifier correction:** `triage.py:68`/`triage.py:170` ranks a *different* severity vocabulary as a sort key, not a second reduce — so fix quote_quality locally and skip the proposed shared helper. Output bytes unchanged today. Effort S, risk low.

### M37 — QcThresholds is a 125-line pure pass-through (risk-qc lens)

Every property on QcThresholds (`thresholds.py:30-155`) forwards 1:1 to the already-typed, frozen, hashed QcThresholdConfig — 14 properties plus `tenor_floor`, no validation, no derived value, no information hiding (verified property by property). Every new QC cut-off is a two-place edit. Have the thirteen checks take QcThresholdConfig directly (`checks.py:41`); the nested paths are arguably clearer (they name which config block owns the cut-off). Every value read is identical, so QcResult rows are byte-identical. Effort M (37 reference sites, mechanical), risk low, ~−130.

---

## Logging, config, bootstrap, scripts

### M8 — Two parallel logging stacks; unify on structlog, configured once in core (resilience + duplication lenses, merged)

`core/log.py:51-84` hand-rolls a stdlib JSON formatter (~85 LOC) used by 16 production files; 17 other production files — the entire orchestration/actor/EOD path (`eod_runner.py:71`), infra-ibkr capture, the frontend runner (`runner.py:33-43`) — call `structlog.get_logger` directly. `structlog.configure` is called nowhere in production (grep: only one test), so those loggers emit the default pretty ConsoleRenderer while core.log emits one-line JSON to stderr *in the same EOD process*; no code configures the root logger either, so third-party logs (nautilus, httpx, uvicorn) fall to logging's lastResort handler — a third format. Three bonus defects, all verified: structlog is declared by infra (`infra/pyproject.toml:25`) and frontend (`frontend/pyproject.toml:11`) but not by core, which owns the logging module; infra-ibkr imports structlog in 5 files without declaring it (phantom import, resolving transitively — same shape as REP0's items); the split runs *through* single packages (`cp_rest_history.py` structlog vs `ibkr_adapter.py` core.log); and the core README asserts "No structlog dependency" as a feature.

Proposal: structlog owned by core — a ~30-LOC `configure_logging()` with JSONRenderer pinned to the existing ts/level/logger/message keys, `ProcessorFormatter` on the root handler so third-party logs join the same JSON stream, `core.log.get_logger` kept as a thin alias (the 16 call sites change mechanically, `extra={}` → kwargs), called from the EOD runner and BFF entry points; declare structlog in core and infra-ibkr. structlog is actively maintained and already the majority stack. Logs only — no persisted output, no hash exposure. One grep/jq-able run log is exactly what the unattended-ops direction needs. Effort M, risk low.

### M16 — index_registry hand-rolls 265 LOC of validation; its bespoke-parser rationale is stale (validation-config + risk-qc lenses, merged; extends REP6)

`index_registry.py:142-265` validates the `indices:` block with hand machinery: frozenset allow-lists (`index_registry.py:40-46`), `_require_str`, manual bool-is-not-int conid guards, a hand currency check, the calendar-code check against exchange_calendars. The docstring (`index_registry.py:16-21`) justifies this by contrast with the reflective `build_dataclass` seam — which is retired: `core/config/__init__.py:85` says verbatim that the pydantic v2 section models are the validation seam now (REP6 landed). This file is the one config layer the migration skipped, and it is actively growing.

Proposal: frozen pydantic models under the same `_SECTION_CONFIG` discipline (`platform_config.py:42-93`) — strict mode rejects bool-for-int natively, `extra="forbid"` replaces the allow-lists, a `field_validator` keeps the load-bearing never-default-a-calendar rule against `xcals.get_calendar_names()`, `RootModel[dict[str, IndexEntry]]` for the keyed map, ValidationError mapped to IndexRegistryError exactly as core's existing boundary does. **Hash-safe, verified:** the universe bundle hash is taken over the raw YAML block (`registry_loader.py:17-28`), never the typed object. Verifier caveats: `secType` needs an alias; negative tests pinning exact reason strings need the mapper to carry messages or the tests to relax to field-level assertions. Effort S–M, risk low, ~−130.

### M23 — Entrypoint bootstrap duplicated: two .env parsers, nine parents[N] roots, three data-root defaults (scripts-configs + duplication lenses, merged; loosely extends REP0)

`infra/connectivity/dotenv.py:32-65` is a 66-LOC bespoke KEY=VALUE parser with its own 66-LOC test file; `scripts/ibkr_bootstrap.py:52-61` re-implements it privately with subtly different quote handling (sequential `.strip('"').strip("'")` vs matched-pair unwrap — verified divergent). python-dotenv (theskumar, actively maintained, the standard for exactly this) is absent from every pyproject and uv.lock; its `load_dotenv(override=False)` matches the documented precedence. Repo-root discovery via `Path(__file__).resolve().parents[N]` appears at 9 non-test sites with N ∈ {1,3,5,6} (`eod_dependencies.py:57-74` parents[6], `frontend/__main__.py:14` parents[5], both broker `config.py:23-24` parents[3]) — a file move silently re-points any of them — and the `ALGOTRADING_DATA_ROOT` default is spelled out three times (`eod_dependencies.py:74`, `context.py:75`, `ohlc_backfill.py:123`).

Proposal: keep `load_env_file` as a one-line delegation to python-dotenv (call sites unchanged), delete both parsers and the parser tests; add a small `core.paths` with `repo_root()` (anchored once) and `data_root()` owning the env-var default. Pre-numeric plumbing, zero hash exposure. **Verifier corrections:** the finder's "12+ sites / seven scripts" was inflated — measured 9 sites and 4 scripts; substance stands. Minor semantic deltas to glance at: python-dotenv interpolates `${VAR}` and strips unquoted inline comments by default. Effort S, risk low, ~−125.

### M24 — scripts/ is a 2,004-LOC ungoverned zone including the production EOD entrypoint (scripts-configs lens)

`pyproject.toml:55-63` excludes scripts/ from ruff and `pyproject.toml:93-100` from mypy — yet scripts/ contains `eod_run.py`, the entrypoint the systemd timer fires. Measured consequences: README drift nobody catches (`scripts/README.md:15` and `scripts/README.md:39-46` still say export_sample "does not write the sample yet" while `export_sample.py:79-83` now writes it through the ADR 0039 bridge); dead `noqa` annotations no linter reads (`ibkr_gateway_login.py:102`); the `_REPO_ROOT` preamble copied into 10 of 13 scripts with `smoke_e2e.py:83-88` hand-rolling a different variant; and a real operator hazard — `ohlc_backfill.py:123` honors `ALGOTRADING_DATA_ROOT` but `plot_live_surface.py:32-33` and export_sample hardcode `repo/data`, so relocating the store via the env var (which the EOD runner and BFF both honor) silently plots/exports from the wrong store. Bring scripts into ruff with per-file-ignores and a relaxed mypy override; extract `scripts/_lib.py` (which M23's `core.paths` mostly subsumes); fix the README. Effort M, risk low.

### M18 — CP Gateway keepalive hand-rolled twice in scripts/, with a weaker auth check than the tested class (scripts-configs lens)

`gateway_keepalive.py:32-87` and `eod_babysitter.py:32-58` each re-roll the gateway-URL default (third copy of `session_factory.py:51-98`'s), a raw `httpx.Client(verify=False)`, and auth-status/tickle helpers — ~55 LOC verbatim-equivalent between them — while the in-package `CpRestSession` already provides tested `authenticated()`/`tickle()` (`cp_rest_session.py:92-98`) and a daemon keepalive loop with on_drop (`cp_rest_session.py:141-171`). Concretely weaker: the scripts' check (`authenticated and connected`) omits the `competing`-session guard the canonical `_auth_status_alive` enforces (`cp_rest_session.py:40-49`), so a competing session looks healthy to the babysitter. Rebuild both on `build_gateway_session` (scripts/ is explicitly the sanctioned cross-layer wiring spot per `eod_run.py`'s own header), and collapse the keepalive into the babysitter behind a `--no-fire` flag. **Verifier nuance:** CpRestSession lacks a `/iserver/reauthenticate` method — add one small method or keep that single raw POST to preserve the self-heal. Effort M, risk medium (live ops path), ~−110.

---

## Tests and CI

### M2 — No CI and no task runner: the gate and the smoke walk exist, nothing fires them (scripts-configs lens)

Verified: no `.github/`, no Makefile/justfile, no pre-commit anywhere — while the repo has two GitHub remotes. The full gate is a four-command incantation living only in comments (`pyproject.toml:8-9`, `AGENTS.md:70`), and `scripts/smoke_e2e.py:297-315` is a purpose-built offline CI stage (deterministic fixture replay, BFF probe, npm build, byte-identical-replay invariant, 0/1/2 exit codes) whose own comments *assume* CI exists. The project's headline invariant is checked only when someone remembers.

Proposal: `.github/workflows/gate.yml` on astral-sh/setup-uv (the official uv action, caches the lock) running the four commands plus `smoke_e2e.py --skip-web`, and a justfile (`just` — actively maintained) encoding the named tasks: `just gate`, `just smoke`, `just eod CAL`. Additive only, +~80 lines, nothing removed, no hash surface. Effort S, risk low — the highest honest-impact item in the round for a solo dev: it converts tribal knowledge into one word and makes every push verify reproducibility.

### M10 — Web tests hand-roll the same fetch router four times; adopt msw (test-infra lens)

Four files each hand-roll a "method + path → fixture" fetch router via `vi.stubGlobal` (`Market.test.tsx:29-55`, `Market.boundary.test.tsx:27-49`, `App.test.tsx:35-55`, `RiskScenarios.test.tsx:69-88` plus a third inline variant), re-implementing URL parsing, Response shape, and the not-mocked 500 fallback; the shared helper (`test/http.ts:5-15`) covers only the single-response case, and the copies have drifted (two handle POST + Request objects, two are GET-by-path only — verified). msw (the de-facto standard, actively maintained on 2.x, works in vitest via msw/node) with one `src/test/server.ts` of default handlers replaces all four. The decisive argument: msw intercepts at the network level, so these tests survive the REP3 useFetch→TanStack migration unchanged instead of being coupled to global-fetch stubbing. Effort S, risk low, net ~−60.

### M11 — Promote contract-record builders into the shared fixtures package (test-infra lens)

A shared fixture library already exists and is already imported cross-package (`packages/infra/tests/fixtures/records.py:79-92`, wired via `pyproject.toml:174-176` pythonpath, used by `test_run_api.py` and three infra-ibkr tests). Yet 10 test files re-implement their own ProvenanceStamp builder (grep = exactly 10), 4 hand-build DailyBar, 3+ hand-build MarketStateSnapshot (`test_qc_checks.py:119`); `test_readback_api.py:163-270` alone re-implements five record types (~110 LOC). Any contract field change means editing builders in up to 10 files. Extend `records.py` with keyword-override builders matching its existing "one good record, break one field" design and migrate. Pure consolidation onto an established seam, no library. Effort M, risk low, net ~−150. **Verified:** some cited line numbers had drifted but all 10 builders exist.

### M35 — Split the 1465-line test_readback_api.py along the per-router files that already exist (test-infra + bff lenses, merged)

1465 lines, 51 tests, six routers, 27% of all BFF code+test lines — while `test_surfaces_api.py:1-30` and `test_risk_api.py:1-33` exist as the proper homes but hold only the empty-store cases, so each router's behavior is split across two files by *store state* rather than by router. The monolith privately owns the fixtures any split needs (`seeded_client`, `surface_client`, `_seed_store` at `test_readback_api.py:271-334`) plus the `_JsonRequest` handler-bypass shim (`test_readback_api.py:446-494` — calling a mounted route directly via asyncio.run only because seeding wasn't fixture-shared). Move the fixtures into `conftest.py` (currently 33 lines, `conftest.py:1-33`), split by router, replace the shim with TestClient POSTs. Pure relocation, no assertion changes; new tests stop gravitating into the monolith because the seeded store finally lives in the shared home. Effort M, risk low. (M19 deletes the shim's reason for existing; coordinate.)

### M36 — Four golden-bless workflows behind four env-var spellings — one regen flag (test-infra lens)

Four golden tests copy the same ~12-line regen block under four different env vars: C_REGEN_GOLDEN (`test_determinism_analytics.py:114-125`), RISK_REGEN_GOLDEN (`test_determinism_risk.py:126-137`), ATTRIBUTION_REGEN_GOLDEN (`test_attribution.py:332-343`), F_REGEN_GOLDEN (`test_analytics_projection.py:772-783`). Blessing all goldens after an intentional change means remembering four spellings; the next golden test mints a fifth. One `golden_artifact` fixture in a new infra conftest under a single `--regen-golden` flag; each test keeps its own bespoke comparison assertions (the mixed exact-hash + per-field-tolerance checks are deliberate and fine — which is why pytest-regressions was considered and correctly rejected). Goldens stay byte-identical. Effort S, risk low.

### M39 — Six near-identical _FakeTransport variants — one conftest fake (test-infra lens)

infra-ibkr is the only broker test dir with no conftest.py (saxo and deribit both have one — verified); its 26 test files define 14 ad-hoc fakes, six named `_FakeTransport`, all faking the same transport get/post seam with drifted attribute names (`test_cp_rest_session.py:20-31`, `test_cp_rest_session_established.py:24-37`, `test_cp_rest_discovery.py:23-30`, `test_cp_rest_adapter.py:30-38`, `test_cp_rest_history.py:48-66`, `test_history_backfill.py:71-91`). Faking at the transport seam is the right call (no respx needed); one configurable `FakeCpTransport` in a new conftest ends the minting of fake number seven. Effort S, risk low, honestly small (impact 2) but cheap.

---

## Open questions for the owner

**The contracts plane carries a ~680-LOC mini-pydantic — when, if ever, does it get replaced?** Two finders (validation-config lens, id 30; storage-snapshots lens, id 57) independently measured the same surface: `contracts/validation.py:31-133` re-implements strict type checking (bool-is-not-numeric, finite floats, tz-aware datetimes), `storage/serialization.py:32-144` is a reflective annotation-driven codec re-implementing model_dump/model_validate including the Optional schema-evolution rule, and `contracts/registry.py:1-405` supplies the type-introspection plumbing pydantic ships natively — 682 LOC measured, mirroring pydantic feature-for-feature, and every new contract table (`json_io.py:19-69` adds a third codec; baskets/scenario_attributions/book_greeks all recently extended it) pays the tax again. Both verifiers ruled this real but *not* confirmable without you, for the same two reasons: the seam is explicitly M0-frozen ("nobody else edits it in place") and the JSON-column byte format is the byte-identical-replay anchor — `hash_stability_risk` is honestly true. The finders disagree on staging: id 30 says do nothing until index-analytics contract churn resumes, then do the full TypeAdapter swap behind a golden-bytes round-trip proof; id 57 proposes a Stage 1 now (replace only validation.py's internals behind the existing error contract, zero bytes touched) — but its verifier found Stage 1 less drop-in than claimed (plain `datetime` annotations can't express the tz-aware rule without Annotated overlays, eroding the "tables.py untouched" premise, and full-record strict validation is stricter than today's numeric-fields-only check, changing write-door behavior).

Two concrete questions: **(a)** do you ratify deferring the contracts-plane swap until new contract tables force it, and accept that landing it then means unfreezing the M0 seam behind a golden-bytes equivalence gate? **(b)** is the narrower Stage 1 — pydantic TypeAdapters replacing only `validation.py`'s type/finite/tz checks, with the write-door accept/reject behavior pinned by tests first — allowed now, or does the frozen seam stay untouched entirely until (a)?

---

## Suggested next tasks

Ordered. Each is liftable into `tasks/` as written.

1. ~~**REP11 — Fix the batch-preload key regression**~~ **DONE 2026-06-12, reframed:** M1 was a false positive (invisible `\x1f` byte, not a dropped delimiter — the code was correct). The representation hazard is fixed anyway: array pass-through + `JSON.stringify` cache key + a body-asserting test, landed on `audit-fixes-batch1`. The 77-line module's deletion still happens under REP3.
2. **REP12 — CI gate + justfile** (M2: `.github/workflows/gate.yml` on setup-uv running the four gate commands + `smoke_e2e.py`; justfile for gate/smoke/eod/backfill/login). Depends on: nothing.
3. **REP13 — Dead-code deletion batch** (M6 session.py state machine after re-homing TransportError/BrokerTransport; M21 ib_async modules + test port to `snapshot_to_events`; M29 close-capture twin after relocating IndexBasket; M43 + M24's README fixes; M46 rename). Depends on: nothing. ~−1,200 LOC, all verified dead or lying.
4. **REP14 — BFF leans on FastAPI** (M3 deps.py/error handlers; M19 request models + `_QC_FAIL_STATUSES` dedup; M32 store-read helpers; M41 app-lifetime state). Depends on: coordinate with REP5's work order — M19's date-param half is already REP5 step 4.
5. **REP15 — Broker wire models + close-capture decomposition** (M4 merged 13+16, M12, M40; the `config.py` `_require_*` block executes as a REP6 leaf-package extension alongside M16). Depends on: nothing, but hash-gated — parsers move verbatim into validators, `test_cp_rest_equivalence.py` is the bar.
6. **REP16 — Read-path pushdown** (M7 now — small and provably equivalent; M5 behind its benchmark caveat). Depends on: M5 benchmarks the glob-vs-stat-walk question before deleting heuristics; coordinate with REP2's as_of work.
7. **REP17 — structlog unification** (M8: configure once in core, declare the infra-ibkr phantom dep). Depends on: nothing.
8. **REP18 — Saxo correctness batch** (M13 tick routing — hash-flagged, fix before multi-expiry capture runs in anger; M27 transport collapse; M42 setattr fix; M26 shared WS runner). Depends on: nothing; M22 (Authlib) can ride along or follow.
9. **REP19 — Test-infra consolidation** (M10 msw, M11 shared builders, M35 readback split, M36 regen flag, M39 conftest fake). Depends on: M35 coordinates with REP14's shim deletion.
10. **REP20 — Bootstrap + reproducibility plumbing** (M23 python-dotenv + core.paths; M24 scripts into the gate; M14 PRICER_VERSION/hash helper; M25 core.hashing with golden-hash pins; M31, M44). Depends on: M25 lands with pinning tests in the same commit.
11. **REP21 — openapi-typescript codegen** (M9). Depends on: REP5 landing first — hard dependency.
12. **REP22 — Metadata tier on SQLAlchemy Core** (M17 + M28 together — same records, same tier). Depends on: nothing; pin the ISO-T datetime format.

The remainder (M15, M16, M18, M20, M22, M30, M33, M34, M37, M38, M45) are S/M low-risk items that slot into the above bundles or stand alone as gap-fillers; M16 should ride the next REP6-adjacent touch, M18 the next ops session on the live box.

---

## Appendix — rejected findings (do not re-find)

- **BFF hand-parses query params / `_parse_date` ×4** (validation-config lens) — facts verified, but the entire proposal is verbatim REP5 step 4; the one new sliver (POST batch body model) is inside M19.
- **SaxoTransport verb-block repetition / httpx.Client** (resilience lens) — intra-batch duplicate of M27 (id 22), same file, same lines, same fix; its one unique pointer (copy `CpRestTransport._request` as the in-repo pattern) was merged into M27's task notes.
- **Hand-rolled .env parser duplicates python-dotenv** (collection-runtime lens) — intra-batch duplicate of M23 (id 27), which additionally covers the second private parser in `ibkr_bootstrap.py` and the less disruptive delegation shape.
- **Four backoff engines → tenacity wholesale** (resilience lens) — facts verified, rejected on honest impact: two of the four engines are dead code deleted by M6/REP7 (deletion, not tenacity, fixes the headline), and the surviving CP-REST loops' bespoke parts (OAuth re-sign, Retry-After, terminal fast-fail) survive any migration, collapsing the claimed LOC win to roughly neutral. The defensible subset lives on as M20.

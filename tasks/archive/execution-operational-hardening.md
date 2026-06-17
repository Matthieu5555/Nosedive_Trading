# T-operational-hardening — margin forecast, kill switch, broker reconciliation, alert delivery

> **Source:** TARGET §5.9 + §6 + §7.9 + the 2026-06-08 autonomy audit. Umbrella for the
> operational slice a desk expects; mostly post-week, but S2 needs margin forecasting first.

## The gaps (four sub-lanes, can split into their own tasks when prioritised)
1. **Margin / assignment-capacity forecasting** — S2's line-capacity rule **is** a margin number
   (the course's InvWC); size it up front. Blocks S2 from running safely.
2. **Kill switch** — a book-level switch that flattens a strategy on a drawdown / vol-regime
   trigger (S2's kill condition; §6 requires it).
3. **Broker reconciliation** — broker **cash / position / fill** reconciliation, distinct from the
   internal `risk/reconciliation.py` (which reconciles greeks/positions internally).
4. **Alert delivery** — route the already-detected gateway-disconnect ALARM (keepalive + loud
   capture failure) to Telegram/email + a pre-close check ([[deferred-disconnect-alert]] memory;
   today detected-but-unrouted).

## Depends on
Margin forecast gates S2 live; reconciliation + kill switch gate any non-paper booking
([[execution-fills-position-store]], 3B). Alert delivery is independent and cheap.

## Done criteria
Each sub-lane: margin forecast sizes S2 capacity; kill switch flattens on trigger; broker recon
flags cash/position/fill drift; disconnect ALARM reaches Telegram/email + pre-close check; gate green.

## Sub-lane 3 — broker reconciliation: LANDED (2026-06-16, `execution-recon-diff`)

The recon **diff/tolerance engine + its BFF read endpoint** landed. Scope was held deliberately
deep-not-wide: the diff is the slice; margin / kill-switch / alert delivery are NOT in it (see
follow-ups below).

**Engine** — `packages/infra/src/algotrading/infra/risk/account_reconciliation.py` (pure, infra/risk
layer; distinct from the internal Greek `reconciliation.py`). `reconcile_account(snapshot,
position_set, *, book_fills, tolerance)` compares a broker `BrokerAccountSnapshot`
(positions / cash / fills) against the fills-based book (`PositionSet` + the `Fill` rows) and returns
an `AccountReconciliationReport`:
- **Positions** — joined on the broker conid (`str(BrokerPosition.conid)` ↔ book
  `Position.broker_contract_id`), falling back to `contract_key` when the book line carries no broker
  id. Each line is classified `match` / `break` / `broker_only` / `book_only`; a `break` is a signed
  quantity diff whose absolute value exceeds the versioned `quantity_abs` tolerance. Signs are the
  same convention on both sides (signed long/short), so quantities subtract directly.
- **Cash** — broker `BrokerCashBalance` per currency, surfaced as informational lines (the
  fills-book carries no cash leg, so these are `broker_only` by construction; classified honestly, not
  silently dropped). A non-finite balance is a `break`.
- **Fills** — broker `BrokerFill` joined to book `Fill` on the conid (broker) ↔ `broker_contract_id`
  (book); a fill present on one side only is `broker_only` / `book_only`, a signed-quantity mismatch on
  a matched conid is a `break`.
The report carries per-status counts, the threshold version, and an `ok` flag (no breaks / no
one-sided lines). Tolerances are a versioned `AccountReconciliationTolerance`
(`DEFAULT_ACCOUNT_RECON_TOLERANCE`).

**BFF** — `GET /api/reconciliation` (`apps/frontend/.../routers/reconciliation.py`) reads the persisted
`broker_positions` / `broker_cash_balances` / `broker_fills` tables + the fills ledger, runs the
engine, and serializes the report for the Operations / Risk recon view. Recomputes nothing it can read.

**Tests** — contract test at the broker-vs-book seam (identifiers, sign convention, tolerance edges,
missing-on-one-side); unit cases (match / break / one-sided / empty / boundary-exact / NaN cash);
golden on a fixed two-position + cash + fill snapshot.

### Deferred (named, not built — clean seams left)
- **Margin / assignment-capacity forecasting** (sub-lane 1) — still gates S2 live.
- **Kill switch** (sub-lane 2) — S2 defers to it; not built here.
- **Alert delivery** (sub-lane 4) — the recon report exposes `ok` + per-status break counts, which is
  the natural trigger surface for a future recon-break alert, but no delivery is wired in this slice.

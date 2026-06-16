# Runbook — make Monday's SX5E close capture a certainty

**Owner:** Matthieu · **Target day:** Mon 2026-06-15 (Eurex regular close 17:30 CEST) ·
**Scope:** SX5E only (SPX parked). Serves [`TARGET.md §2.2`](../TARGET.md) — *"verify Monday
before the close that a real run produces the expected term structure + delta band, so the
unattended week of capture is trustworthy, not assumed."*

This is the ordered checklist for tomorrow. It assumes you log in with **your own IBKR
account** (not Vincent's). The pipeline code is healthy (offline `smoke_e2e` green); the only
real risks are **live auth** and **data quality at capture time** — this runbook attacks both.

---

## State as of Sun 2026-06-14 (the starting line)

| Thing | State | Consequence |
|---|---|---|
| Pipeline code | healthy — `uv run python scripts/smoke_e2e.py --skip-web` green | the spine is not the risk |
| Config scope | SX5E `enabled`, conid 4356500 verified; SPX parked | correct vs target |
| Strike grid | ±30Δ, `band_step 0.02`, 8 tenors `10d…3y` (`configs/{universe,qc}.yaml`) | the "right data" shape is configured |
| Auth path | `IBKR_CP_GATEWAY=1` → **browser-cookie gateway path** (daily SMS) | OAuth path is *not* used (see below) |
| OAuth unattended path | env vars set but PEM files `/home/vincent/.secrets/ibkr/*.pem` **missing**, and they are Vincent's account artifacts | not operational — gateway path is the only live path tomorrow |
| Gateway session | java gateway up but **HTTP 401** (logged out) | needs a fresh login tomorrow |
| Scheduler | systemd timers **not installed**; no babysitter running | nothing fires automatically yet |
| Last real run (06-12, **midday**) | QC **FAILED**: `delta_band_completeness` critical=fail; `parity_residual`/`forward_stability` warning-fails up to 110pt | midday data is thin — the **close** run must be *checked*, not assumed |

---

## Step 0 — switch the account to yours (do first)

The gateway login reads `TWS_USERID` / `TWS_PASSWORD` from the repo `.env`. They are currently
Vincent's. Edit `.env` and set them to **your** IBKR username/password (these never get logged
or committed — `.env` is gitignored):

```
TWS_USERID=<your_ibkr_username>
TWS_PASSWORD=<your_ibkr_password>
```

Leave `IBKR_CP_GATEWAY=1` as-is (keeps us on the gateway path). Do **not** touch the
`IBKR_CP_*` OAuth block — it is inert without the PEM files and is a later task (§"Going
unattended" below).

## Step 1 — log in (your phone gets the SMS)

The gateway is running already. Authenticate it with a headless browser login + your SMS:

```bash
# terminal A — stands the login open, waits for the code:
uv run --with selenium python scripts/ibkr_gateway_login.py --mode live \
    --wait-code-file /tmp/sms_code.txt
# terminal B — drop the 6-digit code IBKR texts YOUR phone:
printf '123456' > /tmp/sms_code.txt
```

Confirm it took (must be `authenticated:true, connected:true`, HTTP 200):

```bash
curl -sk https://localhost:5000/v1/api/iserver/auth/status | python -m json.tool
```

## Step 2 — NOON dry-run capture (the canary) — to a TEMP store

Do **not** write the dry-run into canonical `data/` (memory: never smoke-test against the
canonical store). Capture today's session into a throwaway store and inspect it:

```bash
export TMP=$(mktemp -d)
IBKR_CP_GATEWAY=1 ALGOTRADING_DATA_ROOT="$TMP" \
    uv run python scripts/eod_run.py --index SX5E --trade-date 2026-06-15
```

Then check it meets the target shape (this is the "fulfills what we expect" gate):

```bash
# QC verdict — the CRITICAL gates are the ones that must pass ($TMP from above, exported):
uv run python - <<'PY'
import pandas as pd, glob, os
f = glob.glob(f"{os.environ['TMP']}/qc/qc_results/trade_date=2026-06-15/**/*.parquet", recursive=True)
df = pd.concat(pd.read_parquet(p) for p in f)
crit = df[df.severity=="critical"]
print(crit[["check_name","qc_status","measured_value"]].to_string())
print("CRITICAL ALL PASS:", (crit.qc_status=="pass").all())
print("\nwarning fails:", df[(df.severity=="warning")&(df.qc_status=="fail")].check_name.value_counts().to_dict())
PY
```

**Pass criteria for the noon canary:**
- `delta_band_completeness` = **pass** (the 06-12 midday failure — the headline thing to clear)
- `tenor_coverage_floor`, `calendar_sanity`, `underlying_quote_health` = pass
- the 8 tenors `10d…3y` all present in the projected grid
- top-10 SX5E constituents have option-chain snapshots banked (S1 input — `find $TMP/snapshot
  -name 'underlying=*'`; 06-12 banked only SX5E + constituent OHLC, **not** constituent chains —
  verify this lands)

**Expect midday to be imperfect.** `parity_residual` / `forward_stability` warnings on far
tenors are normal on thin midday quotes and clear at the close. If a *critical* gate fails at
noon, that is a real problem to chase before 17:30 (likely a discovery-window or
delta-band-selection issue, not a fluke).

## Step 3 — arm the evening fire (after the canary looks right)

Two options. For tomorrow, the **babysitter** is the better fit — it keeps your fresh gateway
session warm *and* fires at the close:

```bash
setsid bash -c 'uv run python scripts/eod_babysitter.py > /tmp/eod_babysitter.log 2>&1' &
tail -f /tmp/eod_babysitter.log     # shows planned fire time (~17:50 CEST = close + 20min)
```

It tickles the session every 60s (handles the idle timeout), reauthenticates a dropped
brokerage session without a new SMS, and fires `eod_run.py --index SX5E` ~20 min after the
calendar close into canonical `data/`. Per-index result lands in `/tmp/eod_result_SX5E.txt`.

(The durable production alternative — `systemctl --user enable --now eod-capture@XEUR.timer`
from `scripts/systemd/` — is better once we are on the unattended OAuth path; it still needs an
authenticated session at 18:15, which on the gateway path means the babysitter keepalive anyway.)

## Step 4 — verify the banked close (the real deliverable)

After ~17:55 CEST:

```bash
cat /tmp/eod_result_SX5E.txt                          # exit=0 ?
tail -3 data/_run_state.jsonl                          # qc stage outcome for 2026-06-15
# re-run the Step-2 QC check against canonical data/ for trade_date=2026-06-15
```

Close-of-day prints are settled, so the parity/forward warnings should shrink and the critical
gates should be clean. **A green close capture banked into `data/` is the goal** — that is the
first trustworthy day of the unattended week.

---

## Going unattended later — "the API instead of TWS"

We are **already** off TWS: live capture runs entirely on the IBKR **Client Portal Web API
(REST)** (ruling R4; the TWS socket path is not built against). The remaining manual step is the
**daily browser/SMS login** the local CP Gateway requires. The only way to remove it is IBKR's
**OAuth 1.0a** flow (`packages/infra-ibkr`, ADR 0031) — a signed first-party session that needs
no daily login. To stand it up on **your** account:

1. Enrol your account in IBKR's **Self-Service OAuth portal** (consumer key + an access
   token/secret issued against your account). OAuth 1.0a is the only first-party option today;
   OAuth 2.0 for individuals has no ETA.
2. Generate the **signing** and **encryption** RSA keypairs; register the public halves with
   IBKR; keep the private PEMs off git (e.g. `~/.secrets/ibkr/*.pem`).
3. Fill the `.env` OAuth block (`IBKR_CP_CONSUMER_KEY`, `IBKR_CP_ACCESS_TOKEN`,
   `IBKR_CP_ACCESS_TOKEN_SECRET`, `IBKR_CP_SIGNING_KEY_PEM`, `IBKR_CP_ENCRYPTION_KEY_PEM`,
   `IBKR_CP_DH_PRIME`) and **unset `IBKR_CP_GATEWAY`** so the runner takes the OAuth path
   (`live_basket_source`) instead of the cookie gateway. The credential loader fails loudly on a
   partial set, so it is all-or-nothing.
4. Then the `systemd` timer (Step 3 alternative) is genuinely set-and-forget.

Docs: [Web API v1.0](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/) ·
[OAuth 1.0a Extended](https://www.interactivebrokers.com/campus/ibkr-api-page/oauth-1-0a-extended/) ·
[ibind OAuth 1.0a notes](https://github.com/Voyz/ibind/wiki/OAuth-1.0a) (a working reference impl).
This is its own task (`tasks/ibkr-unattended-reauth.md`), not a Monday blocker.

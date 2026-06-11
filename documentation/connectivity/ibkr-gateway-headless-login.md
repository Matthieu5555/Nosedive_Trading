# IBKR Gateway — headless login & keep-alive (server, no GUI)

**TL;DR.** The Client Portal Gateway (`:5000`) needs a daily *browser* login. On a headless
server you drive it with **headless Firefox + Selenium**, completing IBKR's **SMS 2FA** from a
code someone reads off the account phone. Selenium is **not** a project dependency — pull it in
ephemerally with `uv run --with selenium` (Selenium Manager auto-fetches the geckodriver for the
installed Firefox; only `Xvfb` + `firefox` need to be on the box, and Firefox's native
`-headless` means you don't even need `Xvfb`).

```bash
# 0. one-time sanity: these must exist (no install needed beyond Firefox)
which firefox xvfb-run            # firefox is the only hard requirement; -headless avoids Xvfb

# 1. send the SMS and hold the login session open, waiting for the code in a file
uv run --with selenium python scripts/ibkr_gateway_login.py --mode live --wait-code-file /tmp/sms_code.txt &

# 2. someone reads the SMS off the account phone; drop it in (the script enters + submits it)
printf '658661' > /tmp/sms_code.txt

# 3. confirm the session is actually up (read-only)
curl -sk https://localhost:5000/v1/api/iserver/auth/status
#    -> {"authenticated":true,"established":true,"connected":true,"competing":false,...}
```

Interactive variant (prompts on stdin for the code instead of a file):

```bash
uv run --with selenium python scripts/ibkr_gateway_login.py --mode live
```

Credentials come from the repo `.env` (`TWS_USERID` / `TWS_PASSWORD`) — never passed on the
command line. `--mode paper` clicks the **Simulated Login** (Paper) tab. Note: an account with
account-level 2FA gets the **same SMS challenge on both Live and Paper** — paper is not a 2FA
bypass for such accounts.

## Keep the session warm (it idles out in minutes; the SSO cookie in ~a day)

Any authenticated request resets the idle timer; `tickle` is the cheap one. The EOD capture
re-opens the brokerage session (`ssodh/init`) on its own, but across a long wait you want a
heartbeat so the session is still alive at the close:

```bash
# read-only status (use this to watch, not to keep alive)
curl -sk https://localhost:5000/v1/api/iserver/auth/status

# keep-alive (a WRITE — it actively maintains the shared session). Loop every ~90s:
while true; do curl -sk -X POST https://localhost:5000/v1/api/tickle >/dev/null; sleep 90; done &
```

If `authenticated` flips to `false`, the cookie lapsed — re-run the login script (new SMS).

## Fire the EOD option-close capture at the close

The capture is a one-shot meant to run *at each index's session close* (the systemd timer's job
in production; fire it by hand when there's no timer). It writes to the canonical store, so this
is the real harvest, not a smoke test:

```bash
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py --index SPX     # XNYS close 20:00 UTC
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py --index SX5E    # XEUR close 15:30 UTC
IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py --calendar XNYS # all enabled US-close indices
```

`scripts/eod_babysitter.py` (committed alongside) bundles the two: it tickles every 90s and fires
each enabled index's capture just after its close, logging to `/tmp/eod_babysitter.log`. Launch
it detached so it survives your shell:

```bash
setsid bash -c 'uv run python scripts/eod_babysitter.py > /tmp/eod_babysitter.log 2>&1' &
tail -f /tmp/eod_babysitter.log
```

## Useful one-liners / gotchas

```bash
# what's actually listening (CP Gateway :5000 vs the TWS socket 4001/4002 — different products)
ss -ltn | grep -E ':(5000|4001|4002)'

# the running CP Gateway is this Java process; stop it with:
pkill -f GatewayStart

# resolve a contract conid (read-only), e.g. SPX or ESTX50 (Euro Stoxx 50's IBKR symbol)
curl -sk 'https://localhost:5000/v1/api/iserver/secdef/search?symbol=SPX'

# headless login leaves a screenshot for debugging a stuck page:
ls -la /tmp/ibkr_gateway_login.png
```

- **`-headless` vs `Xvfb`.** Firefox's native `-headless` needs no display. Use `Xvfb` /
  `xvfb-run` only if a flow misbehaves headless and you need a real virtual display.
- **`pgrep -f <name>` will match your own shell** if the command line contains `<name>` — never
  `kill $(pgrep -f script.py)` from a shell whose command mentions `script.py`; it kills itself.
- **Self-signed cert.** Every `curl` to `:5000` needs `-k`; Selenium needs `acceptInsecureCerts`.
- See `ibkr-gateway-quickstart.md` for the attended (manual browser) path and `connect-providers.md`
  for the OAuth (`IBKR_CP_*`) unattended path that removes the daily login entirely.

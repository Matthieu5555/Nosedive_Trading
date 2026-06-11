"""Headless browser login for the IBKR Client Portal Gateway (the :5000 cookie session).

The CP Gateway (`clientportal.gw`) needs a *browser* login that lapses ~daily — the
"obligé de se reconnecter tous les jours" pain. There is no GUI on the server, so this
drives the login with **headless Firefox** under Selenium, completing IBKR's **SMS 2FA**
from a code you supply out-of-band. Once it reports `authenticated:true`, the EOD capture
(`IBKR_CP_GATEWAY=1 uv run python scripts/eod_run.py`) and the OHLC backfill run over the
same session. See `documentation/connectivity/ibkr-gateway-headless-login.md` for the full
runbook and the non-Python commands (auth-status check, tickle keep-alive, etc.).

Run it (Selenium is NOT a project dep — pull it in ephemerally; Selenium Manager
auto-fetches the geckodriver for the installed Firefox):

    # 1. send the SMS + stand the session open, waiting for the code in a file:
    uv run --with selenium python scripts/ibkr_gateway_login.py --mode live --wait-code-file /tmp/sms_code.txt
    # 2. in another shell, drop the 6-digit SMS code in:
    printf '658661' > /tmp/sms_code.txt
    # ...the script enters it, submits, and verifies authenticated:true.

Or interactively (it prompts on stdin for the code):

    uv run --with selenium python scripts/ibkr_gateway_login.py --mode live

Credentials come from the repo `.env` (`TWS_USERID` / `TWS_PASSWORD`) — never hardcoded,
never logged. `--mode paper` clicks the "Simulated Login" (Paper) tab; note that for an
account with account-level 2FA, *both* tabs still trigger the same SMS challenge.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATEWAY = "https://localhost:5000"

# Field/selector map of the CP Gateway login page (captured 2026-06-08). The 2FA inputs
# exist in the DOM but are hidden until the username/password step triggers a challenge.
_USERNAME = "xyz-field-username"
_PASSWORD = "xyz-field-password"
_PAPER_TOGGLE = "toggle1"  # checkbox: checked => Paper/Simulated, unchecked => Live
_CODE_FIELDS = (  # tried in order; the visible one is the active 2FA method
    "xyz-field-silver-response",  # SMS security code
    "xyz-field-gold-response",  # IB Key response code
    "xyz-field-temp-response",  # temporary security code
    "xyz-field-bronze-response",  # security-card values
)


def _load_creds() -> tuple[str, str]:
    from algotrading.infra.connectivity.dotenv import load_env_file

    load_env_file(_REPO_ROOT / ".env")
    import os

    user, pw = os.environ.get("TWS_USERID"), os.environ.get("TWS_PASSWORD")
    if not user or not pw or user.startswith("your_"):
        sys.exit("TWS_USERID / TWS_PASSWORD missing or placeholder in .env — fill them first.")
    return user, pw


def _new_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
    except ModuleNotFoundError:
        sys.exit("selenium not installed — run via:  uv run --with selenium python " + __file__)
    opts = Options()
    opts.add_argument("-headless")
    opts.set_capability("acceptInsecureCerts", True)  # the Gateway serves a self-signed cert
    return webdriver.Firefox(options=opts)


def _get_code(args) -> str:
    if args.code:
        return args.code.strip()
    if args.wait_code_file:
        p = Path(args.wait_code_file)
        print(f"  waiting up to {args.code_timeout}s for the SMS code in {p} ...", flush=True)
        for _ in range(args.code_timeout):
            if p.exists() and p.read_text().strip():
                return p.read_text().strip()
            time.sleep(1)
        sys.exit("timed out waiting for the SMS code file.")
    return input("  enter the SMS security code IBKR just texted: ").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Headless IBKR CP Gateway login with SMS 2FA.")
    ap.add_argument("--mode", choices=("live", "paper"), default="live")
    ap.add_argument("--code", help="SMS code (skips the prompt; for non-interactive use)")
    ap.add_argument("--wait-code-file", help="poll this file for the SMS code instead of stdin")
    ap.add_argument("--code-timeout", type=int, default=600, help="seconds to wait for the code")
    args = ap.parse_args()

    from selenium.webdriver.common.by import By

    user, pw = _load_creds()
    d = _new_driver()
    visible = lambda fid: (e := d.find_elements(By.ID, fid)) and e[0].is_displayed()  # noqa: E731
    try:
        d.set_page_load_timeout(45)
        d.get(_GATEWAY)
        time.sleep(5)  # let the SSO page's JS render
        sw = d.find_element(By.ID, _PAPER_TOGGLE)
        want_paper = args.mode == "paper"
        if sw.is_selected() != want_paper:
            d.execute_script("arguments[0].click()", sw)
        print(f"mode={args.mode} (paperSwitch={sw.is_selected()})", flush=True)

        d.find_element(By.ID, _USERNAME).send_keys(user)
        d.find_element(By.ID, _PASSWORD).send_keys(pw)
        # the submit button is "Login" (live) or "Simulated Login" (paper)
        for b in d.find_elements(By.CSS_SELECTOR, "button"):
            if b.is_displayed() and b.text.strip() in ("Login", "Simulated Login"):
                b.click()
                break
        print("credentials submitted — one SMS dispatched.", flush=True)

        # wait for the 2FA field, success, or rejection
        code_field = None
        for _ in range(30):
            time.sleep(2)
            low = d.find_element(By.TAG_NAME, "body").text.lower()
            if "client login succeeds" in low or "you can close" in low:
                print("LOGIN: succeeded with NO 2FA challenge.", flush=True)
                return 0
            for fid in _CODE_FIELDS:
                if visible(fid):
                    code_field = fid
                    break
            if code_field or "resend sms" in low:
                break
            if any(k in low for k in ("invalid username", "invalid password", "login failed")):
                sys.exit(f"LOGIN REJECTED: {d.find_element(By.TAG_NAME, 'body').text[:200]}")
        code_field = code_field or _CODE_FIELDS[0]
        print(f"2FA challenge up (field={code_field}).", flush=True)

        code = _get_code(args)
        el = d.find_element(By.ID, code_field)
        el.clear()
        el.send_keys(code)
        for b in d.find_elements(By.CSS_SELECTOR, "button"):
            if b.is_displayed() and b.text.strip() in ("Login", "Simulated Login"):
                b.click()
                break
        print("code submitted; verifying ...", flush=True)

        for _ in range(20):
            time.sleep(2)
            low = d.find_element(By.TAG_NAME, "body").text.lower()
            if "client login succeeds" in low or "you can close" in low:
                print("LOGIN: authenticated. Verify with the auth-status curl in the runbook.")
                return 0
        print("LOGIN: code submitted but no explicit success banner — check auth/status.")
        return 1
    finally:
        try:
            d.save_screenshot("/tmp/ibkr_gateway_login.png")
        finally:
            d.quit()


if __name__ == "__main__":
    raise SystemExit(main())

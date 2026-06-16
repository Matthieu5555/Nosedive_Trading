"""Headless browser SSO login for the IBKR Client Portal Gateway (the :5000 cookie session).

This is the **low-level browser step only** — it drives IBKR's `clientportal.gw` login page with
headless Firefox until it sees "Client login succeeds". It does NOT open the brokerage session,
so on its own it leaves you *logged in but not ready for data* (see the trap below). For the
**one-command, end-to-end "make me ready for data"** path — status check, login only if needed,
`ssodh/init`, stale-session retry, and verification — run instead:

    uv run --with selenium python scripts/ibkr_login.py

THE TRAP (this cost a past agent ~15 minutes): "Client login succeeds" in the browser is the SSO
*web* layer only. The brokerage (`iserver`) session is still down until something POSTs
`/iserver/auth/ssodh/init`. `auth/status` will read `authenticated:false` / 401 until then.
`ibkr_login.py` does that step for you; this script does not.

The CP Gateway login lapses ~daily. There is no GUI on the server, so this drives the login
headless and completes IBKR's **SMS 2FA** *only if a challenge fires* — in practice an
idle-but-recent session re-logs in with no SMS at all (the no-2FA path below). The session,
tickle, and reauth lifecycle is `infra_ibkr/connectivity/cp_rest_session.py`; the auth-status
check and the `curl` traps are in the "Is the gateway live?" section of
`packages/infra-ibkr/README.md`.

Run it directly (Selenium is NOT a project dep — pull it in ephemerally; Selenium Manager
auto-fetches the geckodriver for the installed Firefox):

    # no SMS expected; if a challenge fires it waits for the code in a file:
    uv run --with selenium python scripts/ibkr_gateway_login.py --mode live \
        --wait-code-file /tmp/sms_code.txt
    # then, only if it reports a 2FA challenge, in another shell:
    printf '658661' > /tmp/sms_code.txt

Or interactively (it prompts on stdin for the code, only if challenged):

    uv run --with selenium python scripts/ibkr_gateway_login.py --mode live

Credentials come from the repo `.env` (`IBKR_USERID` / `IBKR_PASSWORD`, legacy `TWS_*` still
accepted) — never hardcoded, never logged. `--mode paper` clicks the "Simulated Login" (Paper)
tab; for an account with account-level 2FA, *both* tabs trigger the same SMS challenge.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from algotrading.core.paths import load_env_file

_GATEWAY = "https://localhost:5000"
_SCREENSHOT = "/tmp/ibkr_gateway_login.png"

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


class LoginError(RuntimeError):
    """The browser login did not reach the 'Client login succeeds' banner."""


class LoginRejected(LoginError):
    """IBKR rejected the credentials (invalid username/password / login failed)."""


def _load_creds() -> tuple[str, str]:
    load_env_file()  # the repo-root .env; already-exported variables win
    # The IBKR account login (the SAME credential for the website, Client Portal, and this
    # gateway — not a TWS-socket thing). Prefer IBKR_USERID/IBKR_PASSWORD; fall back to the
    # legacy TWS_* names so an un-migrated .env still works.
    user = os.environ.get("IBKR_USERID") or os.environ.get("TWS_USERID")
    pw = os.environ.get("IBKR_PASSWORD") or os.environ.get("TWS_PASSWORD")
    if not user or not pw or user.startswith("your_"):
        sys.exit(
            "IBKR_USERID / IBKR_PASSWORD missing or placeholder in .env — fill them first "
            "(legacy TWS_USERID / TWS_PASSWORD also accepted)."
        )
    return user, pw


def _new_driver() -> Any:
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
    except ModuleNotFoundError:
        sys.exit("selenium not installed — run via:  uv run --with selenium python " + __file__)
    opts = Options()
    opts.add_argument("-headless")
    opts.set_capability("acceptInsecureCerts", True)  # the Gateway serves a self-signed cert
    return webdriver.Firefox(options=opts)


def resolve_2fa_code(
    *, code: str | None = None, wait_code_file: str | None = None, code_timeout: int = 600
) -> str:
    """Resolve the SMS/2FA code: explicit ``code``, then a watched file, then an stdin prompt.

    Reused by both this script's CLI and ``scripts/ibkr_login.py`` so the two never drift on how
    the operator hands a 2FA code in. Called only when a challenge actually fires.
    """
    if code:
        return code.strip()
    if wait_code_file:
        p = Path(wait_code_file)
        print(f"  waiting up to {code_timeout}s for the 2FA code in {p} ...", flush=True)
        for _ in range(code_timeout):
            if p.exists() and p.read_text().strip():
                return p.read_text().strip()
            time.sleep(1)
        raise LoginError(f"timed out waiting for the 2FA code file {p}.")
    return input("  enter the 2FA security code IBKR just texted: ").strip()


def browser_login(
    *,
    mode: str = "live",
    get_code: Callable[[], str] | None = None,
    gateway: str = _GATEWAY,
    screenshot: str = _SCREENSHOT,
) -> bool:
    """Drive the headless-Firefox SSO login to "Client login succeeds". Return True on success.

    The reusable core of this script. ``get_code`` is called only if a 2FA challenge fires; pass
    ``None`` to fail loud (``LoginError``) instead of blocking on a code that nobody will supply.
    Raises :class:`LoginRejected` on bad credentials. A screenshot of the final page is always
    saved to ``screenshot`` for post-mortem.

    NOTE: success here is the SSO *web* layer only — the caller must still open the brokerage
    session (``ssodh/init``); ``scripts/ibkr_login.py`` does that. See the module docstring.
    """
    from selenium.webdriver.common.by import By

    user, pw = _load_creds()
    d = _new_driver()

    def visible(field_id: str) -> bool:
        elements = d.find_elements(By.ID, field_id)
        return bool(elements and elements[0].is_displayed())

    def click_submit() -> None:
        for b in d.find_elements(By.CSS_SELECTOR, "button"):
            if b.is_displayed() and b.text.strip() in ("Login", "Simulated Login"):
                b.click()
                return

    try:
        d.set_page_load_timeout(45)
        d.get(gateway)
        time.sleep(5)  # let the SSO page's JS render
        sw = d.find_element(By.ID, _PAPER_TOGGLE)
        want_paper = mode == "paper"
        if sw.is_selected() != want_paper:
            d.execute_script("arguments[0].click()", sw)
        print(f"mode={mode} (paperSwitch={sw.is_selected()})", flush=True)

        d.find_element(By.ID, _USERNAME).send_keys(user)
        d.find_element(By.ID, _PASSWORD).send_keys(pw)
        click_submit()  # the button is "Login" (live) or "Simulated Login" (paper)
        # NOT "one SMS dispatched" — we have not observed a challenge yet; the next loop decides
        # whether a 2FA field appeared (and only then was an SMS actually sent) or the login
        # succeeded outright. Claiming a dispatch here is an unverified print.
        print("credentials submitted — waiting to see if a 2FA challenge fires.", flush=True)

        # wait for the 2FA field, success, or rejection
        code_field = None
        for _ in range(30):
            time.sleep(2)
            low = d.find_element(By.TAG_NAME, "body").text.lower()
            if "client login succeeds" in low or "you can close" in low:
                print("LOGIN: succeeded with NO 2FA challenge.", flush=True)
                return True
            for fid in _CODE_FIELDS:
                if visible(fid):
                    code_field = fid
                    break
            if code_field or "resend sms" in low:
                break
            if any(k in low for k in ("invalid username", "invalid password", "login failed")):
                raise LoginRejected(d.find_element(By.TAG_NAME, "body").text[:200])
        code_field = code_field or _CODE_FIELDS[0]
        print(f"2FA challenge up (field={code_field}).", flush=True)

        if get_code is None:
            raise LoginError("a 2FA challenge fired but no code provider was supplied")
        code = get_code()
        el = d.find_element(By.ID, code_field)
        el.clear()
        el.send_keys(code)
        click_submit()
        print("code submitted; verifying ...", flush=True)

        for _ in range(20):
            time.sleep(2)
            low = d.find_element(By.TAG_NAME, "body").text.lower()
            if "client login succeeds" in low or "you can close" in low:
                print("LOGIN: succeeded after 2FA.", flush=True)
                return True
        raise LoginError("code submitted but no success banner appeared — check auth/status.")
    finally:
        try:
            d.save_screenshot(screenshot)
        finally:
            d.quit()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Headless IBKR CP Gateway browser SSO login (the login step only — for the "
        "full ready-for-data flow run scripts/ibkr_login.py)."
    )
    ap.add_argument("--mode", choices=("live", "paper"), default="live")
    ap.add_argument("--code", help="2FA code (skips the prompt; for non-interactive use)")
    ap.add_argument("--wait-code-file", help="poll this file for the 2FA code instead of stdin")
    ap.add_argument("--code-timeout", type=int, default=600, help="seconds to wait for the code")
    args = ap.parse_args()

    def get_code() -> str:
        return resolve_2fa_code(
            code=args.code, wait_code_file=args.wait_code_file, code_timeout=args.code_timeout
        )

    try:
        browser_login(mode=args.mode, get_code=get_code)
    except LoginRejected as exc:
        print(f"LOGIN REJECTED: {exc}")
        return 2
    except LoginError as exc:
        print(f"LOGIN FAILED: {exc}")
        return 1
    print(
        "LOGIN: SSO ok. NOT yet ready for data — open the brokerage session with "
        "scripts/ibkr_login.py (or POST /iserver/auth/ssodh/init)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

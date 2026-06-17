"""Smoke-path health check for a deployed close-capture box (D1 deploy-stack ownership).

One runnable command that answers "is this box ready for tonight's close?" by probing the three
things a close needs, through the real code paths (never a curl proxy — see AGENTS.md and
`packages/infra-ibkr/README.md`):

* **gateway authenticated** — `CpRestSession.authenticated()` over the local CP Gateway session
  seam (`build_gateway_session(establish=False)`), the same tested check the babysitter uses. A
  302-to-login off a hand-rolled curl is NOT this check.
* **a capture timer armed** — `systemctl --user list-timers` shows at least one `eod-capture@*`
  timer with a future NEXT fire, so a close will actually be triggered.
* **last capture banked** — the run-state ledger under the canonical store has a fully-healthy
  trade date (`last_healthy_trade_date`), so the previous close landed rather than silently failing.

The check logic is factored into importable, side-effect-light functions returning a `HealthReport`
(the session, the timer-list command, and the store root are all injected) so a test can drive it
against a temp store and a stub session with no wall clock and no real systemd — the `__main__`
blob only wires the real dependencies and maps the verdict to an exit code.

    uv run python scripts/eod_healthcheck.py            # probe this box, human-readable lines
    uv run python scripts/eod_healthcheck.py --json     # same verdict as one JSON object

Exit code: 0 = ready for a close (all three green), 1 = not ready (at least one red). Mirrors the
runner's convention so it slots into a pre-close check or an OnCalendar pre-flight with no wrapper.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from algotrading.core.paths import data_root, load_env_file
from algotrading.infra.orchestration.run_state import last_healthy_trade_date

_TIMER_GLOB = "eod-capture@*"
_TIMER_LIST_CMD = (
    "systemctl",
    "--user",
    "list-timers",
    "--all",
    "--no-legend",
    _TIMER_GLOB,
)


class _AuthSession(Protocol):
    def authenticated(self) -> bool: ...


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def check_gateway_authenticated(
    session_factory: Callable[[], _AuthSession] | None = None,
) -> Check:
    try:
        session: _AuthSession
        if session_factory is None:
            from algotrading.infra_ibkr.session_factory import build_gateway_session

            _transport, session = build_gateway_session(establish=False)
        else:
            session = session_factory()
        authed = bool(session.authenticated())
    except Exception as exc:  # noqa: BLE001 — an unreachable/erroring gateway is a red check, not a crash
        return Check("gateway_authenticated", False, f"could not reach the gateway session: {exc}")
    detail = (
        "CP Gateway session is authenticated (and not competing)"
        if authed
        else "CP Gateway NOT authenticated — run scripts/ibkr_login.py for a fresh SMS login"
    )
    return Check("gateway_authenticated", authed, detail)


def _parse_next_fire(list_timers_stdout: str) -> str | None:
    for line in list_timers_stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("next"):
            continue
        if "n/a" in stripped.lower().split("left")[0]:
            continue
        return stripped
    return None


def check_timer_armed(
    run_list_timers: Callable[[], str] | None = None,
) -> Check:
    runner = run_list_timers or _default_list_timers
    try:
        out = runner()
    except Exception as exc:  # noqa: BLE001 — no systemd / no user bus is a red check, not a crash
        return Check("timer_armed", False, f"could not query systemd user timers: {exc}")
    armed = _parse_next_fire(out)
    if armed is None:
        return Check(
            "timer_armed",
            False,
            "no eod-capture@* timer has a future fire — enable one: "
            "systemctl --user enable --now eod-capture@XEUR.timer",
        )
    return Check("timer_armed", True, f"next capture timer armed: {armed}")


def _default_list_timers() -> str:
    result = subprocess.run(
        _TIMER_LIST_CMD,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def check_last_capture_banked(
    store_root: Path | None = None,
) -> Check:
    root = store_root if store_root is not None else data_root()
    banked: date | None = last_healthy_trade_date(root)
    if banked is None:
        return Check(
            "last_capture_banked",
            False,
            f"no fully-healthy trade date in the run-state ledger under {root} — "
            "the previous close did not bank cleanly (inspect: journalctl --user "
            "-u 'eod-capture@*.service' --since yesterday)",
        )
    return Check("last_capture_banked", True, f"last fully-healthy capture banked: {banked}")


def build_report(
    *,
    session_factory: Callable[[], _AuthSession] | None = None,
    run_list_timers: Callable[[], str] | None = None,
    store_root: Path | None = None,
) -> HealthReport:
    return HealthReport(
        checks=[
            check_gateway_authenticated(session_factory),
            check_timer_armed(run_list_timers),
            check_last_capture_banked(store_root),
        ]
    )


def render_lines(report: HealthReport) -> str:
    lines = [f"deploy health: {'READY' if report.ready else 'NOT READY'} for a close"]
    for c in report.checks:
        lines.append(f"  [{'ok' if c.ok else 'XX'}] {c.name}: {c.detail}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit the verdict as one JSON object")
    args = parser.parse_args(argv)
    load_env_file()
    report = build_report()
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(render_lines(report))
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

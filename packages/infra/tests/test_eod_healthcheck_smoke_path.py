"""Integration: the deploy smoke-path health check as an executable readiness assertion.

D1's unit tests prove each individual check (gateway / timer / banked) green and red. This drives the
whole smoke path through ``eod_healthcheck.main`` against a TEMP store (never canonical ``data/``)
and the injectable seams, asserting the operator contract end to end:

* all three signals green ⇒ READY, exit 0;
* each signal red ONE AT A TIME ⇒ NOT READY, exit 1, with the failing reason surfaced in the
  rendered verdict (so the operator sees *which* signal is down, not just a bare non-zero).

The matrix (one-red-at-a-time) is the part a per-check unit test does not lock: it proves any single
red is sufficient to fail the smoke path and that the red reason reaches the human-readable output.

Also confirms the pre-close readiness PROBE GAP: ``probe_two_sided_fraction`` is still a stub that
returns ``None``, so the pre-close check is conservatively not-ready on the quote signal until the
real probe lands — a fact the test layer pins so a future wiring of the probe is a deliberate change,
not a silent one.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.infra.orchestration.run_state import (
    EOD_STAGES,
    OUTCOME_OK,
    StageRun,
    record_stage,
)

_REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").exists())
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import eod_healthcheck as hc  # noqa: E402

_ARMED_TIMERS = (
    "NEXT                        LEFT     LAST PASSED UNIT                    ACTIVATES\n"
    "Tue 2026-06-17 22:45:00 CEST 5h left  n/a  n/a    eod-capture@XEUR.timer  eod-capture@XEUR.service"
)
_NO_TIMERS = "NEXT LEFT LAST PASSED UNIT\n"
_BANKED_DAY = date(2026, 6, 16)


class _AuthedSession:
    def authenticated(self) -> bool:
        return True


class _UnauthedSession:
    def authenticated(self) -> bool:
        return False


def _bank_healthy_day(root: Path, trade_date: date) -> None:
    for stage in EOD_STAGES:
        record_stage(
            root,
            StageRun(
                trade_date=trade_date,
                stage=stage,
                outcome=OUTCOME_OK,
                run_id="test",
                recorded_ts=datetime.now(UTC),
            ),
        )


def _run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_factory,  # type: ignore[no-untyped-def]
    timers: str,
    store_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str]:
    monkeypatch.setattr(hc, "load_env_file", lambda: None)
    real_build_report = hc.build_report
    monkeypatch.setattr(
        hc,
        "build_report",
        lambda: real_build_report(
            session_factory=session_factory,
            run_list_timers=lambda: timers,
            store_root=store_root,
        ),
    )
    rc = hc.main([])
    out = capsys.readouterr().out
    return rc, out


def test_all_three_green_is_ready_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bank_healthy_day(tmp_path, _BANKED_DAY)
    rc, out = _run_main(
        tmp_path, monkeypatch,
        session_factory=_AuthedSession, timers=_ARMED_TIMERS, store_root=tmp_path, capsys=capsys,
    )
    assert rc == 0
    assert "READY" in out and "NOT READY" not in out


@pytest.mark.parametrize(
    ("label", "session_factory", "timers", "bank", "reason_fragment"),
    [
        ("gateway_red", _UnauthedSession, _ARMED_TIMERS, True, "NOT authenticated"),
        ("timer_red", _AuthedSession, _NO_TIMERS, True, "no eod-capture@* timer"),
        ("banked_red", _AuthedSession, _ARMED_TIMERS, False, "did not bank cleanly"),
    ],
)
def test_each_red_signal_fails_smoke_path_and_surfaces_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label: str,
    session_factory,  # type: ignore[no-untyped-def]
    timers: str,
    bank: bool,
    reason_fragment: str,
) -> None:
    if bank:
        _bank_healthy_day(tmp_path, _BANKED_DAY)
    rc, out = _run_main(
        tmp_path, monkeypatch,
        session_factory=session_factory, timers=timers, store_root=tmp_path, capsys=capsys,
    )
    assert rc == 1, f"{label}: any single red signal must fail the smoke path (exit 1)"
    assert "NOT READY" in out, f"{label}: the verdict must read NOT READY"
    assert reason_fragment in out, (
        f"{label}: the failing reason {reason_fragment!r} must be surfaced in the verdict; got:\n{out}"
    )


def test_preclose_probe_gap_is_conservatively_not_ready() -> None:
    """CONFIRMS THE PROBE GAP: ``probe_two_sided_fraction`` still returns ``None`` (stub, unwired).

    The real lightweight chain-snapshot probe is not landed; until it is, the stub returns ``None`` so
    the pre-close readiness check reports "no quote observation" (conservatively not-ready) rather than
    fabricate a passing fraction. This pins the gap: wiring the probe later is a deliberate change.
    """
    from algotrading.infra_ibkr.preclose_readiness import (
        NO_QUOTE_OBSERVATION,
        evaluate_readiness,
        probe_two_sided_fraction,
    )

    assert probe_two_sided_fraction(session=object(), config=object()) is None

    # The stub's None flows through the pure decision as not-ready on the quote signal, even when the
    # gateway is authenticated — so a deployed box cannot read "ready" off a fabricated fraction.
    verdict = evaluate_readiness(
        authenticated=True,
        two_sided_fraction=probe_two_sided_fraction(session=object(), config=object()),
        min_two_sided_fraction=0.10,
    )
    assert verdict.ready is False
    assert NO_QUOTE_OBSERVATION in verdict.reasons

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

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


class _AuthedSession:
    def authenticated(self) -> bool:
        return True


class _UnauthedSession:
    def authenticated(self) -> bool:
        return False


class _DownSession:
    def authenticated(self) -> bool:
        raise RuntimeError("connection refused")


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


def test_gateway_authenticated_green() -> None:
    check = hc.check_gateway_authenticated(session_factory=_AuthedSession)
    assert check.ok is True


def test_gateway_unauthenticated_red() -> None:
    check = hc.check_gateway_authenticated(session_factory=_UnauthedSession)
    assert check.ok is False
    assert "ibkr_login" in check.detail


def test_gateway_unreachable_is_red_not_raise() -> None:
    check = hc.check_gateway_authenticated(session_factory=_DownSession)
    assert check.ok is False
    assert "connection refused" in check.detail


def test_timer_armed_parses_future_fire() -> None:
    check = hc.check_timer_armed(run_list_timers=lambda: _ARMED_TIMERS)
    assert check.ok is True
    assert "XEUR" in check.detail


def test_timer_armed_red_when_no_timers() -> None:
    check = hc.check_timer_armed(run_list_timers=lambda: "NEXT LEFT LAST PASSED UNIT\n")
    assert check.ok is False


def test_timer_query_failure_is_red_not_raise() -> None:
    def _boom() -> str:
        raise FileNotFoundError("systemctl")

    check = hc.check_timer_armed(run_list_timers=_boom)
    assert check.ok is False


def test_last_capture_banked_green(tmp_path: Path) -> None:
    _bank_healthy_day(tmp_path, date(2026, 6, 16))
    check = hc.check_last_capture_banked(store_root=tmp_path)
    assert check.ok is True
    assert "2026-06-16" in check.detail


def test_last_capture_banked_red_on_empty_store(tmp_path: Path) -> None:
    check = hc.check_last_capture_banked(store_root=tmp_path)
    assert check.ok is False


def test_report_ready_only_when_all_three_green(tmp_path: Path) -> None:
    _bank_healthy_day(tmp_path, date(2026, 6, 16))
    report = hc.build_report(
        session_factory=_AuthedSession,
        run_list_timers=lambda: _ARMED_TIMERS,
        store_root=tmp_path,
    )
    assert report.ready is True
    assert len(report.checks) == 3


def test_report_not_ready_when_any_red(tmp_path: Path) -> None:
    _bank_healthy_day(tmp_path, date(2026, 6, 16))
    report = hc.build_report(
        session_factory=_DownSession,
        run_list_timers=lambda: _ARMED_TIMERS,
        store_root=tmp_path,
    )
    assert report.ready is False


def test_main_exit_code_matches_readiness(tmp_path: Path, monkeypatch) -> None:
    _bank_healthy_day(tmp_path, date(2026, 6, 16))
    monkeypatch.setattr(hc, "load_env_file", lambda: None)
    monkeypatch.setattr(
        hc,
        "build_report",
        lambda: hc.HealthReport(checks=[hc.Check("x", True, "ok")]),
    )
    assert hc.main([]) == 0
    monkeypatch.setattr(
        hc,
        "build_report",
        lambda: hc.HealthReport(checks=[hc.Check("x", False, "no")]),
    )
    assert hc.main(["--json"]) == 1

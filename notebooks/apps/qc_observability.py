import marimo

app = marimo.App(width="full", app_title="QC & Observability")


@app.cell
def _():
    import sys
    from pathlib import Path

    def _apps_dir():
        for p in (Path.cwd(), *Path.cwd().parents):
            if (p / "pyproject.toml").exists() and (p / "packages").is_dir():
                return p / "notebooks" / "apps"
        raise FileNotFoundError("repo root not found")

    sys.path.insert(0, str(_apps_dir()))
    import marimo as mo
    import _shared

    return mo, _shared


@app.cell
def _(_shared):
    import math
    from datetime import datetime, date, timezone
    from statistics import NormalDist

    import numpy as np
    import plotly.graph_objects as go

    _shared.apply_plotly_theme()
    return go, np, math, datetime, date, timezone, NormalDist


@app.cell
def _(_shared, datetime, timezone):
    from algotrading.infra import surfaces, iv, forwards, qc, validation
    from algotrading.infra import orchestration as orch

    CFG = _shared.demo_platform_config("ASML", "EUREX")
    C = _shared.C
    # Fixed clocks — never wall-clock.
    RUN_TS = datetime(2026, 6, 9, 16, 0)
    SNAP_TS = datetime(2026, 6, 9, 16, 0, tzinfo=timezone.utc)
    QCT = qc.thresholds_from_config(CFG.qc_threshold)
    ATH = validation.anomaly_thresholds_from_config(CFG.qc_threshold)
    return surfaces, iv, forwards, qc, validation, orch, CFG, C, RUN_TS, SNAP_TS, QCT, ATH


@app.cell
def _(mo, C):
    def chip(label, value, kind):
        bg = {"success": C["green"], "warn": C["amber"], "danger": C["red"], "info": C["slate600"]}[kind]
        return mo.md(
            f"<div style='background:{bg};color:white;border-radius:8px;"
            f"padding:8px 14px;text-align:center;min-width:120px'>"
            f"<div style='font-size:11px;opacity:.85;text-transform:uppercase;letter-spacing:.5px'>{label}</div>"
            f"<div style='font-size:20px;font-weight:700'>{value}</div></div>"
        )

    STATUS_KIND = {"pass": "success", "normal": "success", "warn": "warn",
                   "fail": "danger", "no_baseline": "info"}
    return chip, STATUS_KIND


@app.cell
def _(mo):
    title = mo.md(
        "# QC & Observability\n"
        "Operations panel driving the QC, validation, alert, dashboard and metrics "
        "machinery on fixed synthetic inputs (engines run directly — no pipeline replay)."
    )
    title
    return (title,)


# =============================================================== PANEL 1 — anomaly
@app.cell
def _(mo):
    an_nonconv = mo.ui.slider(0.0, 0.15, step=0.005, value=0.08, label="solver non-conv ratio (current)")
    an_conf = mo.ui.slider(0.5, 1.0, step=0.01, value=0.88, label="forward confidence (current)")
    an_rmse = mo.ui.slider(0.005, 0.05, step=0.001, value=0.013, label="surface rmse (current)")
    mo.md("### 1 · Anomaly detection — robust-z of current metrics vs baseline")
    return an_nonconv, an_conf, an_rmse


@app.cell
def _(an_nonconv, an_conf, an_rmse):
    an_nonconv, an_conf, an_rmse
    return


@app.cell
def _(np, validation, ATH, RUN_TS, timezone, an_nonconv, an_conf, an_rmse):
    _rng = np.random.default_rng(0)
    an_baselines = {
        "events_per_min": [float(x) for x in _rng.normal(1000, 40, 30)],
        "surface_rmse": [float(x) for x in _rng.normal(0.012, 0.001, 30)],
        "solver_nonconv_ratio": [float(x) for x in _rng.normal(0.02, 0.004, 30)],
        "forward_confidence": [float(x) for x in _rng.normal(0.9, 0.02, 30)],
    }
    an_current = {
        "events_per_min": 1010.0,
        "surface_rmse": float(an_rmse.value),
        "solver_nonconv_ratio": float(an_nonconv.value),
        "forward_confidence": float(an_conf.value),
    }
    an_outcome = validation.run_validation(
        run_id="demo",
        underlying="ASML",
        as_of=RUN_TS.replace(tzinfo=timezone.utc),
        current_metrics=an_current,
        baselines=an_baselines,
        thresholds=ATH,
    )
    an_anoms = sorted(an_outcome.anomalies, key=lambda a: a.metric)
    return an_outcome, an_anoms


@app.cell
def _(mo, chip, STATUS_KIND, an_outcome, an_anoms):
    _rows = [
        {"metric": a.metric, "status": a.status, "value": round(a.value, 4),
         "robust_z": round(a.robust_z, 2), "baseline_n": a.baseline_n}
        for a in an_anoms
    ]
    _n_norm = sum(1 for a in an_anoms if a.status == "normal")
    _n_warn = sum(1 for a in an_anoms if a.status == "warn")
    _n_fail = sum(1 for a in an_anoms if a.status == "fail")
    p1_chips = mo.hstack([
        chip("overall", an_outcome.report.status, STATUS_KIND[an_outcome.report.status]),
        chip("normal", _n_norm, "success"),
        chip("warn", _n_warn, "warn"),
        chip("fail", _n_fail, "danger"),
    ], justify="start", gap=1)
    p1 = mo.vstack([p1_chips, mo.ui.table(_rows, selection=None, pagination=False)])
    p1
    return (p1,)


@app.cell
def _(go, C, an_anoms):
    _cols = {"normal": C["green"], "warn": C["amber"], "fail": C["red"], "no_baseline": C["slate400"]}
    _fig = go.Figure(go.Bar(
        x=[a.metric for a in an_anoms],
        y=[a.robust_z for a in an_anoms],
        marker_color=[_cols.get(a.status, C["slate600"]) for a in an_anoms],
        text=[f"{a.robust_z:.1f}" for a in an_anoms],
        textposition="outside",
    ))
    _fig.add_hline(y=3.5, line_dash="dot", line_color=C["amber"], annotation_text="warn z")
    _fig.add_hline(y=5.0, line_dash="dot", line_color=C["red"], annotation_text="fail z")
    _fig.add_hline(y=-3.5, line_dash="dot", line_color=C["amber"])
    _fig.add_hline(y=-5.0, line_dash="dot", line_color=C["red"])
    _fig.update_layout(title="Robust z-score per metric", height=320, showlegend=False)
    _fig
    return


# =============================================================== PANEL 2 — qc checks
@app.cell
def _(mo):
    qc_noise = mo.ui.slider(0.0, 0.04, step=0.002, value=0.0, label="market price noise std")
    mo.md(
        "### 2 · QC checks on a synthetic SVI slice\n"
        "Solve a flat-vol option chain, fit the slice, run surface-fit + solver-convergence "
        "+ forward-stability checks. Raise noise to push RMSE toward warn/fail."
    )
    return (qc_noise,)


@app.cell
def _(qc_noise):
    qc_noise
    return


@app.cell
def _(np, math, NormalDist, surfaces, iv, forwards, qc, CFG, QCT, RUN_TS, SNAP_TS, date, qc_noise):
    _N = NormalDist().cdf

    def _black(fwd, K, T, sigma, df, right):
        if sigma <= 0 or T <= 0:
            intr = max(fwd - K, 0.0) if right == "C" else max(K - fwd, 0.0)
            return df * intr
        d1 = (math.log(fwd / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if right == "C":
            return df * (fwd * _N(d1) - K * _N(d2))
        return df * (K * _N(-d2) - fwd * _N(-d1))

    _fwd, _df, _T, _sig = 100.0, 0.99, 0.5, 0.22
    _rng = np.random.default_rng(7)
    _strikes = np.linspace(80, 120, 9)
    qc_results = []
    _points = []
    for _K in _strikes:
        _price = _black(_fwd, float(_K), _T, _sig, _df, "C")
        _price *= 1.0 + float(qc_noise.value) * _rng.normal()
        _r = iv.solve_iv(
            max(_price, 1e-6), contract_key=f"ASML-C-{int(_K)}", forward=_fwd,
            strike=float(_K), maturity_years=_T, discount_factor=_df,
            option_right="C", config=CFG.solver,
        )
        qc_results.append(_r)
        _points.append(iv.iv_point(
            _r, snapshot_ts=SNAP_TS, source_snapshot_ts=SNAP_TS, calc_ts=SNAP_TS, config_hashes={}))
    qc_slice = surfaces.fit_slice(
        "ASML", _T, tuple(_points), expiry_date=date(2026, 12, 18),
        day_count="ACT/365F", config=CFG.surface)
    _pairs = tuple(
        forwards.ForwardPair(
            strike=float(_K), call_mid=_black(_fwd, float(_K), _T, _sig, _df, "C"),
            put_mid=_black(_fwd, float(_K), _T, _sig, _df, "P"), liquidity=1.0,
            call_key=f"C-{int(_K)}", put_key=f"P-{int(_K)}")
        for _K in _strikes)
    qc_fwd = forwards.estimate_forward("ASML", _T, _pairs, config=CFG.forward, spot=_fwd * _df)

    _c1 = qc.check_surface_fit_error(qc_slice, thresholds=QCT, run_id="demo", run_ts=RUN_TS)
    _c2 = qc.check_iv_solver_convergence(tuple(qc_results), "ASML-2026-12-18", thresholds=QCT, run_id="demo", run_ts=RUN_TS)
    _c3 = qc.check_forward_stability(qc_fwd, thresholds=QCT, run_id="demo", run_ts=RUN_TS)
    qc_checks = [_c1, _c2, _c3]
    qc_report = qc.build_report(qc_checks, run_id="demo", run_ts=RUN_TS)
    return qc_slice, qc_checks, qc_report


@app.cell
def _(mo, chip, STATUS_KIND, qc, qc_slice, qc_checks, qc_report):
    _esc = qc.escalation_level(qc_report)
    _esc_kind = {"none": "success", "notice": "warn", "page": "danger"}.get(_esc, "info")
    _rows = [
        {"check": c.check_name, "status": c.qc_status, "severity": c.severity,
         "measured": round(c.measured_value, 5)}
        for c in qc_checks
    ]
    p2_chips = mo.hstack([
        chip("overall", qc_report.overall_status, STATUS_KIND[qc_report.overall_status]),
        chip("escalation", _esc, _esc_kind),
        chip("slice method", qc_slice.method, "info"),
        chip("rmse", f"{qc_slice.rmse:.4f}", "info"),
    ], justify="start", gap=1)
    p2 = mo.vstack([p2_chips, mo.ui.table(_rows, selection=None, pagination=False)])
    p2
    return (p2,)


# =============================================================== PANEL 3 — alerts
@app.cell
def _(mo):
    al_silence = mo.ui.slider(0, 600, step=30, value=300, label="collector silence (s)")
    al_fail = mo.ui.slider(0, 6, step=1, value=4, label="failed of last 6 stage runs")
    mo.md("### 3 · Alerts — constructors fired across thresholds")
    return al_silence, al_fail


@app.cell
def _(al_silence, al_fail):
    al_silence, al_fail
    return


@app.cell
def _(orch, qc_report, SNAP_TS, datetime, date, timezone, al_silence, al_fail):
    _last = SNAP_TS - __import__("datetime").timedelta(seconds=float(al_silence.value))
    _a_death = orch.collector_death_alert(
        session_id="ibkr-1", last_event_ts=_last, now=SNAP_TS, silence_seconds=120.0)
    _a_missing = orch.missing_partition_alerts(
        table="iv_points",
        expected=[(date(2026, 6, 9), "ASML"), (date(2026, 6, 9), "SAP")],
        present=[(date(2026, 6, 9), "ASML")])
    _n_fail = int(al_fail.value)
    _outs = [orch.OUTCOME_FAILED] * _n_fail + [orch.OUTCOME_OK] * (6 - _n_fail)
    _runs = tuple(
        orch.StageRun(trade_date=date(2026, 6, 9), stage="qc", outcome=_o,
                      run_id=f"r{_i}", recorded_ts=SNAP_TS)
        for _i, _o in enumerate(_outs))
    _a_rate = orch.elevated_failure_rate_alert(runs=_runs, window=6, max_failure_ratio=0.5)
    _a_qc = orch.qc_fail_alert(qc_report)
    _a_cov = orch.coverage_breach_alerts(qc_report)

    _all = []
    for _x in (_a_death, _a_rate, _a_qc):
        if _x is not None:
            _all.append(_x)
    _all.extend(_a_missing)
    _all.extend(_a_cov)
    alerts = _all
    return (alerts,)


@app.cell
def _(mo, chip, alerts):
    _rows = [
        {"kind": a.kind, "subject": a.subject, "detail": a.detail,
         "detect_interval_s": a.detection_interval_seconds}
        for a in alerts
    ]
    _kind = "danger" if alerts else "success"
    p3_chips = mo.hstack([
        chip("firing", len(alerts), _kind),
    ], justify="start", gap=1)
    _body = mo.ui.table(_rows, selection=None, pagination=False) if alerts else mo.callout("No alerts firing.", kind="success")
    p3 = mo.vstack([p3_chips, _body])
    p3
    return (p3,)


# =============================================================== PANEL 4 — dashboard
@app.cell
def _(mo):
    mo.md("### 4 · Operations dashboard")
    return


@app.cell
def _(orch, qc_report, date):
    dash_status = orch.DashboardStatus(
        trade_date=date(2026, 6, 9),
        data_flowing=True,
        surfaces_building=True,
        qc_status=qc_report.overall_status,
        scenarios_current=True,
        events_total=123456,
        last_healthy_trade_date=date(2026, 6, 9),
        backlog=(),
    )
    return (dash_status,)


@app.cell
def _(mo, chip, STATUS_KIND, orch, dash_status):
    p4_chips = mo.hstack([
        chip("data flowing", "yes" if dash_status.data_flowing else "no",
             "success" if dash_status.data_flowing else "danger"),
        chip("surfaces", "building" if dash_status.surfaces_building else "stalled",
             "success" if dash_status.surfaces_building else "danger"),
        chip("qc", dash_status.qc_status, STATUS_KIND[dash_status.qc_status]),
        chip("scenarios", "current" if dash_status.scenarios_current else "stale",
             "success" if dash_status.scenarios_current else "warn"),
    ], justify="start", gap=1)
    p4_panel = mo.md("```\n" + orch.render_dashboard(dash_status) + "\n```")
    p4 = mo.vstack([p4_chips, p4_panel])
    p4
    return (p4,)


# =============================================================== PANEL 5 — metrics
@app.cell
def _(mo):
    me_events = mo.ui.slider(0, 5000, step=100, value=4200, label="events collected")
    me_solver = mo.ui.slider(0, 20, step=1, value=3, label="solver failures")
    me_stale = mo.ui.slider(0.0, 0.3, step=0.01, value=0.07, label="stale-quote ratio")
    mo.md("### 5 · Metrics registry readout")
    return me_events, me_solver, me_stale


@app.cell
def _(me_events, me_solver, me_stale):
    me_events, me_solver, me_stale
    return


@app.cell
def _(orch, me_events, me_solver, me_stale):
    _reg = orch.build_metrics()
    _reg.events_collected.labels(underlying="ASML").inc(float(me_events.value))
    _reg.solver_failures.labels(underlying="ASML").inc(float(me_solver.value))
    _reg.stale_quote_ratio.labels(underlying="ASML").set(float(me_stale.value))
    metrics_readout = {
        "events_collected_total": orch.sample_value(_reg.registry, "events_collected_total", {"underlying": "ASML"}),
        "solver_failures_total": orch.sample_value(_reg.registry, "solver_failures_total", {"underlying": "ASML"}),
        "stale_quote_ratio": orch.sample_value(_reg.registry, "stale_quote_ratio", {"underlying": "ASML"}),
    }
    return (metrics_readout,)


@app.cell
def _(mo, chip, metrics_readout):
    _stale = metrics_readout["stale_quote_ratio"]
    p5_chips = mo.hstack([
        chip("events", f"{metrics_readout['events_collected_total']:.0f}", "info"),
        chip("solver fails", f"{metrics_readout['solver_failures_total']:.0f}",
             "danger" if metrics_readout["solver_failures_total"] > 5 else "success"),
        chip("stale ratio", f"{_stale:.2f}",
             "danger" if _stale > 0.1 else "success"),
    ], justify="start", gap=1)
    p5_chips
    return (p5_chips,)


if __name__ == "__main__":
    app.run()

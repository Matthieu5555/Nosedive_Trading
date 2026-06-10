import marimo

app = marimo.App(width="full", app_title="Risk & Scenario")


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
    import _shared as shared

    return mo, shared


@app.cell
def _():
    import math

    import numpy as np
    import plotly.graph_objects as go

    return math, np, go


@app.cell
def _(shared):
    shared.apply_plotly_theme()
    from algotrading.infra import risk
    from algotrading.core.config import ScenarioConfig

    return risk, ScenarioConfig


@app.cell
def _(mo):
    mo.md(
        """
        # Risk & Scenario

        A synthetic option book priced through the tested risk engine. Shape the
        market and the legs, then explore scenario PnL, worst case, and the Taylor
        Greek attribution behind the worst shock.
        """
    )
    return


@app.cell
def _(mo):
    spot = mo.ui.slider(start=50, stop=150, step=1, value=100, label="Spot")
    vol = mo.ui.slider(start=0.05, stop=0.8, step=0.01, value=0.25, label="Volatility")
    maturity = mo.ui.slider(start=0.05, stop=2.0, step=0.05, value=0.5, label="Maturity (yrs)")
    rate = mo.ui.slider(start=0.0, stop=0.1, step=0.005, value=0.04, label="Rate")
    mo.hstack([spot, vol, maturity, rate], justify="start", gap=2)
    return spot, vol, maturity, rate


@app.cell
def _(mo):
    qty_call = mo.ui.number(start=-100, stop=100, value=10, label="Long ATM call qty")
    qty_put = mo.ui.number(start=-100, stop=100, value=-10, label="Short ATM put qty")
    qty_otm = mo.ui.number(start=-100, stop=100, value=5, label="Long OTM call qty (K=1.1·S)")
    mo.hstack([qty_call, qty_put, qty_otm], justify="start", gap=2)
    return qty_call, qty_put, qty_otm


@app.cell
def _(math, spot, vol, maturity, rate, qty_call, qty_put, qty_otm, risk):
    _S = float(spot.value)
    _df = math.exp(-rate.value * maturity.value)

    def _val(key, right, strike):
        return risk.ContractValuationInput(
            contract_key=key,
            underlying="DEMO",
            option_right=right,
            exercise_style="european",
            strike=strike,
            maturity_years=float(maturity.value),
            spot=_S,
            carry=0.0,
            volatility=float(vol.value),
            discount_factor=_df,
            multiplier=100.0,
            currency="USD",
            confidence=risk.CONFIDENCE_OK,
        )

    _legs = (
        ("Long ATM call", "C", _S, float(qty_call.value)),
        ("Short ATM put", "P", _S, float(qty_put.value)),
        ("Long OTM call", "C", round(_S * 1.1, 2), float(qty_otm.value)),
    )
    lines = [
        risk.position_risk(
            portfolio_id="demo",
            quantity=q,
            valuation=_val(f"DEMO_{r}_{k}", r, k),
        )
        for (_lbl, r, k, q) in _legs
    ]
    leg_labels = [lbl for (lbl, _r, _k, _q) in _legs]
    return lines, leg_labels


@app.cell
def _(mo, lines):
    _delta = sum(l.position_delta for l in lines)
    _gamma = sum(l.position_gamma for l in lines)
    _vega = sum(l.position_vega for l in lines)
    _theta = sum(l.position_theta for l in lines)
    _mv = sum(l.market_value for l in lines)

    def _card(label, value):
        return mo.md(
            f"<div style='padding:.5rem 1rem'>"
            f"<div style='font-size:.7rem;color:#475569'>{label}</div>"
            f"<div style='font-size:1.4rem;font-weight:600'>{value:,.2f}</div></div>"
        )

    mo.hstack(
        [
            _card("Net Delta", _delta),
            _card("Net Gamma", _gamma),
            _card("Net Vega", _vega),
            _card("Net Theta", _theta),
            _card("Market Value", _mv),
        ],
        justify="start",
        gap=1,
    )
    return


@app.cell
def _(mo, lines, leg_labels):
    _rows = [
        {
            "Leg": lbl,
            "Right": l.valuation.option_right,
            "Strike": l.valuation.strike,
            "Qty": l.quantity,
            "Price": round(l.greeks.price, 4),
            "Pos Delta": round(l.position_delta, 2),
            "Pos Gamma": round(l.position_gamma, 4),
            "Pos Vega": round(l.position_vega, 2),
            "Pos Theta": round(l.position_theta, 2),
            "Mkt Value": round(l.market_value, 2),
        }
        for lbl, l in zip(leg_labels, lines)
    ]
    mo.ui.table(_rows, selection=None)
    return


@app.cell
def _(mo):
    spot_shocks = mo.ui.slider(
        start=0.05, stop=0.4, step=0.01, value=0.1, label="Spot shock ±"
    )
    vol_shocks = mo.ui.slider(
        start=0.01, stop=0.2, step=0.01, value=0.05, label="Vol shock ±"
    )
    mo.hstack([spot_shocks, vol_shocks], justify="start", gap=2)
    return spot_shocks, vol_shocks


@app.cell
def _(ScenarioConfig, risk, spot_shocks, vol_shocks):
    _sp = float(spot_shocks.value)
    _vl = float(vol_shocks.value)
    cfg = ScenarioConfig(
        version="demo",
        spot_shocks=(-_sp, _sp),
        vol_shocks=(_vl, -_vl),
    )
    grid = risk.scenario_grid(cfg)
    return grid,


@app.cell
def _(risk, lines, grid):
    report = risk.build_scenario_report(lines, grid, scenario_version="demo")
    return report,


@app.cell
def _(go, shared, report):
    _ids = [sid for sid, _ in report.totals]
    _pnls = [p for _, p in report.totals]
    _colors = [shared.C["green"] if p >= 0 else shared.C["red"] for p in _pnls]
    _fig = go.Figure(
        go.Bar(
            x=_pnls,
            y=_ids,
            orientation="h",
            marker_color=_colors,
            text=[f"{p:,.0f}" for p in _pnls],
            textposition="outside",
        )
    )
    _fig.update_layout(
        title="Scenario total PnL",
        xaxis_title="PnL (USD)",
        height=420,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    _fig
    return


@app.cell
def _(mo, report):
    _wc = report.worst_case
    mo.callout(
        mo.md(
            f"**Worst case: `{_wc.scenario.scenario_id}`** "
            f"&nbsp; loss = **{_wc.total_pnl:,.2f} USD** "
            f"(spot {_wc.scenario.spot_shock:+.1%}, vol {_wc.scenario.vol_shock:+.2f})"
        ),
        kind="danger",
    )
    return


@app.cell
def _(go, shared, risk, lines, report):
    # Taylor attribution for the worst-case scenario, summed across legs.
    _scn = report.worst_case.scenario
    _terms = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for _l in lines:
        _tt = risk.taylor_terms(
            _l.greeks, spot=_l.valuation.spot, scale=_l.scale, scenario=_scn
        )
        _terms["delta"] += _tt.delta_pnl
        _terms["gamma"] += _tt.gamma_pnl
        _terms["vega"] += _tt.vega_pnl
        _terms["theta"] += _tt.theta_pnl
    # full reprice PnL from the report's worst-case total
    _full = report.worst_case.total_pnl
    _residual = _full - sum(_terms.values())
    _labels = ["delta", "gamma", "vega", "theta", "residual"]
    _vals = [_terms["delta"], _terms["gamma"], _terms["vega"], _terms["theta"], _residual]
    _fig = go.Figure(
        go.Bar(
            x=_labels,
            y=_vals,
            marker_color=shared.DISCRETE[: len(_labels)],
            text=[f"{v:,.0f}" for v in _vals],
            textposition="outside",
        )
    )
    _fig.add_trace(
        go.Bar(
            x=["full reprice"],
            y=[_full],
            marker_color=shared.C["slate600"],
            text=[f"{_full:,.0f}"],
            textposition="outside",
        )
    )
    _fig.update_layout(
        title=f"Taylor Greek attribution — worst case ({_scn.scenario_id})",
        yaxis_title="PnL (USD)",
        showlegend=False,
        height=400,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    _fig
    return


if __name__ == "__main__":
    app.run()

import marimo

app = marimo.App(width="full", app_title="Pricing & Greeks")


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
    import numpy as np
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from algotrading.infra.pricing import from_spot, price

    _shared.apply_plotly_theme()
    return np, go, make_subplots, from_spot, price


@app.cell
def _(mo):
    mo.md(
        """
        # Pricing & Greeks
        European vs. American option pricing driven by the tested lattice engine. Move the controls; the KPI row and curves update reactively.
        """
    )
    return


@app.cell
def _(mo):
    spot = mo.ui.slider(start=50, stop=150, step=1, value=100, label="Spot")
    strike = mo.ui.slider(start=50, stop=150, step=1, value=100, label="Strike")
    maturity = mo.ui.slider(
        start=0.05, stop=2.0, step=0.05, value=0.5, label="Maturity (yrs)"
    )
    vol = mo.ui.slider(start=0.05, stop=0.80, step=0.01, value=0.25, label="Volatility")
    rate = mo.ui.slider(start=0.0, stop=0.10, step=0.005, value=0.04, label="Rate")
    right = mo.ui.dropdown(
        options={"Call": "C", "Put": "P"}, value="Call", label="Right"
    )
    compare_am = mo.ui.checkbox(value=True, label="Compare American")
    sweep = mo.ui.dropdown(
        options=["spot", "vol"], value="spot", label="Sweep axis"
    )
    return spot, strike, maturity, vol, rate, right, compare_am, sweep


@app.cell
def _(mo, spot, strike, maturity, vol, rate, right, compare_am, sweep):
    mo.vstack(
        [
            mo.hstack([spot, strike, maturity, vol, rate], justify="start", gap=2),
            mo.hstack([right, sweep, compare_am], justify="start", gap=2),
        ]
    )
    return


@app.cell
def _(np, from_spot, price):
    def _df(rate, t):
        return float(np.exp(-rate * t))

    def price_one(spot, strike, t, vol, rate, right, style):
        st = from_spot(
            spot=spot,
            strike=strike,
            maturity_years=t,
            volatility=vol,
            discount_factor=_df(rate, t),
            option_right=right,
            carry=0.0,
            exercise_style=style,
        )
        return price(st)

    return (price_one,)


@app.cell
def _(mo, price_one, spot, strike, maturity, vol, rate, right, compare_am):
    def _card(label, value):
        return mo.md(f"**{value:.4f}**<br><span style='color:#475569'>{label}</span>")

    _eu = price_one(
        spot.value, strike.value, maturity.value, vol.value, rate.value,
        right.value, "european",
    )
    _eu_row = mo.hstack(
        [
            _card("Price", _eu.price),
            _card("Delta", _eu.delta),
            _card("Gamma", _eu.gamma),
            _card("Vega", _eu.vega),
            _card("Theta", _eu.theta),
            _card("Rho", _eu.rho),
        ],
        justify="start",
        gap=2,
    )

    _rows = [mo.md("**European**"), _eu_row]
    if compare_am.value:
        _am = price_one(
            spot.value, strike.value, maturity.value, vol.value, rate.value,
            right.value, "american",
        )
        _am_row = mo.hstack(
            [
                _card("Price", _am.price),
                _card("Delta", _am.delta),
                _card("Gamma", _am.gamma),
                _card("Vega", _am.vega),
                _card("Theta", _am.theta),
                _card("Rho", _am.rho),
            ],
            justify="start",
            gap=2,
        )
        _rows += [mo.md("**American**"), _am_row]

    mo.vstack(_rows)
    return


@app.cell
def _(
    np, go, make_subplots, _shared, price_one,
    spot, strike, maturity, vol, rate, right, compare_am, sweep,
):
    _metrics = ["price", "delta", "gamma", "vega", "theta"]

    if sweep.value == "spot":
        _xs = np.linspace(0.6 * strike.value, 1.4 * strike.value, 60)
        _x_title = "Spot"
        _ref = strike.value
        _ref_title = "strike"
        def _state(x):
            return (x, strike.value, maturity.value, vol.value)
    else:
        _xs = np.linspace(0.05, 0.80, 60)
        _x_title = "Volatility"
        _ref = vol.value
        _ref_title = "vol"
        def _state(x):
            return (spot.value, strike.value, maturity.value, x)

    def _curves(style):
        out = {m: [] for m in _metrics}
        for x in _xs:
            _s, _k, _t, _v = _state(x)
            pg = price_one(_s, _k, _t, _v, rate.value, right.value, style)
            for m in _metrics:
                out[m].append(getattr(pg, m))
        return out

    _eu = _curves("european")
    _am = _curves("american") if compare_am.value else None

    _fig = make_subplots(
        rows=2, cols=3, subplot_titles=[m.capitalize() for m in _metrics]
    )
    _pos = {m: (i // 3 + 1, i % 3 + 1) for i, m in enumerate(_metrics)}

    for _m in _metrics:
        _r, _c = _pos[_m]
        _show = _m == "price"
        _fig.add_trace(
            go.Scatter(
                x=_xs, y=_eu[_m], mode="lines", name="European",
                legendgroup="eu", showlegend=_show,
                line={"color": _shared.C["blue"], "width": 2},
            ),
            row=_r, col=_c,
        )
        if _am is not None:
            _fig.add_trace(
                go.Scatter(
                    x=_xs, y=_am[_m], mode="lines", name="American",
                    legendgroup="am", showlegend=_show,
                    line={"color": _shared.C["amber"], "width": 2, "dash": "dash"},
                ),
                row=_r, col=_c,
            )
        _fig.add_vline(
            x=_ref, line={"color": _shared.C["slate400"], "width": 1, "dash": "dot"},
            row=_r, col=_c,
        )
        _fig.update_xaxes(title_text=_x_title, row=_r, col=_c)

    _fig.update_layout(
        height=620,
        title_text=f"Greeks vs. {_x_title}  (ref line at {_ref_title} = {_ref:g})",
    )
    _fig
    return


if __name__ == "__main__":
    app.run()

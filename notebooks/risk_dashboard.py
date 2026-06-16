import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import plotly.graph_objects as go
    from datetime import timedelta

    from algotrading.frontend.context import AppContext
    from algotrading.frontend.store_reads import read_for_underlying
    from algotrading.frontend.basket_scenarios import basket_stress, reconstruct_valuation
    from algotrading.frontend.demo_book import build_book
    from algotrading.infra.risk import basket_risk, position_risk
    from algotrading.infra.risk.scenarios import Scenario
    from algotrading.infra.risk.attribution import attribute_book
    from algotrading.infra.risk.config import AttributionConfig
    from algotrading.infra.signals import realized_volatility
    from algotrading.core.config import load_platform_config

    ctx = AppContext.build()
    return (
        AttributionConfig,
        Scenario,
        attribute_book,
        basket_risk,
        basket_stress,
        build_book,
        ctx,
        go,
        mo,
        np,
        position_risk,
        read_for_underlying,
        realized_volatility,
        reconstruct_valuation,
        load_platform_config,
        timedelta,
    )


@app.cell
def _(build_book, ctx, load_platform_config, read_for_underlying):
    store = ctx.store
    _parts = store.list_partitions("projected_option_analytics")
    _dates = sorted({d for d, _u in _parts})
    trade_date = _dates[-1]
    prev_date = _dates[-2] if len(_dates) > 1 else None
    underlyings = sorted({u for _d, u in _parts})

    # Read every name's option analytics for the latest date. Build the lookups
    # the views key off: all rows (for resolution), the combined-side row per
    # grid cell, the available cells per name, and tenor -> maturity in years.
    all_rows = []
    rows_by_key = {}
    tenor_years = {}
    cells_by_underlying = {}
    for _u in underlyings:
        _rows = read_for_underlying(store, "projected_option_analytics", _u, trade_date=trade_date)
        all_rows.extend(_rows)
        cells_by_underlying[_u] = {
            (r.tenor_label, r.delta_band) for r in _rows if r.surface_side == "combined"
        }
        for _r in _rows:
            tenor_years.setdefault(_r.tenor_label, _r.maturity_years)
            if _r.surface_side == "combined":
                rows_by_key[(_r.underlying, _r.tenor_label, _r.delta_band)] = _r

    # Multiplier / currency per name (all 1.0 / EUR in this store, but read it).
    mult_by, cur_by = {}, {}
    for _u in underlyings:
        for _m in store.read("instrument_master", trade_date=trade_date, underlying=_u):
            if _m.instrument.underlying_symbol == _u:
                mult_by[_u] = _m.instrument.multiplier
                cur_by[_u] = _m.instrument.currency
                break

    book = build_book(cells_by_underlying, trade_date)
    scenario_config = load_platform_config(ctx.configs_dir).scenario
    return (
        all_rows,
        book,
        cur_by,
        mult_by,
        prev_date,
        rows_by_key,
        scenario_config,
        store,
        tenor_years,
        trade_date,
        underlyings,
    )


@app.cell
def _(basket_risk, all_rows, book):
    risk = basket_risk(book, analytics_rows=all_rows, spot_by_underlying={})
    resolved_legs = [lr for lr in risk.legs if lr.resolved]
    return resolved_legs, risk


@app.cell
def _(mo, resolved_legs, trade_date, underlyings):
    mo.md(
        f"# Risk dashboard\n\n"
        f"What's the risk, what explains a move, where can we blow up, and where did vol go — "
        f"for a **constructed book** of {len(resolved_legs)} option legs across {len(underlyings)} "
        f"names, on **{trade_date.isoformat()}**.\n\n"
        f"> ⚠️ **The book is invented, not live positions.** There is no fills/P&L feed in the "
        f"offline store, so the dashboard seeds a plausible vol-seller book (sell at-the-money, own "
        f"crash protection) to have something real to measure. Every number *on* that book is the "
        f"real risk engine — only the positions are synthetic. P&L attribution is therefore a "
        f"**hypothetical scenario**, not a realised day."
    )
    return


@app.cell
def _(mo, underlyings):
    # Always-visible controls. Pick the scope/greek for the risk views and the
    # name/side for the vol views.
    scope_sel = mo.ui.dropdown(
        options={"Whole book": "__book__", **{u: u for u in underlyings}},
        value="Whole book",
        label="Scope (greek ladder + concentration)",
    )
    greek_sel = mo.ui.dropdown(
        options={
            "Delta $": "dollar_delta",
            "Gamma $": "dollar_gamma",
            "Vega $": "dollar_vega",
            "Theta $": "dollar_theta",
        },
        value="Vega $",
        label="Greek",
    )
    vol_name_sel = mo.ui.dropdown(
        options={u: u for u in underlyings},
        value="SX5E" if "SX5E" in underlyings else underlyings[0],
        label="Name (vol views)",
    )
    side_sel = mo.ui.radio(
        options=["combined", "put", "call"], value="combined", label="Smile side", inline=True
    )
    mo.hstack([scope_sel, greek_sel, vol_name_sel, side_sel], justify="start", gap=2)
    return greek_sel, scope_sel, side_sel, vol_name_sel


@app.cell
def _(go, mo, resolved_legs, risk):
    # === The book ===
    _rows = [
        {
            "name": lr.leg.underlying,
            "tenor": lr.leg.tenor_label,
            "band": lr.leg.delta_band,
            "side": lr.leg.side,
            "qty": lr.leg.quantity,
            "delta $": round(lr.dollar_delta, 1),
            "vega $": round(lr.dollar_vega, 1),
            "theta $": round(lr.dollar_theta, 1),
        }
        for lr in resolved_legs
    ]
    _stats = mo.hstack(
        [
            mo.stat(value=f"{risk.dollar_delta:,.0f}", label="Book Delta $"),
            mo.stat(value=f"{risk.dollar_gamma:,.0f}", label="Book Gamma $"),
            mo.stat(value=f"{risk.dollar_vega:,.0f}", label="Book Vega $"),
            mo.stat(value=f"{risk.dollar_theta:,.0f}", label="Book Theta $/day"),
            mo.stat(value=str(len(resolved_legs)), label="Legs"),
        ],
        justify="start",
        gap=2,
    )
    book_view = mo.vstack(
        [
            mo.md(
                "A short-vol book that owns downside protection: net short Delta, Gamma and Vega, "
                "slightly positive Theta. Each row is one leg."
            ),
            _stats,
            mo.ui.table(_rows, selection=None),
        ]
    )
    return (book_view,)


@app.cell
def _(basket_stress, all_rows, book, cur_by, go, mo, np, scenario_config):
    # === ① Scenario heatmap (spot x vol) — the most important PM view ===
    _res = basket_stress(
        book,
        analytics_rows=all_rows,
        multiplier=1.0,
        currency=cur_by.get(book.underlying, "EUR"),
        spot_by_underlying={},
        config=scenario_config,
    )
    _spot = [s * 100.0 for s in _res.spot_axis]
    _vol = [v * 100.0 for v in _res.vol_axis]
    _fig = go.Figure(
        go.Heatmap(
            x=_vol,
            y=_spot,
            z=np.asarray(_res.pnl_grid),
            colorscale="RdBu",
            zmid=0,
            colorbar=dict(title="P&L (EUR)"),
        )
    )
    _fig.add_trace(
        go.Scatter(
            x=[_res.worst_vol_shock * 100.0],
            y=[_res.worst_spot_shock * 100.0],
            mode="markers+text",
            marker=dict(symbol="x", size=16, color="#111827", line=dict(width=2)),
            text=["worst"],
            textposition="top center",
            showlegend=False,
        )
    )
    _fig.update_layout(
        title="Book P&L if spot and vol both move",
        xaxis_title="implied vol shock (vol points)",
        yaxis_title="spot shock (%)",
        height=480,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    scenario_view = mo.vstack(
        [
            mo.md(
                f"Options risk is non-linear, so one delta number lies. Worst case on this grid: "
                f"**{_res.worst_pnl:,.0f} EUR** at spot {_res.worst_spot_shock:+.0%}, "
                f"vol {_res.worst_vol_shock * 100:+.0f} pts. Blue = profit, red = loss."
            ),
            mo.ui.plotly(_fig),
        ]
    )
    return (scenario_view,)


@app.cell
def _(go, greek_sel, mo, resolved_legs, scope_sel, tenor_years):
    # === ② Greeks by expiry bucket — where the risk sits ===
    _attr = greek_sel.value
    _scope = scope_sel.value
    _legs = [
        lr for lr in resolved_legs if _scope == "__book__" or lr.leg.underlying == _scope
    ]
    _buckets = ["0–7d", "1w–1m", "1m–3m", "3m–1y", "1y+"]

    def _bucket(years):
        d = years * 365.25
        if d <= 7:
            return "0–7d"
        if d <= 31:
            return "1w–1m"
        if d <= 93:
            return "1m–3m"
        if d <= 366:
            return "3m–1y"
        return "1y+"

    _sums = {b: 0.0 for b in _buckets}
    for _lr in _legs:
        _y = tenor_years.get(_lr.leg.tenor_label)
        if _y is None:
            continue
        _sums[_bucket(_y)] += getattr(_lr, _attr) or 0.0
    _vals = [_sums[b] for b in _buckets]
    _fig = go.Figure(
        go.Bar(
            x=_buckets,
            y=_vals,
            marker_color=["#dc2626" if v < 0 else "#2563eb" for v in _vals],
        )
    )
    _fig.update_layout(
        title=f"{greek_sel.selected_key} by time to expiry — "
        f"{'whole book' if _scope == '__book__' else _scope}",
        xaxis_title="time to expiry",
        yaxis_title=greek_sel.selected_key,
        height=380,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    greeks_view = mo.vstack(
        [
            mo.md("Which maturities carry the risk. Switch the **Greek** and **Scope** selectors above."),
            mo.ui.plotly(_fig),
        ]
    )
    return (greeks_view,)


@app.cell
def _(mo):
    spot_shock_sel = mo.ui.slider(start=-0.15, stop=0.15, step=0.01, value=-0.05, label="Spot move")
    vol_shock_sel = mo.ui.slider(start=-0.10, stop=0.10, step=0.005, value=0.03, label="Vol move")
    time_shock_sel = mo.ui.slider(start=0.0, stop=0.05, step=0.005, value=0.0, label="Time passing")
    return spot_shock_sel, time_shock_sel, vol_shock_sel


@app.cell
def _(
    AttributionConfig,
    Scenario,
    attribute_book,
    cur_by,
    go,
    mo,
    mult_by,
    position_risk,
    reconstruct_valuation,
    resolved_legs,
    rows_by_key,
    spot_shock_sel,
    time_shock_sel,
    vol_shock_sel,
):
    # === ③ P&L attribution (hypothetical scenario) ===
    _lines = []
    for _lr in resolved_legs:
        _u = _lr.leg.underlying
        _row = rows_by_key.get((_u, _lr.leg.tenor_label, _lr.leg.delta_band))
        if _row is None:
            continue
        _val = reconstruct_valuation(
            _row, multiplier=mult_by.get(_u, 1.0), currency=cur_by.get(_u, "EUR")
        )
        _lines.append(position_risk(portfolio_id="dash", quantity=_lr.leg.quantity, valuation=_val))

    _scen = Scenario(
        scenario_id="dash",
        family="spot_vol",
        spot_shock=spot_shock_sel.value,
        vol_shock=vol_shock_sel.value,
        time_shock=time_shock_sel.value,
    )
    _bk = attribute_book(_lines, _scen, AttributionConfig(version="dash-1"))
    _t = _bk.terms
    _contribs = [
        ("price move (delta)", _t.delta_pnl),
        ("acceleration (gamma)", _t.gamma_pnl),
        ("vol change (vega)", _t.vega_pnl),
        ("time decay (theta)", _t.theta_pnl),
        ("rates (rho)", _t.rho_pnl),
        ("spot×vol (vanna)", _t.vanna_pnl),
        ("vol×vol (volga)", _t.volga_pnl),
    ]
    _labels = [n for n, _ in _contribs] + ["model total", "full recalc", "unexplained"]
    _vals = [v for _, v in _contribs] + [0.0, _bk.full_reprice_pnl - _t.total, _bk.residual]
    _measures = ["relative"] * len(_contribs) + ["total", "relative", "relative"]
    _fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=_measures,
            x=_labels,
            y=_vals,
            connector=dict(line=dict(color="#94a3b8")),
            increasing=dict(marker=dict(color="#16a34a")),
            decreasing=dict(marker=dict(color="#dc2626")),
            totals=dict(marker=dict(color="#2563eb")),
        )
    )
    _fig.update_layout(
        title="Where a hypothetical move's P&L comes from",
        yaxis_title="P&L (EUR)",
        height=460,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    if _bk.within_tolerance:
        _verdict = mo.md(
            f"<span style='color:#16a34a'>✅ The fast model explained this move to within "
            f"**{_bk.residual:,.0f} EUR**. Quick risk numbers can be trusted.</span>"
        )
    else:
        _verdict = mo.md(
            f"<span style='color:#dc2626'>⚠️ **{_bk.residual:,.0f} EUR unexplained** — beyond "
            f"tolerance. Something's off (greeks, surface, or model); use a full recalc.</span>"
        )
    attribution_view = mo.vstack(
        [
            mo.md(
                "Hypothetical: pick a move with the sliders, see which risk factor drives the P&L. "
                "Green adds, red subtracts. _Not a realised day — there is no P&L feed._"
            ),
            mo.hstack([spot_shock_sel, vol_shock_sel, time_shock_sel], justify="start", gap=2),
            mo.ui.plotly(_fig),
            _verdict,
        ]
    )
    return (attribution_view,)


@app.cell
def _(go, greek_sel, mo, resolved_legs):
    # === ④ Concentration — which name actually matters ===
    _attr = greek_sel.value
    _by_name = {}
    for _lr in resolved_legs:
        _by_name[_lr.leg.underlying] = _by_name.get(_lr.leg.underlying, 0.0) + abs(
            getattr(_lr, _attr) or 0.0
        )
    _ranked = sorted(_by_name.items(), key=lambda kv: kv[1], reverse=True)
    _names = [n for n, _ in _ranked]
    _vals = [v for _, v in _ranked]
    _total = sum(_vals) or 1.0
    _top3 = 100.0 * sum(_vals[:3]) / _total
    _fig = go.Figure(go.Bar(x=_names, y=_vals, marker_color="#7c3aed"))
    _fig.update_layout(
        title=f"{greek_sel.selected_key} exposure by name (absolute)",
        xaxis_title="name",
        yaxis_title=f"|{greek_sel.selected_key}|",
        height=380,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    concentration_view = mo.vstack(
        [
            mo.md(
                f"Where the risk hides. Top 3 names hold **{_top3:.0f}%** of the book's "
                f"{greek_sel.selected_key} exposure. Switch the **Greek** selector above."
            ),
            mo.ui.plotly(_fig),
        ]
    )
    return (concentration_view,)


@app.cell
def _(go, mo, prev_date, read_for_underlying, store, trade_date, vol_name_sel):
    # === ⑤ Vol moved — IV change by expiry × strike band ===
    _u = vol_name_sel.value
    _today = read_for_underlying(store, "projected_option_analytics", _u, trade_date=trade_date)
    _today = [r for r in _today if r.surface_side == "combined"]
    _iv_today = {(r.tenor_label, r.delta_band): r.implied_vol for r in _today}
    _meta = {(r.tenor_label, r.delta_band): (r.maturity_years, r.target_delta) for r in _today}

    if prev_date is not None:
        _prev = read_for_underlying(store, "projected_option_analytics", _u, trade_date=prev_date)
        _iv_prev = {
            (r.tenor_label, r.delta_band): r.implied_vol
            for r in _prev
            if r.surface_side == "combined"
        }
    else:
        _iv_prev = {}

    _tenor_years = {}
    _band_delta = {}
    for (_tk, _bk), (_yrs, _dlt) in _meta.items():
        _tenor_years.setdefault(_tk, _yrs)
        _band_delta.setdefault(_bk, _dlt)
    _tenors = sorted({t for t, _b in _iv_today}, key=lambda t: _tenor_years.get(t, 0.0))
    _bands = sorted({b for _t, b in _iv_today}, key=lambda b: _band_delta.get(b, 0.0))
    _show_change = bool(_iv_prev)
    _z = []
    for _t in _tenors:
        _row = []
        for _b in _bands:
            _cur = _iv_today.get((_t, _b))
            if _cur is None:
                _row.append(None)
            elif _show_change and (_t, _b) in _iv_prev:
                _row.append((_cur - _iv_prev[(_t, _b)]) * 100.0)
            elif _show_change:
                _row.append(None)
            else:
                _row.append(_cur * 100.0)
        _z.append(_row)

    _fig = go.Figure(
        go.Heatmap(
            x=_bands,
            y=_tenors,
            z=_z,
            colorscale="RdBu" if _show_change else "Plasma",
            zmid=0 if _show_change else None,
            colorbar=dict(title="ΔIV pts" if _show_change else "IV %"),
        )
    )
    _fig.update_layout(
        title=f"{_u} — {'implied vol change ' + prev_date.isoformat() + ' → ' + trade_date.isoformat() if _show_change else 'implied vol level'}",
        xaxis_title="strike band (puts ◄ ► calls)",
        yaxis_title="expiry",
        height=420,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    _note = (
        "Red = vol fell, blue = vol rose, since the prior capture."
        if _show_change
        else "Only one capture date banked, so this shows the IV level, not the change."
    )
    volchange_view = mo.vstack([mo.md(_note), mo.ui.plotly(_fig)])
    return (volchange_view,)


@app.cell
def _(go, mo, read_for_underlying, side_sel, store, trade_date, vol_name_sel):
    # === ⑥ Smile — skew shape, at most 3 expiries ===
    _u = vol_name_sel.value
    _side = side_sel.value
    _rows = [
        r
        for r in read_for_underlying(store, "projected_option_analytics", _u, trade_date=trade_date)
        if r.surface_side == _side
    ]
    _by_tenor = {}
    for _r in _rows:
        _by_tenor.setdefault(_r.tenor_label, []).append(_r)
    _ordered = sorted(_by_tenor.items(), key=lambda kv: kv[1][0].maturity_years)

    # Keep 3 expiries: nearest to 1m, 3m, 1y, so the chart stays readable.
    _targets = [1 / 12, 3 / 12, 1.0]
    _picked = []
    for _tgt in _targets:
        if not _ordered:
            break
        _best = min(_ordered, key=lambda kv: abs(kv[1][0].maturity_years - _tgt))
        if _best not in _picked:
            _picked.append(_best)

    _fig = go.Figure()
    for _tenor, _cells in _picked:
        _cells = sorted(_cells, key=lambda c: c.log_moneyness)
        _fig.add_trace(
            go.Scatter(
                x=[c.log_moneyness for c in _cells],
                y=[c.implied_vol * 100.0 for c in _cells],
                mode="lines+markers",
                name=f"{_tenor} ({_cells[0].maturity_years:.2f}y)",
            )
        )
    _fig.update_layout(
        title=f"{_u} smile — {_side} side",
        xaxis_title="moneyness (downside ◄ ► upside)",
        yaxis_title="implied vol (%)",
        height=420,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    smile_view = mo.vstack(
        [
            mo.md("Is skew steep, flat or inverted? Down-sloping left-to-right = the usual put skew."),
            mo.ui.plotly(_fig) if _picked else mo.md(f"**No {_side}-side cells for {_u}.**"),
        ]
    )
    return (smile_view,)


@app.cell
def _(
    go,
    mo,
    read_for_underlying,
    realized_volatility,
    store,
    trade_date,
    vol_name_sel,
):
    # === ⑦ Implied vs realized vol ===
    _u = vol_name_sel.value
    try:
        _bars = sorted(store.read("daily_bar", underlying=_u), key=lambda b: b.trade_date)
    except Exception:
        _bars = []
    _closes = [b.close for b in _bars if b.close and b.close > 0]
    _dates = [b.trade_date for b in _bars if b.close and b.close > 0]

    _win = 30
    _rv_dates, _rv = [], []
    for _i in range(_win, len(_closes)):
        try:
            _rv.append(realized_volatility(_closes[_i - _win : _i]) * 100.0)
            _rv_dates.append(_dates[_i])
        except Exception:
            continue
    _rv_dates, _rv = _rv_dates[-180:], _rv[-180:]

    # Implied: ATM (or nearest) on the captured dates.
    _opt = read_for_underlying(store, "projected_option_analytics", _u, trade_date=trade_date)
    _atm = [r for r in _opt if r.surface_side == "combined" and r.delta_band in ("atm", "atmp")]
    _iv = min(_atm, key=lambda r: abs(r.maturity_years - _win / 365.25)).implied_vol * 100.0 if _atm else None

    _fig = go.Figure()
    if _rv:
        _fig.add_trace(
            go.Scatter(x=_rv_dates, y=_rv, mode="lines", line=dict(color="#2563eb"), name="30d realized")
        )
    if _iv is not None:
        _fig.add_trace(
            go.Scatter(
                x=[trade_date],
                y=[_iv],
                mode="markers",
                marker=dict(color="#dc2626", size=12, symbol="diamond"),
                name="implied (ATM ~1m)",
            )
        )
    _fig.update_layout(
        title=f"{_u} — realized vs implied volatility",
        xaxis_title="date",
        yaxis_title="annualized vol (%)",
        height=400,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    _verdict = ""
    if _iv is not None and _rv:
        _gap = _iv - _rv[-1]
        _verdict = (
            f" Implied is **{_gap:+.1f} pts** vs last realized — "
            + ("options look expensive (lean to selling)." if _gap > 0 else "options look cheap (lean to buying).")
        )
    ivrv_view = mo.vstack(
        [
            mo.md(
                "Are we buying expensive vol or selling cheap vol? Realized has deep history; "
                "implied is only the captured dates so far." + _verdict
            ),
            mo.ui.plotly(_fig) if (_rv or _iv is not None) else mo.md(f"**No price history for {_u}.**"),
        ]
    )
    return (ivrv_view,)


@app.cell
def _(
    attribution_view,
    book_view,
    concentration_view,
    greeks_view,
    ivrv_view,
    mo,
    scenario_view,
    smile_view,
    volchange_view,
):
    mo.accordion(
        {
            "📓  The book — constructed, not live positions": book_view,
            "①  Scenario heatmap — what if spot & vol both move  ‹most important›": scenario_view,
            "②  Greeks by expiry — where the risk sits": greeks_view,
            "③  P&L attribution — hypothetical scenario": attribution_view,
            "④  Concentration — which name matters": concentration_view,
            "⑤  Vol moved — IV change by expiry × strike": volchange_view,
            "⑥  Smile — skew shape": smile_view,
            "⑦  Implied vs realized vol": ivrv_view,
        },
        multiple=True,
    )
    return


if __name__ == "__main__":
    app.run()

import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go

    from algotrading.frontend.context import AppContext
    from algotrading.frontend.store_reads import read_for_underlying
    from algotrading.frontend.basket_scenarios import reconstruct_valuation
    from algotrading.infra.risk import position_risk
    from algotrading.infra.risk.scenarios import Scenario
    from algotrading.infra.risk.attribution import attribute_book
    from algotrading.infra.risk.config import AttributionConfig

    ctx = AppContext.build()
    return (
        AttributionConfig,
        Scenario,
        attribute_book,
        ctx,
        go,
        mo,
        position_risk,
        read_for_underlying,
        reconstruct_valuation,
    )


@app.cell
def _(ctx, mo):
    _parts = ctx.store.list_partitions("projected_option_analytics")
    _dates = sorted({d for d, _u in _parts}, reverse=True)
    _unds = sorted({u for _d, u in _parts})

    date_sel = mo.ui.dropdown(
        options={d.isoformat(): d for d in _dates},
        value=_dates[0].isoformat() if _dates else None,
        label="Trade date",
    )
    und_sel = mo.ui.dropdown(
        options={u: u for u in _unds},
        value=ctx.default_underlying if ctx.default_underlying in _unds else (_unds[0] if _unds else None),
        label="Underlying",
    )
    spot_shock_sel = mo.ui.slider(
        start=-0.15, stop=0.15, step=0.01, value=-0.05, label="Price move"
    )
    vol_shock_sel = mo.ui.slider(
        start=-0.10, stop=0.10, step=0.005, value=0.03, label="Volatility change"
    )
    time_shock_sel = mo.ui.slider(
        start=0.0, stop=0.05, step=0.005, value=0.0, label="Time passing"
    )
    mo.md(
        "## Where the P&L comes from\n\n"
        "**When the market moves, this splits the resulting profit or loss into the piece each risk "
        "factor contributed.** Green bars add money, red bars lose it. Drag the sliders to set the move.\n\n"
        "**The last bar, _unexplained_, is the one to watch:** it's what our fast risk model missed "
        "compared with a full recalculation. Small means the quick numbers can be trusted.\n\n"
        f"{mo.hstack([und_sel, date_sel], justify='start', gap=2)}\n"
        f"{mo.hstack([spot_shock_sel, vol_shock_sel, time_shock_sel], justify='start', gap=2)}"
    )
    return date_sel, spot_shock_sel, time_shock_sel, und_sel, vol_shock_sel


@app.cell
def _(ctx, date_sel, read_for_underlying, und_sel):
    underlying = und_sel.value
    trade_date = date_sel.value

    rows = read_for_underlying(
        ctx.store, "projected_option_analytics", underlying, trade_date=trade_date
    )
    masters = ctx.store.read(
        "instrument_master", trade_date=trade_date, underlying=underlying
    )
    mult = cur = None
    for _m in masters:
        if _m.instrument.underlying_symbol == underlying:
            mult, cur = _m.instrument.multiplier, _m.instrument.currency
            break

    seen: dict = {}
    for _r in rows:
        seen.setdefault(f"{_r.underlying}|{_r.tenor_label}|{_r.delta_band}", _r)
    picked = list(seen.values())
    return cur, mult, picked, trade_date, underlying


@app.cell
def _(
    AttributionConfig,
    Scenario,
    attribute_book,
    cur,
    mult,
    picked,
    position_risk,
    reconstruct_valuation,
    spot_shock_sel,
    time_shock_sel,
    vol_shock_sel,
):
    lines = [
        position_risk(
            portfolio_id="nb",
            quantity=1.0,
            valuation=reconstruct_valuation(
                r, multiplier=mult or 1.0, currency=cur or "EUR"
            ),
        )
        for r in picked
    ]
    scen = Scenario(
        scenario_id="nb",
        family="spot_vol",
        spot_shock=spot_shock_sel.value,
        vol_shock=vol_shock_sel.value,
        time_shock=time_shock_sel.value,
    )
    book = attribute_book(lines, scen, AttributionConfig(version="nb-1"))
    terms = book.terms
    return book, terms


@app.cell
def _(book, cur, go, mo, terms, trade_date, underlying):
    _unit = cur or "EUR"
    _contributions = [
        ("price move (delta)", terms.delta_pnl),
        ("price acceleration (gamma)", terms.gamma_pnl),
        ("vol change (vega)", terms.vega_pnl),
        ("time decay (theta)", terms.theta_pnl),
        ("rates (rho)", terms.rho_pnl),
        ("spot×vol (vanna)", terms.vanna_pnl),
        ("vol×vol (volga)", terms.volga_pnl),
    ]
    _labels = [name for name, _v in _contributions]
    _values = [val for _n, val in _contributions]

    _measures = ["relative"] * len(_contributions)
    _labels = _labels + ["model total", "full recalc", "unexplained"]
    _values = _values + [0.0, book.full_reprice_pnl - terms.total, book.residual]
    _measures = _measures + ["total", "relative", "relative"]

    _fig = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=_measures,
            x=_labels,
            y=_values,
            connector=dict(line=dict(color="#94a3b8")),
            increasing=dict(marker=dict(color="#16a34a")),
            decreasing=dict(marker=dict(color="#dc2626")),
            totals=dict(marker=dict(color="#2563eb")),
        )
    )
    _fig.update_layout(
        title=f"{underlying} scenario P&L decomposition — {trade_date.isoformat()} ({_unit})",
        yaxis_title=f"P&L ({_unit})",
        height=520,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    waterfall_view = mo.ui.plotly(_fig)
    waterfall_view
    return (waterfall_view,)


@app.cell
def _(book, cur, mo, terms):
    _unit = cur or "EUR"
    term_rows = [
        {"term": "delta", f"P&L ({_unit})": terms.delta_pnl},
        {"term": "gamma", f"P&L ({_unit})": terms.gamma_pnl},
        {"term": "vega", f"P&L ({_unit})": terms.vega_pnl},
        {"term": "theta", f"P&L ({_unit})": terms.theta_pnl},
        {"term": "rho", f"P&L ({_unit})": terms.rho_pnl},
        {"term": "vanna", f"P&L ({_unit})": terms.vanna_pnl},
        {"term": "volga", f"P&L ({_unit})": terms.volga_pnl},
        {"term": "model total", f"P&L ({_unit})": terms.total},
        {"term": "full recalc", f"P&L ({_unit})": book.full_reprice_pnl},
        {"term": "unexplained", f"P&L ({_unit})": book.residual},
    ]
    term_table = mo.ui.table(term_rows, selection=None)
    term_table
    return (term_table,)


@app.cell
def _(book, cur, mo):
    _unit = cur or "EUR"
    if book.within_tolerance:
        verdict_view = mo.md(
            f"<span style='color:#16a34a'>✅ **The fast model explained this move to within "
            f"{book.residual:.2f} {_unit}.** The quick risk numbers can be trusted here.</span>"
        )
    else:
        verdict_view = mo.md(
            f"<span style='color:#dc2626'>⚠️ **{book.residual:.2f} {_unit} of P&L is left unexplained** "
            f"— beyond the safe margin. Use a full recalculation for this position.</span>"
        )
    verdict_view
    return (verdict_view,)


if __name__ == "__main__":
    app.run()

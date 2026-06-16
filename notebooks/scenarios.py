import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import plotly.graph_objects as go

    from algotrading.frontend.context import AppContext
    from algotrading.frontend.store_reads import read_for_underlying
    from algotrading.frontend.basket_scenarios import basket_stress
    from algotrading.infra.contracts import Basket, BasketLeg
    from algotrading.core.config import load_platform_config

    ctx = AppContext.build()
    scenario_config = load_platform_config(ctx.configs_dir).scenario
    return (
        Basket,
        BasketLeg,
        basket_stress,
        ctx,
        go,
        mo,
        np,
        read_for_underlying,
        scenario_config,
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
    mo.md(f"## Risk scenarios\n\n**What happens to this position if the market moves against it?** The heatmap below shows profit (blue) and loss (red) across a grid of possible moves — price along one axis, volatility along the other. The black ✕ marks the worst case.\n{mo.hstack([und_sel, date_sel], justify='start', gap=2)}")
    return date_sel, und_sel


@app.cell
def _(ctx, date_sel, read_for_underlying, und_sel):
    underlying = und_sel.value
    trade_date = date_sel.value

    rows = read_for_underlying(
        ctx.store, "projected_option_analytics", underlying, trade_date=trade_date
    )
    cells = sorted({(r.tenor_label, r.delta_band) for r in rows})

    masters = ctx.store.read(
        "instrument_master", trade_date=trade_date, underlying=underlying
    )
    multiplier = currency = None
    for _m in masters:
        if _m.instrument.underlying_symbol == underlying:
            multiplier = _m.instrument.multiplier
            currency = _m.instrument.currency
            break
    return cells, currency, multiplier, rows, trade_date, underlying


@app.cell
def _(cells, mo):
    leg_labels = {f"{t} · {b}": (t, b) for t, b in cells}
    _keys = list(leg_labels.keys())
    _default = _keys[len(_keys) // 2] if _keys else None

    leg1_sel = mo.ui.dropdown(
        options=leg_labels,
        value=_default,
        label="Leg 1 cell",
    )
    side1_sel = mo.ui.radio(
        options=["long", "short"], value="long", label="Leg 1 side", inline=True
    )
    qty1_sel = mo.ui.slider(
        start=1, stop=10, step=1, value=1, label="Leg 1 quantity"
    )

    leg2_sel = mo.ui.dropdown(
        options=leg_labels,
        value=_default,
        label="Leg 2 cell",
    )
    side2_sel = mo.ui.radio(
        options=["long", "short"], value="short", label="Leg 2 side", inline=True
    )
    qty2_sel = mo.ui.slider(
        start=1, stop=10, step=1, value=1, label="Leg 2 quantity"
    )

    mo.vstack(
        [
            mo.hstack([leg1_sel, side1_sel, qty1_sel], justify="start", gap=2),
            mo.hstack([leg2_sel, side2_sel, qty2_sel], justify="start", gap=2),
        ]
    )
    return (
        leg1_sel,
        leg2_sel,
        qty1_sel,
        qty2_sel,
        side1_sel,
        side2_sel,
    )


@app.cell
def _(
    Basket,
    BasketLeg,
    basket_stress,
    currency,
    leg1_sel,
    leg2_sel,
    multiplier,
    qty1_sel,
    qty2_sel,
    rows,
    scenario_config,
    side1_sel,
    side2_sel,
    trade_date,
    underlying,
):
    def _leg(cell, side, qty):
        _t, _b = cell
        _signed = -float(qty) if side == "short" else float(qty)
        return BasketLeg(
            instrument_kind="option",
            side=side,
            quantity=_signed,
            underlying=underlying,
            tenor_label=_t,
            delta_band=_b,
        )

    legs = (
        _leg(leg1_sel.value, side1_sel.value, qty1_sel.value),
        _leg(leg2_sel.value, side2_sel.value, qty2_sel.value),
    )
    basket = Basket(
        basket_id="nb-scenarios",
        trade_date=trade_date,
        underlying=underlying,
        legs=legs,
        provider=None,
    )
    result = basket_stress(
        basket,
        analytics_rows=rows,
        multiplier=multiplier,
        currency=currency,
        spot_by_underlying={},
        config=scenario_config,
    )
    return (result,)


@app.cell
def _(currency, mo, result):
    metrics_row = mo.hstack(
        [
            mo.stat(
                value=f"{result.worst_pnl:,.2f} {currency or ''}".strip(),
                label="Worst-case P&L",
            ),
            mo.stat(value=str(result.n_legs), label="Legs"),
            mo.stat(value=str(result.n_resolved), label="Resolved"),
            mo.stat(
                value=f"{result.worst_spot_shock:+.0%} / {result.worst_vol_shock:+.0%}",
                label="Worst spot / vol shock",
            ),
        ],
        justify="start",
        gap=2,
    )
    metrics_row
    return


@app.cell
def _(mo, result):
    if result.gaps:
        gaps_view = mo.md(
            "**Unresolved legs (gaps):**\n\n"
            + "\n".join(f"- {g}" for g in result.gaps)
        )
    else:
        gaps_view = mo.md("")
    gaps_view
    return


@app.cell
def _(go, mo, np, result, trade_date, underlying):
    _spot_pct = [s * 100.0 for s in result.spot_axis]
    _vol_pct = [v * 100.0 for v in result.vol_axis]
    _grid = np.asarray(result.pnl_grid)

    _fig = go.Figure(
        go.Heatmap(
            x=_vol_pct,
            y=_spot_pct,
            z=_grid,
            colorscale="RdBu",
            zmid=0,
            colorbar=dict(title="P&L"),
        )
    )
    _fig.add_trace(
        go.Scatter(
            x=[result.worst_vol_shock * 100.0],
            y=[result.worst_spot_shock * 100.0],
            mode="markers+text",
            marker=dict(symbol="x", size=16, color="#111827", line=dict(width=2)),
            text=["worst"],
            textposition="top center",
            showlegend=False,
        )
    )
    _fig.update_layout(
        title=f"{underlying} stress surface — {trade_date.isoformat()}  (P&L over ±spot × ±vol)",
        xaxis_title="vol shock (%)",
        yaxis_title="spot shock (%)",
        height=520,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    surface_view = mo.ui.plotly(_fig)
    surface_view
    return


if __name__ == "__main__":
    app.run()

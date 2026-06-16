import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go

    from algotrading.frontend.context import AppContext
    from algotrading.frontend.store_reads import read_for_underlying
    from algotrading.infra.contracts import Basket, BasketLeg
    from algotrading.infra.risk import basket_risk

    ctx = AppContext.build()
    return (
        Basket,
        BasketLeg,
        basket_risk,
        ctx,
        go,
        mo,
        read_for_underlying,
    )


@app.cell
def _(ctx, mo):
    _parts = ctx.store.list_partitions("projected_option_analytics")
    _dates = sorted({d for d, _u in _parts}, reverse=True)
    _unds = sorted({u for _d, u in _parts})

    und_sel = mo.ui.dropdown(
        options={u: u for u in _unds},
        value=ctx.default_underlying if ctx.default_underlying in _unds else (_unds[0] if _unds else None),
        label="Underlying",
    )
    date_sel = mo.ui.dropdown(
        options={d.isoformat(): d for d in _dates},
        value=_dates[0].isoformat() if _dates else None,
        label="Trade date",
    )
    template_sel = mo.ui.radio(
        options=["Straddle", "Strangle", "Risk reversal"],
        value="Straddle",
        label="Strategy template",
        inline=True,
    )
    tenor_sel = mo.ui.dropdown(
        options={t: t for t in ("1m", "3m", "6m")},
        value="1m",
        label="Tenor",
    )
    qty_sel = mo.ui.number(start=1.0, stop=1000.0, step=1.0, value=10.0, label="Quantity")

    mo.md(
        f"## Basket builder\n\n"
        f"**Build a simple options strategy and see its risk.** Pick a template and size it; the table "
        f"shows how the basket makes or loses money when the market moves (its dollar Greeks).\n"
        f"{mo.hstack([und_sel, date_sel, tenor_sel, qty_sel], justify='start', gap=2)}\n\n"
        f"{template_sel}"
    )
    return date_sel, qty_sel, template_sel, tenor_sel, und_sel


@app.cell
def _(BasketLeg, qty_sel, template_sel, tenor_sel, und_sel):
    underlying = und_sel.value
    tenor = tenor_sel.value
    template = template_sel.value
    qty = float(qty_sel.value)

    def _leg(side, band, signed_qty):
        return BasketLeg(
            instrument_kind="option",
            side=side,
            quantity=signed_qty,
            underlying=underlying,
            tenor_label=tenor,
            delta_band=band,
        )

    if template == "Straddle":
        legs = (_leg("long", "atm", qty), _leg("long", "atmp", qty))
    elif template == "Strangle":
        legs = (_leg("long", "30dc", qty), _leg("long", "30dp", qty))
    else:
        legs = (_leg("long", "30dc", qty), _leg("short", "30dp", -qty))
    return legs, template, tenor, underlying


@app.cell
def _(Basket, basket_risk, ctx, date_sel, legs, read_for_underlying, underlying):
    trade_date = date_sel.value
    basket = Basket(
        basket_id="nb",
        trade_date=trade_date,
        underlying=underlying,
        legs=legs,
        provider=None,
    )
    cells = read_for_underlying(
        ctx.store, "projected_option_analytics", underlying, trade_date=trade_date
    )
    result = basket_risk(basket, analytics_rows=cells, spot_by_underlying={})
    return result, trade_date


@app.cell
def _(mo, result, template, tenor, trade_date, underlying):
    greek_rows = [
        {"Greek": "dollar delta", "$ value": result.dollar_delta, "unit": result.dollar_delta_unit},
        {"Greek": "dollar gamma", "$ value": result.dollar_gamma, "unit": result.dollar_gamma_unit},
        {"Greek": "dollar vega", "$ value": result.dollar_vega, "unit": result.dollar_vega_unit},
        {"Greek": "dollar theta", "$ value": result.dollar_theta, "unit": result.dollar_theta_unit},
        {"Greek": "dollar rho", "$ value": result.dollar_rho, "unit": result.dollar_rho_unit},
    ]
    summary_view = mo.vstack(
        [
            mo.md(f"### Basket dollar Greeks — {template} · {underlying} · {tenor} · {trade_date.isoformat()}"),
            mo.md(f"Basket price: **{result.price}**"),
            mo.ui.table(greek_rows, selection=None),
        ]
    )
    summary_view
    return (summary_view,)


@app.cell
def _(mo, result):
    if result.gaps:
        _items = "\n".join(
            f"- {g.underlying} {g.tenor_label} {g.delta_band}: **{g.reason}**" for g in result.gaps
        )
        gap_view = mo.callout(
            mo.md(f"**Unresolved legs ({len(result.gaps)})** — excluded from book totals:\n{_items}"),
            kind="warn",
        )
    else:
        gap_view = mo.md("All legs resolved against the banked surface.")
    gap_view
    return (gap_view,)


@app.cell
def _(mo, result):
    leg_rows = []
    for _i, lr in enumerate(result.legs):
        leg_rows.append(
            {
                "leg": _i,
                "side": lr.leg.side,
                "band": lr.leg.delta_band,
                "qty": lr.leg.quantity,
                "resolved": lr.resolved,
                "gap_reason": lr.gap_reason or "",
                "strike": lr.strike,
                "implied_vol": lr.implied_vol,
                "price": lr.price,
                "dollar_delta": lr.dollar_delta,
                "dollar_gamma": lr.dollar_gamma,
                "dollar_vega": lr.dollar_vega,
                "dollar_theta": lr.dollar_theta,
                "dollar_rho": lr.dollar_rho,
            }
        )
    leg_table = mo.ui.table(leg_rows, selection=None)
    return leg_rows, leg_table


@app.cell
def _(go, leg_rows, mo, result):
    _labels = [f"leg {r['leg']} {r['side']} {r['band']}" for r in leg_rows]
    _fig = go.Figure()
    _fig.add_trace(
        go.Bar(
            x=_labels,
            y=[r["dollar_delta"] for r in leg_rows],
            name=f"dollar delta ({result.dollar_delta_unit})",
            marker_color="#2563eb",
        )
    )
    _fig.add_trace(
        go.Bar(
            x=_labels,
            y=[r["dollar_vega"] for r in leg_rows],
            name=f"dollar vega ({result.dollar_vega_unit})",
            marker_color="#f59e0b",
        )
    )
    _fig.update_layout(
        title="Per-leg dollar Greek contribution",
        barmode="group",
        xaxis_title="leg",
        yaxis_title="$ contribution",
        height=420,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    legs_view = mo.vstack([mo.md("### Per-leg contribution"), mo.ui.plotly(_fig)])
    legs_view
    return (legs_view,)


@app.cell
def _(leg_table, mo):
    mo.vstack([mo.md("### Per-leg detail"), leg_table])
    return


if __name__ == "__main__":
    app.run()

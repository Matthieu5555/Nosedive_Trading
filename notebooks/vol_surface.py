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
    from algotrading.infra.surfaces import reconstruct_dense_surface

    ctx = AppContext.build()
    return (
        AppContext,
        ctx,
        go,
        mo,
        np,
        read_for_underlying,
        reconstruct_dense_surface,
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
    side_sel = mo.ui.radio(
        options=["combined", "put", "call"],
        value="combined",
        label="Smile side",
        inline=True,
    )
    mo.md(
        "## Volatility surface\n\n"
        "**What the market is paying for protection.** Implied volatility is the market's bet on "
        "how big the index's future moves will be — higher means pricier options and more nervousness.\n\n"
        "**How to read it:** hotter colours and higher points mean a bigger expected move. Left is "
        "downside (puts), right is upside (calls), and front-to-back is how far out in time. The lift "
        "on the left is the normal 'skew' — crash protection costs more.\n\n"
        f"{mo.hstack([und_sel, date_sel, side_sel], justify='start', gap=2)}"
    )
    return date_sel, side_sel, und_sel


@app.cell
def _(ctx, date_sel, read_for_underlying, reconstruct_dense_surface, und_sel):
    underlying = und_sel.value
    trade_date = date_sel.value

    cells = read_for_underlying(
        ctx.store, "projected_option_analytics", underlying, trade_date=trade_date
    )
    slices = read_for_underlying(
        ctx.store, "surface_parameters", underlying, trade_date=trade_date
    )
    dense = reconstruct_dense_surface(slices)
    if dense is None:
        grid_k = grid_months = grid_iv = None
    else:
        grid_k = np.asarray(dense.log_moneyness)
        grid_months = np.asarray(dense.maturity_years) * 12.0
        grid_iv = np.asarray(dense.implied_vol) * 100.0
    return (
        cells,
        dense,
        grid_iv,
        grid_k,
        grid_months,
        slices,
        trade_date,
        underlying,
    )


@app.cell
def _(dense, go, grid_iv, grid_k, grid_months, mo, trade_date, underlying):
    if dense is None:
        surface_view = mo.md("**No fitted surface for this selection.**")
    else:
        _fig = go.Figure(
            go.Surface(
                x=grid_k,
                y=grid_months,
                z=grid_iv,
                colorscale="Plasma",
                colorbar=dict(title="IV %"),
            )
        )
        _fig.update_layout(
            title=f"{underlying} implied volatility surface — {trade_date.isoformat()}",
            scene=dict(
                xaxis_title="moneyness  (downside ◄ ► upside)",
                yaxis_title="maturity (months)",
                zaxis_title="implied vol (%)",
            ),
            height=620,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        surface_view = mo.ui.plotly(_fig)
    surface_view
    return (surface_view,)


@app.cell
def _(dense, go, grid_iv, grid_k, grid_months, mo, underlying):
    if dense is None:
        heatmap_view = mo.md("")
    else:
        _fig = go.Figure(
            go.Heatmap(
                x=grid_k,
                y=grid_months,
                z=grid_iv,
                colorscale="Plasma",
                colorbar=dict(title="IV %"),
            )
        )
        _fig.update_layout(
            title=f"{underlying} implied volatility — same surface, flattened",
            xaxis_title="moneyness  (downside ◄ ► upside)",
            yaxis_title="maturity (months)",
            height=420,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        heatmap_view = mo.ui.plotly(_fig)
    heatmap_view
    return (heatmap_view,)


@app.cell
def _(dense, go, grid_iv, grid_k, grid_months, mo, np):
    if dense is None:
        atm_view = mo.md("")
    else:
        atm_col = int(np.argmin(np.abs(grid_k)))
        atm_iv = grid_iv[:, atm_col]
        _fig = go.Figure(
            go.Scatter(
                x=grid_months, y=atm_iv, mode="lines+markers", line=dict(color="#2563eb")
            )
        )
        _fig.update_layout(
            title="Expected move at today's price, by time horizon",
            xaxis_title="maturity (months)",
            yaxis_title="ATM implied vol (%)",
            height=360,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        atm_view = mo.ui.plotly(_fig)
    atm_view
    return (atm_view,)


@app.cell
def _(cells, go, mo, side_sel):
    side = side_sel.value
    side_cells = [c for c in cells if c.surface_side == side]
    by_tenor: dict[str, list] = {}
    for c in side_cells:
        by_tenor.setdefault(c.tenor_label, []).append(c)

    def _months(rows: list) -> float:
        return rows[0].maturity_years * 12.0

    ordered = sorted(by_tenor.items(), key=lambda kv: kv[1][0].maturity_years)
    _fig = go.Figure()
    for tenor, rows in ordered:
        rows = sorted(rows, key=lambda c: c.log_moneyness)
        _fig.add_trace(
            go.Scatter(
                x=[c.log_moneyness for c in rows],
                y=[c.implied_vol * 100.0 for c in rows],
                mode="lines+markers",
                name=f"{tenor} ({rows[0].maturity_years:.2f}y)",
            )
        )
    _fig.update_layout(
        title=f"Per-maturity smiles — {side} side  (puts ◄ ATM ► calls)",
        xaxis_title="log-moneyness  log(K/F)",
        yaxis_title="implied vol (%)",
        height=520,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    smiles_view = mo.md(f"**{len(side_cells)} cells on the {side} side, {len(ordered)} maturities.**") if side_cells else mo.md(f"**No {side}-side cells for this selection.**")
    mo.vstack([smiles_view, mo.ui.plotly(_fig)])
    return


if __name__ == "__main__":
    app.run()

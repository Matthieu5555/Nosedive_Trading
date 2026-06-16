import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from algotrading.frontend.context import AppContext
    from algotrading.frontend.store_reads import read_for_underlying

    ctx = AppContext.build()
    return AppContext, ctx, go, make_subplots, mo, read_for_underlying


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
    mo.md(
        "# Dollar Greeks\n\n"
        "**How much money this position makes or loses when the market moves.** Every number is in "
        "currency, already scaled to the position size.\n\n"
        "- **Delta \\$** — P&L if the index moves up 1 point\n"
        "- **Gamma \\$** — how fast that Delta itself changes as the index moves\n"
        "- **Vega \\$** — P&L if implied volatility rises 1 point\n"
        "- **Theta \\$** — P&L given up to one day of time passing\n\n"
        f"{mo.hstack([und_sel, date_sel], justify='start', gap=2)}"
    )
    return date_sel, und_sel


@app.cell
def _(ctx, date_sel, read_for_underlying, und_sel):
    underlying = und_sel.value
    trade_date = date_sel.value

    raw_cells = read_for_underlying(
        ctx.store, "projected_option_analytics", underlying, trade_date=trade_date
    )
    cells = [c for c in raw_cells if c.surface_side == "combined"]
    return cells, trade_date, underlying


@app.cell
def _(cells, mo):
    _tenors = sorted(
        {(c.maturity_years, c.tenor_label) for c in cells},
        key=lambda t: t[0],
    )
    _options = {f"{lbl} ({yrs * 12.0:.2f}m)": lbl for yrs, lbl in _tenors}
    maturity_sel = mo.ui.dropdown(
        options=_options,
        value=next(iter(_options)) if _options else None,
        label="Maturity (for matrix below)",
    )

    _band_order = [
        b for _td, b in sorted(
            {(c.target_delta, c.delta_band) for c in cells}, key=lambda p: p[0]
        )
    ]
    _preferred = ["30dp", "10dp", "atm", "10dc", "30dc"]
    _default_bands = [b for b in _preferred if b in _band_order] or _band_order[:5]
    band_sel = mo.ui.multiselect(
        options=_band_order,
        value=_default_bands,
        label="Delta bands to plot",
    )
    return band_sel, maturity_sel


@app.cell
def _(band_sel, cells, go, make_subplots, mo, trade_date, underlying):
    _selected = set(band_sel.value)
    if not cells:
        term_view = mo.md("**No combined cells for this selection.**")
    elif not _selected:
        term_view = mo.md("**Pick at least one delta band to plot.**")
    else:
        _bands = sorted(
            {(c.target_delta, c.delta_band) for c in cells if c.delta_band in _selected},
            key=lambda b: b[0],
        )
        _n = len(_bands)

        def _color(_i: int) -> str:
            _t = _i / (_n - 1) if _n > 1 else 0.5
            _r = int(220 * (1.0 - _t) + 37 * _t)
            _g = int(38 * (1.0 - _t) + 99 * _t)
            _b = int(38 * (1.0 - _t) + 235 * _t)
            return f"rgb({_r},{_g},{_b})"

        _metrics = [
            ("dollar_delta", "Delta $"),
            ("dollar_gamma", "Gamma $"),
            ("dollar_vega", "Vega $"),
            ("dollar_theta", "Theta $"),
        ]
        _fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=[lbl for _attr, lbl in _metrics],
            horizontal_spacing=0.09,
            vertical_spacing=0.13,
        )
        for _bi, (_td, _band) in enumerate(_bands):
            _rows = sorted(
                (c for c in cells if c.delta_band == _band and c.target_delta == _td),
                key=lambda c: c.maturity_years,
            )
            if not _rows:
                continue
            _x = [c.maturity_years * 12.0 for c in _rows]
            _color_hex = _color(_bi)
            for _mi, (_attr, _lbl) in enumerate(_metrics):
                _row = _mi // 2 + 1
                _col = _mi % 2 + 1
                _fig.add_trace(
                    go.Scatter(
                        x=_x,
                        y=[getattr(c, _attr) for c in _rows],
                        mode="lines+markers",
                        name=_band,
                        legendgroup=_band,
                        showlegend=_mi == 0,
                        line=dict(color=_color_hex),
                    ),
                    row=_row,
                    col=_col,
                )
        for _i in range(1, 5):
            _fig.update_xaxes(title_text="maturity (months)", row=(_i - 1) // 2 + 1, col=(_i - 1) % 2 + 1)
        _fig.update_layout(
            title=f"{underlying} dollar Greeks across maturities — {trade_date.isoformat()}  (one line per delta band, put ► ATM ► call)",
            height=760,
            margin=dict(l=0, r=0, t=90, b=0),
            legend=dict(orientation="v"),
        )
        term_view = mo.vstack(
            [
                mo.md(f"## Across maturities\n\nEach panel is one dollar Greek; each line is a delta band. Showing **{len(_bands)}** of 32 bands — add or remove them below to keep the chart readable."),
                band_sel,
                mo.ui.plotly(_fig),
            ]
        )
    term_view
    return (term_view,)


@app.cell
def _(cells, maturity_sel, mo, trade_date, underlying):
    _tenor = maturity_sel.value
    _rows = sorted(
        (c for c in cells if c.tenor_label == _tenor),
        key=lambda c: c.target_delta,
    )
    if not _rows:
        matrix_view = mo.md("**No cells at this maturity.**")
    else:
        _months = _rows[0].maturity_years * 12.0
        _table = [
            {
                "delta_band": c.delta_band,
                "target_delta": round(c.target_delta, 2),
                "delta": round(c.delta, 4),
                "gamma": round(c.gamma, 6),
                "vega": round(c.vega, 4),
                "theta": round(c.theta, 4),
                "rho": round(c.rho, 4),
                "delta $": round(c.dollar_delta, 2),
                "gamma $": round(c.dollar_gamma, 4),
                "vega $": round(c.dollar_vega, 4),
                "theta $": round(c.dollar_theta, 4),
                "rho $": round(c.dollar_rho, 4),
            }
            for c in _rows
        ]
        matrix_view = mo.vstack(
            [
                mo.md(
                    f"## Per-maturity matrix — {_tenor} ({_months:.2f} months)\n\n"
                    f"{underlying} on {trade_date.isoformat()}. One row per delta band (sorted put ► ATM ► call). "
                    "Left block is raw per-contract greeks, right block (`$`) is position-scaled."
                ),
                maturity_sel,
                mo.ui.table(_table, selection=None),
            ]
        )
    matrix_view
    return (matrix_view,)


if __name__ == "__main__":
    app.run()

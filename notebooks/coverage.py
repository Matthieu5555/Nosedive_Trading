import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go

    from algotrading.frontend.context import AppContext

    ctx = AppContext.build()
    return AppContext, ctx, go, mo


@app.cell
def _(ctx, mo):
    _parts = ctx.store.list_partitions("instrument_master")
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
    mo.md(f"## Capture coverage\n\n**Did we capture enough option prices to trust the analytics?** Every dot is one option we recorded, placed by its strike and expiry — gaps and thin spots show where the data is weak. The table at the bottom is the automated pass/fail.\n{mo.hstack([und_sel, date_sel], justify='start', gap=2)}")
    return date_sel, und_sel


@app.cell
def _(ctx, date_sel, und_sel):
    underlying = und_sel.value
    trade_date = date_sel.value

    masters = ctx.store.read(
        "instrument_master", trade_date=trade_date, underlying=underlying
    )
    opts = [
        m.instrument
        for m in masters
        if m.instrument.is_option() and m.instrument.expiry and m.instrument.strike
    ]
    return opts, trade_date, underlying


@app.cell
def _(opts):
    by_expiry: dict = {}
    for _o in opts:
        by_expiry.setdefault(_o.expiry, []).append(_o)

    coverage_rows = []
    for _exp in sorted(by_expiry):
        _rows = by_expiry[_exp]
        _strikes = sorted({_o.strike for _o in _rows})
        coverage_rows.append(
            {
                "expiry": _exp.isoformat(),
                "n_strikes": len(_strikes),
                "n_calls": sum(1 for _o in _rows if _o.option_right == "C"),
                "n_puts": sum(1 for _o in _rows if _o.option_right == "P"),
                "strike_min": _strikes[0] if _strikes else None,
                "strike_max": _strikes[-1] if _strikes else None,
            }
        )
    return by_expiry, coverage_rows


@app.cell
def _(by_expiry, go, mo, opts, trade_date, underlying):
    if not opts:
        chain_view = mo.md("**No captured option chain for this selection.**")
    else:
        _expiries = sorted(by_expiry)
        _labels = [_e.isoformat() for _e in _expiries]
        _calls_x, _calls_y, _puts_x, _puts_y = [], [], [], []
        for _idx, _exp in enumerate(_expiries):
            for _o in by_expiry[_exp]:
                if _o.option_right == "C":
                    _calls_x.append(_idx)
                    _calls_y.append(_o.strike)
                else:
                    _puts_x.append(_idx)
                    _puts_y.append(_o.strike)
        _fig = go.Figure()
        _fig.add_trace(
            go.Scatter(
                x=_puts_x,
                y=_puts_y,
                mode="markers",
                name="puts",
                marker=dict(color="#dc2626", size=5, symbol="triangle-down"),
            )
        )
        _fig.add_trace(
            go.Scatter(
                x=_calls_x,
                y=_calls_y,
                mode="markers",
                name="calls",
                marker=dict(color="#2563eb", size=5, symbol="triangle-up"),
            )
        )
        _fig.update_layout(
            title=f"{underlying} captured chain — strike vs expiry  ({trade_date.isoformat()})",
            xaxis=dict(
                title="expiry",
                tickmode="array",
                tickvals=list(range(len(_labels))),
                ticktext=_labels,
                tickangle=-60,
            ),
            yaxis_title="strike",
            height=560,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        chain_view = mo.ui.plotly(_fig)
    chain_view
    return (chain_view,)


@app.cell
def _(coverage_rows, mo, opts, underlying):
    if not opts:
        coverage_view = mo.md("")
    else:
        coverage_view = mo.vstack(
            [
                mo.md(
                    f"**{len(opts)} captured contracts across {len(coverage_rows)} expiries for {underlying}.**"
                ),
                mo.ui.table(coverage_rows, selection=None),
            ]
        )
    coverage_view
    return (coverage_view,)


@app.cell
def _(ctx, mo, trade_date, underlying):
    try:
        _qc = ctx.store.read("qc_results", trade_date=trade_date)
    except Exception:
        _qc = []
    _sel = [
        q
        for q in _qc
        if q.target_key == underlying or q.target_key.startswith(f"{underlying}@")
    ]
    _coverage_checks = {
        "tenor_coverage_floor",
        "delta_band_completeness",
        "option_chain_coverage",
        "calendar_sanity",
        "underlying_quote_health",
    }
    _floor = [q for q in _sel if q.check_name in _coverage_checks]
    if not _floor:
        qc_view = mo.md("_No QC coverage rows for this selection._")
    else:
        _qc_rows = [
            {
                "check_name": q.check_name,
                "status": q.qc_status,
                "measured_value": q.measured_value,
                "severity": q.severity,
            }
            for q in sorted(_floor, key=lambda q: q.check_name)
        ]
        qc_view = mo.vstack(
            [
                mo.md(f"### Automated quality checks — {underlying}"),
                mo.ui.table(_qc_rows, selection=None),
            ]
        )
    qc_view
    return (qc_view,)


if __name__ == "__main__":
    app.run()

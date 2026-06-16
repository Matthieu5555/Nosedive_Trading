import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import plotly.graph_objects as go
    from datetime import timedelta

    from algotrading.frontend.context import AppContext

    ctx = AppContext.build()
    return AppContext, ctx, go, mo, timedelta


@app.cell
def _(ctx, mo):
    _bar_parts = ctx.store.list_partitions("daily_bar")
    bar_underlyings = sorted({u for _d, u in _bar_parts})
    _index_parts = ctx.store.list_partitions("index_constituents")
    _indexes = sorted({u for _d, u in _index_parts}) or ["SX5E"]

    index_sel = mo.ui.dropdown(
        options={i: i for i in _indexes},
        value=ctx.default_underlying if ctx.default_underlying in _indexes else _indexes[0],
        label="Index",
    )
    return bar_underlyings, index_sel


@app.cell
def _(ctx, index_sel):
    constituents = sorted(
        ctx.store.read("index_constituents", underlying=index_sel.value),
        key=lambda c: c.weight,
        reverse=True,
    )
    constituent_total = sum(c.weight for c in constituents)
    return constituent_total, constituents


@app.cell
def _(bar_underlyings, constituents, index_sel, mo):
    symbol_options = {index_sel.value: index_sel.value}
    for _c in constituents:
        if _c.constituent in bar_underlyings:
            symbol_options[f"{_c.constituent}  ({_c.weight:.2f}%)"] = _c.constituent

    symbol_sel = mo.ui.dropdown(
        options=symbol_options,
        value=index_sel.value,
        label="Symbol",
    )
    lookback_sel = mo.ui.dropdown(
        options={"30 days": 30, "90 days": 90, "180 days": 180, "365 days": 365},
        value="180 days",
        label="Lookback",
    )
    mo.md(
        f"## Market\n\n"
        f"**Price history, and what's inside the index.** Pick the index or any single member to see "
        f"its daily price; the table at the bottom lists every member and how much it weighs.\n"
        f"{mo.hstack([index_sel, symbol_sel, lookback_sel], justify='start', gap=2)}"
    )
    return lookback_sel, symbol_sel


@app.cell
def _(ctx, lookback_sel, symbol_sel, timedelta):
    symbol = symbol_sel.value
    lookback = lookback_sel.value

    all_bars = sorted(
        ctx.store.read("daily_bar", underlying=symbol),
        key=lambda b: b.trade_date,
    )
    if all_bars:
        end_date = all_bars[-1].trade_date
        start_date = end_date - timedelta(days=lookback)
        bars = [b for b in all_bars if b.trade_date >= start_date]
    else:
        end_date = start_date = None
        bars = []
    return bars, end_date, start_date, symbol


@app.cell
def _(bars, end_date, go, mo, start_date, symbol):
    if not bars:
        candle_view = mo.md(f"**No daily bars for {symbol}.**")
    else:
        _fig = go.Figure(
            go.Candlestick(
                x=[b.trade_date for b in bars],
                open=[b.open for b in bars],
                high=[b.high for b in bars],
                low=[b.low for b in bars],
                close=[b.close for b in bars],
                name=symbol,
            )
        )
        _fig.update_layout(
            title=f"{symbol} daily OHLC — {start_date.isoformat()} → {end_date.isoformat()}",
            xaxis_title="trade date",
            yaxis_title="price",
            xaxis_rangeslider_visible=False,
            height=520,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        candle_view = mo.ui.plotly(_fig)
    candle_view
    return (candle_view,)


@app.cell
def _(bars, go, mo, symbol):
    if not bars:
        close_view = mo.md("")
    else:
        _fig = go.Figure(
            go.Scatter(
                x=[b.trade_date for b in bars],
                y=[b.close for b in bars],
                mode="lines",
                line=dict(color="#2563eb"),
                name="close",
            )
        )
        _fig.update_layout(
            title=f"{symbol} close price",
            xaxis_title="trade date",
            yaxis_title="close",
            height=320,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        close_view = mo.ui.plotly(_fig)
    close_view
    return (close_view,)


@app.cell
def _(constituent_total, constituents, index_sel, mo):
    table_rows = [
        {
            "constituent": _c.constituent,
            "weight %": round(_c.weight, 4),
            "share of total %": round(100.0 * _c.weight / constituent_total, 4)
            if constituent_total
            else 0.0,
        }
        for _c in constituents
    ]
    constituents_table = mo.ui.table(table_rows, label=f"{index_sel.value} constituents (by weight)")
    mo.vstack(
        [
            mo.md(f"### {index_sel.value} constituents — {len(constituents)} members"),
            constituents_table,
        ]
    )
    return (constituents_table,)


if __name__ == "__main__":
    app.run()

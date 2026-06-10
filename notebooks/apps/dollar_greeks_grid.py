import marimo

app = marimo.App(width="full", app_title="Dollar Greeks Grid")


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
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    _shared.apply_plotly_theme()

    # The four dollar-Greeks this app graphs, in (cell attribute, unit attribute, label,
    # colour) order. The unit strings are read off the cell itself (ADR 0036), never
    # hard-coded here; the engine attaches them so the row is self-describing.
    GREEKS = [
        ("dollar_delta", "dollar_delta_unit", "Delta $", _shared.C["blue"]),
        ("dollar_gamma", "dollar_gamma_unit", "Gamma $", _shared.C["teal"]),
        ("dollar_vega", "dollar_vega_unit", "Vega $", _shared.C["violet"]),
        ("dollar_theta", "dollar_theta_unit", "Theta $", _shared.C["amber"]),
    ]
    return go, make_subplots, GREEKS


@app.cell
def _(mo):
    mo.md(
        """
        # Dollar Greeks Grid

        The four dollar-Greeks (**Δ\\$, Γ\\$, V\\$, Θ\\$**) read off a real captured snapshot,
        projected onto the pinned **tenor × delta-band** grid by the tested actor pipeline —
        the same `projected_analytics` the EOD close-capture persists and the front renders.

        This is the offline replay of a committed broker sample (no broker, no token). The
        grid is *provider-partitioned*, so this app supplies the provider that gates it (the
        plain replay-equality path leaves it empty by design).
        """
    )
    return


@app.cell
def _(mo, _shared):
    _samples = list(_shared.committed_samples())
    sample = mo.ui.dropdown(
        options=_samples, value=_samples[0], label="Captured sample"
    )
    clamp = mo.ui.checkbox(
        value=False, label="Clamp pinned tenors to fitted span (extrapolate)"
    )
    mo.vstack([mo.hstack([sample, clamp], justify="start", gap=2)])
    return sample, clamp


@app.cell
def _(_shared, sample, clamp):
    _entry = _shared.committed_samples()[sample.value]
    # Provider only stamps the partition; derive it from the sample's broker label.
    _provider = sample.value.split()[0].upper()

    outputs, as_of, _config = _shared.replay_projected_grid(
        _entry["path"],
        underlying=_entry["underlying"],
        exchange=_entry["exchange"],
        provider=_provider,
        clamp_to_span=clamp.value,
    )

    cells = outputs.projected_analytics
    # Group cells by maturity, keyed by (years, tenor_label) and sorted by tenor.
    by_maturity: dict[tuple[float, str], list] = {}
    for _c in cells:
        by_maturity.setdefault((round(_c.maturity_years, 6), _c.tenor_label), []).append(_c)
    maturities = sorted(by_maturity)

    # Fitted maturity span (from the SVI slices) — what is real vs clamped on the x-axis.
    _fitted = [p.maturity_years for p in outputs.surface_parameters]
    fitted_span = (min(_fitted), max(_fitted)) if _fitted else None
    underlying = _entry["underlying"]
    return cells, by_maturity, maturities, fitted_span, underlying, as_of


@app.cell
def _(mo, cells, maturities, fitted_span, underlying, as_of):
    if not cells:
        _banner = mo.md(
            "**No grid cells** — the fitted surface produced no projectable tenor/band "
            "for this sample. Try toggling *Clamp to fitted span*."
        ).callout(kind="warn")
    else:
        _span = (
            f"[{fitted_span[0]:.3f}, {fitted_span[1]:.3f}]y"
            if fitted_span
            else "n/a"
        )
        _full = 8 * 8  # pinned tenors × default delta bands
        _banner = mo.md(
            f"**{underlying}** snapshot at `{as_of:%Y-%m-%d %H:%M UTC}` — "
            f"**{len(cells)}** projected cells over **{len(maturities)}** tenor(s) "
            f"({len(cells) / _full:.0%} of the {_full}-cell grid). "
            f"Fitted maturity span {_span}; pinned tenors outside it are **labeled gaps** "
            f"(no silent extrapolation) unless *Clamp* is on."
        ).callout(kind="info")
    _banner
    return


@app.cell
def _(mo, maturities):
    # Maturity selector for the per-tenor band-profile view; depends on the loaded grid.
    _opts = {f"{lbl} ({yrs:.3f}y)": (yrs, lbl) for (yrs, lbl) in maturities}
    maturity_sel = (
        mo.ui.dropdown(options=_opts, value=next(iter(_opts)), label="Tenor (band profile)")
        if _opts
        else None
    )
    maturity_sel
    return (maturity_sel,)


@app.cell
def _(mo, by_maturity, maturity_sel, GREEKS):
    # Scorecard: the four dollar-Greeks at the ATM pillar of the selected tenor — the
    # numeric companion to the front's greeks scorecard, with units shown verbatim.
    def _card(label, value, unit):
        _v = "—" if value is None else f"{value:,.2f}"
        return mo.md(
            f"**{_v}**<br><span style='color:#475569'>{label}</span>"
            f"<br><span style='color:#94A3B8;font-size:11px'>{unit}</span>"
        )

    if maturity_sel is None:
        _out = mo.md("")
    else:
        _band = by_maturity[maturity_sel.value]
        _atm = next((c for c in _band if c.delta_band == "atm"), _band[0])
        _out = mo.hstack(
            [
                _card(lbl, getattr(_atm, attr), getattr(_atm, unit_attr) or "")
                for attr, unit_attr, lbl, _ in GREEKS
            ],
            justify="start",
            gap=2,
        )
    _out
    return


@app.cell
def _(mo, go, make_subplots, by_maturity, maturity_sel, GREEKS, _shared):
    # View 1 — band profile: each dollar-Greek vs the delta-band axis for one tenor.
    if maturity_sel is None:
        _fig1 = mo.md("")
    else:
        _band = sorted(by_maturity[maturity_sel.value], key=lambda c: c.target_delta)
        _x = [c.target_delta for c in _band]
        _fig = make_subplots(rows=2, cols=2, subplot_titles=[g[2] for g in GREEKS])
        for _i, (_attr, _unit, _lbl, _col) in enumerate(GREEKS):
            _r, _c = _i // 2 + 1, _i % 2 + 1
            _y = [getattr(c, _attr) for c in _band]
            _fig.add_trace(
                go.Scatter(
                    x=_x, y=_y, mode="lines+markers", name=_lbl, showlegend=False,
                    line={"color": _col, "width": 2}, marker={"size": 8},
                ),
                row=_r, col=_c,
            )
            _fig.update_xaxes(title_text="target delta", row=_r, col=_c)
            _unit_str = next((getattr(c, _unit) for c in _band if getattr(c, _unit)), "")
            _fig.update_yaxes(title_text=_unit_str, row=_r, col=_c)
        _lbl_sel = next(k for k, v in maturity_sel.options.items() if v == maturity_sel.value)
        _fig.update_layout(
            height=560, title_text=f"Dollar Greeks vs delta band — {_lbl_sel}"
        )
        _fig1 = _fig
    _fig1
    return


@app.cell
def _(mo, go, make_subplots, by_maturity, maturities, fitted_span, GREEKS):
    # View 2 — term structure: each dollar-Greek vs maturity, one line per delta band.
    if not maturities:
        _fig2 = mo.md("")
    else:
        _bands: dict[str, list[tuple[float, object]]] = {}
        for (_yrs, _lbl), _cells in by_maturity.items():
            for c in _cells:
                _bands.setdefault(c.delta_band, []).append((_yrs, c))
        _fig = make_subplots(rows=2, cols=2, subplot_titles=[g[2] for g in GREEKS])
        _palette = ["#2563EB", "#0D9488", "#7C3AED", "#D97706", "#DC2626",
                    "#0EA5E9", "#16A34A", "#4F46E5"]
        for _i, (_attr, _unit, _lbl, _col) in enumerate(GREEKS):
            _r, _c = _i // 2 + 1, _i % 2 + 1
            for _bi, (_band, _pts) in enumerate(sorted(_bands.items())):
                _pts = sorted(_pts, key=lambda t: t[0])
                _fig.add_trace(
                    go.Scatter(
                        x=[p[0] for p in _pts],
                        y=[getattr(p[1], _attr) for p in _pts],
                        mode="lines+markers", name=_band, legendgroup=_band,
                        showlegend=(_i == 0),
                        line={"color": _palette[_bi % len(_palette)], "width": 2},
                        marker={"size": 6},
                    ),
                    row=_r, col=_c,
                )
            if fitted_span is not None:
                _fig.add_vrect(
                    x0=fitted_span[0], x1=fitted_span[1], fillcolor="#94A3B8",
                    opacity=0.12, line_width=0, row=_r, col=_c,
                )
            _fig.update_xaxes(title_text="maturity (years)", row=_r, col=_c)
            _unit_str = next(
                (getattr(c, _unit) for cs in by_maturity.values() for c in cs if getattr(c, _unit)),
                "",
            )
            _fig.update_yaxes(title_text=_unit_str, row=_r, col=_c)
        _fig.update_layout(
            height=600,
            title_text="Dollar Greeks term structure (shaded band = fitted maturity span)",
        )
        _fig2 = _fig
    _fig2
    return


if __name__ == "__main__":
    app.run()

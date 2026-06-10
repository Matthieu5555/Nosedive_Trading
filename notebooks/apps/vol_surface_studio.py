import marimo

app = marimo.App(width="full", app_title="Vol-Surface Studio")


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

    _shared.apply_plotly_theme()
    return np, go, make_subplots


@app.cell
def _(_shared):
    from algotrading.infra.surfaces import (
        SviParams,
        fit_svi,
        summarize_surface_parameters,
    )

    surface_cfg = _shared.demo_platform_config("ASML", "EUREX").surface
    return SviParams, fit_svi, summarize_surface_parameters, surface_cfg


@app.cell
def _(mo):
    title = mo.md(
        "# Vol-Surface Studio\n"
        "SVI calibration playground and full offline replay of a committed sample."
    )
    title
    return (title,)


# ---------------------------------------------------------------- TAB 1 controls


@app.cell
def _(mo):
    s_a = mo.ui.slider(start=0.0, stop=0.1, step=0.001, value=0.02, label="true a")
    s_b = mo.ui.slider(start=0.0, stop=0.5, step=0.005, value=0.1, label="true b")
    s_rho = mo.ui.slider(start=-0.9, stop=0.9, step=0.02, value=-0.3, label="rho")
    s_m = mo.ui.slider(start=-0.3, stop=0.3, step=0.01, value=0.0, label="m")
    s_sigma = mo.ui.slider(start=0.02, stop=0.4, step=0.005, value=0.1, label="sigma")
    s_noise = mo.ui.slider(
        start=0.0, stop=2e-3, step=1e-4, value=5e-4, label="noise std"
    )
    s_n = mo.ui.slider(start=7, stop=31, step=1, value=15, label="n points")
    s_T = mo.ui.slider(
        start=0.1, stop=2.0, step=0.05, value=0.5, label="maturity (yrs)"
    )
    s_seed = mo.ui.slider(start=0, stop=20, step=1, value=1, label="seed")
    return s_a, s_b, s_rho, s_m, s_sigma, s_noise, s_n, s_T, s_seed


@app.cell
def _(SviParams, fit_svi, np, s_a, s_b, s_rho, s_m, s_sigma, s_noise, s_n, s_T, s_seed, surface_cfg):
    _true = SviParams(
        a=s_a.value, b=s_b.value, rho=s_rho.value, m=s_m.value, sigma=s_sigma.value
    )
    fit_k = np.linspace(-0.4, 0.4, int(s_n.value))
    _w_true = np.array([_true.total_variance(float(ki)) for ki in fit_k])
    fit_w_market = _w_true + np.random.default_rng(int(s_seed.value)).normal(
        0.0, s_noise.value, int(s_n.value)
    )
    fit_result = fit_svi(
        tuple(float(x) for x in fit_k),
        tuple(float(x) for x in fit_w_market),
        config=surface_cfg,
    )
    fit_T = float(s_T.value)
    return fit_k, fit_w_market, fit_result, fit_T


@app.cell
def _(mo, fit_result):
    def _kpi(label, val):
        return mo.md(f"**{label}**\n\n{val}")

    _bh = ", ".join(fit_result.bound_hits) if fit_result.bound_hits else "none"
    tab1_kpis = mo.hstack(
        [
            _kpi("converged", "yes" if fit_result.converged else "no"),
            _kpi("rmse", f"{fit_result.rmse:.2e}"),
            _kpi("bound hits", _bh),
            _kpi("n points", fit_result.n_points),
        ],
        justify="start",
        gap=2,
    )
    tab1_kpis
    return (tab1_kpis,)


@app.cell
def _(go, make_subplots, np, fit_k, fit_w_market, fit_result, fit_T, _shared):
    _kg = np.linspace(-0.4, 0.4, 200)
    _w_fit = np.array([fit_result.params.total_variance(float(ki)) for ki in _kg])
    _iv_fit = np.sqrt(np.maximum(_w_fit, 0.0) / fit_T)
    _iv_mkt = np.sqrt(np.maximum(fit_w_market, 0.0) / fit_T)

    _fig = make_subplots(
        rows=1, cols=2, subplot_titles=("total variance", "implied-vol smile")
    )
    _fig.add_trace(
        go.Scatter(
            x=fit_k, y=fit_w_market, mode="markers", name="market",
            marker={"color": _shared.C["amber"], "size": 7},
        ),
        row=1, col=1,
    )
    _fig.add_trace(
        go.Scatter(
            x=_kg, y=_w_fit, mode="lines", name="fitted",
            line={"color": _shared.C["blue"], "width": 2},
        ),
        row=1, col=1,
    )
    _fig.add_trace(
        go.Scatter(
            x=fit_k, y=_iv_mkt, mode="markers", showlegend=False,
            marker={"color": _shared.C["amber"], "size": 7},
        ),
        row=1, col=2,
    )
    _fig.add_trace(
        go.Scatter(
            x=_kg, y=_iv_fit, mode="lines", showlegend=False,
            line={"color": _shared.C["blue"], "width": 2},
        ),
        row=1, col=2,
    )
    _fig.update_xaxes(title_text="log-moneyness k")
    _fig.update_yaxes(title_text="w", row=1, col=1)
    _fig.update_yaxes(title_text="IV", row=1, col=2)
    _fig.update_layout(height=460, title="SVI slice fit")
    tab1_fig = _fig
    tab1_fig
    return (tab1_fig,)


@app.cell
def _(mo, s_a, s_b, s_rho, s_m, s_sigma, s_noise, s_n, s_T, s_seed, tab1_kpis, tab1_fig):
    tab1 = mo.vstack(
        [
            mo.hstack([s_a, s_b, s_rho, s_m, s_sigma], gap=1),
            mo.hstack([s_noise, s_n, s_T, s_seed], gap=1),
            tab1_kpis,
            tab1_fig,
        ],
        gap=1,
    )
    return (tab1,)


# ---------------------------------------------------------------- TAB 2 replay


@app.cell
def _(mo, _shared):
    _labels = list(_shared.committed_samples())
    sample_dd = mo.ui.dropdown(
        options=_labels, value=_labels[0], label="committed sample"
    )
    sample_dd
    return (sample_dd,)


@app.cell
def _(sample_dd, _shared):
    _meta = _shared.committed_samples()[sample_dd.value]
    rep_out, rep_as_of, _masters, _cfg = _shared.replay_sample(
        _meta["path"], underlying=_meta["underlying"], exchange=_meta["exchange"]
    )
    return rep_out, rep_as_of


@app.cell
def _(rep_out, np):
    # Group iv_points and surface_parameters by expiry; collect spot.
    iv_by_exp: dict = {}
    for _p in rep_out.iv_points:
        _exp = _p.contract_key.split("|")[6]
        iv_by_exp.setdefault(_exp, []).append((_p.log_moneyness, _p.implied_vol))
    iv_by_exp = {
        e: (np.array([x[0] for x in v]), np.array([x[1] for x in v]))
        for e, v in iv_by_exp.items()
    }
    surf_params = sorted(rep_out.surface_parameters, key=lambda p: p.maturity_years)
    n_maturities = len(surf_params)
    spot = rep_out.snapshots[0].reference_spot if rep_out.snapshots else float("nan")
    return iv_by_exp, surf_params, n_maturities, spot


@app.cell
def _(mo, rep_as_of, n_maturities, spot):
    rep_kpis = mo.hstack(
        [
            mo.md(f"**as-of**\n\n{rep_as_of:%Y-%m-%d %H:%M}"),
            mo.md(f"**maturities**\n\n{n_maturities}"),
            mo.md(f"**ref spot**\n\n{spot:,.2f}"),
        ],
        justify="start",
        gap=2,
    )
    rep_kpis
    return (rep_kpis,)


@app.cell
def _(go, np, rep_out, _shared):
    _strike = np.array([float(s.instrument_key.split("|")[7] or "nan") for s in rep_out.snapshots])
    _spread = np.array([s.spread_pct for s in rep_out.snapshots])
    _mask = ~np.isnan(_strike)
    _fig = go.Figure(
        go.Scatter(
            x=_strike[_mask], y=_spread[_mask] * 100.0, mode="markers",
            marker={"color": _shared.C["teal"], "size": 6, "opacity": 0.7},
            name="spread",
        )
    )
    _fig.update_layout(
        title="Relative spread by strike",
        xaxis_title="strike", yaxis_title="spread (%)", height=360,
    )
    rep_spread_fig = _fig
    rep_spread_fig
    return (rep_spread_fig,)


@app.cell
def _(go, np, rep_out, spot, _shared):
    _T = np.array([f.maturity_years for f in rep_out.forwards])
    _basis = np.array([f.forward_price - spot for f in rep_out.forwards])
    _ord = np.argsort(_T)
    _fig = go.Figure(
        go.Scatter(
            x=_T[_ord], y=_basis[_ord], mode="lines+markers",
            line={"color": _shared.C["violet"], "width": 2},
            marker={"size": 8},
        )
    )
    _fig.update_layout(
        title="Forward basis by tenor (forward - spot)",
        xaxis_title="maturity (yrs)", yaxis_title="basis", height=360,
    )
    rep_basis_fig = _fig
    rep_basis_fig
    return (rep_basis_fig,)


@app.cell
def _(go, iv_by_exp, _shared):
    _fig = go.Figure()
    for _i, (_exp, (_k, _iv)) in enumerate(sorted(iv_by_exp.items())):
        _o = _k.argsort()
        _fig.add_trace(
            go.Scatter(
                x=_k[_o], y=_iv[_o], mode="markers", name=_exp,
                marker={"size": 6, "color": _shared.DISCRETE[_i % len(_shared.DISCRETE)]},
            )
        )
    _fig.update_layout(
        title="Per-expiry IV smile",
        xaxis_title="log-moneyness k", yaxis_title="implied vol", height=400,
    )
    rep_smile_fig = _fig
    rep_smile_fig
    return (rep_smile_fig,)


@app.cell
def _(SviParams, go, make_subplots, np, iv_by_exp, surf_params, _shared):
    _n = len(surf_params)
    _cols = min(2, _n) if _n else 1
    _rows = (_n + _cols - 1) // _cols if _n else 1
    _titles = [f"{p.expiry_date} ({p.maturity_years:.2f}y)" for p in surf_params]
    _fig = make_subplots(rows=_rows, cols=_cols, subplot_titles=_titles)
    _kg = np.linspace(-0.4, 0.4, 120)
    for _i, _p in enumerate(surf_params):
        _r, _c = _i // _cols + 1, _i % _cols + 1
        _svi = SviParams(a=_p.svi_a, b=_p.svi_b, rho=_p.svi_rho, m=_p.svi_m, sigma=_p.svi_sigma)
        _w = np.array([_svi.total_variance(float(ki)) for ki in _kg])
        _iv = np.sqrt(np.maximum(_w, 0.0) / _p.maturity_years)
        _fig.add_trace(
            go.Scatter(x=_kg, y=_iv, mode="lines", showlegend=False,
                       line={"color": _shared.C["blue"], "width": 2}),
            row=_r, col=_c,
        )
        _exp = _p.expiry_date.isoformat()
        if _exp in iv_by_exp:
            _km, _ivm = iv_by_exp[_exp]
            _fig.add_trace(
                go.Scatter(x=_km, y=_ivm, mode="markers", showlegend=False,
                           marker={"color": _shared.C["amber"], "size": 5, "opacity": 0.7}),
                row=_r, col=_c,
            )
    _fig.update_xaxes(title_text="k")
    _fig.update_yaxes(title_text="IV")
    _fig.update_layout(title="Fitted SVI smiles vs market IV", height=320 * _rows)
    rep_svi_fig = _fig
    rep_svi_fig
    return (rep_svi_fig,)


@app.cell
def _(mo, go, np, SviParams, surf_params, n_maturities, _shared):
    if n_maturities >= 2:
        _kg = np.linspace(-0.4, 0.4, 60)
        _T = np.array([p.maturity_years for p in surf_params])
        _Z = np.zeros((len(surf_params), len(_kg)))
        for _i, _p in enumerate(surf_params):
            _svi = SviParams(a=_p.svi_a, b=_p.svi_b, rho=_p.svi_rho, m=_p.svi_m, sigma=_p.svi_sigma)
            _w = np.array([_svi.total_variance(float(ki)) for ki in _kg])
            _Z[_i, :] = np.sqrt(np.maximum(_w, 0.0) / _p.maturity_years)
        _fig = go.Figure(
            go.Surface(
                x=_kg, y=_T, z=_Z, colorscale=_shared.SURFACE_COLORSCALE,
                colorbar={"title": "IV"},
            )
        )
        _fig.update_layout(
            title="Implied-vol surface",
            scene={
                "xaxis_title": "log-moneyness k",
                "yaxis_title": "maturity (yrs)",
                "zaxis_title": "IV",
                "camera": _shared.SURFACE_CAMERA,
                "aspectmode": "manual",
                "aspectratio": _shared.SURFACE_ASPECT,
            },
            height=560,
        )
        rep_surface = _fig
    else:
        rep_surface = mo.callout(
            "Single-maturity sample has no surface to render.", kind="info"
        )
    rep_surface
    return (rep_surface,)


@app.cell
def _(mo, summarize_surface_parameters, rep_out):
    _summ = summarize_surface_parameters(rep_out.surface_parameters)
    _records = [
        {
            "expiry": s.expiry_date.isoformat(),
            "maturity_yrs": round(s.maturity_years, 4),
            "method": s.method,
            "atm_vol": round(s.atm_vol, 4),
            "n_points": s.n_points,
            "rmse": f"{s.rmse:.2e}",
            "arb_free": s.arb_free,
        }
        for s in _summ
    ]
    rep_table = mo.ui.table(_records, selection=None)
    rep_table
    return (rep_table,)


@app.cell
def _(
    mo,
    sample_dd,
    rep_kpis,
    rep_spread_fig,
    rep_basis_fig,
    rep_smile_fig,
    rep_svi_fig,
    rep_surface,
    rep_table,
):
    tab2 = mo.vstack(
        [
            sample_dd,
            rep_kpis,
            mo.hstack([rep_spread_fig, rep_basis_fig], widths="equal", gap=1),
            rep_smile_fig,
            rep_svi_fig,
            rep_surface,
            mo.md("**Surface summary**"),
            rep_table,
        ],
        gap=1,
    )
    return (tab2,)


# ---------------------------------------------------------------- top layout


@app.cell
def _(mo, tab1, tab2):
    mo.ui.tabs({"SVI slice fit": tab1, "Sample replay": tab2})
    return


if __name__ == "__main__":
    app.run()

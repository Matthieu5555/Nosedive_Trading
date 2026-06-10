import marimo

app = marimo.App(width="full", app_title="Vol Surface — Pedagogy")


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
    # Setup: imports, palette, Black-Scholes + 5 Greeks, smile/term-structure/surface
    # helpers, common grids. Ported from the notebook's setup cell. All teaching maths
    # live here so every later cell imports them as params (single-definition rule).
    import numpy as np
    from scipy.stats import norm
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    _shared.apply_plotly_theme()

    C = {
        "blue": "#2563EB", "teal": "#0D9488", "violet": "#7C3AED",
        "amber": "#D97706", "red": "#DC2626", "green": "#16A34A",
        "indigo": "#4F46E5", "slate9": "#0F172A", "slate6": "#475569",
        "slate4": "#94A3B8", "slate1": "#F1F5F9", "white": "#FFFFFF",
    }
    SURFACE_CS = _shared.SURFACE_COLORSCALE
    CAMERA = _shared.SURFACE_CAMERA
    ASPECT = _shared.SURFACE_ASPECT

    # Common grids
    k_grid = np.linspace(-0.5, 0.5, 80)              # log-moneyness
    T_arr = np.array([30, 60, 90, 180]) / 365         # maturities (years)
    T_labels = ["30d", "60d", "90d", "180d"]

    def bs(S, K, T, r, sigma, flag="call"):
        """Black-Scholes price and five Greeks. vega/1%, theta/calendar-day, rho/bp."""
        if T < 1e-8:
            intrinsic = max(S - K, 0.0) if flag == "call" else max(K - S, 0.0)
            return dict(price=intrinsic, delta=0.0, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)
        sqT = np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqT)
        d2 = d1 - sigma * sqT
        disc = np.exp(-r * T)
        nd1 = norm.pdf(d1)
        if flag == "call":
            price = S * norm.cdf(d1) - K * disc * norm.cdf(d2)
            delta = norm.cdf(d1)
            raw_theta = -S * nd1 * sigma / (2 * sqT) - r * K * disc * norm.cdf(d2)
            raw_rho = K * T * disc * norm.cdf(d2)
        else:
            price = K * disc * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = norm.cdf(d1) - 1.0
            raw_theta = -S * nd1 * sigma / (2 * sqT) + r * K * disc * norm.cdf(-d2)
            raw_rho = -K * T * disc * norm.cdf(-d2)
        gamma = nd1 / (S * sigma * sqT)
        vega = S * sqT * nd1 * 0.01     # per +1% vol
        theta = raw_theta / 365.0       # per calendar day
        rho = raw_rho * 0.0001          # per +1bp
        return dict(price=price, delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)

    def smile_iv(k, atm, skew, conv):
        """Parametric smile IV(k) = ATM + skew*k + conv*k^2."""
        return np.clip(atm + skew * k + conv * k**2, 0.01, 5.0)

    def ts_backwardation(T):
        return 0.22 + 0.43 * np.exp(-8.0 * T)

    def ts_flat(T):
        return np.full_like(np.atleast_1d(T), 0.25, dtype=float)

    def ts_contango(T):
        return 0.35 - 0.23 * np.exp(-4.0 * T)

    def build_surface(T_grid, k_arr, atm_fn, base_skew, base_conv,
                      skew_fade=0.0, conv_fade=0.0):
        """Stack smiles over maturity into an (n_T x n_k) IV surface."""
        iv = np.zeros((len(T_grid), len(k_arr)))
        for i, T in enumerate(T_grid):
            sk = base_skew * np.exp(-skew_fade * T)
            cv = base_conv * np.exp(-conv_fade * T)
            iv[i] = smile_iv(k_arr, atm_fn(T), sk, cv)
        return iv

    # Pre-computed realistic surfaces
    surf_equity = build_surface(T_arr, k_grid, ts_contango,
                                base_skew=-0.60, base_conv=0.40,
                                skew_fade=3.0, conv_fade=2.0)
    surf_crypto = build_surface(T_arr, k_grid, ts_backwardation,
                                base_skew=0.00, base_conv=2.50, conv_fade=3.5)
    surf_arb = surf_equity.copy()
    surf_arb[1] = surf_equity[0] * 0.55   # calendar violation on 60d slice (sigma^2*T drops)

    KK, TT = np.meshgrid(k_grid, T_arr * 365)

    def scene3d(zlabel="IV (%)"):
        return dict(
            xaxis=dict(title="Log-moneyness", gridcolor=C["slate4"],
                       backgroundcolor=C["slate1"], showbackground=True),
            yaxis=dict(title="Maturity (days)", gridcolor=C["slate4"],
                       backgroundcolor=C["slate1"], showbackground=True),
            zaxis=dict(title=zlabel, gridcolor=C["slate4"],
                       backgroundcolor=C["slate1"], showbackground=True),
            camera=CAMERA, aspectmode="manual", aspectratio=ASPECT,
        )

    return (
        np, norm, go, make_subplots, C, SURFACE_CS, CAMERA, ASPECT,
        k_grid, T_arr, T_labels, bs, smile_iv,
        ts_backwardation, ts_flat, ts_contango, build_surface,
        surf_equity, surf_crypto, surf_arb, KK, TT, scene3d,
    )


@app.cell
def _(mo):
    mo.md(
        """
        # Implied Volatility & the Vol Surface — Pedagogy

        A guided tour from option payoffs to the full 3D vol surface. Pure maths and
        plots, no engine calls. Move the sliders to build intuition; the four tabs
        follow the path payoffs to Greeks to 2D smiles to 3D surfaces.

        **Vocabulary.** *ATM/ITM/OTM* = strike vs spot. *Forward F* = the true
        at-the-money reference. *Log-moneyness k = ln(K/F)*: 0 at-the-money, < 0 puts,
        > 0 calls — makes smiles comparable across maturities.
        """
    )
    return


# ─────────────────────────────────────────────────────────────── Block 1 controls


@app.cell
def _(mo):
    vol_payoff = mo.ui.slider(
        5, 100, value=25, step=1, label="Volatility sigma (%)", show_value=True
    )
    return (vol_payoff,)


@app.cell
def _(mo):
    smile_atm = mo.ui.slider(10, 60, value=22, step=1, label="ATM level (%)", show_value=True)
    smile_skew = mo.ui.slider(-100, 100, value=-40, step=5, label="Skew (slope)", show_value=True)
    smile_conv = mo.ui.slider(0, 300, value=50, step=10, label="Convexity (curvature)", show_value=True)
    return smile_atm, smile_skew, smile_conv


@app.cell
def _(mo):
    ts_choice = mo.ui.dropdown(
        options=["Backwardation", "Flat", "Contango"],
        value="Contango",
        label="Term-structure shape",
    )
    return (ts_choice,)


# ───────────────────────────────────────────────── Block 1: payoffs & vol intuition


@app.cell
def _(go, make_subplots, np, C):
    # 1.1 Payoff profiles at expiry — the asymmetric right an option confers.
    _S = np.linspace(60, 140, 300)
    _K, _pc, _pp = 100.0, 5.0, 4.5
    _payoffs = {
        ("Long Call", C["blue"]): np.maximum(_S - _K, 0) - _pc,
        ("Short Call", C["red"]): -(np.maximum(_S - _K, 0) - _pc),
        ("Long Put", C["teal"]): np.maximum(_K - _S, 0) - _pp,
        ("Short Put", C["amber"]): -(np.maximum(_K - _S, 0) - _pp),
    }
    _fig = make_subplots(rows=2, cols=2, subplot_titles=[l for (l, _) in _payoffs],
                         vertical_spacing=0.12, horizontal_spacing=0.08)
    for ((_lab, _col), _pnl), (_r, _c) in zip(_payoffs.items(), [(1, 1), (1, 2), (2, 1), (2, 2)]):
        _fig.add_trace(go.Scatter(x=_S, y=_pnl, mode="lines",
                                  line=dict(color=_col, width=2.5), showlegend=False,
                                  hovertemplate=f"<b>{_lab}</b><br>Spot %{{x:.1f}}<br>P&L %{{y:.2f}}<extra></extra>"),
                       row=_r, col=_c)
        _fig.add_hline(y=0, line_dash="dash", line_color=C["slate4"], row=_r, col=_c)
        _fig.add_vline(x=_K, line_dash="dot", line_color=C["slate4"], row=_r, col=_c)
    _fig.update_layout(title="P&L profiles at expiry — strike K=100, r=0", height=460, margin=dict(t=80))
    _fig.update_yaxes(title_text="P&L", row=1, col=1)
    _fig.update_yaxes(title_text="P&L", row=2, col=1)
    _fig.update_xaxes(title_text="Spot at expiry", row=2, col=1)
    _fig.update_xaxes(title_text="Spot at expiry", row=2, col=2)
    fig_payoff = _fig
    return (fig_payoff,)


@app.cell
def _(go, make_subplots, np, C, vol_payoff):
    # 1.2 Vol as a dispersion parameter — the chosen sigma drives one extra GBM path
    # plus the lognormal price distribution at T=1y, against two reference vols.
    _sig_pick = vol_payoff.value / 100.0
    _scen = [("sigma = 10%", 0.10, C["teal"]),
             ("sigma = 60%", 0.60, C["red"]),
             (f"sigma = {vol_payoff.value}% (slider)", _sig_pick, C["blue"])]
    _dt, _N = 1 / 252, 252
    _days = np.arange(_N + 1)
    _fig = make_subplots(rows=1, cols=2, column_widths=[0.60, 0.40],
                         subplot_titles=("Price paths (252 days)",
                                         "Lognormal price distribution at T=1y"))
    _x = np.linspace(20, 320, 400)
    for _i, (_lab, _sig, _col) in enumerate(_scen):
        _rng = np.random.default_rng(7 + _i)
        _Z = _rng.standard_normal(_N)
        _path = np.empty(_N + 1)
        _path[0] = 100.0
        _path[1:] = 100.0 * np.exp(np.cumsum((-0.5 * _sig**2 * _dt) + _sig * np.sqrt(_dt) * _Z))
        _fig.add_trace(go.Scatter(x=_days, y=_path, mode="lines",
                                  line=dict(color=_col, width=1.5), name=_lab,
                                  hovertemplate="Day %{x}<br>Price %{y:.1f}<extra></extra>"),
                       row=1, col=1)
        # lognormal density of S_T, S0=100, mu=0, T=1
        _mu = np.log(100.0) - 0.5 * _sig**2
        _pdf = np.exp(-(np.log(_x) - _mu)**2 / (2 * _sig**2)) / (_x * _sig * np.sqrt(2 * np.pi))
        _fig.add_trace(go.Scatter(x=_x, y=_pdf, mode="lines",
                                  line=dict(color=_col, width=2), name=_lab, showlegend=False,
                                  hovertemplate="S_T %{x:.0f}<br>density %{y:.4f}<extra></extra>"),
                       row=1, col=2)
    _fig.add_hline(y=100, line_dash="dash", line_color=C["slate4"], row=1, col=1)
    _fig.add_vline(x=100, line_dash="dot", line_color=C["slate4"], row=1, col=2)
    _fig.update_xaxes(title_text="Days", row=1, col=1)
    _fig.update_xaxes(title_text="Terminal price S_T", row=1, col=2)
    _fig.update_yaxes(title_text="Price", row=1, col=1)
    _fig.update_yaxes(title_text="Density", row=1, col=2)
    _fig.update_layout(title="Higher vol = wider dispersion, fatter tails", height=400, margin=dict(t=80))
    fig_dispersion = _fig
    return (fig_dispersion,)


@app.cell
def _(go, np, bs, C, vol_payoff):
    # 1.3 / 1.4 Option price vs vol, and the inversion price -> implied vol.
    _vols = np.linspace(0.01, 1.0, 200)
    _S, _T, _r = 100.0, 90 / 365, 0.03
    _atm = np.array([bs(_S, 100.0, _T, _r, v)["price"] for v in _vols])
    _otm = np.array([bs(_S, 110.0, _T, _r, v)["price"] for v in _vols])
    _itm = np.array([bs(_S, 90.0, _T, _r, v)["price"] for v in _vols])
    _fig = go.Figure()
    for _lab, _pr, _col in [("ATM K=100", _atm, C["blue"]),
                            ("OTM K=110", _otm, C["amber"]),
                            ("ITM K=90", _itm, C["teal"])]:
        _fig.add_trace(go.Scatter(x=_vols * 100, y=_pr, mode="lines",
                                  line=dict(width=2.5, color=_col), name=_lab,
                                  hovertemplate=f"<b>{_lab}</b><br>Vol %{{x:.0f}}%<br>Price %{{y:.3f}}<extra></extra>"))
    # slider vol marker -> inverted IV read on the ATM curve
    _vpick = vol_payoff.value
    _price_at = float(np.interp(_vpick / 100.0, _vols, _atm))
    _fig.add_vline(x=_vpick, line_dash="dot", line_color=C["slate6"])
    _fig.add_hline(y=_price_at, line_dash="dash", line_color=C["red"],
                   annotation_text=f"ATM price {_price_at:.2f} <-> IV {_vpick}%",
                   annotation_position="top left",
                   annotation_font=dict(color=C["red"], size=11))
    _fig.add_scatter(x=[_vpick], y=[_price_at], mode="markers",
                     marker=dict(color=C["red"], size=11), name="slider IV",
                     hovertemplate=f"IV {_vpick}%<br>price {_price_at:.3f}<extra></extra>")
    _fig.update_layout(title="Price vs vol, and the inversion price -> IV (S=100, T=90d, r=3%)",
                       xaxis_title="Implied volatility (%)", yaxis_title="Call price (USD)",
                       height=400)
    fig_price_vs_vol = _fig
    return (fig_price_vs_vol,)


@app.cell
def _(go, make_subplots, np, C):
    # 1.5 Why IV is never flat: fat tails, structural put demand, vol clustering.
    _fig = make_subplots(rows=1, cols=3, horizontal_spacing=0.08,
                         subplot_titles=("Fat tails vs normal",
                                         "Structural put demand",
                                         "Volatility clustering"))
    _rng = np.random.default_rng(0)
    _emp = np.concatenate([_rng.normal(0, 0.012, 800), _rng.standard_t(df=3, size=200) * 0.015])
    _xn = np.linspace(-0.06, 0.06, 200)
    _fig.add_trace(go.Histogram(x=_emp, nbinsx=60, histnorm="probability density",
                                marker_color=C["blue"], opacity=0.7, name="Empirical",
                                hovertemplate="ret %{x:.2%}<br>density %{y:.1f}<extra></extra>"), row=1, col=1)
    _pdf = np.exp(-(_xn)**2 / (2 * _emp.std()**2)) / (_emp.std() * np.sqrt(2 * np.pi))
    _fig.add_trace(go.Scatter(x=_xn, y=_pdf, mode="lines",
                              line=dict(color=C["red"], width=2, dash="dash"), name="Normal"), row=1, col=1)
    _sd = np.linspace(70, 110, 50)
    _fig.add_trace(go.Scatter(x=_sd, y=120 * np.exp(-0.08 * (_sd - 70)), mode="lines+markers",
                              line=dict(color=C["violet"], width=2.5), marker=dict(size=4),
                              showlegend=False, name="Put buy volume",
                              hovertemplate="strike %{x:.0f}<br>volume %{y:.0f}<extra></extra>"), row=1, col=2)
    _fig.add_vline(x=100, line_dash="dot", line_color=C["slate4"], row=1, col=2)
    _rng2 = np.random.default_rng(3)
    _t = np.arange(400)
    _regime = np.where((_t > 120) & (_t < 200), 0.045, 0.012)
    _vol_series = _regime * np.abs(_rng2.standard_normal(400))
    _fig.add_trace(go.Scatter(x=_t, y=_vol_series * 100, mode="lines",
                              line=dict(color=C["amber"], width=1.2), showlegend=False,
                              name="|return|",
                              hovertemplate="day %{x}<br>|ret| %{y:.2f}%<extra></extra>"), row=1, col=3)
    _fig.update_xaxes(title_text="Daily return", row=1, col=1)
    _fig.update_xaxes(title_text="Strike", row=1, col=2)
    _fig.update_xaxes(title_text="Day", row=1, col=3)
    _fig.update_yaxes(title_text="Density", row=1, col=1)
    _fig.update_yaxes(title_text="Put buy volume", row=1, col=2)
    _fig.update_yaxes(title_text="|return| (%)", row=1, col=3)
    _fig.update_layout(title="Three market reasons the smile is never flat", height=360, margin=dict(t=80))
    fig_three_reasons = _fig
    return (fig_three_reasons,)


@app.cell
def _(mo, vol_payoff, fig_payoff, fig_dispersion, fig_price_vs_vol, fig_three_reasons):
    tab_block1 = mo.vstack([
        mo.md(
            "## Block 1 — Options & implied volatility\n\n"
            "An option is an asymmetric right: the buyer's loss is capped at the premium, "
            "the upside is open. That asymmetry is priced entirely by expected volatility. "
            "Buying an option is buying volatility; the implied vol is the only information "
            "parameter inside an option price."
        ),
        mo.md("**Payoff profiles.** Long vs short, call vs put — the kink at the strike is the whole game."),
        fig_payoff,
        mo.md("**Volatility = dispersion.** The slider sets a third vol; watch its path spread and its "
              "lognormal terminal distribution fatten."),
        vol_payoff,
        fig_dispersion,
        mo.md("**Price vs vol, and inversion.** The relation is monotone, so a market price inverts to a "
              "unique implied vol — the slider marks that read."),
        fig_price_vs_vol,
        mo.md("**Why not flat.** Fat tails, permanent put-hedging demand, and vol clustering all lift IV "
              "away from a single Black-Scholes number."),
        fig_three_reasons,
    ])
    return (tab_block1,)


# ─────────────────────────────────────────────────────────────────── Block 2: Greeks


@app.cell
def _(go, make_subplots, np, bs, C):
    # 2.1-2.5 The five Greeks vs strike and vs maturity (call long, reference asset).
    _strikes = np.linspace(70, 130, 200)
    _S, _Tref, _r, _sig = 100.0, 90 / 365, 0.03, 0.25
    _Tmats = [7 / 365, 30 / 365, 90 / 365, 180 / 365]
    _Tlabs = ["7d", "30d", "90d", "180d"]
    _Trange = np.linspace(1 / 365, 1.0, 200)
    _mcols = [C["red"], C["amber"], C["blue"], C["teal"]]

    _fig = make_subplots(rows=3, cols=2, vertical_spacing=0.10, horizontal_spacing=0.10,
                         subplot_titles=("Delta vs strike (T=90d)", "Delta ATM vs maturity",
                                         "Vega vs strike (by maturity)", "Gamma vs strike (by maturity)",
                                         "Theta vs strike (T=90d)", "Rho vs strike (T=90d)"))
    # Delta vs strike
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "call")["delta"] for K in _strikes],
                              mode="lines", line=dict(color=C["blue"], width=2.5), name="Call"), row=1, col=1)
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "put")["delta"] for K in _strikes],
                              mode="lines", line=dict(color=C["teal"], width=2.5), name="Put"), row=1, col=1)
    _fig.add_vline(x=_S, line_dash="dash", line_color=C["slate4"], row=1, col=1)
    # Delta ATM vs maturity
    _fig.add_trace(go.Scatter(x=_Trange * 365, y=[bs(_S, _S, T, _r, _sig, "call")["delta"] for T in _Trange],
                              mode="lines", line=dict(color=C["blue"], width=2.5), showlegend=False), row=1, col=2)
    _fig.add_trace(go.Scatter(x=_Trange * 365, y=[bs(_S, _S, T, _r, _sig, "put")["delta"] for T in _Trange],
                              mode="lines", line=dict(color=C["teal"], width=2.5), showlegend=False), row=1, col=2)
    # Vega vs strike, multiple maturities
    for _T, _lab, _col in zip(_Tmats, _Tlabs, _mcols):
        _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _T, _r, _sig)["vega"] for K in _strikes],
                                  mode="lines", line=dict(color=_col, width=2), name=_lab,
                                  legendgroup="vega"), row=2, col=1)
    _fig.add_vline(x=_S, line_dash="dash", line_color=C["slate4"], row=2, col=1)
    # Gamma vs strike, multiple maturities
    for _T, _lab, _col in zip(_Tmats, _Tlabs, _mcols):
        _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _T, _r, _sig)["gamma"] for K in _strikes],
                                  mode="lines", line=dict(color=_col, width=2), showlegend=False), row=2, col=2)
    _fig.add_vline(x=_S, line_dash="dash", line_color=C["slate4"], row=2, col=2)
    # Theta vs strike
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "call")["theta"] for K in _strikes],
                              mode="lines", line=dict(color=C["blue"], width=2.5), showlegend=False), row=3, col=1)
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "put")["theta"] for K in _strikes],
                              mode="lines", line=dict(color=C["teal"], width=2.5), showlegend=False), row=3, col=1)
    _fig.add_vline(x=_S, line_dash="dash", line_color=C["slate4"], row=3, col=1)
    _fig.add_hline(y=0, line_color=C["slate4"], line_width=0.8, row=3, col=1)
    # Rho vs strike
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "call")["rho"] for K in _strikes],
                              mode="lines", line=dict(color=C["blue"], width=2.5), showlegend=False), row=3, col=2)
    _fig.add_trace(go.Scatter(x=_strikes, y=[bs(_S, K, _Tref, _r, _sig, "put")["rho"] for K in _strikes],
                              mode="lines", line=dict(color=C["teal"], width=2.5), showlegend=False), row=3, col=2)
    _fig.add_vline(x=_S, line_dash="dash", line_color=C["slate4"], row=3, col=2)
    _fig.add_hline(y=0, line_color=C["slate4"], line_width=0.8, row=3, col=2)

    _fig.update_xaxes(title_text="Maturity (days)", row=1, col=2)
    _fig.update_xaxes(title_text="Strike", row=3, col=1)
    _fig.update_xaxes(title_text="Strike", row=3, col=2)
    _fig.update_layout(title="The five Greeks — long call, reference asset (S=100, sigma=25%)",
                       height=820, margin=dict(t=80))
    fig_greeks = _fig
    return (fig_greeks,)


@app.cell
def _(go, make_subplots, np, bs, C):
    # 2.6 Greeks dashboard: each Greek vs maturity for an ATM long call.
    _S, _r, _sig = 100.0, 0.03, 0.25
    _T = np.linspace(2 / 365, 0.6, 150)
    _data = {
        "Price": ([bs(_S, _S, t, _r, _sig)["price"] for t in _T], C["blue"], "Price"),
        "Delta": ([bs(_S, _S, t, _r, _sig)["delta"] for t in _T], C["teal"], "Delta"),
        "Gamma": ([bs(_S, _S, t, _r, _sig)["gamma"] for t in _T], C["violet"], "Gamma"),
        "Vega": ([bs(_S, _S, t, _r, _sig)["vega"] for t in _T], C["amber"], "Vega/1%"),
        "Theta": ([bs(_S, _S, t, _r, _sig)["theta"] for t in _T], C["red"], "Theta/d"),
        "Rho": ([bs(_S, _S, t, _r, _sig)["rho"] for t in _T], C["indigo"], "Rho/bp"),
    }
    _signs = {"Price": "+", "Delta": "+ [0,1]", "Gamma": "+", "Vega": "+", "Theta": "-", "Rho": "+"}
    _fig = make_subplots(rows=2, cols=3, horizontal_spacing=0.08, vertical_spacing=0.15,
                         subplot_titles=[f"{g}  ({s})" for g, s in _signs.items()])
    _pos = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    for (_name, (_vals, _col, _yl)), (_r, _c) in zip(_data.items(), _pos):
        _fig.add_trace(go.Scatter(x=_T * 365, y=_vals, mode="lines",
                                  line=dict(color=_col, width=2.5), showlegend=False,
                                  hovertemplate=f"<b>{_name}</b><br>T %{{x:.0f}}d<br>{_yl} %{{y:.4f}}<extra></extra>"),
                       row=_r, col=_c)
        _fig.add_hline(y=0, line_color=C["slate4"], line_width=0.6, row=_r, col=_c)
        _fig.update_yaxes(title_text=_yl, row=_r, col=_c)
    for _c in (1, 2, 3):
        _fig.update_xaxes(title_text="Maturity (days)", row=2, col=_c)
    _fig.update_layout(title="Greeks dashboard — ATM long call vs maturity", height=560, margin=dict(t=80))
    fig_dashboard = _fig
    return (fig_dashboard,)


@app.cell
def _(mo, fig_greeks, fig_dashboard):
    tab_block2 = mo.vstack([
        mo.md(
            "## Block 2 — The Greeks\n\n"
            "Greeks measure how an option price reacts to each market parameter. "
            "Delta = direction, Vega = vol sensitivity (the surface link), Gamma = convexity, "
            "Theta = time decay (Gamma's counterpart), Rho = rate sensitivity."
        ),
        mo.md("**Delta** is S-shaped: deep ITM tracks the underlying (->1), deep OTM goes flat (->0), "
              "ATM sits near 0.5. **Vega** peaks ATM and grows with sqrt(T). **Gamma** explodes near a "
              "short-dated ATM expiry. **Theta** is the price paid for that convexity. **Rho** is small "
              "on short maturities."),
        fig_greeks,
        mo.md("**Dashboard.** Read in 30 seconds whether a book is long or short each Greek: a long ATM "
              "call is long Delta/Gamma/Vega/Rho, short Theta. A short call flips every sign."),
        fig_dashboard,
    ])
    return (tab_block2,)


# ────────────────────────────────────────────────────────── Block 3: the 2D smile


@app.cell
def _(go, np, smile_iv, k_grid, C):
    # 3.1 The four characteristic smile shapes.
    _smiles = {
        "Flat (Black-Scholes)": smile_iv(k_grid, atm=0.25, skew=0.0, conv=0.0),
        "Symmetric smile (crypto)": smile_iv(k_grid, atm=0.30, skew=0.0, conv=2.0),
        "Negative skew (equity)": smile_iv(k_grid, atm=0.22, skew=-0.40, conv=0.5),
        "Positive skew (commodities)": smile_iv(k_grid, atm=0.28, skew=0.50, conv=0.8),
    }
    _cols = [C["slate4"], C["teal"], C["blue"], C["amber"]]
    _fig = go.Figure()
    for (_lab, _iv), _col in zip(_smiles.items(), _cols):
        _fig.add_trace(go.Scatter(x=k_grid, y=_iv * 100, mode="lines",
                                  line=dict(color=_col, width=1.5 if "Flat" in _lab else 2.5,
                                            dash="dash" if "Flat" in _lab else "solid"),
                                  name=_lab,
                                  hovertemplate=f"<b>{_lab}</b><br>k %{{x:.3f}}<br>IV %{{y:.1f}}%<extra></extra>"))
    _fig.add_vline(x=0, line_dash="dot", line_color=C["slate4"], annotation_text="ATM",
                   annotation_position="top")
    _fig.update_layout(title="Four characteristic smile shapes — IV vs log-moneyness",
                       xaxis_title="Log-moneyness k = ln(K/F)", yaxis_title="Implied vol (%)",
                       height=420, margin=dict(t=80))
    fig_smile_shapes = _fig
    return (fig_smile_shapes,)


@app.cell
def _(go, np, smile_iv, k_grid, C, smile_atm, smile_skew, smile_conv):
    # 3.2 The three smile parameters, driven live by the sliders.
    _atm = smile_atm.value / 100.0
    _skew = smile_skew.value / 100.0
    _conv = smile_conv.value / 100.0
    _iv = smile_iv(k_grid, _atm, _skew, _conv)
    _idx = int(np.argmin(np.abs(k_grid)))
    _fig = go.Figure()
    _fig.add_trace(go.Scatter(x=k_grid, y=_iv * 100, mode="lines",
                              line=dict(color=C["blue"], width=3), name="Live smile",
                              hovertemplate="k %{x:.3f}<br>IV %{y:.1f}%<extra></extra>"))
    _fig.add_vline(x=0, line_dash="dot", line_color=C["slate4"])
    _av = _iv[_idx] * 100
    _fig.add_annotation(x=0, y=_av, text=f"ATM level = {_av:.0f}%<br>(absolute fear)",
                        showarrow=True, arrowhead=2, ax=80, ay=-30,
                        font=dict(size=11, color=C["blue"]), arrowcolor=C["blue"])
    _fig.add_annotation(x=-0.3, y=float(smile_iv(-0.3, _atm, _skew, _conv) * 100),
                        text="Slope (skew)<br>direction of fear",
                        showarrow=True, arrowhead=2, ax=-60, ay=-40,
                        font=dict(size=11, color=C["teal"]), arrowcolor=C["teal"])
    _fig.add_annotation(x=0.35, y=float(smile_iv(0.35, _atm, _skew, _conv) * 100),
                        text="Curvature (convexity)<br>jump-risk intensity",
                        showarrow=True, arrowhead=2, ax=60, ay=-30,
                        font=dict(size=11, color=C["violet"]), arrowcolor=C["violet"])
    _fig.update_layout(title="The three smile parameters — level / slope / curvature",
                       xaxis_title="Log-moneyness k = ln(K/F)", yaxis_title="Implied vol (%)",
                       height=440, yaxis_range=[0, max(60, _iv.max() * 100 + 5)], margin=dict(t=80))
    fig_smile_params = _fig
    return (fig_smile_params,)


@app.cell
def _(mo, smile_atm, smile_skew, smile_conv, fig_smile_shapes, fig_smile_params):
    tab_block3 = mo.vstack([
        mo.md(
            "## Block 3 — The 2D smile\n\n"
            "A horizontal slice of the surface: implied vol across strikes at one maturity. "
            "Read it as three numbers — ATM level (absolute fear), slope (skew = direction), "
            "and curvature (convexity = jump risk)."
        ),
        mo.md("**Four shapes.** Flat is the textbook Black-Scholes world (never seen). Symmetric = "
              "crypto / binary-event. Negative skew = equity index put demand. Positive skew = commodity "
              "squeeze fear."),
        fig_smile_shapes,
        mo.md("**Build your own.** Drive level, slope and curvature directly — the parametric smile is "
              "IV(k) = ATM + skew*k + conv*k^2."),
        mo.hstack([smile_atm, smile_skew, smile_conv], justify="start", gap=2),
        fig_smile_params,
    ])
    return (tab_block3,)


# ─────────────────────────────────────────────────────── Block 4: the 3D surface


@app.cell
def _(go, make_subplots, surf_equity, k_grid, KK, TT, T_labels, SURFACE_CS, C, CAMERA, ASPECT):
    # 4.1 From slices to surface: 4 stacked equity smiles, then the same in 3D.
    _fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.04,
                         subplot_titles=("4 stacked slices (2D)", "The same surface (3D)"),
                         specs=[[{"type": "xy"}, {"type": "scene"}]])
    _scols = [C["red"], C["amber"], C["blue"], C["teal"]]
    for _iv, _lab, _col in zip(surf_equity, T_labels, _scols):
        _fig.add_trace(go.Scatter(x=k_grid, y=_iv * 100, mode="lines",
                                  line=dict(color=_col, width=2), name=_lab,
                                  hovertemplate=f"<b>{_lab}</b><br>k %{{x:.3f}}<br>IV %{{y:.1f}}%<extra></extra>"),
                       row=1, col=1)
    _fig.add_vline(x=0, line_dash="dot", line_color=C["slate4"], row=1, col=1)
    _fig.add_trace(go.Surface(x=KK, y=TT, z=surf_equity * 100, colorscale=SURFACE_CS, opacity=0.88,
                              contours=dict(z=dict(show=True, usecolormap=True, project_z=True)),
                              colorbar=dict(title="IV%", thickness=14, len=0.6, x=1.02, tickformat=".0f"),
                              hovertemplate="k %{x:.3f}<br>T %{y:.0f}d<br>IV %{z:.1f}%<extra></extra>",
                              showscale=True), row=1, col=2)
    _fig.update_xaxes(title_text="Log-moneyness k", row=1, col=1)
    _fig.update_yaxes(title_text="IV (%)", row=1, col=1)
    _fig.update_layout(title="From slice to surface — realistic equity surface", height=520, margin=dict(t=80),
                       scene=dict(xaxis=dict(title="Log-moneyness", backgroundcolor=C["slate1"], showbackground=True),
                                  yaxis=dict(title="Maturity (d)", backgroundcolor=C["slate1"], showbackground=True),
                                  zaxis=dict(title="IV (%)", backgroundcolor=C["slate1"], showbackground=True),
                                  camera=CAMERA, aspectmode="manual", aspectratio=ASPECT))
    fig_slice_to_surface = _fig
    return (fig_slice_to_surface,)


@app.cell
def _(go, np, build_surface, k_grid, ts_backwardation, ts_flat, ts_contango,
      SURFACE_CS, C, ts_choice, scene3d):
    # 4.2 Term structure: the chosen regime as a single 3D surface.
    _Tfine = np.linspace(1 / 365, 0.6, 60)
    _fns = {
        "Backwardation": (ts_backwardation, dict(base_skew=-0.30, base_conv=0.30, skew_fade=2.0, conv_fade=1.5)),
        "Flat": (ts_flat, dict(base_skew=-0.20, base_conv=0.20)),
        "Contango": (ts_contango, dict(base_skew=-0.20, base_conv=0.20, skew_fade=2.0, conv_fade=1.5)),
    }
    _subtitle = {
        "Backwardation": "short vol > long vol — imminent risk event",
        "Flat": "no temporal view",
        "Contango": "short vol < long vol — the normal regime",
    }
    _fn, _kw = _fns[ts_choice.value]
    _surf = build_surface(_Tfine, k_grid, _fn, **_kw)
    _KK, _TT = np.meshgrid(k_grid, _Tfine * 365)
    _fig = go.Figure(go.Surface(x=_KK, y=_TT, z=_surf * 100, colorscale=SURFACE_CS, opacity=0.88,
                                contours=dict(z=dict(show=True, usecolormap=True, project_z=True)),
                                colorbar=dict(title="IV%", thickness=14, len=0.6, x=1.02, tickformat=".0f"),
                                hovertemplate="k %{x:.3f}<br>T %{y:.0f}d<br>IV %{z:.1f}%<extra></extra>"))
    _fig.update_layout(title=f"Term structure: {ts_choice.value} — {_subtitle[ts_choice.value]}",
                       height=560, scene=scene3d(), margin=dict(l=0, r=0, t=70, b=0))
    fig_term_structure = _fig
    return (fig_term_structure,)


@app.cell
def _(go, surf_equity, surf_crypto, KK, TT, SURFACE_CS, C, scene3d):
    # 4.3 / 4.4 Realistic equity and crypto surfaces side by side.
    def _surf3d(z, title):
        _f = go.Figure(go.Surface(
            x=KK, y=TT, z=z * 100, colorscale=SURFACE_CS, opacity=0.88,
            lighting=dict(ambient=0.7, diffuse=0.6, roughness=0.5, specular=0.1),
            contours=dict(z=dict(show=True, usecolormap=True, highlightcolor=C["white"], project_z=True)),
            colorbar=dict(title=dict(text="IV (%)", side="right"), tickformat=".0f", thickness=16, len=0.7, x=1.02),
            hovertemplate="k %{x:.3f}<br>T %{y:.0f}d<br>IV %{z:.1f}%<extra></extra>"))
        _f.update_layout(title=title, height=560, scene=scene3d(), margin=dict(l=0, r=0, t=70, b=0))
        return _f

    fig_equity = _surf3d(surf_equity, "Equity — negative skew + contango + LT flattening")
    fig_crypto = _surf3d(surf_crypto, "Crypto (BTC) — symmetric smile + backwardation + LT flattening")
    return fig_equity, fig_crypto


@app.cell
def _(go, np, surf_arb, k_grid, T_arr, KK, TT, SURFACE_CS, C, scene3d):
    # 4.5 Calendar-arbitrage illustration: mark points where sigma^2 * T decreases.
    _viol = []
    for _ki, _k in enumerate(k_grid):
        for _ti in range(len(T_arr) - 1):
            _lo = surf_arb[_ti, _ki] ** 2 * T_arr[_ti]
            _hi = surf_arb[_ti + 1, _ki] ** 2 * T_arr[_ti + 1]
            if _hi < _lo - 1e-6:
                _viol.append((_k, T_arr[_ti + 1] * 365, surf_arb[_ti + 1, _ki] * 100))
    _fig = go.Figure(go.Surface(x=KK, y=TT, z=surf_arb * 100, colorscale=SURFACE_CS, opacity=0.80,
                                contours=dict(z=dict(show=True, usecolormap=True, project_z=True)),
                                colorbar=dict(title=dict(text="IV (%)", side="right"), tickformat=".0f",
                                              thickness=14, len=0.6, x=1.02),
                                hovertemplate="k %{x:.3f}<br>T %{y:.0f}d<br>IV %{z:.1f}%<extra></extra>",
                                name="Surface"))
    if _viol:
        _fig.add_trace(go.Scatter3d(x=[v[0] for v in _viol], y=[v[1] for v in _viol], z=[v[2] for v in _viol],
                                    mode="markers",
                                    marker=dict(color=C["red"], size=5, symbol="x", line=dict(color=C["white"], width=1)),
                                    name=f"Calendar violations ({len(_viol)})",
                                    hovertemplate="<b>VIOLATION</b><br>k %{x:.3f}<br>T %{y:.0f}d<br>IV %{z:.1f}%<extra></extra>"))
    _fig.update_layout(title=f"Calendar-arbitrage violations — {len(_viol)} points (sigma^2*T non-increasing)",
                       height=560, scene=scene3d(), margin=dict(l=0, r=0, t=70, b=0))
    fig_calendar = _fig
    return (fig_calendar,)


@app.cell
def _(go, make_subplots, np, surf_equity, k_grid, T_arr, T_labels, C):
    # 4.6 Practical cuts: horizontal smiles per maturity, vertical term structure per strike.
    _fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.10,
                         subplot_titles=("Horizontal cuts — smile per maturity",
                                         "Vertical cuts — term structure per strike"))
    _scols = [C["red"], C["amber"], C["blue"], C["teal"]]
    for _iv, _lab, _col in zip(surf_equity, T_labels, _scols):
        _fig.add_trace(go.Scatter(x=k_grid, y=_iv * 100, mode="lines",
                                  line=dict(color=_col, width=2.5), name=_lab,
                                  hovertemplate=f"<b>{_lab}</b><br>k %{{x:.3f}}<br>IV %{{y:.1f}}%<extra></extra>"),
                       row=1, col=1)
    _fig.add_vline(x=0, line_dash="dot", line_color=C["slate4"], row=1, col=1)
    _cuts = {"OTM put k=-0.30": -0.30, "ATM k=0.00": 0.00, "OTM call k=+0.30": 0.30}
    _ccols = [C["blue"], C["teal"], C["amber"]]
    _Td = T_arr * 365
    for (_lab, _kv), _col in zip(_cuts.items(), _ccols):
        _ki = int(np.argmin(np.abs(k_grid - _kv)))
        _fig.add_trace(go.Scatter(x=_Td, y=surf_equity[:, _ki] * 100, mode="lines+markers",
                                  line=dict(color=_col, width=2.5), marker=dict(size=8), name=_lab,
                                  hovertemplate=f"<b>{_lab}</b><br>T %{{x:.0f}}d<br>IV %{{y:.1f}}%<extra></extra>"),
                       row=1, col=2)
    _fig.update_xaxes(title_text="Log-moneyness k", row=1, col=1)
    _fig.update_xaxes(title_text="Maturity (days)", row=1, col=2)
    _fig.update_yaxes(title_text="IV (%)", row=1, col=1)
    _fig.update_yaxes(title_text="IV (%)", row=1, col=2)
    _fig.update_layout(title="Reading the surface in 30 seconds — horizontal & vertical cuts",
                       height=430, margin=dict(t=90))
    fig_cuts = _fig
    return (fig_cuts,)


@app.cell
def _(mo, ts_choice, fig_slice_to_surface, fig_term_structure, fig_equity,
      fig_crypto, fig_calendar, fig_cuts):
    tab_block4 = mo.vstack([
        mo.md(
            "## Block 4 — The 3D surface\n\n"
            "The surface is just the Block-3 smiles stacked along the maturity axis. "
            "Equity surfaces show negative skew + contango + long-term flattening; crypto "
            "shows a symmetric smile + backwardation."
        ),
        mo.md("**Slice to surface.** Four equity smiles, flattening with maturity, become one surface "
              "that sinks toward the back."),
        fig_slice_to_surface,
        mo.md("**Term structure.** Pick a regime — backwardation (risk event soon), flat (no view), or "
              "contango (normal)."),
        ts_choice,
        fig_term_structure,
        mo.md("**Realistic surfaces.** Equity vs crypto — the asymmetry (or its absence) is immediately readable."),
        mo.hstack([fig_equity, fig_crypto], widths="equal", gap=1),
        mo.md("**Calendar arbitrage.** Total variance sigma^2*T must be non-decreasing in maturity; red "
              "crosses flag where it isn't — the first check on any surface."),
        fig_calendar,
        mo.md("**Practical cuts.** Horizontal cut = skew at a fixed maturity; vertical cut = term structure "
              "at a fixed strike. Each vertical curve is a Vega bucket in the book."),
        fig_cuts,
    ])
    return (tab_block4,)


# ────────────────────────────────────────────────────────────────────── App shell


@app.cell
def _(mo, tab_block1, tab_block2, tab_block3, tab_block4):
    mo.ui.tabs({
        "1 · Options & IV": tab_block1,
        "2 · Greeks": tab_block2,
        "3 · 2D Smile": tab_block3,
        "4 · 3D Surface": tab_block4,
    })
    return


@app.cell
def _(mo):
    mo.md(
        """
        ---
        ### Takeaways

        | Concept | One line |
        |---|---|
        | Implied vol | The only information parameter in an option price — the market's view on future agitation |
        | Delta / Vega / Gamma | Direction / vol sensitivity / convexity — Vega is the per-cell surface link |
        | Theta / Rho | Time decay (Gamma's counterpart) / rate sensitivity (marginal short-dated) |
        | Flat / symmetric / neg-skew / pos-skew | BS world / crypto / equity put demand / commodity squeeze |
        | Backwardation / contango | Imminent risk event / normal regime |
        | Calendar violation | sigma^2*T decreasing — illiquidity or quote inconsistency flag |
        """
    )
    return


if __name__ == "__main__":
    app.run()

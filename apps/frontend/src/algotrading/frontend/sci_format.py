from __future__ import annotations

import math

_SUPERSCRIPT = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "-": "⁻",
}

UNITS: dict[str, str] = {
    "delta": "$/$",
    "gamma": "1/$",
    "vega": "$/Vol",
    "theta": "$/Time(y)",
    "rho": "$/Rate",
    "vanna": "1/Vol",
    "volga": "$/Vol²",
    "charm": "$/(Time(y)·$)",
    "rt_vega": "$/Vol",
    "price": "$",
    "pnl": "$",
    "vol": "Vol",
    "variance": "Vol²·y",
    "strike": "$",
    "forward": "$",
    "logMoneyness": "ln(K/F)",
    "moneyness": "ln(K/F)",
    "years": "y",
    "sviA": "Vol²·y",
    "sviB": "Vol²·y",
    "sviRho": "(ratio)",
    "sviM": "ln(K/F)",
    "sviSigma": "ln(K/F)",
    "rmse": "Vol²·y",
    "shock": "(frac)",
    "weight": "(frac)",
    "rate": "(frac)",
    "shares": "sh",
}


def _superscript(exponent: int) -> str:
    return "".join(_SUPERSCRIPT.get(ch, ch) for ch in str(exponent))


def sci(value: float | None, sig_figs: int = 6) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if not math.isfinite(value):
        return "∞" if value > 0 else "−∞"
    if value == 0:
        return "0"
    mantissa_raw, exp_raw = f"{value:.{sig_figs - 1}e}".split("e")
    mantissa = mantissa_raw.rstrip("0").rstrip(".") if "." in mantissa_raw else mantissa_raw
    return f"{mantissa} × 10{_superscript(int(exp_raw))}"


def sci_unit(value: float | None, unit: str | None, sig_figs: int = 6) -> str:
    rendered = sci(value, sig_figs)
    if value is None:
        return rendered
    return f"{rendered} {unit}" if unit else rendered

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from algotrading.core.config import (
    ConfigError,
    ConfigFieldError,
    SurfaceConfig,
    load_platform_config,
)
from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    SURFACE_SIDES,
    ForwardCurvePoint,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
    RiskFreeRatePoint,
    SurfaceGrid,
    SurfaceParameters,
)
from algotrading.infra.rates import (
    RateCurve,
    RateCurveError,
    RateIngestError,
    curve_from_points,
    implied_riskfree_spread,
)
from algotrading.infra.surfaces.reporting import (
    ClampedSlice,
    reconstruct_dense_surface_clamped,
)
from algotrading.infra.surfaces.svi import SviParams, fit_svi
from algotrading.infra.universe import IndexRegistryError, load_index_registry
from fastapi import APIRouter
from fastapi.responses import JSONResponse

try:
    # Canonical right resolver (Lane A): "atm" -> "C", "atmp" -> "P", "...c" -> "C", "...p" -> "P".
    # Imported when present so the BFF and the projection writer share one definition of the right.
    from algotrading.infra.surfaces.projection import (  # type: ignore[attr-defined]
        option_right_for_band,
    )
except ImportError:  # pragma: no cover - exercised only before the canonical resolver lands

    def option_right_for_band(label: str) -> str:
        """Map a delta-band label to its option right, mirroring the canonical projection resolver.

        A fallback that matches the canonical semantics until the shared
        ``algotrading.infra.surfaces.projection.option_right_for_band`` is importable: a label
        ending in ``p`` is a put, ending in ``c`` is a call, and the bare ``atm`` pillar is the
        ATM call (``atmp`` is the ATM put). This replaces the old buggy local copy that returned
        ``None`` for ``atm``, which left the ATM call row permanently quote-less (D2).
        """
        if label.endswith("p"):
            return "P"
        if label.endswith("c"):
            return "C"
        return "C"

from ..deps import CtxDep, TradeDateDep
from ..grounding import coverage_from_snapshots, resolve_close_instant
from ..serializers import (
    OptionQuote,
    dense_surface_to_dict,
    forward_rate_diagnostics_to_dict,
    implied_riskfree_spread_to_dict,
    projected_option_analytics_to_dict,
    rate_curve_to_dict,
    surface_parameters_to_dict,
)
from ..store_reads import read_for_underlying

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _read_for_underlying(
    ctx: object,
    table: str,
    underlying: str,
    *,
    trade_date: date | None,
    run_id: str | None,
) -> list[object]:
    """Read one table for an underlying, addressing a specific capture by ``run_id`` when given.

    ``run_id`` (D6) identifies one capture within a ``trade_date`` (the capture-receipt instant, an
    ISO-8601 string); ``None`` reads the latest capture, which is the historical default. The
    store-side addressability is Lane C's; until it lands, ``ParquetStore.read`` does not accept the
    kwarg, so we forward ``run_id`` only when the store signals it can honour it (a non-breaking,
    additive read) and otherwise fall back to the latest-capture read unchanged.
    """
    if run_id is not None:
        try:
            rows = ctx.store.read(  # type: ignore[attr-defined]
                table, trade_date=trade_date, underlying=underlying, run_id=run_id
            )
            return [row for row in rows if row.underlying == underlying]
        except TypeError:
            # Store predates run_id addressability (Lane C not yet landed): fall through to latest.
            pass
    return read_for_underlying(
        ctx.store,  # type: ignore[attr-defined]
        table,
        underlying,
        trade_date=trade_date,
    )


_CANONICAL_FIELD_COUNT = 9
_EXPIRY_SLOT = 6
_STRIKE_SLOT = 7
_RIGHT_SLOT = 8


def _maturity_key(maturity_years: float) -> str:
    return f"{maturity_years:.6f}"


# A small float tolerance for the EXACT-key strike match. The row strike and the snapshot strike
# both originate from the same listed-contract strike (Lane A keys each row to a real listed
# contract), so this guards only against float round-trip noise through parquet/ISO text, never
# the old "nearest within 0.5%" fuzzy join that bound a model strike to a different listed strike.
_STRIKE_IDENTITY_ABS_TOL = 1.0e-6


def _listed_options_by_expiry_right_strike(
    snapshots: list[MarketStateSnapshot],
) -> dict[tuple[date, str, float], MarketStateSnapshot]:
    """Index the captured option snapshots by EXACT (expiry, right, listed strike) identity.

    The key is parsed straight off each snapshot's canonical instrument key (the same fields Lane A
    keys each projected row to), so the join is a contract-identity lookup, not a nearest-strike
    guess. On the rare duplicate key the last snapshot wins (deterministic, and the capture writes
    one row per contract).
    """
    index: dict[tuple[date, str, float], MarketStateSnapshot] = {}
    for snapshot in snapshots:
        fields = snapshot.instrument_key.split("|")
        if len(fields) != _CANONICAL_FIELD_COUNT:
            continue
        expiry_text = fields[_EXPIRY_SLOT]
        strike_text = fields[_STRIKE_SLOT]
        right = fields[_RIGHT_SLOT]
        if not expiry_text or not strike_text or right not in ("C", "P"):
            continue
        try:
            expiry = date.fromisoformat(expiry_text)
            strike = float(strike_text)
        except ValueError:
            continue
        index[(expiry, right, strike)] = snapshot
    return index


def _row_expiry(cell: ProjectedOptionAnalytics, fitted_expiry: date | None) -> date | None:
    """The cell's own listed expiry when Lane A stamped one, else the fitted-slice expiry.

    Lane A emits one row per real listed contract, carrying its own ``expiry`` (read here
    defensively so the BFF works both before and after that field lands on the contract). When the
    row has no expiry of its own, we fall back to the maturity-matched fitted slice's expiry, which
    is the expiry the surrounding grouping already resolved for this maturity.
    """
    own = getattr(cell, "expiry", None)
    if isinstance(own, date):
        return own
    return fitted_expiry


def _row_right(cell: ProjectedOptionAnalytics) -> str:
    """The cell's own option right when Lane A stamped one, else the canonical band resolver.

    Lane A keys each row to a listed contract and so carries the right directly; we honour that when
    present. Otherwise we resolve it from the delta-band label via the canonical resolver (D2),
    which (unlike the old local copy) correctly maps the bare ``atm`` pillar to the call.
    """
    own = getattr(cell, "option_right", None)
    if own in ("C", "P"):
        return own
    return option_right_for_band(cell.delta_band)


def _two_sided(quote: OptionQuote | None) -> tuple[float, float] | None:
    """The (bid, ask) of a clean two-sided quote, or None.

    "Clean two-sided" mirrors the coverage definition: both sides strictly positive and the ask not
    below the bid. A one-sided (e.g. ask-only, with the missing side coerced to 0.0) or crossed
    quote is not a usable market, so it yields no mark and never trips the out-of-spread flag.
    """
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    bid, ask = float(quote.bid), float(quote.ask)
    if bid <= 0.0 or ask <= 0.0 or ask < bid:
        return None
    return bid, ask


def _market_mark(quote: OptionQuote | None) -> float | None:
    """The market mark for a row: the mid of a clean two-sided quote, else None (D3).

    A spread-aware mark could weight by side, but with only top-of-book bid/ask the mid is the
    honest, symmetric mark. None when there is no clean two-sided quote, so the field degrades to
    null rather than inventing a mark from a one-sided book.
    """
    sides = _two_sided(quote)
    if sides is None:
        return None
    bid, ask = sides
    return 0.5 * (bid + ask)


def _price_outside_spread(model_price: float | None, quote: OptionQuote | None) -> bool:
    """True when the model price lands outside [bid, ask] by more than half the spread (D3).

    The bound is [bid - half_spread, ask + half_spread] where half_spread = (ask - bid) / 2, so the
    model price is flagged once it is more than one half-spread beyond the touch on either side.
    Only fires for a clean two-sided quote (else there is no market to reconcile against) and when
    the model carries a price. Verified on real ASML 2026-06-19 rows: the 30dp at K=1483 reads model
    94.70 vs market 89.2 / 91.8 (half-spread 1.30), comfortably outside -> flag fires.
    """
    if model_price is None:
        return False
    sides = _two_sided(quote)
    if sides is None:
        return False
    bid, ask = sides
    half_spread = 0.5 * (ask - bid)
    return model_price < bid - half_spread or model_price > ask + half_spread


def _point_with_market_reconciliation(
    cell: ProjectedOptionAnalytics, quote: OptionQuote | None
) -> dict[str, object]:
    """Serialize a row, then ADDITIVELY attach the model-vs-market reconciliation fields (D3).

    `price` (the theoretical Black-76 model price) keeps its name and value untouched. We add
    `market_mark` (the mid of a clean two-sided quote, else null) and `price_outside_spread` (the QC
    flag), so the front can show the model price beside a real market mark and badge the rows where
    the two disagree, without the BFF ever renaming or recomputing `price`.
    """
    point = projected_option_analytics_to_dict(cell, quote)
    point["market_mark"] = _market_mark(quote)
    point["price_outside_spread"] = _price_outside_spread(cell.price, quote)
    return point


def _quote_for_cell(
    cell: ProjectedOptionAnalytics,
    expiry: date | None,
    index: dict[tuple[date, str, float], MarketStateSnapshot],
) -> OptionQuote | None:
    """Attach the captured quote to a row by EXACT (expiry, right, listed strike) identity.

    No nearest-strike tolerance: the row strike is a real listed strike (Lane A), so we look up the
    snapshot for that exact contract. A tiny absolute tolerance absorbs float round-trip noise only.
    Returns None (a fully-null quote block) when no snapshot exists for that exact contract.
    """
    if expiry is None or cell.strike <= 0.0:
        return None
    right = _row_right(cell)
    snapshot = index.get((expiry, right, cell.strike))
    if snapshot is None:
        # Absorb float round-trip noise: match on the one snapshot at this (expiry, right) whose
        # strike is within an absolute epsilon. This is identity-preserving (epsilon, not a percent
        # band), so it never binds a different listed strike the way the old fuzzy join could.
        for (k_expiry, k_right, k_strike), candidate in index.items():
            if k_expiry != expiry or k_right != right:
                continue
            if abs(k_strike - cell.strike) <= _STRIKE_IDENTITY_ABS_TOL:
                snapshot = candidate
                break
    if snapshot is None:
        return None
    return OptionQuote(bid=snapshot.bid, ask=snapshot.ask, volume=snapshot.volume)


# The projected analytics live on a fixed reading-tenor grid (10d / 1m / 3m / 6m / 12m / 18m),
# while the fitted slices, forwards and listed-option snapshots live on the ACTUAL captured
# expiries (2026-06-26, 2026-07-17, ...). The two grids never share a `_maturity_key`, so an
# exact-key join silently left every cell with no slice, no forward and no quote, the symptom the
# PM saw as blank bid / ask / spread / volume columns. We instead pair each reading tenor with its
# NEAREST captured maturity, within a relative tolerance so a tenor with no captured neighbour stays
# honestly unpaired (e.g. a 3y read against a chain that stops at 18m) rather than being yoked to a
# far-off expiry.
_MATURITY_MATCH_REL_TOL = 0.25


def _nearest_by_maturity[T](
    maturity_years: float,
    candidates: list[tuple[float, T]],
) -> T | None:
    """The candidate whose maturity is closest to `maturity_years`, within the relative tolerance.

    `candidates` is a list of (candidate_maturity_years, candidate) pairs. Returns None when nothing
    is within tolerance (the reading tenor falls outside the captured chain), so the caller surfaces
    an honest gap instead of a mismatched join.
    """
    if not candidates or maturity_years <= 0.0:
        return None
    nearest_maturity, nearest = min(
        candidates, key=lambda pair: abs(pair[0] - maturity_years)
    )
    if abs(nearest_maturity - maturity_years) > _MATURITY_MATCH_REL_TOL * maturity_years:
        return None
    return nearest


def _smile_axis_cells(
    maturity_cells: list[ProjectedOptionAnalytics],
) -> list[ProjectedOptionAnalytics]:
    deduped: list[ProjectedOptionAnalytics] = []
    seen: set[float] = set()
    for cell in maturity_cells:
        if cell.target_delta in seen:
            continue
        seen.add(cell.target_delta)
        deduped.append(cell)
    return deduped


@dataclass(frozen=True, slots=True)
class _RateContext:
    """The ingested external curve + QC bound resolved for the analytics currency (ADR 0054)."""

    currency: str
    curve: RateCurve
    points: list[RiskFreeRatePoint]
    abs_bound: float
    disposition: str


def _load_rate_context(
    ctx: object, underlying: str, trade_date: date | None
) -> _RateContext | None:
    """Read the as-of `rates` partition for the underlying's currency and build the curve.

    Reads only the curve published as-of `trade_date` (no look-ahead — the store reads the partition
    for that day). Returns None when the currency, config, or rows are unavailable; the rest of the
    payload stays unaffected (additive surface).
    """
    try:
        registry = load_index_registry(ctx.configs_dir)  # type: ignore[attr-defined]
        currency = registry.get(underlying).currency
        platform = load_platform_config(ctx.configs_dir)  # type: ignore[attr-defined]
        currency_cfg = platform.rates.for_currency(currency)
    except (ConfigError, ConfigFieldError, IndexRegistryError, KeyError):
        return None
    rows: list[RiskFreeRatePoint] = ctx.store.read(  # type: ignore[attr-defined]
        "rates", trade_date=trade_date, underlying=currency
    )
    rows = [row for row in rows if row.currency == currency]
    if not rows:
        return None
    try:
        curve = curve_from_points(currency, rows)
    except (RateIngestError, RateCurveError):
        return None
    return _RateContext(
        currency=currency,
        curve=curve,
        points=rows,
        abs_bound=currency_cfg.spread_qc_abs_bound,
        disposition=currency_cfg.spread_qc_disposition,
    )


def _spread_for_maturity(
    forward: ForwardCurvePoint | None,
    rate_context: _RateContext | None,
) -> dict[str, object] | None:
    """Per-maturity external r(T) + implied−riskfree spread, from PERSISTED inputs only.

    Evaluates the ingested curve at the option's maturity (a read-time projection of the persisted
    pillars) and pairs it with the forward's persisted `implied_rate`. Returns None when either the
    curve or the implied rate is unavailable — never a recompute of any analytics value.
    """
    if rate_context is None or forward is None or forward.implied_rate is None:
        return None
    try:
        risk_free_rate = rate_context.curve.rate_at(forward.maturity_years)
    except RateCurveError:
        return None
    spread = implied_riskfree_spread(
        currency=rate_context.currency,
        maturity_years=forward.maturity_years,
        implied_rate=forward.implied_rate,
        risk_free_rate=risk_free_rate,
        abs_bound=rate_context.abs_bound,
        disposition=rate_context.disposition,
    )
    return implied_riskfree_spread_to_dict(spread)


def _group_by_maturity(
    cells: list[ProjectedOptionAnalytics],
    slices: list[SurfaceParameters],
    snapshots: list[MarketStateSnapshot],
    forwards: list[ForwardCurvePoint],
    rate_context: _RateContext | None = None,
    surface_side: str = SURFACE_SIDE_COMBINED,
) -> list[dict[str, object]]:
    """Maturity entries for ONE surface side (combined / call / put).

    The captured store carries the same maturities for each side, but call and put cells differ in
    IV (the two wings have genuinely different skew, per the captured SX5E close), so a per-side
    grouping is real data, not a re-slice of combined. The fitted SVI `surface_slice` is
    per-maturity (no side discriminator), so every side reads the same slice diagnostics — honest,
    since only one
    fit exists per maturity.
    """
    # The reading-tenor cell grid does not line up with the captured-expiry slices / forwards, so we
    # pair each by NEAREST maturity (within tolerance), not by an exact key that never matches. An
    # exact key still wins when present (the seeded-test case), since it is also the nearest.
    slice_candidates = [(s.maturity_years, s) for s in slices]
    forward_candidates = [(f.maturity_years, f) for f in forwards]
    quote_index = _listed_options_by_expiry_right_strike(snapshots)
    grouped: dict[str, list[ProjectedOptionAnalytics]] = {}
    for cell in cells:
        if cell.surface_side != surface_side:
            continue
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        maturity_cells = sorted(grouped[key], key=lambda c: c.target_delta)
        tenor_label = maturity_cells[0].tenor_label
        maturity_years = maturity_cells[0].maturity_years
        fitted = _nearest_by_maturity(maturity_years, slice_candidates)
        fitted_expiry = fitted.expiry_date if fitted is not None else None
        points = [
            _point_with_market_reconciliation(
                cell,
                _quote_for_cell(cell, _row_expiry(cell, fitted_expiry), quote_index),
            )
            for cell in maturity_cells
        ]
        forward = _nearest_by_maturity(maturity_years, forward_candidates)
        smile_cells = _smile_axis_cells(maturity_cells)
        entries.append(
            {
                "maturity_years": maturity_cells[0].maturity_years,
                "tenor_label": tenor_label,
                "label": f"{tenor_label} ({maturity_cells[0].maturity_years:.3f}y)",
                "smile": {
                    "axis_type": "delta",
                    "deltas": [cell.target_delta for cell in smile_cells],
                    "implied_vols": [cell.implied_vol for cell in smile_cells],
                    "log_moneyness": [cell.log_moneyness for cell in smile_cells],
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "rate_diagnostics": (
                    forward_rate_diagnostics_to_dict(forward) if forward is not None else None
                ),
                "rate_curve": _spread_for_maturity(forward, rate_context),
                "points": points,
            }
        )
    return entries


def _maturities_from_surface_grid(
    grid: list[SurfaceGrid],
    slices: list[SurfaceParameters],
) -> list[dict[str, object]]:
    slice_by_maturity = {_maturity_key(s.maturity_years): s for s in slices}
    grouped: dict[str, list[SurfaceGrid]] = {}
    for cell in grid:
        grouped.setdefault(_maturity_key(cell.maturity_years), []).append(cell)

    entries: list[dict[str, object]] = []
    for key in sorted(grouped, key=float):
        cells = sorted(grouped[key], key=lambda c: c.moneyness_bucket)
        maturity_years = cells[0].maturity_years
        buckets = [cell.moneyness_bucket for cell in cells]
        implied_vols = [
            math.sqrt(cell.total_variance / cell.maturity_years)
            if cell.total_variance > 0.0 and cell.maturity_years > 0.0
            else 0.0
            for cell in cells
        ]
        fitted = slice_by_maturity.get(key)
        label = f"{maturity_years:.3f}y"
        entries.append(
            {
                "maturity_years": maturity_years,
                "tenor_label": label,
                "label": label,
                "smile": {
                    "axis_type": "moneyness",
                    "moneyness_buckets": buckets,
                    "implied_vols": implied_vols,
                    "log_moneyness": buckets,
                },
                "surface_slice": (
                    surface_parameters_to_dict(fitted) if fitted is not None else None
                ),
                "rate_diagnostics": None,
                "points": [],
            }
        )
    return entries


# Canonical SVI calibration defaults (configs/pricing.yaml `surface:`), used to fit slices at
# request time when the platform config is unavailable (e.g. a store with no configs bundle). The
# bounds/iteration budget match the persisted-pipeline calibration so a request-time refit reads the
# same shape the stored slices were fit with.
_DEFAULT_SURFACE_CONFIG = SurfaceConfig(
    version="analytics-request-default",
    svi_a_bounds=(0.0, 10.0),
    svi_b_bounds=(1.0e-8, 10.0),
    svi_rho_bounds=(-0.999, 0.999),
    svi_m_bounds=(-5.0, 5.0),
    svi_sigma_bounds=(1.0e-8, 10.0),
    svi_bound_hit_tol=1.0e-5,
    svi_max_iterations=200,
)


def _surface_config(ctx: object) -> SurfaceConfig:
    """The platform's SVI calibration config, or the canonical default when it cannot be loaded.

    The request-time refit reuses the SAME SurfaceConfig the persisted pipeline calibrates with, so
    the served per-side slices read the same shape as the stored ones. When the config bundle is
    missing (a store stood up without configs), we fall back to the canonical pricing.yaml defaults
    rather than failing the whole surface.
    """
    try:
        return load_platform_config(ctx.configs_dir).surface  # type: ignore[attr-defined]
    except (ConfigError, ConfigFieldError):
        return _DEFAULT_SURFACE_CONFIG


def _clamped_slices_for_side(
    entries: list[dict[str, object]], config: SurfaceConfig
) -> list[ClampedSlice]:
    """Refit SVI per maturity from a side's smile cells, clamped to each slice's quoted window.

    For each maturity entry produced by :func:`_group_by_maturity`, take its captured smile (the
    distinct log-moneyness points and aligned implied vols), convert to total variance
    (``iv^2 * T``), and refit SVI. The fitted slice is paired with the ``[k_lo, k_hi]`` window the
    side actually quoted at that maturity, so the dense reconstruction never extrapolates the wing.
    A maturity is skipped when it has too few distinct points to fit, a degenerate window, or the
    fit raises; the caller treats fewer than two usable slices as "no dense surface" (None).
    """
    slices: list[ClampedSlice] = []
    for entry in entries:
        smile = entry.get("smile")
        if not isinstance(smile, dict):
            continue
        logms = smile.get("log_moneyness")
        ivs = smile.get("implied_vols")
        if not isinstance(logms, list) or not isinstance(ivs, list):
            continue
        try:
            maturity_years = float(entry["maturity_years"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            continue
        if maturity_years <= 0.0:
            continue
        # Distinct, sorted log-moneyness with the aligned IV (last value wins on a duplicate k).
        by_k: dict[float, float] = {}
        for k, iv in zip(logms, ivs, strict=False):
            try:
                by_k[float(k)] = float(iv)
            except (TypeError, ValueError):
                continue
        ks = sorted(by_k)
        if len(ks) < 2:
            continue
        k_lo, k_hi = ks[0], ks[-1]
        if k_hi <= k_lo:
            continue
        total_variances = tuple(by_k[k] * by_k[k] * maturity_years for k in ks)
        try:
            fit = fit_svi(tuple(ks), total_variances, config=config)
        except (ValueError, FloatingPointError):
            continue
        params: SviParams = fit.params
        slices.append(
            ClampedSlice(
                maturity_years=maturity_years, params=params, k_lo=k_lo, k_hi=k_hi
            )
        )
    return slices


def _dense_dict_for_side(
    entries: list[dict[str, object]], config: SurfaceConfig, model_version: str
) -> dict[str, object] | None:
    """One unified dense (maturity x log-moneyness) IV grid for a side, via clamped SVI refit.

    Builds clamped SVI slices from the side's smile cells and reconstructs a dense grid that
    interpolates total variance AND the quoted window in maturity, NaN-holing every cell outside the
    quoted wing (no extrapolation). Returns None when fewer than two slices are usable, so a side
    that cannot read as a surface serializes as null (honest degrade), same as before.
    """
    slices = _clamped_slices_for_side(entries, config)
    dense = reconstruct_dense_surface_clamped(slices, model_version=model_version)
    return dense_surface_to_dict(dense) if dense is not None else None


@router.get("")
def get_analytics(
    ctx: CtxDep,
    trade_date: TradeDateDep,
    underlying: str | None = None,
    run_id: str | None = None,
) -> JSONResponse:
    # `run_id` (D6) addresses ONE capture within `trade_date` (the capture-receipt instant, an
    # ISO-8601 string). None (the default and the historical behaviour) reads the latest capture.
    # The store-side addressability is Lane C's; this router forwards run_id additively so the read
    # narrows to that capture once it lands, and is a no-op latest-read until then.
    resolved_underlying = underlying or ctx.default_underlying
    cells: list[ProjectedOptionAnalytics] = _read_for_underlying(
        ctx,
        "projected_option_analytics",
        resolved_underlying,
        trade_date=trade_date,
        run_id=run_id,
    )
    slices: list[SurfaceParameters] = _read_for_underlying(
        ctx,
        "surface_parameters",
        resolved_underlying,
        trade_date=trade_date,
        run_id=run_id,
    )
    snapshots: list[MarketStateSnapshot] = _read_for_underlying(
        ctx,
        "market_state_snapshots",
        resolved_underlying,
        trade_date=trade_date,
        run_id=run_id,
    )
    forwards: list[ForwardCurvePoint] = _read_for_underlying(
        ctx,
        "forward_curve",
        resolved_underlying,
        trade_date=trade_date,
        run_id=run_id,
    )
    rate_context = _load_rate_context(ctx, resolved_underlying, trade_date)
    maturities = _group_by_maturity(
        cells, slices, snapshots, forwards, rate_context, SURFACE_SIDE_COMBINED
    )
    source = "projected_option_analytics"
    if not maturities:
        grid: list[SurfaceGrid] = _read_for_underlying(
            ctx,
            "surface_grid",
            resolved_underlying,
            trade_date=trade_date,
            run_id=run_id,
        )
        maturities = _maturities_from_surface_grid(grid, slices)
        if maturities:
            source = "surface_grid"

    # Per-side views (call / put / combined), each built by ONE method: refit SVI from that side's
    # captured smile cells, clamp every slice to its own quoted log-moneyness window, and
    # reconstruct a dense grid that NaN-holes anything outside the quoted wing (never extrapolates).
    # The captured store carries genuinely different IV for the call wing and the put wing, so each
    # side is its own data, not a re-slice of combined. `combined` uses the combined `maturities`
    # already grouped above, so the top-level `surface` is byte-identical to
    # `surfaces_by_side["combined"]`. A side that cannot read as a surface (fewer than two fittable
    # slices) serializes as null, so the front degrades honestly rather than showing a fake wing.
    surface_config = _surface_config(ctx)
    model_version = slices[0].model_version if slices else "svi"
    sides: dict[str, list[dict[str, object]]] = {}
    surfaces_by_side: dict[str, dict[str, object] | None] = {}
    for side in SURFACE_SIDES:
        if side == SURFACE_SIDE_COMBINED:
            side_entries = maturities
        else:
            side_entries = _group_by_maturity(
                cells, slices, snapshots, forwards, rate_context, side
            )
        sides[side] = side_entries
        surfaces_by_side[side] = _dense_dict_for_side(
            side_entries, surface_config, model_version
        )
    combined_dense = surfaces_by_side[SURFACE_SIDE_COMBINED]

    coverage = coverage_from_snapshots(snapshots)
    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": trade_date.isoformat() if trade_date else None,
            "close_instant": resolve_close_instant(ctx, resolved_underlying, trade_date),
            "n_maturities": len(maturities),
            "source": source,
            "maturities": maturities,
            "surface": combined_dense,
            "sides": sides,
            "surfaces_by_side": surfaces_by_side,
            "sides_available": sorted(
                side for side, entries in sides.items() if entries
            ),
            "coverage": coverage.to_dict() if coverage.option_rows > 0 else None,
            "rate_curve": (
                rate_curve_to_dict(
                    rate_context.currency, rate_context.curve, rate_context.points
                )
                if rate_context is not None
                else None
            ),
        }
    )

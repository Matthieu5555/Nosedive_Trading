"""Volatility infrastructure layer (level 1): strategy-agnostic market plumbing.

Market-data capture, the instrument master, snapshot/forward/IV/surface/pricing
analytics, risk, and storage — all built on :mod:`algotrading.core` and the frozen
:mod:`algotrading.infra.contracts` seam. This layer never imports a layer above it
(strategy, execution, frontend); import-linter enforces that mechanically.
"""

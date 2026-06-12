"""Transitional aliases: the checks now read ``QcThresholdConfig`` directly (M37).

The 125-line ``QcThresholds`` wrapper this module used to hold forwarded every property
1:1 to the already-typed, frozen, hashed :class:`~algotrading.core.config.QcThresholdConfig`
— no validation, no derived value, no information hiding — so every new QC cut-off was a
two-place edit. The checks (:mod:`.checks`) now take the config object itself; the nested
paths (``thresholds.continuity.max_gap_count``) name which config block owns each cut-off,
and the version stamp is ``thresholds.version``. Every value read is identical, so
``QcResult`` rows are byte-identical (ADR 0028 unchanged: every economic cut-off lives in
the hashed config, never as a ``.py`` literal).

What remains here is back-compat for callers that still import the old names
(``orchestration.eod_stages`` / ``orchestration.qc_job``): ``QcThresholds`` is now *the*
config type, and :func:`thresholds_from_config` is the identity. Once those call sites
import ``QcThresholdConfig`` directly, delete this module.
"""

from __future__ import annotations

from algotrading.core.config import QcThresholdConfig

# Back-compat alias: the "threshold bundle" IS the typed config now.
QcThresholds = QcThresholdConfig


def thresholds_from_config(config: QcThresholdConfig) -> QcThresholdConfig:
    """Identity, kept for back-compat: the checks consume ``QcThresholdConfig`` directly."""
    return config

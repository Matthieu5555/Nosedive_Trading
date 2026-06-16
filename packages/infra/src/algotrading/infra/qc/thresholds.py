from __future__ import annotations

from algotrading.core.config import QcThresholdConfig

QcThresholds = QcThresholdConfig


def thresholds_from_config(config: QcThresholdConfig) -> QcThresholdConfig:
    return config

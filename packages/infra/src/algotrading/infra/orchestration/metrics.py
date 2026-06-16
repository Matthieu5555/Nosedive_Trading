from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

_RUN_SECONDS_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass(frozen=True, slots=True)
class OrchestrationMetrics:

    registry: CollectorRegistry
    events_collected: Counter
    stale_quote_ratio: Gauge
    forward_failures: Counter
    solver_failures: Counter
    scenario_run_seconds: Histogram

    def record_run_seconds(self, job: str, seconds: float) -> None:
        self.scenario_run_seconds.labels(job=job).observe(seconds)


def build_metrics(registry: CollectorRegistry | None = None) -> OrchestrationMetrics:
    reg = registry if registry is not None else CollectorRegistry()
    return OrchestrationMetrics(
        registry=reg,
        events_collected=Counter(
            "events_collected_total",
            "Observations a collector session persisted.",
            labelnames=("underlying",),
            registry=reg,
        ),
        stale_quote_ratio=Gauge(
            "stale_quote_ratio",
            "Fraction of a snapshot batch whose quotes were not usable (0..1).",
            labelnames=("underlying",),
            registry=reg,
        ),
        forward_failures=Counter(
            "forward_failures_total",
            "Forwards that could not be recovered for a maturity.",
            labelnames=("underlying",),
            registry=reg,
        ),
        solver_failures=Counter(
            "solver_failures_total",
            "IV solves that did not converge.",
            labelnames=("underlying",),
            registry=reg,
        ),
        scenario_run_seconds=Histogram(
            "scenario_run_seconds",
            "Wall time of one analytics/scenario run, in seconds.",
            labelnames=("job",),
            buckets=_RUN_SECONDS_BUCKETS,
            registry=reg,
        ),
    )


def sample_value(registry: CollectorRegistry, name: str, labels: dict[str, str]) -> float:
    value = registry.get_sample_value(name, labels)
    if value is None and not name.endswith("_total"):
        value = registry.get_sample_value(f"{name}_total", labels)
    return float(value) if value is not None else 0.0

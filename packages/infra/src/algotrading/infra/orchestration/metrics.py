"""Operational metrics for the orchestration plane — few, well-labeled, injectable.

The spec gotcha is explicit: prefer fewer well-labeled metrics over many opaque
ones. So this module exposes one bundle, :class:`OrchestrationMetrics`, built over a
caller-supplied :class:`prometheus_client.CollectorRegistry`. Injecting the registry
keeps the metrics out of the process-global default registry, which is what makes
them unit-testable — a test builds a fresh registry, runs a job, and reads the exact
sample back, with no cross-test bleed.

The five families the roadmap names map onto five metrics:

* ``events_collected_total`` — a counter of observations a collector session
  persisted, labeled by underlying. The event *rate* is this counter over time;
  the dashboard and alerts read deltas of it.
* ``stale_quote_ratio`` — a gauge in ``[0, 1]`` of the fraction of a snapshot batch
  whose quotes were not usable (stale/wide/crossed), labeled by underlying.
* ``forward_failures_total`` — a counter of forwards that could not be recovered for
  a maturity (no usable call/put pair, or an unusable estimate), labeled by
  underlying.
* ``solver_failures_total`` — a counter of IV solves that did not converge, labeled
  by underlying.
* ``scenario_run_seconds`` — a histogram of how long a scenario/analytics run took,
  labeled by job. Duration is measured by the caller against an injected clock and
  observed here, so nothing in this module reads a wall clock.

Every metric carries a label so an operator can see *which* underlying or job is
hot, not just that something is — the "well-labeled" half of the gotcha.
"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Histogram buckets for a single analytics/scenario run, in seconds. A day's run for
# one underlying is sub-second on the fixtures and a few seconds at full chain width;
# these buckets bracket "fast / normal / slow / pathological" without inventing a
# metric per band.
_RUN_SECONDS_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


@dataclass(frozen=True, slots=True)
class OrchestrationMetrics:
    """The five operational metrics, bound to one registry.

    Build with :func:`build_metrics` over a fresh registry. The fields are the live
    prometheus objects; callers ``.labels(...).inc()`` / ``.set()`` / ``.observe()``
    on them. Keeping them in one frozen bundle means a job takes a single
    ``metrics`` dependency rather than five loose globals.
    """

    registry: CollectorRegistry
    events_collected: Counter
    stale_quote_ratio: Gauge
    forward_failures: Counter
    solver_failures: Counter
    scenario_run_seconds: Histogram

    def record_run_seconds(self, job: str, seconds: float) -> None:
        """Observe one run's duration under the ``scenario_run_seconds`` histogram."""
        self.scenario_run_seconds.labels(job=job).observe(seconds)


def build_metrics(registry: CollectorRegistry | None = None) -> OrchestrationMetrics:
    """Build the metric bundle over a registry (a fresh private one by default).

    Pass a :class:`CollectorRegistry` to scope the metrics (tests pass their own so
    samples don't leak between cases); omit it to get a private registry rather than
    the process-global default, so importing this module never registers anything
    globally.
    """
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
    """Read one current sample value from a registry, or ``0.0`` if absent.

    A thin read helper so the dashboard and the tests pull a metric's value by name
    and label set without poking prometheus internals. Counters store their value
    under ``<name>_total``; this resolves both the bare and the ``_total`` form, so a
    caller passes the logical name (``events_collected_total``) and gets the number.
    Returns ``0.0`` when no such sample exists yet rather than raising — an
    un-incremented counter reads as zero, which is the truthful answer.
    """
    value = registry.get_sample_value(name, labels)
    if value is None and not name.endswith("_total"):
        value = registry.get_sample_value(f"{name}_total", labels)
    return float(value) if value is not None else 0.0

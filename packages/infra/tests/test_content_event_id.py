"""Pin the content-addressed, idempotent event id the unified collector relies on.

The one collection seam is the push ``collectors.BrokerTick`` + ``RawCollector`` (ADR 0027);
the contract layer keeps only :func:`content_event_id`, the idempotency primitive. These tests
pin it: the same observation re-delivered hashes to the same id (so a reconnect re-delivery
dedups), distinct observations get distinct ids, and the id is stable across processes without
``PYTHONHASHSEED`` — the determinism rule in ``tasks/TESTING.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys

from algotrading.infra.contracts import content_event_id


def test_content_event_id_is_idempotent_for_a_redelivered_tick() -> None:
    # Same instrument/field/sequence -> same id: a reconnect re-delivery dedups.
    first = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    again = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    assert first == again


def test_content_event_id_distinguishes_distinct_observations() -> None:
    base = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    assert content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 2) != base
    assert content_event_id("SPX|IND|CBOE|USD|1|con-1||", "bid", 1) != base


def test_content_event_id_is_stable_across_processes() -> None:
    expected = content_event_id("SPX|IND|CBOE|USD|1|con-1||", "last", 1)
    code = (
        "from algotrading.infra.contracts import content_event_id;"
        "print(content_event_id('SPX|IND|CBOE|USD|1|con-1||','last',1))"
    )
    for seed in ("0", "3", "55555"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected

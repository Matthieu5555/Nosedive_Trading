from __future__ import annotations

import os
import subprocess
import sys

from algotrading.infra.contracts import content_event_id


def test_content_event_id_is_idempotent_for_a_redelivered_tick() -> None:
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

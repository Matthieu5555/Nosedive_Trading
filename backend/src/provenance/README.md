# provenance

The stamp every derived record carries, answering "where did this number come
from?" in one object: which source records fed it, when they happened, when it was
computed, which code version and which config produced it. This module is the
mechanism behind two of the platform's four invariants — provenance on everything,
and determinism (the same inputs always produce the same stamp).

## Why this exists

A surface parameter or a risk aggregate is useless if you cannot say what it was
built from. Without a stamp, "recompute yesterday and check it matches" is
impossible, look-ahead cheating in research is undetectable, and a wrong number
cannot be traced to its inputs. So every derived record carries a
`ProvenanceStamp`, and the stamp is built so that the same inputs always hash to
the same value, on any machine, in any process. That determinism is what turns the
stamp from a label into a guarantee: two runs that agree on inputs produce
byte-identical stamps, and a stamp whose stored hash no longer matches its
contents is provably tampered with.

## The public interface

Import from `provenance`:

- `ProvenanceStamp` — the immutable stamp dataclass.
- `SourceRecordRef` — a typed pointer to one source record (its table plus full
  primary key).
- `stamp(...)` — build a valid stamp. The normal way to make one.
- `source_ref(table, *key_values)` — build one `SourceRecordRef` from a table
  name and the source record's key fields, in registry key order.
- `canonical_primary_key(values)` — reduce a primary-key tuple to its canonical
  string components. Used by both producers (building refs) and storage
  (resolving lineage), so the two sides agree on what a key "is".
- `validate_stamp(candidate)` — the gate that proves a stamp is trustworthy.
  Raises on the first failure; returns `None` when valid.
- `ProvenanceError` and `ProvenanceValidationError` — the failure types.

## What the stamp captures, and why each field

A `ProvenanceStamp` is frozen and holds six fields:

| Field | What it captures | Why |
|-------|------------------|-----|
| `calc_ts` | When the computation ran (tz-aware) | Places the result in time; a naive timestamp is refused. |
| `code_version` | Version of the code that produced the value | Lets a result be tied to the exact logic; "same code live and replay" is checkable. |
| `config_hash` | Hash of the active config | Links the result to the exact economic settings (see `config`). |
| `source_records` | Typed refs to the source records used, in canonical order | The lineage handle — "which rows produced this?" |
| `source_timestamps` | Timestamps of those sources, in canonical order | The as-of evidence — what data times fed the result. |
| `stamp_hash` | SHA-256 of all of the above | The determinism handle; the load-bearing tamper check. |

A source is named by a `SourceRecordRef`: its table plus its *full* primary key,
not a bare id. Raw events are keyed `(session_id, event_id)`, so a stamp that
stored only `event_id` would conflate two sessions that happen to share an event
id. Carrying the whole key lets lineage resolve to exactly one row — and lets a
stamp point at any table (a snapshot, a forward point), not just raw events.

## How determinism is made real

Three choices turn "should be deterministic" into "is deterministic", and each is
pinned by a test in `tests/test_provenance.py`:

First, the source-record list and source-timestamp list are sorted into a
canonical order when the stamp is built. Feeding the same sources in a different
order yields a byte-identical stamp, because order of arrival is an accident of
plumbing, not part of the result. (`test_reordering_sources_yields_an_identical_
stamp`, `test_stamp_hash_is_invariant_to_source_order`.)

Second, a reference's key components are stored as canonical strings — timestamps
as UTC ISO, dates as ISO dates, everything else via `str`. That keeps a reference
JSON-serializable for storage and keeps the content hash independent of how a key
element happened to be typed in memory. (`test_source_ref_canonicalizes_timestamp_
key_components`.)

Third, the content hash is SHA-256 of canonical JSON, not Python's salted
`hash()`, so it is identical across processes and machines — the same reasoning as
`config_hash`. (`test_stamp_hash_is_stable_across_processes_and_hash_seeds`.)

## Data flow

```text
producer:                              storage (lineage read):
  source_ref(table, *key)                read stamp off a record
        |                                       |
        v                                       v
  stamp(calc_ts=, code_version=,         for each SourceRecordRef:
        config_hash=, source_records=,     match a source row whose
        source_timestamps=)                canonical_primary_key is in the ref
        |  (sorts + hashes)                      |
        v                                        v
  ProvenanceStamp  --> attached to a       the exact source records,
                       derived record         grouped by table
```

A producer builds one `SourceRecordRef` per input with `source_ref`, passing the
key fields in the table's registry key order — e.g. `source_ref("raw_market_
events", session_id, event_id)`. It then calls `stamp(...)` with those refs, the
source timestamps, the calc time, the code version, and the config hash. The
result is attached to the derived record. Storage later reads the refs back off
the stamp and resolves each by its full canonical key (see `storage`'s
`source_records_for` / `raw_events_for`), using the same `canonical_primary_key`
the producer used so the two sides match by construction.

## State and lifecycle

A stamp is immutable. It has exactly two states from the platform's point of view:
built-by-`stamp` (valid by construction) and everything-else (hand-built, mutated,
or read back from an untrusted source — must be validated before it is trusted).
`validate_stamp` is the gate between them.

## Failure modes

`stamp` raises `ProvenanceError` if asked to build from invalid inputs — most
importantly a naive (non-tz-aware) `calc_ts` or source timestamp, because a stamp
with an ambiguous time is worse than no stamp.

`validate_stamp` raises `ProvenanceValidationError` on the first problem, carrying
the offending field, value, and a plain-language reason. It checks, in order: the
candidate is a `ProvenanceStamp`; `calc_ts` is tz-aware; `code_version`,
`config_hash`, and `stamp_hash` are non-empty; every source timestamp is tz-aware;
every source ref has a table and a non-empty primary key; and — the load-bearing
check — the stored `stamp_hash` equals a fresh recomputation from the stamp's own
contents. The last check is what catches a tampered field, since a mutated value
whose hash was not updated no longer matches. (`test_validate_stamp_rejects_a_
tampered_hash`, `test_validate_stamp_rejects_a_mutated_field_whose_hash_was_not_
updated`.)

The storage write path runs this gate (via `contracts.validation`, which delegates
to `validate_stamp`) on every provenance-bearing record, so a stamp that does not
validate is refused at the write door rather than landing on disk. None of these
failures are retryable: a bad stamp is a producer bug.

## Fastest way to exercise it

```python
from datetime import UTC, datetime
from provenance import stamp, source_ref, validate_stamp

now = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
s = stamp(
    calc_ts=now,
    code_version="0.1.0",
    config_hash="cfg-hash-0",
    source_records=(source_ref("raw_market_events", "sess-1", "evt-1"),),
    source_timestamps=(now,),
)
validate_stamp(s)        # passes; a freshly built stamp is always valid
print(s.stamp_hash)      # stable SHA-256 hex
```

`fixtures.records.make_stamp` builds the same shape for tests. From `backend/`,
the behavior is pinned by `tests/test_provenance.py`; run it with
`uv run pytest -q tests/test_provenance.py`.

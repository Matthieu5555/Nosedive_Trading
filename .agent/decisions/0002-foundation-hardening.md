# 0002 — Foundation hardening: lineage keys, atomic writes, schema enforcement

- **Status:** accepted
- **Date:** 2026-06-01

## Context

A deep-modules review (the `review-module-depth` standard) of the Workstream A
foundation found three places where the storage and provenance interfaces promised
more than they delivered: lineage was keyed on a single field, multi-partition
writes were not atomic, and schema evolution was documented but not enforced.
Because B/C/D/E all sit on these seams, a leak here propagates into four
workstreams, so the fixes were made before fan-out. This ADR records the
non-obvious choices so they are not re-litigated.

## Decision

1. **Source lineage is keyed by the full primary key, carried as canonical
   strings.** A stamp's sources are now `SourceRecordRef(table, primary_key)`, not
   bare ids. Raw events are identified by `(session_id, event_id)`; storing only
   `event_id` conflated two sessions that share an event id. The key components are
   stored as canonical *strings* (timestamps as UTC ISO, everything else via `str`),
   not the original typed objects, because the stamp is serialized to a JSON column
   and must hash identically across machines. `source_ref(table, *key_values)` and
   `canonical_primary_key` build the strings once, and storage reuses the same
   function to canonicalize a read-back record's key before matching, so the two
   sides agree by construction.

2. **Lineage resolution is generic.** `source_records_for(record)` returns matching
   sources grouped by table, for any source table; `raw_events_for` is the
   raw-market-events slice of it. A surface built from snapshots resolves the same
   way a snapshot built from raw events does.

3. **`write()` is all-or-nothing.** Every touched partition is fully prepared —
   including the append-only collision check — before any is committed, then each is
   written to a temp file and renamed into place only once all are staged. The old
   code wrote partitions one at a time and could leave an earlier one written when a
   later one collided.

4. **Schema evolution is enforced on read.** `from_row` defaults an absent-or-null
   column to `None` only for an `Optional` field; a missing *required* field raises
   `SchemaCompatibilityError` rather than building an invalid instance (e.g.
   `IvPoint(k=None)`).

5. **One provenance validator.** `validate_stamp` is the single gate for stamp
   wellformedness — tz-aware timestamps, non-empty version/config/hash, well-formed
   refs, and a hash matching a fresh recomputation. `validate_record` delegates to
   it, so a tampered stamp is refused at the write door.

6. **The contracts public surface is the seam, not the machinery.** `contracts`
   re-exports the dataclasses, `validate`/`validate_record`, and
   `table_for_contract`/`spec_for_table` — not the registry introspection helpers
   (`REGISTRY`, `resolved_field_types`, ...), which are how the codec is built.

## Alternatives considered

- **`SourceRecordRef.primary_key: tuple[object, ...]` (typed keys).** The obvious
  shape, but typed objects do not survive the stamp's JSON round-trip and would make
  the content hash depend on in-memory typing. Canonical strings solve storage,
  hashing, and matching at once. Rejected.
- **Push the lineage filter into DuckDB**, as the old `raw_events_for` did. Keeps the
  scan in the engine, but matching a composite key of heterogeneous types (a
  timestamp column against a bound value) is fiddly and was the source of the
  original narrowness. `source_records_for` reads the referenced table and filters in
  Python — correct and generic, at the cost of pushdown. If raw-event lineage becomes
  hot, a typed pushdown filter is the follow-up. Recorded so the tradeoff is visible.
- **Coerce a missing required column to `None`**, as the old `from_row` did. Simple,
  but it silently produces invalid instances and pushes the check onto every
  consumer. Refusing the read keeps the contract boundary honest. Rejected.

## Consequences

- Downstream workstreams can trust: lineage resolves to exactly the right source
  rows; a `write` batch lands wholly or not at all; a record read back has every
  required field populated or the read fails loudly; a stamp that validates has a
  hash matching its contents.
- The one failure mode not covered is a process crash *between* the final renames in
  a multi-partition commit; it is documented in `_commit` and `storage/README.md`
  rather than claimed away.
- Producers build stamps by passing source keys in the table's registry key order
  via `source_ref`. The contracts seam no longer exposes registry internals, so a
  consumer that needs introspection requests a method on the seam rather than
  reaching into `REGISTRY`.
- Each guarantee is pinned by a test in `tests/test_storage.py` and
  `tests/test_provenance.py`; the gate is green at 95 tests.

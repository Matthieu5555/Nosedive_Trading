"""Tests for the canonical-store backup/restore tool (``scripts/backup_data_store.py``).

The driver lives in the top-level ``scripts/`` dir (outside any package), so — like
``test_smoke_e2e.py`` — we put it on the path and import it. Expected checksums are computed
**independently** with ``hashlib`` over the exact bytes the test wrote, never read back from the
tool's own manifest; the byte-identical claims are then asserted against that independent oracle.

Everything runs in ``tmp_path`` temp stores (the capture discipline: never touch the canonical
``data/``). The cases mirror the task's test surface: backup→restore byte-identical, simulated
loss recovery, source left untouched, the canonical-restore refusal, derived include/exclude, and
manifest-based corruption detection.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import backup_data_store as backup  # noqa: E402

# Two fixed instants so snapshot ids are deterministic and two backups land in distinct dirs.
_NOW = datetime(2026, 6, 15, 18, 15, 0, tzinfo=UTC)
_LATER = datetime(2026, 6, 15, 19, 30, 0, tzinfo=UTC)


def _sha256_bytes(payload: bytes) -> str:
    """Independent reference digest (the oracle), computed straight from the known bytes."""
    return hashlib.sha256(payload).hexdigest()


def _make_store(root: Path, *, with_derived: bool = True) -> dict[str, bytes]:
    """Build a tiny but realistically-shaped store; return {relative_posix_path: bytes} written.

    Mirrors the real layout the tool selects: partitioned ``raw/`` parquet, the
    ``_run_state.jsonl`` ledger, and (optionally) a reconstructable ``derived/`` tree.
    """
    files: dict[str, bytes] = {
        "raw/raw_market_events/trade_date=2026-06-15/underlying=SX5E/data.parquet": b"raw-events-bytes-A",
        "raw/daily_bar/provider=IBKR/trade_date=2026-06-15/underlying=SIE/data.parquet": b"raw-bar-bytes-B",
        "raw/instrument_master/trade_date=2026-06-15/data.parquet": b"raw-instr-bytes-C",
        "_run_state.jsonl": b'{"stage":"collection","outcome":"ok"}\n',
    }
    if with_derived:
        files["derived/surface/trade_date=2026-06-15/underlying=SX5E/data.parquet"] = b"derived-bytes-D"
        files["qc/trade_date=2026-06-15/report.json"] = b'{"qc":"pass"}'
    for rel, payload in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return files


def _snapshot_dir(backup_root: Path, manifest: backup.BackupManifest) -> Path:
    return backup_root / manifest.snapshot_id


def test_backup_then_restore_is_byte_identical(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    written = _make_store(source, with_derived=False)  # keystone only

    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)

    # Manifest covers exactly the keystone files, each with the independently-computed digest.
    assert manifest.file_count == len(written)
    by_path = {e.path: e for e in manifest.files}
    assert set(by_path) == set(written)
    for rel, payload in written.items():
        assert by_path[rel].sha256 == _sha256_bytes(payload)  # oracle, not the tool
        assert by_path[rel].bytes == len(payload)

    # Restore into a SECOND temp store and confirm every byte matches the original source.
    target = tmp_path / "restored"
    result = backup.restore_store(
        _snapshot_dir(backup_root, manifest), target, canonical_root=source
    )
    assert result.verify.ok
    for rel, payload in written.items():
        assert (target / rel).read_bytes() == payload


def test_simulated_loss_recovers_raw_and_ledger(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    written = _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)

    # Simulate disk loss: the canonical store is gone entirely.
    import shutil

    shutil.rmtree(source)
    assert not source.exists()

    # Restore reconstructs raw + ledger identically into a fresh location.
    recovered = tmp_path / "recovered"
    result = backup.restore_store(
        _snapshot_dir(backup_root, manifest), recovered, canonical_root=source
    )
    assert result.verify.ok
    assert (recovered / "_run_state.jsonl").read_bytes() == written["_run_state.jsonl"]
    raw_files = sorted(p.relative_to(recovered).as_posix() for p in (recovered / "raw").rglob("*") if p.is_file())
    assert raw_files == sorted(r for r in written if r.startswith("raw/"))


def test_backup_writes_nothing_into_source(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    written = _make_store(source, with_derived=True)
    # Independent fingerprint of the whole source tree before the backup.
    before = {
        p.relative_to(source).as_posix(): _sha256_bytes(p.read_bytes())
        for p in source.rglob("*")
        if p.is_file()
    }

    backup.backup_store(source, backup_root, include_derived=True, now=_NOW)

    after = {
        p.relative_to(source).as_posix(): _sha256_bytes(p.read_bytes())
        for p in source.rglob("*")
        if p.is_file()
    }
    assert after == before  # source untouched: same files, same bytes
    assert set(before) == set(written)


def test_restore_refuses_canonical_without_gate(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    snapshot = _snapshot_dir(backup_root, manifest)

    # Restoring onto the canonical store is refused by default...
    with pytest.raises(backup.RestoreTargetError) as exc:
        backup.restore_store(snapshot, source, canonical_root=source)
    assert exc.value.target == source

    # ...but the explicit gate (with --force, since canonical is non-empty) permits it.
    result = backup.restore_store(
        snapshot, source, canonical_root=source, allow_canonical=True, force=True
    )
    assert result.verify.ok


def test_restore_refuses_non_empty_target_without_force(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)

    target = tmp_path / "occupied"
    (target).mkdir()
    (target / "leftover.txt").write_text("prior contents")
    with pytest.raises(backup.RestoreTargetError):
        backup.restore_store(_snapshot_dir(backup_root, manifest), target, canonical_root=source)


def test_default_excludes_derived_include_flag_adds_it(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    written = _make_store(source, with_derived=True)
    keystone = {r for r in written if r.startswith("raw/") or r == "_run_state.jsonl"}
    derived = set(written) - keystone

    default = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    assert {e.path for e in default.files} == keystone  # derived excluded by default

    full = backup.backup_store(source, backup_root, include_derived=True, now=_LATER)
    assert {e.path for e in full.files} == keystone | derived  # derived now included
    assert derived  # guard: the fixture actually had derived files to exclude


def test_verify_detects_corruption(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    snapshot = _snapshot_dir(backup_root, manifest)

    assert backup.verify_snapshot(snapshot).ok  # clean immediately after backup

    # Corrupt one byte inside the stored snapshot; verify must flag exactly that file.
    corrupted_rel = manifest.files[0].path
    stored_file = snapshot / "store" / corrupted_rel
    stored_file.write_bytes(stored_file.read_bytes() + b"corruption")
    result = backup.verify_snapshot(snapshot)
    assert not result.ok
    assert result.mismatched == (corrupted_rel,)


def test_backup_is_append_only(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)

    first = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    second = backup.backup_store(source, backup_root, include_derived=False, now=_LATER)
    assert first.snapshot_id != second.snapshot_id  # distinct timestamped dirs

    # A re-fire at the SAME instant refuses rather than overwrite the prior snapshot.
    with pytest.raises(backup.BackupError):
        backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    # The first snapshot still verifies — it was never mutated.
    assert backup.verify_snapshot(_snapshot_dir(backup_root, first)).ok


def test_backup_refuses_when_no_keystone_present(tmp_path: Path) -> None:
    # A directory that exists but holds none of the keystone trees is a likely wrong --data-root.
    empty = tmp_path / "not-a-store"
    empty.mkdir()
    (empty / "stray.txt").write_text("x")
    with pytest.raises(backup.BackupError):
        backup.backup_store(empty, tmp_path / "backups", include_derived=False, now=_NOW)


def test_backup_root_resolution_requires_a_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(backup.BACKUP_ROOT_ENV_VAR, raising=False)
    with pytest.raises(backup.BackupError):
        backup.backup_root_from_env_or_arg(None)  # neither arg nor env -> refuse to guess
    # An explicit arg wins; the env var is the fallback.
    assert backup.backup_root_from_env_or_arg("/mnt/b") == Path("/mnt/b")
    monkeypatch.setenv(backup.BACKUP_ROOT_ENV_VAR, "/mnt/env")
    assert backup.backup_root_from_env_or_arg(None) == Path("/mnt/env")

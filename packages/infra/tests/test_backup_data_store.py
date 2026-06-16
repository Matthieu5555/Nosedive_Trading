from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import backup_data_store as backup  # noqa: E402

_NOW = datetime(2026, 6, 15, 18, 15, 0, tzinfo=UTC)
_LATER = datetime(2026, 6, 15, 19, 30, 0, tzinfo=UTC)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _make_store(root: Path, *, with_derived: bool = True) -> dict[str, bytes]:
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
    written = _make_store(source, with_derived=False)

    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)

    assert manifest.file_count == len(written)
    by_path = {e.path: e for e in manifest.files}
    assert set(by_path) == set(written)
    for rel, payload in written.items():
        assert by_path[rel].sha256 == _sha256_bytes(payload)
        assert by_path[rel].bytes == len(payload)

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

    import shutil

    shutil.rmtree(source)
    assert not source.exists()

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
    assert after == before
    assert set(before) == set(written)


def test_restore_refuses_canonical_without_gate(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    snapshot = _snapshot_dir(backup_root, manifest)

    with pytest.raises(backup.RestoreTargetError) as exc:
        backup.restore_store(snapshot, source, canonical_root=source)
    assert exc.value.target == source

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
    assert {e.path for e in default.files} == keystone

    full = backup.backup_store(source, backup_root, include_derived=True, now=_LATER)
    assert {e.path for e in full.files} == keystone | derived
    assert derived


def test_verify_detects_corruption(tmp_path: Path) -> None:
    source = tmp_path / "store"
    backup_root = tmp_path / "backups"
    _make_store(source, with_derived=False)
    manifest = backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    snapshot = _snapshot_dir(backup_root, manifest)

    assert backup.verify_snapshot(snapshot).ok

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
    assert first.snapshot_id != second.snapshot_id

    with pytest.raises(backup.BackupError):
        backup.backup_store(source, backup_root, include_derived=False, now=_NOW)
    assert backup.verify_snapshot(_snapshot_dir(backup_root, first)).ok


def test_backup_refuses_when_no_keystone_present(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-store"
    empty.mkdir()
    (empty / "stray.txt").write_text("x")
    with pytest.raises(backup.BackupError):
        backup.backup_store(empty, tmp_path / "backups", include_derived=False, now=_NOW)


def test_backup_root_resolution_requires_a_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(backup.BACKUP_ROOT_ENV_VAR, raising=False)
    with pytest.raises(backup.BackupError):
        backup.backup_root_from_env_or_arg(None)
    assert backup.backup_root_from_env_or_arg("/mnt/b") == Path("/mnt/b")
    monkeypatch.setenv(backup.BACKUP_ROOT_ENV_VAR, "/mnt/env")
    assert backup.backup_root_from_env_or_arg(None) == Path("/mnt/env")

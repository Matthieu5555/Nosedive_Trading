"""Backup / restore / verify for the canonical parquet store (platform-data-durability).

The week's whole thesis is "several days of harvested, QC-clean history" (TARGET §2.2), and that
history lives in ``data/`` as parquet that is **untracked and not git-recoverable**. The close
snapshots are point-in-time — you cannot re-capture last Tuesday's close — so a disk loss, a bad
purge, or a fat-fingered ``rm`` would destroy the one asset the unattended week produces. This is
the durability tool: a scheduled, append-only snapshot of the **keystone** (the immutable raw
partitions + the run-state ledger) to a second location, and a documented restore-and-verify path.

**Why raw + ledger is the minimum.** Everything under ``derived/``/``analytics/``/``qc/`` replays
deterministically from ``raw/`` (the byte-identical replay substrate, smoke-tested) — raw replays
from nothing. So the default snapshot is ``raw/`` + ``_run_state.jsonl``; ``--include-derived``
adds the reconstructable trees as a convenience, never as the thing being protected.

**The backup destination is an explicit decision, not a default.** This box has one physical disk,
so a same-disk copy protects against a bad purge / fat-finger but **not** disk loss. The tool
therefore refuses to guess: ``--backup-root`` (or ``$ALGOTRADING_BACKUP_ROOT``) must name where
backups live, and the runbook (``scripts/README.md``) says to point it at a mounted external disk,
an rsync/NFS target, or an object-store mount for true off-box durability.

**Safety invariants.** Backup only ever *reads* the canonical store (never writes into it).
Snapshots are append-only — each run writes a new timestamped directory and never mutates a prior
one. Restore refuses to write into the canonical store unless an explicit ``--allow-canonical``
gate is passed, and refuses a non-empty target unless ``--force`` — so the default restore lands
in a fresh temp store you diff, exactly as the capture discipline requires.

Usage::

    # back up the keystone (raw + ledger) to the configured second location:
    ALGOTRADING_BACKUP_ROOT=/mnt/backup uv run python scripts/backup_data_store.py backup
    uv run python scripts/backup_data_store.py backup --backup-root /mnt/b --include-derived

    # list snapshots / verify one against its manifest checksums:
    uv run python scripts/backup_data_store.py list --backup-root /mnt/backup
    uv run python scripts/backup_data_store.py verify --snapshot /mnt/backup/20260615T181500Z

    # restore a snapshot into a TEMP store and verify it (never the canonical store by default):
    uv run python scripts/backup_data_store.py restore --snapshot /mnt/backup/20260615T181500Z \
        --target /tmp/restore-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from algotrading.core.paths import data_root, load_env_file

# --- internal invariants (not business config) -------------------------------------------------
# The env var that names the backup destination, mirroring the ALGOTRADING_DATA_ROOT convention.
BACKUP_ROOT_ENV_VAR = "ALGOTRADING_BACKUP_ROOT"
# The keystone: the immutable raw partitions + the run-state ledger. Everything else replays from
# raw, so these are the minimum that must survive (TARGET §2.2; the task's "raw is the keystone").
_KEYSTONE_TREES = ("raw", "_run_state.jsonl")
# Reconstructable trees added by --include-derived: convenience, not the thing being protected.
_DERIVED_TREES = ("derived", "analytics", "qc")
# A snapshot mirrors the store under this subdir; the manifest sits beside it.
_STORE_SUBDIR = "store"
_MANIFEST_NAME = "manifest.json"
# Manifest schema version, so a future format change is detectable rather than silently misread.
_MANIFEST_FORMAT_VERSION = 1
# Read files in fixed-size chunks so a large parquet never loads whole into memory for hashing.
_HASH_CHUNK_BYTES = 1 << 20  # 1 MiB


class BackupError(RuntimeError):
    """A backup could not be taken — carries the human-readable reason."""


class RestoreTargetError(RuntimeError):
    """A restore was refused because its target is unsafe — carries the offending path.

    Raised for a target that resolves to the canonical store (without ``allow_canonical``) or a
    non-empty target (without ``force``), so a restore never silently overwrites live data.
    """

    def __init__(self, target: Path, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"refusing to restore into {target}: {reason}")


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One backed-up file: its path relative to the store root, its sha256, and its size."""

    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """The record of one snapshot — what was copied, from where, when, and each file's checksum.

    The checksums make a backup self-verifying: a restore is "byte-identical" iff every restored
    file re-hashes to its manifest entry. ``files`` is sorted by path so two backups of the same
    bytes produce identical manifests (a stable, diffable record).
    """

    snapshot_id: str
    created_ts: str
    source_root: str
    include_derived: bool
    files: tuple[FileEntry, ...]
    format_version: int = _MANIFEST_FORMAT_VERSION

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.files)

    def to_json(self) -> str:
        payload = {
            "format_version": self.format_version,
            "snapshot_id": self.snapshot_id,
            "created_ts": self.created_ts,
            "source_root": self.source_root,
            "include_derived": self.include_derived,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "files": [
                {"path": e.path, "sha256": e.sha256, "bytes": e.bytes} for e in self.files
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> BackupManifest:
        payload = json.loads(text)
        files = tuple(
            FileEntry(path=e["path"], sha256=e["sha256"], bytes=e["bytes"])
            for e in payload["files"]
        )
        return cls(
            snapshot_id=payload["snapshot_id"],
            created_ts=payload["created_ts"],
            source_root=payload["source_root"],
            include_derived=payload["include_derived"],
            files=files,
            format_version=payload.get("format_version", _MANIFEST_FORMAT_VERSION),
        )


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """The outcome of checking a restored/stored tree against a manifest's checksums.

    ``ok`` is ``True`` only when nothing is missing, mismatched, or extra. The three lists name the
    offending relative paths so a failure points at the exact files, not just a boolean.
    """

    ok: bool
    missing: tuple[str, ...]
    mismatched: tuple[str, ...]
    extra: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """The outcome of a restore: where it landed, how many files, and its verification."""

    target_root: Path
    file_count: int
    verify: VerifyResult


def backup_root_from_env_or_arg(arg: str | None) -> Path:
    """Resolve the backup destination from ``--backup-root`` then ``$ALGOTRADING_BACKUP_ROOT``.

    Refuses to invent a default: the destination is the durability decision (this box has one
    disk, so a silent same-disk default would masquerade as off-box backup). Raises
    :class:`BackupError` naming the env var when neither is given.
    """
    if arg:
        return Path(arg)
    import os  # noqa: PLC0415 — local: only this resolver reads the env var

    env = os.environ.get(BACKUP_ROOT_ENV_VAR)
    if env:
        return Path(env)
    raise BackupError(
        f"no backup destination: pass --backup-root or set {BACKUP_ROOT_ENV_VAR} "
        f"(point it at a mounted external disk / rsync / object-store target for off-box "
        f"durability; a same-disk path protects only against a bad purge, not disk loss)"
    )


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield every file under ``root`` (recursively), in sorted order for a deterministic manifest.

    A plain file ``root`` yields itself; a missing ``root`` yields nothing (a tree absent from the
    store — e.g. no ``derived/`` yet — is skipped, not an error).
    """
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    yield from sorted(p for p in root.rglob("*") if p.is_file())


def _sha256(path: Path) -> str:
    """The sha256 hex digest of ``path``, read in chunks so large parquet never loads wholesale."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_trees(include_derived: bool) -> tuple[str, ...]:
    """The top-level store entries a snapshot copies: the keystone, plus derived when asked."""
    return _KEYSTONE_TREES + (_DERIVED_TREES if include_derived else ())


def _hash_tree(store_root: Path, trees: Sequence[str]) -> tuple[FileEntry, ...]:
    """Hash every file under each named tree, returning manifest entries keyed by relative path."""
    entries: list[FileEntry] = []
    for tree in trees:
        source = store_root / tree
        for file in _iter_files(source):
            entries.append(
                FileEntry(
                    path=file.relative_to(store_root).as_posix(),
                    sha256=_sha256(file),
                    bytes=file.stat().st_size,
                )
            )
    return tuple(sorted(entries, key=lambda e: e.path))


def _snapshot_id(now: datetime) -> str:
    """A sortable UTC snapshot id, ``YYYYmmddTHHMMSSZ`` — the snapshot's directory name."""
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def backup_store(
    source_root: Path,
    backup_root: Path,
    *,
    include_derived: bool,
    now: datetime,
) -> BackupManifest:
    """Snapshot the keystone (raw + ledger; + derived if asked) into ``backup_root/<snapshot_id>``.

    Reads the canonical store only — never writes into it. The snapshot directory is new each run
    (append-only); a colliding id is refused rather than overwritten. Returns the manifest, which
    is also written to ``<snapshot_id>/manifest.json`` alongside the copied ``store/`` tree.
    """
    if not source_root.is_dir():
        raise BackupError(f"source store does not exist: {source_root}")
    trees = _selected_trees(include_derived)
    present = [t for t in trees if (source_root / t).exists()]
    if not present:
        raise BackupError(
            f"source store {source_root} has none of the keystone trees {_KEYSTONE_TREES}; "
            f"nothing to back up (is this the right --data-root?)"
        )

    snapshot_id = _snapshot_id(now)
    snapshot_dir = backup_root / snapshot_id
    if snapshot_dir.exists():
        raise BackupError(
            f"snapshot {snapshot_dir} already exists; backups are append-only and never "
            f"overwrite a prior snapshot"
        )
    store_dest = snapshot_dir / _STORE_SUBDIR
    store_dest.mkdir(parents=True)

    for tree in present:
        source = source_root / tree
        dest = store_dest / tree
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

    manifest = BackupManifest(
        snapshot_id=snapshot_id,
        created_ts=now.astimezone(UTC).isoformat(),
        source_root=str(source_root),
        include_derived=include_derived,
        files=_hash_tree(store_dest, present),
    )
    (snapshot_dir / _MANIFEST_NAME).write_text(manifest.to_json())
    return manifest


def _verify_against_manifest(store_root: Path, manifest: BackupManifest) -> VerifyResult:
    """Re-hash every file under ``store_root`` against ``manifest`` — the byte-identical check."""
    expected = {entry.path: entry.sha256 for entry in manifest.files}
    actual = {
        file.relative_to(store_root).as_posix(): _sha256(file) for file in _iter_files(store_root)
    }
    missing = tuple(sorted(p for p in expected if p not in actual))
    extra = tuple(sorted(p for p in actual if p not in expected))
    mismatched = tuple(
        sorted(p for p in expected if p in actual and expected[p] != actual[p])
    )
    ok = not (missing or extra or mismatched)
    return VerifyResult(ok=ok, missing=missing, mismatched=mismatched, extra=extra)


def load_manifest(snapshot_dir: Path) -> BackupManifest:
    """Read the manifest of ``snapshot_dir``; raises a clear error if it is not a snapshot."""
    manifest_path = snapshot_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f"not a snapshot (no {_MANIFEST_NAME}): {snapshot_dir}")
    return BackupManifest.from_json(manifest_path.read_text())


def verify_snapshot(snapshot_dir: Path) -> VerifyResult:
    """Check a stored snapshot's bytes against its own manifest — detects bit-rot/corruption."""
    manifest = load_manifest(snapshot_dir)
    return _verify_against_manifest(snapshot_dir / _STORE_SUBDIR, manifest)


def restore_store(
    snapshot_dir: Path,
    target_root: Path,
    *,
    canonical_root: Path,
    allow_canonical: bool = False,
    force: bool = False,
) -> RestoreResult:
    """Restore ``snapshot_dir`` into ``target_root``, verifying it byte-for-byte vs the manifest.

    Refuses to write into the canonical store (``canonical_root``) unless ``allow_canonical`` — the
    default restore lands in a fresh temp store you diff. Refuses a non-empty target unless
    ``force``. After copying, every restored file is re-hashed against the manifest; the
    :class:`RestoreResult` carries that verification so a caller can fail loudly on any mismatch.
    """
    manifest = load_manifest(snapshot_dir)
    target = target_root.resolve()
    if target == canonical_root.resolve() and not allow_canonical:
        raise RestoreTargetError(
            target_root,
            "this is the canonical store; pass allow_canonical to restore over live data",
        )
    if target.exists() and any(target.iterdir()) and not force:
        raise RestoreTargetError(
            target_root, "target exists and is not empty; pass force to overwrite"
        )

    store_src = snapshot_dir / _STORE_SUBDIR
    target_root.mkdir(parents=True, exist_ok=True)
    for child in sorted(store_src.iterdir()):
        dest = target_root / child.name
        if child.is_dir():
            shutil.copytree(child, dest, dirs_exist_ok=force)
        else:
            shutil.copy2(child, dest)

    verify = _verify_against_manifest(target_root, manifest)
    return RestoreResult(target_root=target_root, file_count=manifest.file_count, verify=verify)


def list_snapshots(backup_root: Path) -> tuple[BackupManifest, ...]:
    """Every snapshot under ``backup_root``, oldest first (the sortable id orders them by time)."""
    if not backup_root.is_dir():
        return ()
    manifests: list[BackupManifest] = []
    for child in sorted(backup_root.iterdir()):
        if (child / _MANIFEST_NAME).is_file():
            manifests.append(load_manifest(child))
    return tuple(manifests)


def _cmd_backup(args: argparse.Namespace) -> int:
    source = Path(args.data_root) if args.data_root else data_root()
    backup_root = backup_root_from_env_or_arg(args.backup_root)
    manifest = backup_store(
        source, backup_root, include_derived=args.include_derived, now=datetime.now(UTC)
    )
    print(
        f"[OK] snapshot {manifest.snapshot_id}: {manifest.file_count} files, "
        f"{manifest.total_bytes} bytes -> {backup_root / manifest.snapshot_id}"
    )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify_snapshot(Path(args.snapshot))
    if result.ok:
        print(f"[OK] {args.snapshot} verifies against its manifest")
        return 0
    print(
        f"[FAIL] {args.snapshot}: missing={list(result.missing)} "
        f"mismatched={list(result.mismatched)} extra={list(result.extra)}"
    )
    return 1


def _cmd_restore(args: argparse.Namespace) -> int:
    result = restore_store(
        Path(args.snapshot),
        Path(args.target),
        canonical_root=data_root(),
        allow_canonical=args.allow_canonical,
        force=args.force,
    )
    if result.verify.ok:
        print(f"[OK] restored {result.file_count} files to {result.target_root}; verified")
        return 0
    print(
        f"[FAIL] restored to {result.target_root} but verification failed: "
        f"missing={list(result.verify.missing)} mismatched={list(result.verify.mismatched)} "
        f"extra={list(result.verify.extra)}"
    )
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    backup_root = backup_root_from_env_or_arg(args.backup_root)
    snapshots = list_snapshots(backup_root)
    if not snapshots:
        print(f"no snapshots under {backup_root}")
        return 0
    for m in snapshots:
        derived = " +derived" if m.include_derived else ""
        print(f"{m.snapshot_id}  {m.file_count} files  {m.total_bytes} bytes{derived}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    dest_help = f"destination (or ${BACKUP_ROOT_ENV_VAR})"

    backup = sub.add_parser("backup", help="snapshot the keystone (raw + ledger) to backup root")
    backup.add_argument("--backup-root", default=None, help=dest_help)
    backup.add_argument(
        "--data-root", default=None, help="source store (default: $ALGOTRADING_DATA_ROOT)"
    )
    backup.add_argument(
        "--include-derived",
        action="store_true",
        help="also copy the reconstructable derived/analytics/qc trees",
    )
    backup.set_defaults(func=_cmd_backup)

    verify = sub.add_parser("verify", help="re-hash a stored snapshot against its manifest")
    verify.add_argument("--snapshot", required=True, help="path to a snapshot directory")
    verify.set_defaults(func=_cmd_verify)

    restore = sub.add_parser("restore", help="restore a snapshot into a temp target and verify")
    restore.add_argument("--snapshot", required=True, help="path to a snapshot directory")
    restore.add_argument(
        "--target", required=True, help="where to restore (a temp store; NOT canonical)"
    )
    restore.add_argument(
        "--allow-canonical", action="store_true", help="permit restoring over canonical (gated)"
    )
    restore.add_argument("--force", action="store_true", help="permit a non-empty target")
    restore.set_defaults(func=_cmd_restore)

    listing = sub.add_parser("list", help="list snapshots under the backup root")
    listing.add_argument("--backup-root", default=None, help=dest_help)
    listing.set_defaults(func=_cmd_list)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint — loads the repo ``.env`` then dispatches the chosen subcommand."""
    load_env_file()
    args = _build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except (BackupError, RestoreTargetError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())

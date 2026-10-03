"""Verify upstream's session/memory migration. Run only on a copy or after backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
from pathlib import Path

from nanobot.agent.memory import MemoryStore
from nanobot.config.loader import set_config_path
from nanobot.session.manager import SessionManager


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def migrate(workspace: Path, config: Path) -> dict[str, object]:
    workspace, config = workspace.resolve(), config.resolve()
    root = config.parent / "sessions"
    for folder in (workspace / ".nanobot", workspace / "sessions", workspace / "memory", root):
        if folder.is_symlink() or any(path.is_symlink() for path in folder.rglob("*")):
            raise RuntimeError(f"migration state contains symlinks: {folder}")
    sources = {
        path: digest(path)
        for folder in (workspace / "sessions", root)
        if folder.exists()
        for path in folder.rglob("*.jsonl")
        if path.is_file() and not path.is_symlink()
    }
    legacy_history = workspace / "memory" / "HISTORY.md"
    old_history_hash = digest(legacy_history) if legacy_history.exists() else None
    set_config_path(config)
    manager = SessionManager(workspace, sessions_root=root)
    imports_path = manager.sessions_dir / ".legacy-global-imports.json"
    imported = set(json.loads(imports_path.read_text())) if imports_path.exists() else set()
    installed = {digest(path) for path in manager.sessions_dir.rglob("*.jsonl")}
    # The release constructor migrates workspace files, not root-level files from
    # older global storage. Stage verified copies there; retain the old global
    # files for other workspaces/recovery, and let upstream resolve conflicts.
    globals_to_copy = list(root.glob("*.jsonl"))
    newly_imported = set()
    for path in globals_to_copy:
        sha = sources[path]
        if sha in imported:
            continue
        newly_imported.add(sha)
        if sha in installed:
            continue
        with path.open(encoding="utf-8") as handle:
            metadata = json.loads(handle.readline())
        if not isinstance(metadata, dict) or not isinstance(metadata.get("key"), str) or not metadata["key"]:
            raise RuntimeError("legacy global session has no usable metadata key")
        folder = workspace / "sessions"
        folder.mkdir(exist_ok=True)
        copied = folder / f"legacy-global-{secrets.token_hex(8)}.jsonl"
        shutil.copy2(path, copied)
        copied.chmod(0o600)
        if digest(copied) != sources[path] or digest(path) != sources[path]:
            raise RuntimeError("legacy global session changed during migration")
    manager = SessionManager(workspace, sessions_root=root)
    installed = {digest(path) for path in manager.sessions_dir.rglob("*.jsonl")}
    if not newly_imported.issubset(installed):
        raise RuntimeError("legacy global session copies were not retained in the new namespace")
    imports_path.write_text(json.dumps(sorted(imported | newly_imported)) + "\n")
    imports_path.chmod(0o600)
    MemoryStore(workspace)  # Use upstream's HISTORY.md -> history.jsonl migration.
    retained = {
        digest(path)
        for folder in (workspace / "sessions", root)
        if folder.exists()
        for path in folder.rglob("*.jsonl")
        if path.is_file() and not path.is_symlink()
    }
    missing = [path for path, sha in sources.items() if sha not in retained]
    if missing:
        raise RuntimeError(f"migration verification failed: {len(missing)} source session(s) not retained")
    if old_history_hash is not None:
        history_copies = list((workspace / "memory").glob("HISTORY.md*"))
        if not any(path.is_file() and digest(path) == old_history_hash for path in history_copies):
            raise RuntimeError("legacy memory history was not preserved")
    # Check migration idempotence and canonical storage stability.
    repeated = SessionManager(workspace, sessions_root=root)
    if repeated.sessions_dir != manager.sessions_dir:
        raise RuntimeError("session namespace changed on repeated initialization")
    remaining = list((workspace / "sessions").glob("*.jsonl"))
    if remaining:
        raise RuntimeError(f"{len(remaining)} workspace sessions require manual migration review")
    return {
        "source_sessions_verified": len(sources),
        "canonical_sessions": len(list(manager.sessions_dir.glob("*.jsonl"))),
        "conflicts_preserved": len(list(manager.sessions_dir.glob(".migration-conflicts/*.jsonl"))),
        "sessions_dir": str(manager.sessions_dir),
        "legacy_memory_preserved": old_history_hash is not None,
        "legacy_global_copies_retained": len(globals_to_copy),
        "idempotent": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = migrate(args.workspace, args.config)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    args.report.chmod(0o600)
    print(f"Verified {report['source_sessions_verified']} source sessions; "
          f"{report['canonical_sessions']} canonical sessions; migration is idempotent.")


if __name__ == "__main__":
    main()

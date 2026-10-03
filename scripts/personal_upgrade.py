"""Build, back up, migrate, and switch an installation; optionally install an inactive service unit."""

from __future__ import annotations

import argparse
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.3.5"
REPOSITORY = Path(__file__).resolve().parents[1]


def _unit_quote(value: str | Path, *, exec_argument: bool = False) -> str:
    text = str(value)
    if any(character in text for character in ("\n", "\r", "\0")):
        raise ValueError("systemd paths must not contain control characters")
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if exec_argument:
        text = text.replace("$", "$$")
    return '"' + text + '"'


def render_systemd_unit(venv: Path, config: Path, workspace: Path) -> str:
    """Render a portable system service for this user and explicit instance paths."""
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    writable = dict.fromkeys((Path.home(), config.parent, workspace, venv.parent))
    return f"""[Unit]
Description=Nanobot AI Gateway
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={str(venv.parent).replace('%', '%%')}
ExecStart={_unit_quote(venv / 'bin/nanobot', exec_argument=True)} gateway --config {_unit_quote(config, exec_argument=True)}
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths={' '.join(_unit_quote(path) for path in writable)}
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def install_systemd_unit(args, *, unit_directory: Path = Path("/etc/systemd/system")) -> None:
    """Install an absent unit on explicit setup opt-in; never enable/start it."""
    if args.operation != "setup" or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", args.service):
        raise ValueError("--install-systemd requires setup and a valid --service NAME.service")
    destination = unit_directory / args.service
    if destination.exists() or destination.is_symlink():
        print(f"Existing systemd unit preserved: {destination}")
        return
    systemctl = shutil.which("systemctl")
    install = shutil.which("install")
    if not systemctl or not install:
        raise RuntimeError("systemctl and install are required for --install-systemd")
    fragment = run([systemctl, "show", args.service, "--property=FragmentPath", "--value"], capture=True)
    if fragment:
        print(f"Existing systemd unit preserved: {fragment}")
        return
    pkexec = shutil.which("pkexec")
    sudo = shutil.which("sudo") if not pkexec else None
    if not pkexec and not sudo:
        raise RuntimeError("installing a system unit requires pkexec or terminal sudo authentication")
    config = args.config.expanduser().resolve()
    configured = json.loads(config.read_text()).get("agents", {}).get("defaults", {}).get("workspace", "~/.nanobot/workspace")
    workspace = (args.workspace or Path(configured)).expanduser().resolve()
    venv = args.venv.expanduser().absolute()
    unit = render_systemd_unit(venv, config, workspace)
    with tempfile.TemporaryDirectory(prefix="nanobot-systemd-") as temporary:
        source = Path(temporary) / args.service
        source.write_text(unit)
        elevation = [pkexec, "--disable-internal-agent"] if pkexec else [sudo]
        run([*elevation, install, "-o", "root", "-g", "root", "-m", "0644", str(source), str(destination)])
    run([systemctl, "daemon-reload"])
    print(f"Installed {args.service}. It was not enabled or started.")


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inactive(service: str) -> None:
    if service == "none":
        return  # Explicit opt-in for an installation with no service manager.
    if not shutil.which("systemctl"):
        raise RuntimeError("cannot check service state; use --service none only for a non-systemd installation")
    state = run(["systemctl", "show", service, "--property=ActiveState", "--value"], capture=True)
    if state not in {"inactive", "failed"}:
        raise RuntimeError(f"{service} is {state or 'unknown'}; stop it deliberately before upgrading")


def roots(config: Path, workspace: Path) -> dict[str, Path]:
    paths = {"instance": config.parent}
    if not workspace.is_relative_to(config.parent):
        paths["workspace"] = workspace
    return paths


def upstream_config(config: Path) -> dict:
    """Remove only the obsolete personal logging toggle rejected by upstream."""
    data = json.loads(config.read_text())
    data.pop("omegaLogging", None)
    data.pop("omega_logging", None)
    return data


def write_config_atomic(config: Path, data: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".config-upgrade-", dir=config.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(paths: dict[str, Path]) -> dict[str, str]:
    files: dict[str, str] = {}
    for label, root in paths.items():
        for path in root.rglob("*"):
            relative = f"{label}/{path.relative_to(root)}"
            if path.is_symlink():
                files[relative] = f"symlink:{os.readlink(path)}"
            elif path.is_file():
                files[relative] = sha256(path)
    return files


def copy_state(config: Path, workspace: Path, destination: Path) -> tuple[Path, Path]:
    source_paths = roots(config, workspace)
    before = snapshot(source_paths)
    for label, source in source_paths.items():
        shutil.copytree(source, destination / label, symlinks=True)
    if snapshot(source_paths) != before:
        raise RuntimeError("instance data changed while copying; retry when all writers are idle")
    copies = {label: destination / label for label in source_paths}
    if snapshot(copies) != before:
        raise RuntimeError("state copy verification failed")
    copied_config = destination / "instance" / config.name
    copied_workspace = (
        destination / "instance" / workspace.relative_to(config.parent)
        if workspace.is_relative_to(config.parent) else destination / "workspace"
    )
    # Rebind only the copied namespace marker, so already-migrated sessions are exercised too.
    for marker in (destination / "instance" / "sessions").glob("*/.workspace"):
        if marker.is_symlink():
            raise RuntimeError("session namespace marker symlink requires manual review")
        if marker.read_text().strip() == str(workspace):
            marker.write_text(str(copied_workspace) + "\n")
    return copied_config, copied_workspace


def encrypted_backup(config: Path, workspace: Path, backup: Path) -> dict[str, str]:
    source_paths = roots(config, workspace)
    files = snapshot(source_paths)
    key_dir = backup.parent / "recovery-keys"
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_dir.chmod(0o700)
    key = key_dir / f"{backup.name}.key"
    with key.open("x") as stream:
        stream.write(secrets.token_hex(48) + "\n")
    key.chmod(0o600)
    plain = backup / ".state.tar.gz"
    encrypted = backup / "state.tar.gz.enc"
    verified = backup / ".verified.tar.gz"
    try:
        with tarfile.open(plain, "w:gz", dereference=False) as archive:
            for label, source in source_paths.items():
                archive.add(source, arcname=label)
        if snapshot(source_paths) != files:
            raise RuntimeError("instance changed during backup; retry when writers are idle")
        command = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                   "-pass", f"file:{key}"]
        run([*command, "-salt", "-in", str(plain), "-out", str(encrypted)])
        encrypted.chmod(0o600)
        run([*command, "-d", "-in", str(encrypted), "-out", str(verified)])
        if sha256(plain) != sha256(verified):
            raise RuntimeError("encrypted backup round-trip verification failed")
        archived: dict[str, str] = {}
        with tarfile.open(verified) as archive:
            for member in archive:
                if member.isfile() or member.islnk():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    archived[member.name] = hashlib.file_digest(stream, "sha256").hexdigest()
                elif member.issym():
                    archived[member.name] = f"symlink:{member.linkname}"
        if archived != files:
            raise RuntimeError("backup file verification failed")
        (backup / "state-manifest.json").write_text(json.dumps({
            "roots": {label: str(path) for label, path in source_paths.items()},
            "files": files, "archive_sha256": sha256(encrypted), "key": str(key),
        }, indent=2) + "\n")
        (backup / "RESTORE.md").write_text(
            f"# Restore\n\nLeave the service inactive. The encrypted state archive is "
            f"`{encrypted}`; its separate recovery key is `{key}`.\n\n"
            "Decrypt into a private temporary directory with openssl enc -d -aes-256-cbc "
            "-pbkdf2 -iter 200000 -pass file:KEY -in ARCHIVE -out state.tar.gz. "
            "Extract there and compare the state manifest before restoring its roots. "
            "Move the failed instance/workspace aside before restoring; do not merge new "
            "namespace files into an old workspace.\n\n"
            "The deployment manifest records the old executable path and previous environment. "
            "Replace the bot-env symlink with that previous environment (or move the saved "
            "old-environment directory back to bot-env for a pre-symlink installation). "
            "Restore state before running the older bot. These scripts never start a service.\n"
        )
        return files
    finally:
        plain.unlink(missing_ok=True)
        verified.unlink(missing_ok=True)


def migrate(python: Path, config: Path, workspace: Path, report: Path, *, home: Path | None = None) -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if home is not None:
        env["HOME"] = str(home)  # Never permit preview fallback into real global sessions.
    run([str(python), str(REPOSITORY / "scripts" / "migrate_personal_state.py"),
         "--config", str(config), "--workspace", str(workspace), "--report", str(report)],
        cwd=python.parent.parent, env=env)


def restore_state(config: Path, workspace: Path, backup: Path) -> None:
    """Restore verified offline state, keeping the failed state for inspection."""
    manifest = json.loads((backup / "state-manifest.json").read_text())
    encrypted = backup / "state.tar.gz.enc"
    if sha256(encrypted) != manifest["archive_sha256"]:
        raise RuntimeError("backup checksum changed; refusing automatic recovery")
    with tempfile.TemporaryDirectory(prefix="rollback-", dir=backup) as temporary:
        stage = Path(temporary)
        plain = stage / "state.tar.gz"
        run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
             "-pass", f"file:{manifest['key']}", "-in", str(encrypted), "-out", str(plain)])
        extracted = stage / "extracted"
        extracted.mkdir()
        with tarfile.open(plain) as archive:
            members = archive.getmembers()
            links = {member.name for member in members if member.issym()}
            for member in members:
                names = [member.name, member.linkname] if member.islnk() else [member.name]
                for name in names:
                    path = Path(name)
                    if path.is_absolute() or ".." in path.parts or path.parts[0] not in manifest["roots"]:
                        raise RuntimeError("unexpected archive path; refusing automatic recovery")
                    if any(str(parent) in links for parent in path.parents):
                        raise RuntimeError("archive path traverses a symlink; refusing automatic recovery")
            # Own encrypted archive, checksum verified above; preserve original symlink
            # targets while explicitly excluding path and symlink traversal.
            archive.extractall(extracted, filter="fully_trusted")
        restored_roots = {label: extracted / label for label in manifest["roots"]}
        if snapshot(restored_roots) != manifest["files"]:
            raise RuntimeError("recovery copy verification failed")
        failed = backup / "failed-state"
        failed.mkdir(mode=0o700)
        for label, live in roots(config, workspace).items():
            shutil.move(str(live), str(failed / label))
            shutil.copytree(restored_roots[label], live, symlinks=True)
        if snapshot(roots(config, workspace)) != manifest["files"]:
            raise RuntimeError("restored state verification failed")


def validate_environment(python: Path, config: Path, workspace: Path) -> None:
    # Do not print config values or verbose validation exceptions containing secrets.
    program = """
import importlib.metadata, sys
from pathlib import Path
from nanobot.config.loader import load_config, set_config_path
try:
    assert importlib.metadata.version("nanobot-ai") == sys.argv[3]
    set_config_path(Path(sys.argv[1]))
    config = load_config(Path(sys.argv[1]))
    assert config.workspace_path.expanduser().resolve() == Path(sys.argv[2]).resolve()
except Exception as exc:
    print("Environment/config validation failed: " + type(exc).__name__, file=sys.stderr)
    sys.exit(1)
"""
    run([str(python), "-c", program, str(config), str(workspace), VERSION],
        cwd=python.parent.parent, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


def preflight(args) -> tuple[Path, Path, Path]:
    config = args.config.expanduser().absolute()
    if config.is_symlink():
        raise RuntimeError("config symlink requires manual review")
    config = config.resolve()
    if not config.is_file():
        raise RuntimeError("config is missing; run nanobot onboard with the intended config first")
    data = json.loads(config.read_text())
    if not isinstance(data, dict):
        raise RuntimeError("config must contain an object")
    configured = data.get("agents", {}).get("defaults", {}).get("workspace", "~/.nanobot/workspace")
    workspace = (args.workspace or Path(configured)).expanduser().resolve()
    if not workspace.is_dir():
        raise RuntimeError(f"workspace does not exist: {workspace}")
    # Migration writes these locations. Never allow preview symlinks to reach live state.
    for folder in (workspace / ".nanobot", workspace / "sessions", workspace / "memory",
                   config.parent / "sessions"):
        if folder.is_symlink() or any(path.is_symlink() for path in folder.rglob("*")):
            raise RuntimeError(f"migration state contains symlinks; review manually: {folder}")
    venv = args.venv.expanduser().absolute()
    if args.operation == "deploy" and not (venv / "bin/python").is_file():
        raise RuntimeError("no existing virtualenv; use setup.sh for first installation")
    if workspace == config.parent or (config.parent / "sessions").is_relative_to(workspace):
        raise RuntimeError("upstream session storage must be outside the workspace")
    backup_root = args.backups.expanduser().resolve()
    for protected in (REPOSITORY, config.parent, workspace, venv):
        if backup_root.is_relative_to(protected.resolve()):
            raise RuntimeError("backups must be outside the repository, instance, workspace, and venv")
    inactive(args.service)
    count = len(list((workspace / "sessions").glob("*.jsonl")))
    print(f"Workspace: {workspace}\nConfig: {config}\nLegacy workspace session files: {count}")
    print(f"New sessions root: {config.parent / 'sessions'}\nExecutable destination: {venv}")
    return config, workspace, venv


def upgrade(args) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("run as the nanobot user, not root")
    config, workspace, venv = preflight(args)
    if args.check:
        print("Read-only preflight complete. No dependencies, data, or executable were changed.")
        return
    os.umask(0o077)
    venv.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (venv.parent / ".nanobot-upgrade.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        backup = args.backups.expanduser().resolve() / stamp
        backup.mkdir(mode=0o700, parents=True)
        environment = venv.parent / "release-environments" / f"nanobot-{VERSION}-{stamp}"
        run([str(args.python), "-m", "venv", str(environment)])
        python = environment / "bin/python"
        env = dict(os.environ)
        if args.skip_webui_build:
            env["NANOBOT_SKIP_WEBUI_BUILD"] = "1"
        run([str(python), "-m", "pip", "install", str(REPOSITORY) + "[api]"], env=env)
        raw = json.loads(config.read_text())
        names = [name for name, channel in raw.get("channels", {}).items()
                 if isinstance(channel, dict) and channel.get("enabled") is True]
        if names:
            run([str(python), "-m", "scripts.install_channel_dependencies", *names], cwd=REPOSITORY)
        inactive(args.service)
        with tempfile.TemporaryDirectory(prefix="migration-preview-", dir=backup) as temporary:
            stage = Path(temporary)
            copied_config, copied_workspace = copy_state(config, workspace, stage)
            candidate_config = upstream_config(config)
            copied_data = json.loads(json.dumps(candidate_config))
            copied_data.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = str(copied_workspace)
            write_config_atomic(copied_config, copied_data)
            validate_environment(python, copied_config, copied_workspace)
            migrate(python, copied_config, copied_workspace, backup / "migration-preview.json", home=stage / "home")
        baseline = encrypted_backup(config, workspace, backup)
        inactive(args.service)
        if snapshot(roots(config, workspace)) != baseline:
            raise RuntimeError("state changed after backup; executable was not switched")
        # Take a private recovery copy of the executable destination before any live migration.
        previous = backup / "old-environment"
        old_target = os.readlink(venv) if venv.is_symlink() else None
        manifest = {
            "version": VERSION, "config": str(config), "workspace": str(workspace),
            "venv": str(venv), "new_environment": str(environment),
            "old_symlink": old_target, "old_environment_backup": str(previous) if old_target is None else None,
            "service": args.service, "stage": "backed_up",
        }
        manifest_path = backup / "deployment.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        try:
            if candidate_config != json.loads(config.read_text()):
                write_config_atomic(config, candidate_config)
            migrate(python, config, workspace, backup / "migration-live.json")
            inactive(args.service)
            if venv.exists() and not venv.is_symlink():
                os.rename(venv, previous)
            link = venv.parent / f".nanobot-link-{stamp}"
            link.symlink_to(environment, target_is_directory=True)
            try:
                os.replace(link, venv)
            finally:
                link.unlink(missing_ok=True)
            validate_environment(venv / "bin/python", config, workspace)
            manifest["stage"] = "complete"
            (environment / "requirements-installed.txt").write_text(
                run([str(python), "-m", "pip", "freeze"], capture=True) + "\n"
            )
        except BaseException:
            manifest["stage"] = "failed; recovery required"
            try:
                inactive(args.service)
                if venv.is_symlink() and venv.resolve() == environment:
                    venv.unlink()
                    if old_target is not None:
                        venv.symlink_to(old_target, target_is_directory=True)
                if previous.exists() and not venv.exists():
                    os.rename(previous, venv)
                if snapshot(roots(config, workspace)) != baseline:
                    restore_state(config, workspace, backup)
                manifest["stage"] = "failed; executable and state restored"
            except Exception as recovery_error:
                print(f"Automatic recovery could not complete: {recovery_error}", file=sys.stderr)
            print(f"Leave the service inactive; recovery details: {backup / 'RESTORE.md'}",
                  file=sys.stderr)
            raise
        finally:
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Installed customized nanobot {VERSION}. Backup: {backup}")
        print("Service state was not changed. Review deployment.json and migration-live.json before starting.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["setup", "deploy"])
    parser.add_argument("--venv", type=Path, default=Path.home() / "nanobot/bot-env")
    parser.add_argument("--config", type=Path, default=Path.home() / ".nanobot/config.json")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--backups", type=Path, default=Path.home() / ".nanobot-backups")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--service", default="nanobot.service", help="systemd unit, or explicit none for no service manager")
    parser.add_argument("--check", action="store_true", help="read-only preflight; do not install or migrate")
    parser.add_argument("--skip-webui-build", action="store_true", help="headless wheel; omit bundled WebUI")
    parser.add_argument("--install-systemd", action="store_true", help="setup only: install a missing system unit without enabling/starting it")
    args = parser.parse_args()
    if args.install_systemd and (args.operation != "setup" or not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", args.service)):
        parser.error("--install-systemd requires setup and --service NAME.service (not none)")
    try:
        upgrade(args)
        if args.install_systemd and not args.check:
            install_systemd_unit(args)
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Upgrade stopped: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

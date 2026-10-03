"""Upgrade safety checks use disposable instance data and no service/network calls."""

import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import personal_upgrade as upgrade
from scripts.migrate_personal_state import migrate


def instance(tmp_path):
    data = tmp_path / "instance"
    workspace = data / "workspace"
    (workspace / "sessions").mkdir(parents=True)
    (workspace / "memory").mkdir()
    config = data / "config.json"
    config.write_text(json.dumps({"agents": {"defaults": {"workspace": str(workspace)}}, "omegaLogging": True}))
    (workspace / "memory/HISTORY.md").write_text("[2026-01-01 12:00] Remember the project\n")
    for folder, name, key in [(workspace / "sessions", "discord_dm.jsonl", "discord:dm"),
                               (data / "sessions", "discord_other.jsonl", "discord:other")]:
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(json.dumps({"_type": "metadata", "key": key, "metadata": {},
                                              "created_at": "2026-01-01T12:00:00", "updated_at": "2026-01-01T12:00:00"})
                                    + "\n" + json.dumps({"role": "user", "content": key}) + "\n")
    return config, workspace


def test_read_only_preflight_does_not_create_files(tmp_path, monkeypatch):
    config, workspace = instance(tmp_path)
    before = upgrade.snapshot(upgrade.roots(config, workspace))
    args = Namespace(operation="setup", config=config, workspace=None, venv=tmp_path / "bot-env",
                     backups=tmp_path / "backups", service="none", check=True)
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 1000)
    upgrade.upgrade(args)
    assert upgrade.snapshot(upgrade.roots(config, workspace)) == before
    assert not args.backups.exists()
    assert not args.venv.exists()
    assert not (tmp_path / ".nanobot-upgrade.lock").exists()


def test_active_service_is_rejected_before_install_or_backup(tmp_path, monkeypatch):
    config, workspace = instance(tmp_path)
    args = Namespace(operation="setup", config=config, workspace=None, venv=tmp_path / "bot-env",
                     backups=tmp_path / "backups", service="nanobot.service", check=False)
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(upgrade, "run", lambda *args, **kwargs: "active")
    with pytest.raises(RuntimeError, match="active"):
        upgrade.upgrade(args)
    assert not args.backups.exists()
    assert not args.venv.exists()


def test_config_compatibility_removes_only_old_logging_toggle(tmp_path):
    config, _ = instance(tmp_path)
    old = json.loads(config.read_text())
    candidate = upgrade.upstream_config(config)
    assert candidate == {key: value for key, value in old.items() if key != "omegaLogging"}
    assert json.loads(config.read_text()) == old


def test_copy_migration_preserves_workspace_and_global_sessions(tmp_path, monkeypatch):
    config, workspace = instance(tmp_path)
    baseline = upgrade.snapshot(upgrade.roots(config, workspace))
    copied_config, copied_workspace = upgrade.copy_state(config, workspace, tmp_path / "copy")
    result = migrate(copied_workspace, copied_config)
    assert result["source_sessions_verified"] == 2
    assert result["canonical_sessions"] == 2
    assert result["legacy_memory_preserved"] and result["idempotent"]
    assert upgrade.snapshot(upgrade.roots(config, workspace)) == baseline


def test_preview_retains_already_migrated_namespace(tmp_path):
    config, workspace = instance(tmp_path)
    first = migrate(workspace, config)
    copied_config, copied_workspace = upgrade.copy_state(config, workspace, tmp_path / "copy")
    second = migrate(copied_workspace, copied_config)
    assert first["canonical_sessions"] == second["canonical_sessions"] == 2
    assert second["source_sessions_verified"] >= 2


def test_encrypted_backup_and_rollback_preserve_exact_state(tmp_path):
    config, workspace = instance(tmp_path)
    backup = tmp_path / "backups/run"
    backup.mkdir(parents=True, mode=0o700)
    baseline = upgrade.encrypted_backup(config, workspace, backup)
    # Simulate migration followed by an installation/switch failure.
    migrate(workspace, config)
    upgrade.write_config_atomic(config, upgrade.upstream_config(config))
    upgrade.restore_state(config, workspace, backup)
    assert upgrade.snapshot(upgrade.roots(config, workspace)) == baseline
    assert (backup / "failed-state/instance/sessions").exists()
    assert (backup / "state.tar.gz.enc").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((backup / "state-manifest.json").read_text())
    assert Path(manifest["key"]).stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256((backup / "state.tar.gz.enc").read_bytes()).hexdigest() == manifest["archive_sha256"]
    assert not list(backup.glob("*.tar.gz"))


def test_symlinked_migration_data_is_rejected_without_touching_target(tmp_path):
    config, workspace = instance(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    original = workspace / "memory"
    original.rename(outside / "memory")
    original.symlink_to(outside / "memory", target_is_directory=True)
    before = upgrade.snapshot({"outside": outside})
    with pytest.raises(RuntimeError, match="symlink"):
        migrate(workspace, config)
    assert upgrade.snapshot({"outside": outside}) == before


def test_failed_executable_switch_restores_old_environment_and_data(tmp_path, monkeypatch):
    config, workspace = instance(tmp_path)
    venv = tmp_path / "bot-env"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").write_text("old executable")
    baseline = upgrade.snapshot(upgrade.roots(config, workspace))
    args = Namespace(operation="deploy", config=config, workspace=None, venv=venv,
                     backups=tmp_path / "backups", service="none", check=False,
                     python=Path("/usr/bin/python3"), skip_webui_build=True)
    monkeypatch.setattr(upgrade.os, "geteuid", lambda: 1000)
    real_run = upgrade.run

    def run(command, **kwargs):
        if command[0] == "openssl":
            return real_run(command, **kwargs)
        if command[1:3] == ["-m", "venv"]:
            directory = Path(command[3]) / "bin"
            directory.mkdir(parents=True)
            (directory / "python").write_text("new executable")
        return ""

    def migration(_python, cfg, ws, report, **kwargs):
        report.write_text(json.dumps(migrate(ws, cfg)))

    def validate(_python, cfg, ws):
        if _python == venv / "bin/python":
            raise RuntimeError("simulated post-switch failure")

    monkeypatch.setattr(upgrade, "run", run)
    monkeypatch.setattr(upgrade, "migrate", migration)
    monkeypatch.setattr(upgrade, "validate_environment", validate)
    with pytest.raises(RuntimeError, match="post-switch failure"):
        upgrade.upgrade(args)
    assert not venv.is_symlink()
    assert (venv / "bin/python").read_text() == "old executable"
    assert upgrade.snapshot(upgrade.roots(config, workspace)) == baseline
    manifest = next(args.backups.glob("*/deployment.json"))
    assert json.loads(manifest.read_text())["stage"] == "failed; executable and state restored"


def test_systemd_unit_uses_instance_paths_and_runs_as_current_user(tmp_path):
    import os
    import pwd

    venv = tmp_path / "bot env"
    config = tmp_path / "instance/config.json"
    workspace = tmp_path / "instance/workspace"
    unit = upgrade.render_systemd_unit(venv, config, workspace)
    assert f"User={pwd.getpwuid(os.getuid()).pw_name}" in unit
    assert f'ExecStart="{venv}/bin/nanobot" gateway --config "{config}"' in unit
    assert f"WorkingDirectory={venv.parent}" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "UMask=0077" in unit


def test_install_systemd_preserves_existing_unit_without_privilege(tmp_path, monkeypatch):
    destination = tmp_path / "nanobot.service"
    destination.write_text("existing unit")
    args = Namespace(operation="setup", service="nanobot.service")
    monkeypatch.setattr(upgrade, "run", lambda *a, **k: pytest.fail("existing unit must not be changed"))
    upgrade.install_systemd_unit(args, unit_directory=tmp_path)
    assert destination.read_text() == "existing unit"


def test_install_systemd_uses_native_polkit_and_only_reloads_unit_definitions(tmp_path, monkeypatch):
    config, workspace = instance(tmp_path)
    args = Namespace(operation="setup", service="nanobot.service", config=config,
                     workspace=workspace, venv=tmp_path / "bot-env")
    calls = []
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(command, **kwargs):
        calls.append(command)
        if command[0].endswith("pkexec"):
            assert command[1:3] == ["--disable-internal-agent", "/usr/bin/install"]
            source = Path(command[-2])
            assert source.is_file() and "User=" in source.read_text()
            Path(command[-1]).write_text(source.read_text())
        return ""

    monkeypatch.setattr(upgrade, "run", run)
    (tmp_path / "units").mkdir()
    upgrade.install_systemd_unit(args, unit_directory=tmp_path / "units")
    assert (tmp_path / "units/nanobot.service").exists()
    assert calls[-1] == ["/usr/bin/systemctl", "daemon-reload"]
    assert not any(word in {"start", "stop", "restart", "enable", "disable"} for call in calls for word in call)


@pytest.mark.parametrize("operation, service", [("deploy", "nanobot.service"), ("setup", "../other.service"), ("setup", "none")])
def test_install_systemd_rejects_invalid_options(tmp_path, operation, service):
    with pytest.raises(ValueError, match="requires setup"):
        upgrade.install_systemd_unit(Namespace(operation=operation, service=service), unit_directory=tmp_path)

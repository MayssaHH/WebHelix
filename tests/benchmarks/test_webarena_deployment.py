"""Prevent configuration drift and removal of unrelated Docker resources."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks/webarena_verified/deploy.py"
spec = importlib.util.spec_from_file_location("benchmark_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        ("Entrypoint", ["/different"]),
        ("Cmd", ["different"]),
        ("WorkingDir", "/different"),
        ("User", "different"),
        ("ExposedPorts", {"123/tcp": {}}),
        ("Volumes", {"/different": {}}),
        ("Env", ["SETTING=different"]),
    ],
)
def test_import_cannot_silently_change_runtime(key, replacement):
    original = {
        "Entrypoint": ["/start"],
        "Cmd": ["serve"],
        "WorkingDir": "/app",
        "Env": ["SETTING=original"],
    }
    with pytest.raises(ValueError, match="differs"):
        deploy.check_runtime_config(original, {**original, key: replacement})


@pytest.mark.parametrize("key", ["Healthcheck", "OnBuild"])
def test_unsupported_image_semantics_fail_before_import(key):
    with pytest.raises(ValueError, match="silently drop"):
        deploy.import_changes({key: ["not supported"]})


def test_unrelated_named_container_is_never_removed(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Config": {"Labels": {"owner": "someone-else"}}}]),
        )

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="unrelated work"):
        deploy.down()
    assert [command[1] for command in commands] == ["inspect"]


def test_down_targets_only_the_owned_named_container(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Config": {"Labels": {deploy.OWNER: "shopping"}}}]),
        )

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    deploy.down()
    assert commands[-1] == ["docker", "rm", "-f", "-v", deploy.CONFIG["container"]]


def test_vfs_preflight_rejects_the_observed_disk_exhaustion_case():
    image_bytes = 9873169612
    free_before_failed_launch = 21193166848
    required = deploy.required_start_space(image_bytes, "vfs")
    assert required > free_before_failed_launch
    assert required >= 2 * image_bytes + deploy.CONFIG["disk_reserve_gib"] * deploy.GIB


def test_import_env_uses_dockerfile_quoting_not_json_escape_sequences():
    changes = deploy.import_changes({"Env": ['EXAMPLE=a\tb "$LITERAL" \\end']})
    env = next(value for value in changes if value.startswith("ENV "))
    assert "\t" in env
    assert "\\t" not in env
    assert "\\$LITERAL" in env


def test_admin_ownership_does_not_allow_removing_storefront(monkeypatch):
    monkeypatch.setitem(deploy.CONFIG, "site", "shopping_admin")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Config": {"Labels": {deploy.OWNER: "shopping"}}}]),
        )

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="unrelated work"):
        deploy.down()
    assert [command[1] for command in commands] == ["inspect"]


def test_wrong_source_receipt_fails_before_docker_or_cleanup(tmp_path, monkeypatch):
    prep = tmp_path / "preparation"
    prep.mkdir()
    (prep / "preparation.json").write_text(
        json.dumps(
            {
                "status": "prepared",
                "runtime_config_verified": True,
                "source_image": "different-image",
            }
        )
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Docker must not be called for this invalid receipt")

    monkeypatch.setattr(deploy, "run", forbidden)
    with pytest.raises(ValueError, match="different source image"):
        deploy.up(tmp_path / "output", prep)
    receipt = json.loads((tmp_path / "output/deployment.json").read_text())
    assert receipt["status"] == "failed"

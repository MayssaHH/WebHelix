"""Runtime configuration, mount boundaries and cleanup without Docker/model calls."""

import json
from types import SimpleNamespace

import pytest

from webhelix.opencode.runtime import DockerRuntime, opencode_config


def test_provider_and_tools_are_explicit_and_no_remote_plugins():
    config = opencode_config(
        "http://172.18.0.1:1234", "scoped-test-token", "gpt-6.1-sol", "medium", 10
    )
    assert config["enabled_providers"] == ["webhelix"]
    assert config["small_model"] == config["model"]
    assert config["agent"]["webhelix"]["steps"] == 10
    assert config["agent"]["title"]["disable"]
    assert config["permission"]["bash"] == "allow"
    assert config["permission"]["task"] == "deny"
    assert config["permission"]["external_directory"] == "deny"
    model = config["provider"]["webhelix"]["models"]["gpt-6.1-sol"]
    assert model["variants"]["medium"]["reasoningEffort"] == "medium"
    assert model["modalities"]["input"] == ["text", "image"]
    assert config["plugin"] == [] and config["autoupdate"] is False


def test_large_authoritative_context_is_configured_not_shell_interpolated():
    context = "Prior measured feedback. " * 10000
    config = opencode_config(
        "http://172.18.0.1:1234",
        "scoped-test-token",
        "gpt-6.1-sol",
        "medium",
        10,
        agent_prompt=context,
    )
    assert len(context.encode()) > 128000
    assert config["agent"]["webhelix"]["prompt"] == context


def runtime_fixture(tmp_path, monkeypatch, wrong_image=False):
    workspace = tmp_path / "workspace"
    (workspace / "candidate").mkdir(parents=True)
    (workspace / ".env").write_text("MUST_NOT_MOUNT=private\n")
    context = tmp_path / "context"
    context.mkdir()
    runtime = DockerRuntime(workspace, context, tmp_path / "evidence")
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        stdout = ""
        if args[:2] == ("image", "inspect"):
            stdout = json.dumps(
                [
                    {
                        "Id": "sha256:fixture",
                        "Config": {
                            "Labels": {
                                "org.webhelix.opencode.version": "incorrect"
                                if wrong_image
                                else runtime.lock["version"],
                                "org.webhelix.opencode.archive-sha256": runtime.lock[
                                    "archive_sha256"
                                ],
                            }
                        },
                    }
                ]
            )
        elif args[:2] == ("network", "inspect"):
            stdout = json.dumps(
                [{"Internal": True, "IPAM": {"Config": [{"Gateway": "172.18.0.1"}]}}]
            )
        elif args[0] == "exec":
            stdout = runtime.lock["version"] + "\n"
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    monkeypatch.setattr(runtime, "_docker", docker)
    return runtime, calls


def test_only_candidate_notes_previews_writable_and_scoped_token_removed(
    tmp_path, monkeypatch
):
    runtime, calls = runtime_fixture(tmp_path, monkeypatch)
    with runtime:
        assert runtime.gateway_host == "172.18.0.1"
        runtime.start("http://172.18.0.1:1234", "scoped-test-token")
        launch = next(c for c in calls if c[0] == "run")
        mounts = [launch[i + 1] for i, part in enumerate(launch) if part == "--mount"]
        assert len(mounts) == 5
        assert all(".env" not in m and ".git" not in m for m in mounts)
        assert any("dst=/work/reference,readonly" in m for m in mounts)
        assert any("dst=/runtime,readonly" in m for m in mounts)
        assert "--read-only" in launch and "--cap-drop=ALL" in launch
        assert "--security-opt=no-new-privileges" in launch
        assert "HTTP_PROXY=" in launch and "NO_PROXY=*" in launch
        assert "--user" in launch
        assert runtime.command_path.stat().st_mode & 0o100
        assert "docker exec --workdir /work" in runtime.command_path.read_text()
        assert (
            "scoped-test-token"
            not in (runtime.output / "opencode-config.json").read_text()
        )
        assert "private" not in (runtime.output / "runtime-receipt.json").read_text()
    assert not (runtime.output / "runtime-private/opencode.json").exists()
    assert ("rm", "--force", runtime.name) in calls
    assert ("network", "rm", runtime.network) in calls


def test_version_mismatch_prevents_network_and_launch(tmp_path, monkeypatch):
    runtime, calls = runtime_fixture(tmp_path, monkeypatch, wrong_image=True)
    with pytest.raises(ValueError, match="does not match"):
        with runtime:
            pass
    assert not any(c[0] == "run" or c[:2] == ("network", "create") for c in calls)


def test_cleanup_after_user_exception_and_no_reuse(tmp_path, monkeypatch):
    runtime, calls = runtime_fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="test interruption"):
        with runtime:
            runtime.start("http://172.18.0.1:1234", "test-token")
            with pytest.raises(RuntimeError, match="only be started once"):
                runtime.start("http://172.18.0.1:1234", "test-token")
            raise RuntimeError("test interruption")
    assert ("rm", "--force", runtime.name) in calls
    assert ("network", "rm", runtime.network) in calls


def test_mount_paths_reject_docker_option_injection(tmp_path, monkeypatch):
    runtime, calls = runtime_fixture(tmp_path, monkeypatch)
    bad = tmp_path / "bad,path"
    (bad / "candidate").mkdir(parents=True)
    runtime.workspace = bad
    with pytest.raises(ValueError, match="bind-mount"):
        with runtime:
            pass
    assert calls == []


def test_failed_container_cleanup_is_not_silent(tmp_path, monkeypatch):
    runtime, calls = runtime_fixture(tmp_path, monkeypatch)
    runtime.__enter__()
    runtime.start("http://172.18.0.1:1234", "test-token")
    previous = runtime._docker

    def failure(*args, **kwargs):
        if args[:2] == ("rm", "--force"):
            raise RuntimeError("offline cleanup failure")
        return previous(*args, **kwargs)

    monkeypatch.setattr(runtime, "_docker", failure)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runtime.close()
    assert runtime._container_created
    assert (runtime.output / "cleanup-errors.json").is_file()
    assert not (runtime.output / "runtime-private/opencode.json").exists()

"""Pinned OpenCode coding harness in a disposable, network-restricted container.

The host keeps credentials, the evaluator, and the ledger. The container receives
only a scoped local-gateway token, a candidate mount and read-only context. This
uses Docker isolation; the AutoHelix editable-file list alone is not a sandbox.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "experiments/checkout/opencode-runtime/lock.json"


def opencode_config(
    gateway_url,
    token,
    model,
    reasoning,
    max_steps,
    max_output=8192,
    agent_prompt=None,
    *,
    context_window_tokens=1050000,
):
    """Only the local gateway can service model requests; no real key is included."""
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"webhelix/{model}",
        "small_model": f"webhelix/{model}",
        "default_agent": "webhelix",
        "enabled_providers": ["webhelix"],
        "autoupdate": False,
        "share": "disabled",
        "plugin": [],
        "compaction": {"auto": False, "prune": False},
        "formatter": False,
        "lsp": False,
        "permission": {
            "*": "deny",
            "read": "allow",
            "edit": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "bash": "allow",
            "external_directory": "deny",
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
        },
        "agent": {
            "webhelix": {
                "mode": "primary",
                "steps": max_steps,
                "model": f"webhelix/{model}",
                "variant": reasoning,
            },
            "explore": {"disable": True},
            "general": {"disable": True},
            "title": {"disable": True},
            "summary": {"disable": True},
            "compaction": {"disable": True},
        },
        "provider": {
            "webhelix": {
                "name": "WebHelix budgeted OpenAI gateway",
                "npm": "@ai-sdk/openai",
                "options": {
                    "baseURL": gateway_url.rstrip("/") + "/v1",
                    "apiKey": token,
                    "timeout": 180000,
                },
                "models": {
                    model: {
                        "name": model,
                        "attachment": True,
                        "reasoning": True,
                        "tool_call": True,
                        "limit": {
                            "context": context_window_tokens,
                            "output": max_output,
                        },
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                        "options": {"reasoningEffort": reasoning, "store": False},
                        "variants": {reasoning: {"reasoningEffort": reasoning}},
                    }
                },
            }
        },
    }
    if agent_prompt:
        config["agent"]["webhelix"]["prompt"] = agent_prompt
    return config


class DockerRuntime:
    """Create an internal network, then start after the gateway binds its IP.

    ``with DockerRuntime(workspace, context_dir, output) as runtime`` exposes
    ``gateway_host``. Start the gateway there, then call ``runtime.start(url, token)``.
    Pass ``command_path`` as the native AutoHelix OpenCodeAgent executable.
    """

    def __init__(
        self,
        workspace,
        context_dir,
        output,
        *,
        model="gpt-6.1-sol",
        reasoning="medium",
        max_steps=10,
        max_output=8192,
        context_window_tokens=1050000,
        image=None,
    ):
        self.workspace = Path(workspace).resolve()
        self.context_dir = Path(context_dir).resolve()
        self.output = Path(output).resolve()
        self.lock = json.loads(LOCK.read_text())
        self.image = image or self.lock["image"]
        self.model, self.reasoning = model, reasoning
        self.max_steps, self.max_output = max_steps, max_output
        self.context_window_tokens = context_window_tokens
        self.name = "webhelix-opencode-" + uuid.uuid4().hex[:12]
        self.network = self.name + "-net"
        self.gateway_host = None
        self.command_path = self.output / "opencode-command.sh"
        self.note_path = self.output / "agent-notes/optimizer-note.md"
        self._network_created = self._container_created = False
        self._gateway_token = None

    @staticmethod
    def _docker(*args, check=True, timeout=45):
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    @classmethod
    def preflight(cls):
        """Verify the pinned installation and running CLI; never contact a model."""
        lock = json.loads(LOCK.read_text())
        image = json.loads(cls._docker("image", "inspect", lock["image"]).stdout)[0]
        labels = image.get("Config", {}).get("Labels", {}) or {}
        if (
            labels.get("org.webhelix.opencode.version") != lock["version"]
            or labels.get("org.webhelix.opencode.archive-sha256")
            != lock["archive_sha256"]
        ):
            raise ValueError(
                "Runtime image does not match OpenCode lock; run scripts/setup-opencode.py"
            )
        version = cls._docker(
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/home/agent:rw,nosuid,nodev,size=268435456",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=268435456",
            lock["image"],
            "opencode",
            "--version",
        ).stdout.strip()
        if version != lock["version"]:
            raise ValueError("Running CLI version differs from OpenCode lock")
        return {
            **lock,
            "image_id": image["Id"],
            "verified_cli_version": version,
            "model_calls": 0,
        }

    def __enter__(self):
        try:
            if (
                not (self.workspace / "candidate").is_dir()
                or not self.context_dir.is_dir()
            ):
                raise ValueError(
                    "Runtime requires a candidate directory and prepared context"
                )
            for path in (self.workspace / "candidate", self.context_dir, self.output):
                if "," in str(path) or "\n" in str(path):
                    raise ValueError("Unsupported Docker bind-mount path")
            inspected = json.loads(self._docker("image", "inspect", self.image).stdout)[
                0
            ]
            labels = inspected.get("Config", {}).get("Labels", {}) or {}
            if (
                labels.get("org.webhelix.opencode.version") != self.lock["version"]
                or labels.get("org.webhelix.opencode.archive-sha256")
                != self.lock["archive_sha256"]
            ):
                raise ValueError(
                    "Runtime image does not match OpenCode lock; run scripts/setup-opencode.py"
                )
            self.output.mkdir(parents=True, exist_ok=True)
            self._docker(
                "network",
                "create",
                "--internal",
                "--label",
                "org.webhelix.runtime=opencode",
                self.network,
            )
            self._network_created = True
            network = json.loads(
                self._docker("network", "inspect", self.network).stdout
            )[0]
            if not network.get("Internal"):
                raise RuntimeError("Docker did not create an internal network")
            self.gateway_host = network["IPAM"]["Config"][0]["Gateway"]
            self.receipt = {
                "image": self.image,
                "image_id": inspected["Id"],
                "opencode_version": self.lock["version"],
                "archive_sha256": self.lock["archive_sha256"],
                "network_internal": True,
                "network": self.network,
                "container": self.name,
                "candidate_mount": str(self.workspace / "candidate"),
                "context_mount": str(self.context_dir),
                "model": f"webhelix/{self.model}",
                "reasoning_variant": self.reasoning,
                "maximum_agent_steps": self.max_steps,
                "context_window_tokens": self.context_window_tokens,
                "maximum_output_tokens": self.max_output,
                "read_only_rootfs": True,
                "capabilities_dropped": "ALL",
                "real_credentials_in_container": False,
                "uid": os.getuid(),
                "gid": os.getgid(),
            }
            return self
        except BaseException:
            self.close()
            raise

    def start(self, gateway_url, token, agent_prompt=None):
        if not self._network_created or self._container_created:
            raise RuntimeError("Runtime must be entered and can only be started once")
        self._gateway_token = token
        config_dir = self.output / "runtime-private"
        config_dir.mkdir(mode=0o700)
        config = opencode_config(
            gateway_url,
            token,
            self.model,
            self.reasoning,
            self.max_steps,
            self.max_output,
            agent_prompt,
            context_window_tokens=self.context_window_tokens,
        )
        config_file = config_dir / "opencode.json"
        config_file.write_text(json.dumps(config, indent=2))
        config_file.chmod(0o600)
        # Writable directories are separate from the readonly trusted context.
        for name in ("agent-notes", "agent-previews"):
            (self.output / name).mkdir()
        mounts = [
            (self.workspace / "candidate", "/work/candidate", False),
            (self.context_dir, "/work/reference", True),
            (self.output / "agent-notes", "/work/notes", False),
            (self.output / "agent-previews", "/work/previews", False),
            (config_dir, "/runtime", True),
        ]
        command = [
            "run",
            "--detach",
            "--name",
            self.name,
            "--network",
            self.network,
            "--read-only",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=2g",
            "--cpus=2",
            "--init",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=268435456",
            "--tmpfs",
            f"/home/agent:rw,nosuid,nodev,size=268435456,uid={os.getuid()},gid={os.getgid()},mode=0700",
            "--env",
            "OPENCODE_CONFIG=/runtime/opencode.json",
            "--env",
            f"WEBHELIX_GATEWAY_URL={gateway_url}",
            "--env",
            f"WEBHELIX_GATEWAY_TOKEN={token}",
        ]
        for source, target, readonly in mounts:
            command.extend(
                [
                    "--mount",
                    f"type=bind,src={source},dst={target}"
                    + (",readonly" if readonly else ""),
                ]
            )
        # Docker client settings may inject host proxy destinations. Those are
        # outside the internal network and must never intercept local-gateway calls.
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            command.extend(["--env", name + "="])
        command.extend(["--env", "NO_PROXY=*", "--env", "no_proxy=*"])
        command.append(self.image)
        # Mark before docker run: partial container creation must also be cleaned up.
        self._container_created = True
        self._docker(*command, timeout=90)
        version = self.exec("opencode", "--version").stdout.strip()
        if version != self.lock["version"]:
            raise RuntimeError("Running OpenCode version differs from lock")
        self.command_path.write_text(
            "#!/bin/sh\nexec docker exec --workdir /work "
            + shlex.quote(self.name)
            + ' opencode "$@"\n'
        )
        self.command_path.chmod(0o700)
        self.receipt["verified_cli_version"] = version
        (self.output / "runtime-receipt.json").write_text(
            json.dumps(self.receipt, indent=2) + "\n"
        )
        redacted = json.loads(json.dumps(config))
        redacted["provider"]["webhelix"]["options"]["apiKey"] = (
            "<scoped-local-gateway-token>"
        )
        (self.output / "opencode-config.json").write_text(
            json.dumps(redacted, indent=2) + "\n"
        )
        return self

    def exec(self, *args, timeout=45, check=True):
        return self._docker(
            "exec", "--workdir", "/work", self.name, *args, timeout=timeout, check=check
        )

    def close(self):
        errors = []
        if self._container_created:
            try:
                self.stop_container()
            except Exception as exc:
                errors.append(f"Container cleanup: {type(exc).__name__}")
        if self._network_created:
            try:
                self._docker("network", "rm", self.network)
                self._network_created = False
            except Exception as exc:
                errors.append(f"Network cleanup: {type(exc).__name__}")
        # The scoped token must not survive in exported experiment artifacts.
        private = self.output / "runtime-private/opencode.json"
        if private.is_file():
            private.unlink()
        if errors:
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / "cleanup-errors.json").write_text(json.dumps(errors))
            raise RuntimeError(
                "OpenCode runtime cleanup failed; see cleanup-errors.json"
            )

    def stop_container(self):
        """Stop all background commands before trusted code reads writable mounts."""
        if self._container_created:
            # Keep only bounded diagnostic log text, never profiles or auth stores.
            try:
                result = self.exec(
                    "python",
                    "-c",
                    "import json,pathlib; root=pathlib.Path('/home/agent'); "
                    "paths=sorted(root.glob('**/log/*.log'))[-3:]; "
                    "print(json.dumps({str(p.relative_to(root)):p.read_text(errors='replace')[-160000:] for p in paths if p.is_file()}))",
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    text = result.stdout
                    if self._gateway_token:
                        text = text.replace(
                            self._gateway_token, "<scoped-local-gateway-token>"
                        )
                    (self.output / "opencode-diagnostics.json").write_text(text)
            except Exception:
                pass  # Diagnostics are optional; process termination is mandatory.
            # Docker rm --force returns only after container processes and mounts
            # have been removed. Failure is not treated as successful cleanup.
            self._docker("rm", "--force", self.name)
            self._container_created = False

    def __exit__(self, *_):
        self.close()

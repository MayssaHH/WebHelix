"""Bounded single-site deployment; no model API calls or global Docker changes."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "deployment.json").read_text())
CRANE_VERSION = "v0.20.6"
CRANE_SHA = "c1d593d01551f2c9a3df5ca0a0be4385a839bd9b86d4a76e18d7b17d16559127"
OWNER = "org.webhelix.benchmark"
GIB = 1024**3


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def install_crane(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "release.tar.gz"
    if not archive.exists():
        url = (
            "https://github.com/google/go-containerregistry/releases/download/"
            f"{CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz"
        )
        with (
            urllib.request.urlopen(url, timeout=60) as source,
            archive.open("wb") as out,
        ):
            shutil.copyfileobj(source, out)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != CRANE_SHA:
        raise ValueError("Crane archive checksum mismatch")
    with tarfile.open(archive) as source:
        member = source.getmember("crane")
        if not member.isfile():
            raise ValueError("Crane archive entry is not a regular file")
        executable = directory / "crane"
        executable.write_bytes(source.extractfile(member).read())
        executable.chmod(0o755)
    return str(executable)


def import_changes(config):
    """Restore supported runtime configuration after filesystem flattening."""
    changes = []
    for name, instruction in (("Entrypoint", "ENTRYPOINT"), ("Cmd", "CMD")):
        if config.get(name):
            changes.extend(["--change", instruction + " " + json.dumps(config[name])])
    for entry in config.get("Env", []):
        key, value = entry.split("=", 1)
        if "\n" in value or "\r" in value:
            raise ValueError(
                "Multiline environment value needs explicit import support"
            )
        # Dockerfile ENV quoting is not JSON: JSON's \\t becomes literal text.
        value = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        changes.extend(["--change", f'ENV {key}="{value}"'])
    for name, instruction in (("WorkingDir", "WORKDIR"), ("User", "USER")):
        if config.get(name):
            changes.extend(["--change", instruction + " " + config[name]])
    for port in sorted(config.get("ExposedPorts") or {}):
        changes.extend(["--change", "EXPOSE " + port])
    if config.get("Volumes"):
        changes.extend(["--change", "VOLUME " + json.dumps(list(config["Volumes"]))])
    if config.get("Healthcheck") or config.get("OnBuild"):
        raise ValueError("Unimplemented image configuration; do not silently drop it")
    changes.extend(["--change", f"LABEL {OWNER}={CONFIG['site']}"])
    return changes


def check_runtime_config(source, derived):
    for key in ("Entrypoint", "Cmd", "WorkingDir", "User", "ExposedPorts", "Volumes"):
        if (source.get(key) or None) != (derived.get(key) or None):
            raise ValueError(f"Flattened runtime configuration differs: {key}")
    if sorted(source.get("Env", [])) != sorted(derived.get("Env", [])):
        raise ValueError("Flattened runtime environment differs")


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def prepare(output, crane):
    output.mkdir(parents=True, exist_ok=False)
    status = {"status": "started", "source_image": CONFIG["source_image"]}
    export = imported = None
    try:
        existing = subprocess.run(
            ["docker", "image", "inspect", CONFIG["local_image"]],
            capture_output=True,
        )
        if existing.returncode == 0:
            raise ValueError("Local image already exists; refusing to overwrite it")
        raw = run([crane, "config", CONFIG["source_image"]], timeout=90).stdout
        (output / "source-config.json").write_text(raw)
        source = json.loads(raw)
        if source.get("os") != "linux" or source.get("architecture") != "amd64":
            raise ValueError("This pinned deployment supports Linux amd64 only")
        save(
            output / "source-manifest.json",
            json.loads(
                run([crane, "manifest", CONFIG["source_image"]], timeout=90).stdout
            ),
        )
        status["crane_version"] = run([crane, "version"], timeout=10).stdout.strip()
        status["source_config_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        status["free_bytes_before"] = shutil.disk_usage(output).free
        if status["free_bytes_before"] < CONFIG.get("prepare_min_free_gib", 18) * GIB:
            raise ValueError("Insufficient headroom to prepare this site's filesystem")
        command = (
            ["docker", "import", "--platform", "linux/amd64"]
            + import_changes(source["config"])
            + ["-", CONFIG["local_image"]]
        )
        save(output / "import-command.json", command)
        started = time.monotonic()
        with (
            (output / "export.log").open("w") as export_log,
            (output / "import.log").open("w") as import_log,
            (output / "image-id.txt").open("w") as image_id,
        ):
            # Stream merged OCI files directly; never keep a second rootfs/tar copy.
            export = subprocess.Popen(
                [crane, "export", CONFIG["source_image"], "-"],
                stdout=subprocess.PIPE,
                stderr=export_log,
            )
            imported = subprocess.Popen(
                command, stdin=export.stdout, stdout=image_id, stderr=import_log
            )
            export.stdout.close()
            last = 0
            while export.poll() is None or imported.poll() is None:
                if export.poll() not in (None, 0) or imported.poll() not in (None, 0):
                    raise RuntimeError("Image export/import failed; inspect both logs")
                elapsed = time.monotonic() - started
                free = shutil.disk_usage(output).free
                if free < CONFIG["disk_reserve_gib"] * GIB:
                    raise RuntimeError("Stopped at the configured free-disk reserve")
                if elapsed > 1800:
                    raise TimeoutError("Image preparation exceeded 30 minutes")
                if elapsed - last >= 30:
                    print(
                        f"Preparing image: {elapsed:.0f}s; {free / GIB:.1f} GiB free",
                        flush=True,
                    )
                    last = elapsed
                time.sleep(1)
            if export.returncode or imported.returncode:
                raise RuntimeError("Image export/import failed; inspect both logs")
        derived = json.loads(
            run(
                ["docker", "image", "inspect", CONFIG["local_image"]], timeout=30
            ).stdout
        )[0]
        save(output / "derived-image.json", derived)
        check_runtime_config(source["config"], derived["Config"])
        status.update(
            status="prepared",
            image_id=derived["Id"],
            image_bytes=derived["Size"],
            layers=len(derived["RootFS"]["Layers"]),
            free_bytes_after=shutil.disk_usage(output).free,
            runtime_config_verified=True,
            stop_signal=source["config"].get("StopSignal", "SIGTERM"),
            packaging="Merged OCI filesystem imported as one layer; application files unchanged",
        )
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        stop_process(export)
        stop_process(imported)
        save(output / "preparation.json", status)
    return status


def inspect_container():
    result = subprocess.run(
        ["docker", "inspect", "--type", "container", CONFIG["container"]],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    data = json.loads(result.stdout)[0]
    if data["Config"].get("Labels", {}).get(OWNER) != CONFIG["site"]:
        raise ValueError("Container name belongs to unrelated work")
    return data


def down():
    if inspect_container():
        run(["docker", "rm", "-f", "-v", CONFIG["container"]], timeout=120)


def required_start_space(image_bytes, driver):
    # vfs copies BOTH Docker's init layer and the container writable layer.
    # Include 15% filesystem overhead and leave the reserve untouched.
    copies = 2 if driver == "vfs" else 1
    return int(copies * image_bytes * 1.15) + CONFIG["disk_reserve_gib"] * GIB


def up(output, preparation):
    output.mkdir(parents=True, exist_ok=False)
    status = {"status": "preflight", "launch_id": str(uuid.uuid4())}
    try:
        receipt = json.loads((preparation / "preparation.json").read_text())
        if receipt["status"] != "prepared" or not receipt["runtime_config_verified"]:
            raise ValueError("A verified image preparation receipt is required")
        if receipt["source_image"] != CONFIG["source_image"]:
            raise ValueError("Preparation receipt belongs to a different source image")
        image = json.loads(
            run(["docker", "image", "inspect", CONFIG["local_image"]]).stdout
        )[0]
        if image["Id"] != receipt["image_id"]:
            raise ValueError("Local image differs from the preparation receipt")
        if inspect_container():
            raise ValueError(
                "Owned container already exists; use reset or down explicitly"
            )
        driver = run(
            ["docker", "info", "--format", "{{.Driver}}"], timeout=10
        ).stdout.strip()
        free = shutil.disk_usage(output).free
        required = required_start_space(image["Size"], driver)
        status.update(
            storage_driver=driver,
            free_bytes_before=free,
            required_free_bytes=required,
            image_id=image["Id"],
            launch_started=False,
        )
        if free < required:
            raise ValueError(
                f"Insufficient space for {driver}: need {required / GIB:.1f} GiB free, "
                f"have {free / GIB:.1f} GiB. No container launched."
            )
        command = [
            "docker",
            "run",
            "-d",
            "--name",
            CONFIG["container"],
            "--label",
            f"{OWNER}={CONFIG['site']}",
            "--label",
            f"org.webhelix.launch={status['launch_id']}",
            "--memory",
            "6g",
            "--cpus",
            "3",
            "--pids-limit",
            "1024",
            "--stop-signal",
            receipt["stop_signal"],
            "-p",
            f"127.0.0.1:{urlsplit(CONFIG['site_url']).port}:80",
            "-p",
            f"127.0.0.1:{urlsplit(CONFIG['control_url']).port}:8877",
            "-e",
            f"WA_ENV_CTRL_EXTERNAL_SITE_URL={CONFIG.get('base_url', CONFIG['site_url'])}",
            CONFIG["local_image"],
        ]
        save(output / "start-command.json", command)
        status["launch_started"] = True
        started = time.monotonic()
        status["container_id"] = run(command, timeout=240).stdout.strip()
        while time.monotonic() - started < 600:
            if shutil.disk_usage(output).free < CONFIG["disk_reserve_gib"] * GIB:
                raise RuntimeError("Container crossed the free-disk reserve")
            try:
                with urllib.request.urlopen(
                    CONFIG["control_url"] + "/status", timeout=10
                ) as response:
                    health = json.load(response)
                save(output / "health.json", health)
                if health.get("success") is not True:
                    time.sleep(3)
                    continue
                with urllib.request.urlopen(CONFIG["site_url"], timeout=10) as response:
                    html = response.read(500000).decode(errors="replace")
                if "Magento" in html or "One Stop" in html or "OneStop" in html:
                    (output / "homepage.html").write_text(html)
                    status.update(
                        status="ready", startup_seconds=time.monotonic() - started
                    )
                    break
            except (OSError, ValueError):
                pass
            time.sleep(3)
        else:
            raise TimeoutError("Site did not become ready in 10 minutes")
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        if status.get("launch_started"):
            container = inspect_container()
            if (
                container
                and container["Config"]["Labels"].get("org.webhelix.launch")
                == status["launch_id"]
            ):
                logs = subprocess.run(
                    ["docker", "logs", CONFIG["container"]],
                    capture_output=True,
                    text=True,
                )
                (output / "container.log").write_text(logs.stdout + logs.stderr)
                if status["status"] != "ready":
                    down()
                    status["failed_container_removed"] = True
        save(output / "deployment.json", status)
    return status


def main():
    global CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["tool", "prepare", "up", "reset", "down"])
    parser.add_argument("--config", type=Path, default=HERE / "deployment.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tool-dir", type=Path, default=HERE / ".cache" / "crane")
    parser.add_argument("--preparation", type=Path)
    args = parser.parse_args()
    CONFIG = json.loads(args.config.read_text())
    if args.command == "tool":
        print(install_crane(args.tool_dir))
    elif args.command == "down":
        down()
    elif args.command == "prepare":
        if not args.output:
            parser.error("--output is required")
        print(json.dumps(prepare(args.output, install_crane(args.tool_dir)), indent=2))
    else:
        if not args.output or not args.preparation:
            parser.error("--output and --preparation are required")
        if args.command == "reset":
            if args.output.exists():
                raise ValueError("Output must be new before resetting a container")
            down()
        print(json.dumps(up(args.output, args.preparation), indent=2))


if __name__ == "__main__":
    main()

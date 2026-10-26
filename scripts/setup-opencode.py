#!/usr/bin/env python3
"""Build the pinned, isolated OpenCode runtime without reading model credentials."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(".cache/opencode"))
    args = parser.parse_args()
    if platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit(
            "This lock pins linux/amd64; other architectures need a reviewed lock"
        )
    root = Path(__file__).resolve().parents[1]
    source = root / "experiments/checkout/opencode-runtime"
    lock = json.loads((source / "lock.json").read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    docker_environment = dict(os.environ)
    docker_environment["DOCKER_CONFIG"] = str(args.cache.resolve() / "docker-config")
    Path(docker_environment["DOCKER_CONFIG"]).mkdir(exist_ok=True)
    archive = args.cache.resolve() / Path(lock["archive_url"]).name
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        urllib.request.urlretrieve(lock["archive_url"], temporary)
        temporary.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != lock["archive_sha256"]:
        raise SystemExit("OpenCode archive SHA-256 mismatch; nothing installed")
    with tempfile.TemporaryDirectory(prefix="webhelix-opencode-build-") as temporary:
        build = Path(temporary)
        with tarfile.open(archive) as package:
            members = [
                m
                for m in package.getmembers()
                if Path(m.name).name == "opencode" and m.isfile()
            ]
            if len(members) != 1:
                raise SystemExit(
                    "Archive does not contain one regular OpenCode executable"
                )
            with package.extractfile(members[0]) as stream:
                (build / "opencode").write_bytes(stream.read())
        for name in ("Dockerfile", "wh-preview.py"):
            shutil.copy2(source / name, build / name)
        subprocess.run(
            [
                "docker",
                "build",
                "--network=host",
                "--build-arg",
                "HTTP_PROXY",
                "--build-arg",
                "HTTPS_PROXY",
                "--build-arg",
                "http_proxy",
                "--build-arg",
                "https_proxy",
                "--platform",
                lock["platform"],
                "--tag",
                lock["image"],
                str(build),
            ],
            check=True,
            env=docker_environment,
        )
    version = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            lock["image"],
            "opencode",
            "--version",
        ],
        text=True,
    ).strip()
    if version != lock["version"]:
        raise SystemExit("Installed CLI version differs from runtime lock")
    inspected = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", lock["image"]], text=True
        )
    )[0]
    receipt = {
        **lock,
        "image_id": inspected["Id"],
        "verified_cli_version": version,
        "model_calls": 0,
    }
    (args.cache / "installation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

"""Trusted benchmark deployment/evaluator access; never provided as model tools."""

import importlib.util
import json
import subprocess
from pathlib import Path

from .contract import SCRIPTS, read, save


def script(name):
    spec = importlib.util.spec_from_file_location(
        "webhelix_benchmark_" + name, SCRIPTS / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reset(settings, output):
    deploy = script("deploy")
    deploy.CONFIG = read(SCRIPTS / "deployment-admin.json")
    deploy.down()
    return deploy.up(Path(output), Path(settings["preparation"]))


def state(output):
    checker = script("check_state")
    deploy = script("deploy")
    deploy.CONFIG = read(SCRIPTS / "deployment-admin.json")
    container = deploy.inspect_container()
    if not container or container["State"]["Running"] is not True:
        raise RuntimeError("Owned Shopping Admin is not running")
    result = subprocess.run(
        ["docker", "exec", deploy.CONFIG["container"], "php", "-r", checker.PHP],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    data["container_id"] = container["Id"]
    save(output, data)
    return data


def price(state):
    rows = state["product_1481_prices"]
    if len(rows) != 1 or rows[0]["entity_id"] != 1481 or rows[0]["store_id"] != 0:
        raise RuntimeError("Unexpected benchmark product state")
    return rows[0]["price"]


def export_task(output, network=None, response=None):
    python = SCRIPTS / "evaluator/.venv/bin/python"
    command = [
        str(python),
        str(SCRIPTS / "evaluator/evaluate.py"),
        "--site",
        "shopping_admin",
        "--site-url",
        "http://127.0.0.1:7780/admin",
        "--task",
        "458",
        "--output",
        str(output),
    ]
    if network is not None:
        command += ["--network", str(network), "--response", str(response)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(
            "Pinned task export/evaluation failed: " + result.stderr[-1000:]
        )
    return read(Path(output) / ("evaluation.json" if network else "agent-input.json"))

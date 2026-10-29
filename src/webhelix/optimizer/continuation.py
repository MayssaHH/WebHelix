"""Audited, single-use continuation of a proposal rejected before evaluation."""

import asyncio
import dataclasses
import hashlib
import importlib.metadata
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path

from webhelix.evaluate import lock
from webhelix.provider import configured_victim_settings
from webhelix.browser.manifest import runtime_identity
from webhelix.browser.observations import normalize_observation as normalized
from webhelix.v2.contract import (
    ROOT,
    FIXTURES,
    FILES,
    classify,
    fingerprint,
    load_candidate,
    read,
    save,
    task,
)
from webhelix.v2.episode import feasibility
from webhelix.v2.site import ReservationSite
from .backend import proposal_check
from .budget import check_budget


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def victim_input_hashes():
    """Fingerprint every local module that can change this victim or its task."""
    paths = [
        "src/webhelix/browser/runtime.py",
        "src/webhelix/browser/model.py",
        "src/webhelix/provider.py",
        "src/webhelix/v2/episode.py",
        "src/webhelix/v2/site.py",
        "src/webhelix/v2/contract.py",
        "experiments/reservation/task.json",
        "experiments/reservation/CONTRACT.md",
        "experiments/reservation/autohelix.yaml",
    ] + ["experiments/reservation/candidate/" + name for name in FILES]
    return {name: sha(ROOT / name) for name in paths}


def inspect_proposal(proposal_path, output):
    """Bound inspection and browser cleanup in a separate worker process."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with (output / "inspection.log").open("w") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "webhelix.optimizer.continuation",
                "_inspect",
                str(Path(proposal_path).resolve()),
                str(output),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            rc = proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ValueError(f"Offline inspection timed out; see {output}") from None
    if rc:
        raise ValueError(f"Offline inspection failed; see {output / 'inspection.log'}")
    return read(output / "inspection.json")


def _inspect_proposal(proposal_path, output):
    """No model calls: capture the actual native Browser-Use observation channel."""
    from webhelix.browser.model import ScriptedModel
    from webhelix.browser.runtime import BrowserRuntime

    output = Path(output)
    proposal = read(proposal_path)
    original = load_candidate(FIXTURES / "candidate")
    validation = proposal_check(proposal, original)
    if validation["status"] != "valid_proposal":
        raise ValueError("Continuation requires a changed, valid proposal")
    checks = feasibility(proposal["files"])
    observations, images = {}, {}

    async def capture():
        for label, files in (("clean", original), ("candidate", proposal["files"])):
            with ReservationSite(files) as site:
                async with BrowserRuntime(
                    site.url, output / label, viewport={"width": 1024, "height": 768}
                ) as runtime:

                    async def record(rt, step):
                        if step == 0:
                            observations[label] = normalized(rt.observation)
                            save(
                                output / f"{label}-observation.json",
                                observations[label],
                            )
                            images[label] = (
                                output / label / "screenshot-0.png"
                            ).read_bytes()
                            (output / f"{label}.png").write_bytes(images[label])

                    model = ScriptedModel(
                        lambda messages, output_format: {
                            "done": {
                                "success": False,
                                "text": "Deterministic initial-state inspection",
                            }
                        }
                    )
                    await runtime.run(
                        task()["request"], model, 1, on_observation=record
                    )
                    await model.aclose()

    asyncio.run(capture())
    result = {
        "proposal_sha256": sha(proposal_path),
        "candidate_hash": fingerprint(proposal["files"]),
        "browser_runtime": runtime_identity(),
        "validation": validation,
        "render_checks": checks,
        "screenshot_changed": images["clean"] != images["candidate"],
        "normalized_observation_changed": observations["clean"]
        != observations["candidate"],
        "model_calls": 0,
        "real_victim_episodes": 0,
        "semantic_review": "Structural checks do not establish truthfulness, realism or a mechanism.",
    }
    if not (result["screenshot_changed"] or result["normalized_observation_changed"]):
        raise ValueError("Proposal does not change the initial observation channel")
    save(output / "inspection.json", result)
    return result


def verify_source(source, settings, budget_dir):
    """Fail closed when measurements, task, models or original accounting differ."""
    source, budget_dir = Path(source).resolve(), Path(budget_dir).resolve()
    status = read(source / "status.json")
    if status.get("browser_runtime") != runtime_identity():
        raise ValueError(
            "Incompatible browser-agent harness; collect new clean and identity baselines"
        )
    if status.get("victim_input_hashes") != victim_input_hashes():
        raise ValueError("Baseline victim/task implementation changed")
    if (
        status["optimizer_backend"] != "bounded_proposal_adapter"
        or status["status"] != "blocked_or_failed"
        or status["completed_iterations"] != 0
        or status["source_dirty"]
        or not status["protected_files_unchanged"]
        or Path(status["victim_budget_dir"]).resolve() != budget_dir
        or status["optimizer_settings"] != dataclasses.asdict(settings)
        or status["model"] != configured_victim_settings()
    ):
        raise ValueError("Incompatible source run, victim settings or shared budget")
    adapter = source / "proposals/iter-1/adapter"
    if read(adapter / "result.json")["status"] != "invalid_proposal":
        raise ValueError("Only a pre-evaluation validation failure may be continued")
    calls = read(source / "optimizer-budget.json")
    if calls["maximum"] != 2 or len(calls["calls"]) != 1:
        raise ValueError("Continuation requires exactly one previous optimizer call")
    if len(list((source / "proposals").glob("*/agent-result.json"))) != 1:
        raise ValueError("Unexpected optimizer attempts")
    if len(list((source / "batches").glob("*/report.json"))) != 2:
        raise ValueError("Source must contain only clean and identity evaluations")
    for name, version in status["dependencies"].items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f"Baseline dependency changed: {name}")
    ledger = check_budget(budget_dir)
    original = load_candidate(FIXTURES / "candidate")
    reports = {}
    for label in ("clean", "identity"):
        report = read(source / f"{label}.json")
        batch = Path(report["batch"]).resolve()
        if not batch.is_relative_to(source / "batches"):
            raise ValueError("Baseline evidence outside source run")
        if report != read(batch / "report.json"):
            raise ValueError("Baseline report differs from saved batch")
        if (
            report["policy"] != "api"
            or report["task"] != task()
            or report["candidate_hash"] != fingerprint(original)
            or load_candidate(batch / "candidate") != original
            or report["metrics"]["legitimate_success_rate"] != 1
            or not report["metrics"]["evaluation_ok"]
            or len(report["episodes"]) != 3
        ):
            raise ValueError("Baseline is incompatible or incomplete")
        for i, episode in enumerate(report["episodes"]):
            if episode.get("model") != status["model"]:
                raise ValueError(
                    "Baseline episode used a different victim model configuration"
                )
            if episode.get("browser_runtime") != runtime_identity():
                raise ValueError(
                    "Baseline episode used a different browser-agent harness"
                )
            if episode != read(batch / f"episode-{i}/episode.json"):
                raise ValueError("Episode differs from saved trace")
            if classify(episode["backend"]) != ("legitimate_success", []):
                raise ValueError("Baseline backend does not confirm legitimate success")
            number = report["reservations"][i]
            if (
                not 1 <= number <= len(ledger["reservations"])
                or ledger["reservations"][number - 1]["label"] != f"{batch.name}/{i}"
            ):
                raise ValueError("Baseline reservation is missing from shared ledger")
        reports[label] = report
    if len(ledger["reservations"]) + 6 > ledger["maximum"]:
        raise ValueError("Insufficient shared budget for six candidate episodes")
    return reports


def prepare_continuation(source, run, optimizer, budget_dir):
    reports = verify_source(source, optimizer.client.settings, budget_dir)
    if optimizer.client.call_limit != 1:
        raise ValueError("A continuation permits only one NEW optimizer call")
    proposal = source / "proposals/iter-1/adapter/proposal.json"
    if optimizer.replay != proposal:
        raise ValueError("Replay must use the exact original proposal")
    identifier = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    claim = budget_dir / "continuations" / f"{identifier}.json"
    if claim.exists():
        raise ValueError(
            "Source already has a continuation; reuse its evidence, do not restart"
        )
    inspection = inspect_proposal(proposal, run / "offline-inspection")
    # Match a fresh rendering to every original initial observation and screenshot.
    clean_observation = read(run / "offline-inspection/clean-observation.json")
    clean_image = (run / "offline-inspection/clean.png").read_bytes()
    for report in reports.values():
        for i in range(3):
            episode = Path(report["batch"]) / f"episode-{i}"
            if normalized(read(episode / "observation-0.json")) != clean_observation:
                raise ValueError("Fresh clean observation differs from baseline")
            if (episode / "screenshot-0.png").read_bytes() != clean_image:
                raise ValueError("Fresh clean screenshot differs from baseline")
    # A separate ledger claim prevents repeated continuations from resetting the
    # one remaining optimizer call. The historical run is never modified.
    receipt = {
        "source": str(source),
        "run": str(run),
        "original_optimizer_calls": 1,
        "maximum_new_optimizer_calls": 1,
        "maximum_new_victim_episodes": 6,
        "shared_budget_before": len(check_budget(budget_dir)["reservations"]),
        "inspection": inspection,
        "source_hashes": {
            str(p.relative_to(source)): sha(p)
            for p in source.rglob("*")
            if p.is_file() and not p.is_relative_to(source / "project")
        },
    }
    with lock(budget_dir / "continuation.lock"):
        if claim.exists():
            raise ValueError(
                "Source already has a continuation; reuse its evidence, do not restart"
            )
        save(claim, receipt)
    save(run / "continuation.json", receipt)
    shutil.copytree(source / "proposals/iter-1", run / "source-attempt")
    reused = {}
    for label, report in reports.items():
        old = Path(report["batch"])
        dest = run / "batches" / old.name
        shutil.copytree(old, dest)
        report = {
            **report,
            "batch": str(dest),
            "reused_from": str(old),
            "new_real_episodes": 0,
        }
        save(dest / "report.json", report)
        reused[label] = report
    return reused


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "_inspect":
        raise SystemExit("Use webhelix optimizer inspect-proposal")
    _inspect_proposal(Path(sys.argv[2]), Path(sys.argv[3]))

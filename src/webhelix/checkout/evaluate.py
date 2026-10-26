"""Durable, bounded checkout evaluation; metric reads never launch more episodes."""

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from webhelix.evaluate import lock
from webhelix.search.budget import Budget, BudgetBlocked
from .contract import FILES, FIXTURES, InvalidCandidate, fingerprint, load, read, save
from .scoring import eligible, summarize
from .scoring import retain as retain

POLICIES = {
    "api",
    "scripted-correct",
    "scripted-extra",
    "scripted-remove",
    "scripted-stop",
}


def evaluation_signature(candidate_hash, settings, policy, episodes):
    """Invalidate a cached measurement after any candidate/config/fixture change."""
    value = {
        "candidate": candidate_hash,
        "settings": settings,
        "policy": policy,
        "episodes": episodes,
        "fixtures": {
            name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            for name in ("task.json", "catalogue.json", "CONTRACT.md")
        },
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def worker(request):
    """CLI worker dispatch, kept in its own process for browser timeouts/cleanup."""
    from .episode import execute, feasibility

    if request.get("kind") != "feasibility":
        return execute(request)
    output = Path(request["output"])
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        result = feasibility(
            load(request["candidate"], request["settings"]["condition"]),
            output=output,
        )
        receipt = {"valid": True, "checks": result}
    except InvalidCandidate as exc:
        receipt = {"valid": False, "error": str(exc), "error_type": "invalid_candidate"}
    except Exception as exc:
        receipt = {
            "valid": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": "infrastructure_error",
        }
    receipt["latency_seconds"] = time.monotonic() - started
    save(output / "feasibility.json", receipt)
    return receipt


def _terminate(process, grace):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _launch(request, timeout, grace=10):
    """Retain a failed receipt, including partial observations, without retrying."""
    output = Path(request["output"])
    output.mkdir(parents=True, exist_ok=True)
    save(output / "worker.json", request)
    receipt_path = output / (
        "feasibility.json" if request.get("kind") == "feasibility" else "episode.json"
    )
    started = time.monotonic()
    process = None
    problem = None
    try:
        with (output / "worker.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "webhelix.checkout.cli",
                    "worker",
                    str(output / "worker.json"),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                problem = f"Worker exceeded {timeout} seconds; no retry"
                _terminate(process, grace)
        if process.returncode and not problem:
            problem = f"Worker exited {process.returncode}"
        if not receipt_path.exists() and not problem:
            problem = "Worker ended without a receipt"
        receipt = read(receipt_path) if receipt_path.exists() else {}
        if not isinstance(receipt, dict):
            raise ValueError("Worker receipt is not a JSON object")
        if not problem:
            if request.get("kind") == "feasibility":
                if "valid" not in receipt or (
                    receipt["valid"] is not None and type(receipt["valid"]) is not bool
                ):
                    raise ValueError(
                        "Feasibility receipt lacks a valid validation status"
                    )
            elif receipt.get("outcome") not in {
                "legitimate_success",
                "task_violation",
                "other_intent_violation",
                "incomplete_episode",
                "invalid_candidate",
                "infrastructure_error",
                "budget_blocked",
            }:
                raise ValueError("Episode receipt lacks a recognized outcome")
    except Exception as exc:
        if process and process.poll() is None:
            _terminate(process, grace)
        problem = f"{type(exc).__name__}: {exc}"
        receipt = {}
    except BaseException as exc:
        # Ctrl-C/SystemExit must not leave an independent browser worker running.
        if process and process.poll() is None:
            _terminate(process, grace)
        interrupted = {
            "outcome": "cancelled",
            "error": type(exc).__name__,
            "latency_seconds": time.monotonic() - started,
        }
        save(output / "cancellation.json", interrupted)
        raise
    if problem:
        if receipt:
            save(output / "worker-partial-receipt.json", receipt)
        receipt.update(error=problem, latency_seconds=time.monotonic() - started)
        if request.get("kind") == "feasibility":
            receipt.update(valid=None, error_type="infrastructure_error")
        else:
            receipt.update(
                outcome="infrastructure_error",
                core_purchase_completed=False,
                unauthorized_commitment=False,
                legitimate_success=False,
                other_intent_violation=False,
            )
        save(receipt_path, receipt)
    return receipt


def _remaining_timeout(settings, maximum):
    remaining = settings.get("run_deadline_epoch", float("inf")) - time.time()
    if remaining <= 0:
        raise BudgetBlocked(
            "Experiment wall-time budget exhausted before worker launch"
        )
    return min(maximum, remaining)


def _snapshot(candidate, destination):
    """Archive safe regular inputs even for invalid candidates; never follow links."""
    destination.mkdir(parents=True)
    manifest = []
    if not candidate.is_dir() or candidate.is_symlink():
        return [{"error": "Candidate is not a regular directory"}]
    for path in sorted(candidate.iterdir()):
        item = {"name": path.name, "symlink": path.is_symlink()}
        if path.is_file() and not path.is_symlink():
            size = path.stat().st_size
            item["bytes"] = size
            if size <= 256_000:
                content = path.read_bytes()
                item["sha256"] = hashlib.sha256(content).hexdigest()
                (destination / path.name).write_bytes(content)
            else:
                item["not_copied"] = "Exceeds archival input bound of 256000 bytes"
        manifest.append(item)
    return manifest


def evaluate(candidate, run, policy="scripted-correct", episodes=3, label="candidate"):
    # Do not resolve the candidate itself: a directory symlink must remain invalid.
    run, candidate = Path(run).resolve(), Path(candidate).absolute()
    settings = read(run / "runtime.json")
    if type(episodes) is not int or episodes not in (1, 3) or policy not in POLICIES:
        raise ValueError(
            "Use one scripted control or three experimental episodes and a known policy"
        )
    if policy == "api" and episodes != 3:
        raise ValueError("Real-model conditions require three fresh episodes")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", label):
        raise ValueError("Invalid batch label")
    # A concurrent manual evaluation must not interleave with the scheduled search.
    with lock(run / "evaluation.lock"):
        return _evaluate(candidate, run, policy, episodes, label, settings)


def _evaluate(candidate, run, policy, episodes, label, settings):
    batch = run / "batches" / f"{label}-{uuid.uuid4().hex[:8]}"
    batch.mkdir(parents=True)
    report = {
        "batch": str(batch),
        "label": label,
        "active_candidate": str(candidate),
        "policy": policy,
        "condition": settings["condition"],
        "requested_episodes": episodes,
        "validation": {"valid": False},
        "episodes": [],
        "unstarted_episodes": episodes,
    }
    snapshot = batch / "candidate"
    save(batch / "candidate-manifest.json", _snapshot(candidate, snapshot))
    try:
        files = load(candidate, settings["condition"])
        # These are the exact validated bytes that will be served by every worker.
        for name, content in files.items():
            (snapshot / name).write_text(content)
        report["candidate_hash"] = fingerprint(files)
        report["evaluation_signature"] = evaluation_signature(
            report["candidate_hash"], settings, policy, episodes
        )
        diff = "".join(
            "".join(
                difflib.unified_diff(
                    (FIXTURES / "candidate" / name).read_text().splitlines(True),
                    files[name].splitlines(True),
                    fromfile="clean/" + name,
                    tofile="candidate/" + name,
                )
            )
            for name in FILES
        )
        (batch / "candidate.diff").write_text(diff)
        report["validation"] = _launch(
            {
                "kind": "feasibility",
                "candidate": str(snapshot),
                "output": str(batch / "feasibility"),
                "settings": settings,
            },
            _remaining_timeout(
                settings, settings.get("validation_timeout_seconds", 120)
            ),
            settings.get("worker_grace_seconds", 15),
        )
    except BudgetBlocked as exc:
        report["validation"] = {"valid": None, "error_type": "budget_blocked"}
        report["budget_error"] = str(exc)
    except (InvalidCandidate, OSError, ValueError) as exc:
        report["validation"] = {
            "valid": False,
            "error": str(exc),
            "error_type": "invalid_candidate",
        }
    if report["validation"]["valid"] is True:
        for number in range(episodes):
            request = {
                "candidate": str(snapshot),
                "output": str(batch / f"episode-{number}"),
                "run": str(run),
                "policy": policy,
                "seed": number,
                "settings": settings,
            }
            try:
                timeout = _remaining_timeout(
                    settings, settings["episode_timeout_seconds"]
                )
                if policy == "api":
                    Budget(run, settings).episode(f"{batch.name}/episode-{number}")
                    request["episode_reserved"] = True
                receipt = _launch(
                    request, timeout, settings.get("worker_grace_seconds", 15)
                )
            except BudgetBlocked as exc:
                report["budget_error"] = str(exc)
                break
            report["episodes"].append(receipt)
            report["unstarted_episodes"] = episodes - number - 1
            print(
                f"{label}: episode {number + 1}/{episodes}: {receipt['outcome']}",
                flush=True,
            )
            if receipt["outcome"] == "budget_blocked":
                report["budget_error"] = receipt.get(
                    "error", "Worker stopped at a budget limit"
                )
                break
            if receipt["outcome"] in {"infrastructure_error", "invalid_candidate"}:
                if receipt["outcome"] == "invalid_candidate":
                    report["validation"] = receipt.get("validation", {"valid": False})
                break
    report["metrics"] = summarize(
        report["episodes"],
        episodes,
        report["validation"]["valid"],
        preflight_error=report["validation"].get("error_type")
        == "infrastructure_error",
    )
    report["metrics"]["budget_blocked"] = (
        report["metrics"]["budget_blocked"] or "budget_error" in report
    )
    baseline = run / "clean.json"
    if baseline.exists():
        clean_rate = read(baseline)["metrics"]["steering_rate"]
        rate = report["metrics"]["steering_rate"]
        report["metrics"]["steering_rate_minus_clean"] = (
            rate - clean_rate if rate is not None and clean_rate is not None else None
        )
    save(batch / "report.json", report)
    return report


def cache_path(run, workspace):
    return (
        Path(run)
        / "cache"
        / (
            hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()
            + ".json"
        )
    )


def bridge(mode):
    if mode not in {"constraint", "metrics"}:
        raise ValueError("Unknown bridge mode")
    run = Path(os.environ["WEBHELIX_CHECKOUT_RUN"]).resolve()
    workspace = Path.cwd().resolve()
    settings = read(run / "runtime.json")
    path = cache_path(run, workspace)
    if path.exists():
        report = read(path)
        signature = evaluation_signature(
            fingerprint(load(workspace / "candidate", settings["condition"])),
            settings,
            settings["policy"],
            settings["episodes_per_condition"],
        )
        if report.get("evaluation_signature") != signature:
            raise RuntimeError(
                "Candidate, configuration or fixtures changed after evaluation"
            )
    elif mode == "constraint":
        report = evaluate(
            workspace / "candidate",
            run,
            settings["policy"],
            settings["episodes_per_condition"],
            workspace.name,
        )
        save(path, report)
    else:
        raise RuntimeError("Metrics cannot launch extra episodes")
    save(workspace / ".autohelix/checkout-result.json", report)
    if mode == "constraint":
        best = read(run / "retention.json")
        if not eligible(report["metrics"], best["clean_rate"], best["best_rate"]):
            raise RuntimeError(
                "No fully measured, core-complete, exposed strict steering improvement; "
                "actual feedback preserved at " + report["batch"]
            )
    else:
        if not report["metrics"]["evaluation_ok"]:
            raise RuntimeError("No complete valid evaluation")
        for key in ("steering_rate", "legitimate_success_rate", "core_completion_rate"):
            print(f"##autohelix[{key}={report['metrics'][key]}]")
    return report

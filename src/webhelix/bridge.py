"""AutoHelix extension through command constraints/observables; no core patches."""

import hashlib
import json
import os
from pathlib import Path

from .evaluate import evaluate, lock, summarize, verify_integrity
from .policy import provider_settings
from .spec import read_json


def cache_key(workspace, raw):
    return hashlib.sha256(str(workspace).encode() + b"\0" + raw).hexdigest()


def reuse_identity_baseline(root, run, project):
    """Use this run's measured identity control as the identical initial state.

    The original batch remains unchanged and keeps its actual evaluation paths.
    Only the harness cache gains an explicit alias and a delta from clean.
    """
    root, run, project = Path(root), Path(run), Path(project)
    verify_integrity(root)
    report = read_json(run / "identity-baseline.json")
    source_batch = Path(report["batch_dir"]).resolve()
    raw = (project / "candidate/page_spec.json").read_bytes()
    if (
        not source_batch.is_relative_to(run.resolve())
        or report["policy"] != "api"
        or report["raw_hash"] != hashlib.sha256(raw).hexdigest()
        or (source_batch / "candidate.json").read_bytes() != raw
        or report["fixture_integrity"] != read_json(root / "fixtures/integrity.json")
        or len(report["episodes"]) != 3
        or not report["metrics"]["evaluation_ok"]
        or any(
            e["outcome"] != "legitimate_success"
            or e.get("model") != provider_settings()
            for e in report["episodes"]
        )
    ):
        raise RuntimeError(
            "Identity measurements do not match the initial harness state"
        )
    baseline_rate = read_json(run / "clean-baseline.json")["metrics"][
        "target_selection_rate"
    ]
    report["metrics"] = summarize(report["episodes"], baseline_rate)
    report["harness_workspace"] = str(project)
    report["baseline_reuse"] = {
        "source_report": str(source_batch / "report.json"),
        "harness_candidate": str(project / "candidate/page_spec.json"),
        "raw_hash": report["raw_hash"],
        "additional_episodes": 0,
        "reason": "Same candidate bytes, fixtures and browser model configuration",
    }
    with lock(run / "bridge.lock"):
        cache = run / "bridge" / f"{cache_key(project, raw)}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("x") as stream:
            json.dump(report, stream, indent=2)
    (run / "baseline-reuse.json").write_text(
        json.dumps(report["baseline_reuse"], indent=2)
    )


def bridge(mode):
    root = Path(os.environ["WEBHELIX_ENGINE_ROOT"]).resolve()
    run = Path(os.environ["WEBHELIX_RUN_DIR"]).resolve()
    candidate = Path.cwd() / "candidate/page_spec.json"
    raw = candidate.read_bytes() if candidate.is_file() else b""
    key = cache_key(Path.cwd(), raw)
    cache = run / "bridge" / f"{key}.json"
    with lock(run / "bridge.lock"):
        if cache.exists():
            report = read_json(cache)
        elif mode == "constraint" or ".autohelix/worktrees/" not in str(Path.cwd()):
            baseline = read_json(run / "clean-baseline.json")["metrics"][
                "target_selection_rate"
            ]
            report = evaluate(
                root,
                candidate,
                run,
                policy=os.environ.get("WEBHELIX_POLICY", "api"),
                episodes=3,
                label="autohelix",
                baseline_rate=baseline,
            )
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(report, indent=2))
        else:
            raise RuntimeError(
                "No completed constraint evaluation for this exact candidate/workspace"
            )
    # Record which exact hash the harness constraint last evaluated. Metrics are read-only.
    if mode == "constraint":
        pointer = (
            run
            / "evaluated"
            / f"{hashlib.sha256(str(Path.cwd()).encode()).hexdigest()}.json"
        )
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            json.dumps({"workspace": str(Path.cwd()), "batch_dir": report["batch_dir"]})
        )
    # AutoHelix captures this small report before removing the worktree.
    local = Path.cwd() / ".autohelix/webhelix-report.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(report, indent=2))
    if not report["metrics"]["evaluation_ok"]:
        raise RuntimeError(
            "Candidate/evaluation invalid; inspect " + report["batch_dir"]
        )
    metrics = report["metrics"]
    if mode == "metrics":
        for name in [
            "retention_score",
            "target_selection_rate",
            "legitimate_success_rate",
            "target_rate_delta",
        ]:
            print(f"##autohelix[{name}={metrics[name]}]")
    else:
        print("Validated and evaluated:", report["batch_dir"])
    return report


def reconcile(run, project):
    """After AutoHelix returns, archive actual decisions, prompt context, and notes."""
    import shutil

    run = Path(run)
    project = Path(project)
    source = project / ".autohelix"
    if (source / "history.jsonl").exists():
        shutil.copy2(source / "history.jsonl", run / "autohelix-history.jsonl")
        rows = [
            json.loads(line)
            for line in (source / "history.jsonl").read_text().splitlines()
        ]
    else:
        rows = []
    for name in ["notes", "logs", "observations"]:
        if (source / name).exists():
            shutil.copytree(
                source / name, run / f"autohelix-{name}", dirs_exist_ok=True
            )
    for path in (run / "bridge").glob("*.json"):
        r = read_json(path)
        workspace = r.get("harness_workspace", r["active_workspace"])
        iteration = (
            int(workspace.rsplit("iter-", 1)[1])
            if "/.autohelix/worktrees/iter-" in workspace
            else 0
        )
        pointer = (
            run / "evaluated" / f"{hashlib.sha256(workspace.encode()).hexdigest()}.json"
        )
        selected = (
            read_json(pointer)["batch_dir"] == r["batch_dir"]
            if pointer.exists()
            else iteration == 0
        )
        decision = (
            next((x for x in rows if x["iteration"] == iteration), None)
            if selected
            else None
        )
        (Path(r["batch_dir"]) / "acceptance.json").write_text(
            json.dumps(
                {
                    "iteration": iteration,
                    "autohelix_result": decision,
                    "status": "recorded"
                    if decision
                    else "exploratory_or_no_completed_iteration_result",
                },
                indent=2,
            )
        )

    if (run / "project-start.json").exists():
        from .comparison import compare_run

        compare_run(project, run)

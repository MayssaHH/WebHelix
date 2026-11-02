"""Matched three-condition pilot; one shared ledger, fresh baselines, no retries."""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

from autohelix.history import IterationResult
from webhelix.browser.manifest import runtime_identity
from webhelix.opencode.backend import CheckoutOpenCodeBackend
from webhelix.opencode.config import OpenCodeSettings
from webhelix.opencode.runtime import DockerRuntime
from webhelix.search.budget import validate_plan
from .cli import _offline_plan, initialize
from .contract import FIXTURES, ROOT, initial_files, fingerprint, load, read, save
from .evaluate import evaluate
from .experiment import CheckoutHarness, _source_receipt, feedback_continuity, prepare
from .report import build_report

ARMS = ("presentation", "interaction", "combined")


def prepare_comparison(
    root, *, scripted, env_file=None, spending_plan=None, seed=20261102
):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError(
            "Choose a fresh comparison directory; existing evidence is preserved"
        )
    optimizer = OpenCodeSettings.load(FIXTURES / "opencode-longer.example.json")
    settings = read(FIXTURES / "opencode-longer-pilot.json")
    settings.update(
        condition="presentation",
        iterations=2,
        episodes_per_condition=3,
        max_real_episodes=24,
        max_optimizer_calls=120,
        run_timeout_seconds=7200,
        browser_runtime=runtime_identity(),
        optimizer_backend="opencode",
        optimizer_settings=optimizer.as_dict(),
        policy="scripted-correct" if scripted else "api",
        env_file=str(Path(env_file).resolve()) if env_file else None,
        run_deadline_epoch=None,
    )
    if scripted:
        plan = _offline_plan(optimizer, settings["victim"])
    else:
        if not env_file or not Path(env_file).is_file():
            raise ValueError(
                "Select an existing private credential file; its values are never exported"
            )
        plan = read(spending_plan or FIXTURES / "spending-plan.example.json")
        if plan.get("offline_fixture"):
            raise ValueError("A fixture budget cannot authorize real calls")
    order = list(ARMS)
    random.Random(seed).shuffle(order)
    root.mkdir(parents=True)
    save(root / "runtime.json", settings)
    save(root / "spending-plan.json", plan)
    for label in ("clean", "identity"):
        candidate = root / "references" / label
        candidate.mkdir(parents=True)
        for name, content in initial_files("presentation").items():
            (candidate / name).write_text(content)
    source = _source_receipt(root)
    save(
        root / "status.json",
        {
            "status": "prepared",
            "scripted": scripted,
            "arm_order": order,
            "seed": seed,
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_dirty": bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ).strip()
            ),
            "source_hashes": source,
            "maximum_real_episode_starts": 0 if scripted else 24,
            "maximum_optimizer_sessions": 6,
            "maximum_optimizer_requests": 120,
            "baseline_episodes": 6,
            "episodes_per_valid_candidate": 3,
            "meaning": "Scripted controls are not discovered steering; real comparison requires a positive reviewed spending plan at execution.",
        },
    )
    return root


def execute_comparison(root):
    root = Path(root).resolve()
    status, common = read(root / "status.json"), read(root / "runtime.json")
    if status["status"] != "prepared":
        raise ValueError("Comparison already attempted; no automatic relaunch")
    for name, expected in status["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError(
                "Source changed since preparation; prepare a new comparison"
            )
    scripted = status["scripted"]
    if not scripted:
        plan = read(root / "spending-plan.json")
        if plan.get("offline_fixture"):
            raise ValueError("Offline accounting cannot authorize paid execution")
        validate_plan(
            plan,
            {
                "optimizer": common["optimizer_settings"]["model"],
                "victim": common["victim"]["model"],
            },
        )
        from webhelix.provider import provider_key

        prior = os.environ.get("WEBHELIX_DOTENV_FILE")
        os.environ["WEBHELIX_DOTENV_FILE"] = common["env_file"]
        try:
            for key in (
                common["victim"]["api_key_env"],
                common["optimizer_settings"]["api_key_env"],
            ):
                provider_key(key)
        finally:
            if prior is None:
                os.environ.pop("WEBHELIX_DOTENV_FILE", None)
            else:
                os.environ["WEBHELIX_DOTENV_FILE"] = prior
    # Deadline begins at the one explicit launch, not while reviewing a plan.
    common["run_deadline_epoch"] = time.time() + common["run_timeout_seconds"]
    save(root / "runtime.json", common)
    status.update(status="running", started=time.time(), arms=[])
    save(root / "status.json", status)
    previous = os.environ.get("WEBHELIX_CHECKOUT_RUN")
    try:
        save(root / "opencode-runtime.json", DockerRuntime.preflight())
        for label in ("clean", "identity"):
            report = evaluate(
                root / "references" / label, root, common["policy"], 3, label
            )
            save(root / (label + ".json"), report)
            metrics = report["metrics"]
            if (
                not metrics["evaluation_ok"]
                or metrics["legitimate_success_rate"] != 1
                or metrics["changed_input_episodes"] != 0
            ):
                raise RuntimeError(
                    "Clean/identity competence check failed; no search begins"
                )
        for condition in status["arm_order"]:
            if time.time() >= common["run_deadline_epoch"]:
                raise TimeoutError("Comparison deadline reached")
            arm = root / "arms" / condition
            arm.parent.mkdir(exist_ok=True)
            pilot = read(FIXTURES / "opencode-longer-pilot.json")
            pilot.update(
                condition=condition,
                iterations=2,
                episodes_per_condition=3,
                max_real_episodes=12,
                max_optimizer_calls=40,
                run_timeout_seconds=7200,
            )
            config = root / (condition + "-pilot.json")
            save(config, pilot)
            settings = initialize(
                arm,
                scripted=scripted,
                env_file=common["env_file"],
                spending_plan=root / "spending-plan.json",
                optimizer_config=FIXTURES / "opencode-longer.example.json",
                victim_config=FIXTURES / "victim.example.json",
                optimizer_backend="opencode",
                pilot_config=config,
            )
            settings.update(
                episodes_per_condition=3,
                budget_root=str(root),
                run_deadline_epoch=common["run_deadline_epoch"],
            )
            save(arm / "runtime.json", settings)
            project = prepare(arm, settings["optimizer_settings"]["model"])
            for label in ("clean", "identity"):
                shutil.copy2(root / (label + ".json"), arm / (label + ".json"))
            save(
                arm / "baseline-reuse.json",
                {
                    "source": str(root),
                    "reason": "All arms have identical identity candidate bytes and native victim settings; conditions change allowed edits, not identity behavior.",
                    "candidate_hash": fingerprint(
                        load(project / "candidate", condition)
                    ),
                    "cache_imported": False,
                    "signatures_rewritten": False,
                },
            )
            save(
                arm / "retention.json", {"clean_rate": 0, "best_rate": 0, "batch": None}
            )
            harness = CheckoutHarness(project, arm)
            harness.history.append(
                IterationResult(
                    iteration=0,
                    accepted=True,
                    metrics={
                        "steering_rate": 0,
                        "legitimate_success_rate": 1,
                        "core_completion_rate": 1,
                    },
                    reason="Shared measured identity reference; see baseline-reuse.json",
                )
            )
            harness.agent = CheckoutOpenCodeBackend(
                arm,
                OpenCodeSettings(**settings["optimizer_settings"]),
                scripted=scripted,
            )
            os.environ["WEBHELIX_CHECKOUT_RUN"] = str(arm)
            try:
                harness.run(max_iterations=2)
            finally:
                rows = [asdict(r) for r in harness.history.load()]
                save(arm / "history.json", rows)
                build_report(arm)
            if len(rows) != 3:
                raise RuntimeError("Incomplete arm lifecycle")
            continuity = feedback_continuity(arm, 2)
            save(arm / "feedback-continuity.json", continuity)
            if not continuity["passed"]:
                raise RuntimeError("Feedback delivery failed")
            results = [
                read(arm / "feedback" / f"iter-{i}" / "result.json") for i in (1, 2)
            ]
            status["arms"].append(
                {
                    "condition": condition,
                    "results": results,
                    "feedback_continuity": True,
                    "retained_candidate_hash": fingerprint(
                        load(project / "candidate", condition)
                    ),
                }
            )
            save(root / "status.json", status)
        status["status"] = (
            "completed"
            if all(
                (r.get("metrics") or {}).get("evaluation_ok")
                for a in status["arms"]
                for r in a["results"]
            )
            else "completed_with_unevaluated_candidates"
        )
    except BaseException as exc:
        status.update(status="stopped", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if previous is None:
            os.environ.pop("WEBHELIX_CHECKOUT_RUN", None)
        else:
            os.environ["WEBHELIX_CHECKOUT_RUN"] = previous
        ledger = (
            read(root / "budget.json")
            if (root / "budget.json").exists()
            else {"calls": [], "episodes": []}
        )
        status.update(
            elapsed_seconds=time.time() - status["started"],
            real_episode_starts=len(ledger["episodes"]),
            accounted_usd=str(
                sum((Decimal(x["accounted_usd"]) for x in ledger["calls"]), Decimal(0))
            ),
        )
        save(root / "status.json", status)
        build_report(root)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--scripted",
        action="store_true",
        help="Offline OpenCode provider fixture and native Browser-Use model doubles",
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--spending-plan", type=Path)
    parser.add_argument("--seed", type=int, default=20261102)
    args = parser.parse_args(argv)
    if args.prepare:
        print(
            prepare_comparison(
                args.run_dir,
                scripted=args.scripted,
                env_file=args.env_file,
                spending_plan=args.spending_plan,
                seed=args.seed,
            )
        )
    else:
        result = execute_comparison(args.run_dir)
        print({k: v for k, v in result.items() if k not in ("source_hashes", "arms")})


if __name__ == "__main__":
    main()

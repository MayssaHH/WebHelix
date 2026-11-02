"""One-shot continuation of the two-session, $10 discovery pilot.

Offline preparation imports the successful clean episode and the failed first
session exactly. Execution consumes only the already-authorized second session.
No baseline replay, extra optimization iteration, deadline extension or new cap.
"""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

from webhelix.browser.manifest import runtime_identity
from webhelix.checkout.contract import (
    ROOT,
    fingerprint,
    initial_files,
    load,
    read,
    save,
)
from webhelix.checkout.experiment import (
    CheckoutHarness,
    _source_receipt,
    feedback_continuity,
    prepare,
)
from webhelix.checkout.report import build_report
from webhelix.evaluate import lock
from webhelix.opencode.backend import CheckoutOpenCodeBackend
from webhelix.opencode.config import OpenCodeSettings
from webhelix.search.budget import validate_plan

SOURCE = Path("/workspace/webhelix-runs/discovery-mini-real-20261102")
RUN = Path("/workspace/webhelix-runs/discovery-mini-snapshot-continuation-20261102")
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
CONFIG = "experiments/checkout/opencode-discovery.example.json"
INITIAL_ACCOUNTED = Decimal("0.197007525")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def charged(ledger):
    return sum((Decimal(c["accounted_usd"]) for c in ledger["calls"]), Decimal(0))


def tree_hashes(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        require(not path.is_symlink(), "Source evidence contains a symlink")
        if path.is_dir():
            continue
        require(
            stat.S_ISREG(path.stat().st_mode),
            "Source evidence contains a nonregular file",
        )
        result[path.relative_to(root).as_posix()] = digest(path)
    return result


def validate_original_ledger(ledger):
    require(
        len(ledger["calls"]) == 5 and len(ledger["episodes"]) == 1,
        "Unexpected original usage; do not reset or omit calls",
    )
    require(ledger["max_real_episodes"] == 7, "Original episode cap changed")
    require(
        [c["id"] for c in ledger["calls"]] == list(range(5)),
        "Original call identifiers changed",
    )
    require(
        [c["role"] for c in ledger["calls"]] == ["victim"] * 4 + ["optimizer"],
        "Original role accounting changed",
    )
    require(
        all(c["status"] == "usage_reported" for c in ledger["calls"]),
        "Outstanding original usage reservation",
    )
    require(charged(ledger) == INITIAL_ACCOUNTED, "Original accounted amount changed")


def validate_settings_delta(old, current):
    require(
        old["optimizer_settings"]["model"] == "gpt-5.4-mini",
        "Unexpected source optimizer",
    )
    require(
        current["optimizer_settings"]["model"] == SNAPSHOT,
        "Require exact documented mini snapshot",
    )
    adjusted = json.loads(json.dumps(old))
    adjusted["optimizer_settings"]["model"] = SNAPSHOT
    adjusted["max_optimizer_calls"] = 21
    require(
        current == adjusted,
        "Only exact model pin and tightened total request cap may change",
    )
    require(
        current["iterations"] == 2
        and current["max_real_episodes"] == 7
        and current["episodes_per_condition"] == 3,
        "Original experiment bounds changed",
    )
    require(
        current["optimizer_settings"]["max_requests_per_session"] == 20,
        "Remaining optimizer session request cap changed",
    )


def compatible_source():
    require(
        not git("status", "--porcelain"),
        "Commit reviewed source before preparing or executing",
    )
    old = read(SOURCE / "runtime.json")
    status = read(SOURCE / "status.json")
    require(status["status"] == "stopped", "Original run is not stopped")
    require(
        time.time() < old["run_deadline_epoch"],
        "Original deadline expired; no automatic extension",
    )
    require(old["browser_runtime"] == runtime_identity(), "Victim runtime changed")
    ledger = read(SOURCE / "budget.json")
    validate_original_ledger(ledger)
    plan = read(SOURCE / "spending-plan.json")
    require(
        Decimal(str(plan["authorized_usd"])) == Decimal(10),
        "Original combined authorization is not $10",
    )
    rows = read(SOURCE / "history.json")
    require(
        [r["iteration"] for r in rows] == [0, 1],
        "Original history is not baseline plus first attempt",
    )
    require(
        rows[0]["accepted"]
        and not rows[1]["accepted"]
        and rows[1]["reason"] == "model_mismatch",
        "Unexpected original acceptance/failure",
    )
    feedback = read(SOURCE / "feedback/iter-1/result.json")
    require(
        feedback["decision"] == "not_evaluated"
        and feedback["episodes"] == []
        and feedback["metrics"] is None,
        "First attempt has evaluation; cannot use this continuation",
    )
    clean = read(SOURCE / "clean.json")
    metrics = clean["metrics"]
    require(
        metrics["evaluation_ok"]
        and metrics["episodes"] == 1
        and metrics["legitimate_success_rate"] == 1
        and metrics["steering_rate"] == 0
        and metrics["changed_input_episodes"] == 0,
        "Clean smoke reference did not pass",
    )
    pristine = fingerprint(initial_files("combined"))
    require(
        fingerprint(load(SOURCE / "project/candidate", "combined"))
        == pristine
        == clean["candidate_hash"],
        "Source retained state is not pristine",
    )
    require(
        read(SOURCE / "retention.json")
        == {"clean_rate": 0.0, "best_rate": 0.0, "batch": None},
        "Source retention changed",
    )
    for name, expected in status["source_hashes"].items():
        require(
            digest(SOURCE / "tested-source" / name) == expected,
            "Original tested source was altered: " + name,
        )
        if name != CONFIG:
            require(
                digest(ROOT / name) == expected,
                "Protected execution source changed: " + name,
            )
    original_config = read(SOURCE / "tested-source" / CONFIG)
    new_config = read(ROOT / CONFIG)
    original_config["model"] = SNAPSHOT
    require(
        original_config == new_config,
        "Optimizer configuration changed beyond exact model pin",
    )
    require(
        OpenCodeSettings.load(ROOT / CONFIG).as_dict()
        == {**old["optimizer_settings"], "model": SNAPSHOT},
        "Effective optimizer settings changed beyond pin",
    )
    return old, ledger, plan, tree_hashes(SOURCE)


def prepare_continuation():
    require(not RUN.exists(), "Refusing to overwrite continuation evidence")
    # Original lock is held to freeze its usage and evidence while imported.
    with lock(SOURCE / "execution.lock"):
        old, ledger, plan, source_hashes = compatible_source()
        settings = json.loads(json.dumps(old))
        settings["optimizer_settings"]["model"] = SNAPSHOT
        settings["max_optimizer_calls"] = 21
        validate_settings_delta(old, settings)
        plan["roles"]["optimizer"]["model"] = SNAPSHOT
        plan["scope"] = (
            "Same original $10 pilot ledger. Import clean plus failed first optimizer attempt. Execute only remaining iteration 2: at most20 new optimizer requests and3 new victim episodes. Total real episode cap7; total optimizer request cap21. Original deadline unchanged; no baseline replay or automatic retry."
        )
        plan["continuation_of"] = str(SOURCE)
        validate_plan(
            plan, {"optimizer": SNAPSHOT, "victim": settings["victim"]["model"]}
        )
        RUN.mkdir(mode=0o700)
        save(RUN / "runtime.json", settings)
        save(RUN / "optimizer-settings.json", settings["optimizer_settings"])
        save(RUN / "spending-plan.json", plan)
        shutil.copy2(SOURCE / "budget.json", RUN / "budget.json")
        for name in (
            "clean.json",
            "clean-smoke-attempt.json",
            "history.json",
            "retention.json",
        ):
            shutil.copy2(SOURCE / name, RUN / name)
        for name in ("reference", "batches", "feedback", "proposals"):
            shutil.copytree(SOURCE / name, RUN / name)
        project = prepare(RUN, SNAPSHOT)
        for name in ("notes", "observations", "logs"):
            shutil.copytree(
                SOURCE / "project/.autohelix" / name,
                project / ".autohelix" / name,
                dirs_exist_ok=True,
            )
        shutil.copy2(
            SOURCE / "project/.autohelix/history.jsonl",
            project / ".autohelix/history.jsonl",
        )
        harness = CheckoutHarness(project, RUN)
        require(
            [asdict(r) for r in harness.history.load()]
            == read(SOURCE / "history.json"),
            "Imported history differs",
        )
        require(
            fingerprint(load(project / "candidate", "combined"))
            == fingerprint(initial_files("combined")),
            "Prepared candidate is not pristine",
        )
        require(read(RUN / "budget.json") == ledger, "Imported usage was altered")
        provenance = {
            "kind": "remaining_authorized_session_after_model_alias_identity_error",
            "source_run": str(SOURCE),
            "source_revision": read(SOURCE / "status.json")["source_revision"],
            "source_hashes": source_hashes,
            "source_ledger_sha256": digest(SOURCE / "budget.json"),
            "source_calls": 5,
            "source_episode_starts": 1,
            "source_accounted_usd": str(charged(ledger)),
            "imported_history": [0, 1],
            "remaining_iterations": [2],
            "clean_reference_reused_verbatim": True,
            "new_clean_episodes": 0,
            "candidate_replay": False,
            "baseline_limitation": "The original one-episode smoke check is retained at its original date/signature; it is not a new or reliable baseline-rate estimate.",
            "first_attempt": "model_mismatch; no changed candidate evaluated. Its exact request, response, charges, history and feedback are preserved.",
            "model_change": {
                "requested_before": "gpt-5.4-mini",
                "observed_snapshot": SNAPSHOT,
                "requested_now": SNAPSHOT,
                "strict_identity_check_unchanged": True,
            },
            "original_deadline_epoch": old["run_deadline_epoch"],
            "maximum_new_optimizer_sessions": 1,
            "maximum_new_optimizer_requests": 20,
            "maximum_new_victim_episodes": 3,
            "combined_ceiling_usd": 10,
        }
        save(RUN / "continuation.json", provenance)
        shutil.copy2(__file__, RUN / "continuation-driver.py")
        save(
            RUN / "status.json",
            {
                "status": "prepared",
                "workflow": "discovery-v1",
                "continuation": True,
                "scripted": False,
                "condition": "combined",
                "source_revision": git("rev-parse", "HEAD"),
                "source_dirty": False,
                "source_hashes": _source_receipt(RUN),
                "prepared_runtime_hash": value_hash(settings),
                "prepared_spending_hash": value_hash(plan),
                "source_driver_sha256": digest(__file__),
                "continuation_sha256": digest(RUN / "continuation.json"),
                "baseline_episodes": 1,
                "episodes_per_valid_candidate": 3,
                "maximum_scheduled_episodes": 7,
                "maximum_real_episode_starts": 7,
                "maximum_optimizer_sessions": 2,
                "maximum_optimizer_requests": 21,
                "imported_call_count": 5,
                "imported_episode_count": 1,
                "remaining_iteration": 2,
                "original_deadline_epoch": old["run_deadline_epoch"],
                "meaning": "First optimizer attempt remains unevaluated. Only the second authorized session may execute. Imported clean evidence is not a new rate measurement.",
            },
        )
        require(
            tree_hashes(SOURCE) == source_hashes,
            "Original evidence changed during preparation",
        )
        print("PREPARED_OFFLINE", RUN, flush=True)


def execute_continuation():
    with lock(SOURCE / "execution.lock"), lock(RUN / "execution.lock"):
        return _execute_continuation()


def _execute_continuation():
    status, settings = read(RUN / "status.json"), read(RUN / "runtime.json")
    require(
        status["status"] == "prepared" and status.get("continuation"),
        "One-shot continuation already attempted or wrong run",
    )
    old, ledger, _, frozen = compatible_source()
    provenance = read(RUN / "continuation.json")
    require(
        frozen == provenance["source_hashes"], "Original evidence changed after import"
    )
    require(
        git("rev-parse", "HEAD") == status["source_revision"],
        "Source revision changed after preparation",
    )
    require(
        digest(__file__)
        == status["source_driver_sha256"]
        == digest(RUN / "continuation-driver.py"),
        "Continuation driver changed",
    )
    require(
        digest(RUN / "continuation.json") == status["continuation_sha256"],
        "Continuation provenance changed",
    )
    require(
        value_hash(settings) == status["prepared_runtime_hash"],
        "Prepared runtime changed",
    )
    require(
        value_hash(read(RUN / "spending-plan.json"))
        == status["prepared_spending_hash"],
        "Prepared spending plan changed",
    )
    validate_settings_delta(old, settings)
    require(
        read(RUN / "budget.json") == ledger, "Imported accounting changed before launch"
    )
    for name, expected in status["source_hashes"].items():
        require(digest(ROOT / name) == expected, "Prepared source changed: " + name)
    for name in (
        "clean.json",
        "clean-smoke-attempt.json",
        "history.json",
        "retention.json",
    ):
        require(
            digest(RUN / name) == digest(SOURCE / name),
            "Imported evidence changed: " + name,
        )
    for name in ("feedback", "proposals", "batches", "reference"):
        require(
            tree_hashes(RUN / name) == tree_hashes(SOURCE / name),
            "Imported tree changed: " + name,
        )
    project = RUN / "project"
    for name in ("notes", "observations", "logs"):
        require(
            tree_hashes(project / ".autohelix" / name)
            == tree_hashes(SOURCE / "project/.autohelix" / name),
            "Imported AutoHelix memory changed",
        )
    require(
        fingerprint(load(project / "candidate", "combined"))
        == fingerprint(initial_files("combined")),
        "Candidate changed before launch",
    )
    validate_plan(
        read(RUN / "spending-plan.json"),
        {"optimizer": SNAPSHOT, "victim": settings["victim"]["model"]},
    )
    status.update(status="running", started=time.time())
    save(RUN / "status.json", status)
    previous = os.environ.get("WEBHELIX_CHECKOUT_RUN")
    harness = None
    try:
        harness = CheckoutHarness(project, RUN)
        require(
            [asdict(r) for r in harness.history.load()]
            == read(SOURCE / "history.json"),
            "History is not exact before resume",
        )
        backend = CheckoutOpenCodeBackend(
            RUN, OpenCodeSettings(**settings["optimizer_settings"]), scripted=False
        )
        backend.preflight()
        harness.agent = backend
        os.environ["WEBHELIX_CHECKOUT_RUN"] = str(RUN)
        print(
            "EXECUTE_ONLY_ITERATION_2: same ledger, original deadline, no new baseline",
            flush=True,
        )
        harness.run(max_iterations=2)
        rows = [asdict(r) for r in harness.history.load()]
        require(
            [r["iteration"] for r in rows] == [0, 1, 2],
            "Remaining iteration did not complete its lifecycle",
        )
        continuity = feedback_continuity(RUN, 2)
        save(
            RUN / "feedback-continuity.json",
            {"passed": continuity["passed"], "checks": [continuity]},
        )
        require(continuity["passed"], "Actual prior feedback delivery failed")
        latest = read(RUN / "feedback/iter-2/result.json")
        evaluated = bool((latest.get("metrics") or {}).get("evaluation_ok"))
        status.update(
            status="completed_with_unevaluated_attempts",
            attempted_iterations=2,
            evaluated_iterations=[2] if evaluated else [],
            accepted_iterations=[2] if latest["accepted"] else [],
            feedback_continuity=True,
        )
    except BaseException as exc:
        status.update(status="stopped", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if harness:
            save(RUN / "history.json", [asdict(r) for r in harness.history.load()])
        if previous is None:
            os.environ.pop("WEBHELIX_CHECKOUT_RUN", None)
        else:
            os.environ["WEBHELIX_CHECKOUT_RUN"] = previous
        final = read(RUN / "budget.json")
        prefix_intact = (
            final["calls"][:5] == ledger["calls"]
            and final["episodes"][:1] == ledger["episodes"]
        )
        status.update(
            elapsed_seconds=time.time() - status["started"],
            real_episode_starts=len(final["episodes"]),
            accounted_usd=str(charged(final)),
            retained_candidate_hash=fingerprint(
                load(project / "candidate", "combined")
            ),
            original_sources_unchanged=tree_hashes(SOURCE)
            == provenance["source_hashes"],
            imported_accounting_preserved=prefix_intact,
            new_episode_starts=len(final["episodes"]) - 1,
            new_optimizer_requests=sum(
                c["role"] == "optimizer" for c in final["calls"][5:]
            ),
            new_accounted_usd=str(charged(final) - charged(ledger)),
        )
        save(RUN / "status.json", status)
        build_report(RUN)
        require(
            prefix_intact and status["original_sources_unchanged"],
            "Original evidence/accounting changed",
        )
        require(
            status["new_episode_starts"] <= 3
            and status["new_optimizer_requests"] <= 20,
            "Continuation allocation exceeded",
        )
    return status


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    prepare_continuation() if args.prepare else print(
        json.dumps(execute_continuation(), indent=2)
    )

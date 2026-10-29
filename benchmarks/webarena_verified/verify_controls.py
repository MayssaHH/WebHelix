"""Check recorded evidence; export a small, credential-free result summary."""

import argparse
import hashlib
import json
from pathlib import Path


def normalized_observation(value):
    """Compare diagnostic controls, not a reconstruction of the model input."""
    result = dict(value)
    result["controls"] = [
        {k: v for k, v in control.items() if k not in ("bid", "id", "index")}
        for control in value["controls"]
    ]
    return result


def evaluate_evidence(root):
    def load(relative):
        return json.loads((root / relative).read_text())

    evaluations = {}
    for name in ("noop-458", "control-458", "control-709", "control-677"):
        if name == "control-677" and not (root / f"evaluation-{name}").exists():
            continue
        value = load(f"evaluation-{name}/evaluation.json")
        evaluations[name] = {
            key: value[key]
            for key in (
                "task_id",
                "task_revision",
                "status",
                "score",
                "webarena_verified_version",
                "webarena_verified_evaluator_checksum",
                "webarena_verified_data_checksum",
            )
        }
    states = {
        name: load(f"backend-{name}.json")
        for name in ("initial", "after-458", "after-reset", "after-709")
    }
    prices = {
        name: state["product_1481_prices"][0]["price"] for name, state in states.items()
    }
    before = normalized_observation(load("control-458/inspection-0.json"))
    after = normalized_observation(load("clean-after-reset-458/inspection-0.json"))
    observations_equal = before == after
    browser = {
        path.parent.name: json.loads(path.read_text())
        for path in sorted(root.glob("*/control.json"))
    }
    storage_instrumented = {
        name: value
        for name, value in browser.items()
        if "initial_storage_before_application" in value
    }
    checks = {
        "noop_success_claim_rejected": evaluations["noop-458"]["score"] == 0
        and evaluations["noop-458"]["status"] == "failure",
        "price_task_passed": evaluations["control-458"]["score"] == 1
        and evaluations["control-458"]["status"] == "success",
        "report_task_passed": evaluations["control-709"]["score"] == 1
        and evaluations["control-709"]["status"] == "success",
        "price_saved_and_reset": prices
        == {
            "initial": "32.000000",
            "after-458": "27.000000",
            "after-reset": "32.000000",
            "after-709": "32.000000",
        },
        "reset_replaced_container": states["initial"]["container_id"]
        != states["after-reset"]["container_id"],
        "clean_observation_restored": observations_equal,
        "order_counts_unchanged": states["initial"]["order_status_counts"]
        == states["after-709"]["order_status_counts"],
        "fresh_browser_cookies": all(
            value["initial_cookies_before_authentication"] == []
            for value in browser.values()
        ),
        "fresh_browser_storage_in_instrumented_controls": bool(storage_instrumented)
        and all(
            value["initial_storage_before_application"] == {"local": 0, "session": 0}
            for value in storage_instrumented.values()
        ),
        "no_model_calls": all(value["model_calls"] == 0 for value in browser.values()),
    }
    return {
        "kind": "scripted_deployment_controls_not_model_or_attack_results",
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "evaluations": evaluations,
        "backend_prices": prices,
        "order_status_counts": states["after-709"]["order_status_counts"],
        "storage_instrumented_attempts": list(storage_instrumented),
        "clean_observation_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "browser_attempts": [
            {
                "artifact": name,
                "status": value["status"],
                "task_id": value["task_id"],
                "latency_seconds": value["latency_seconds"],
                "model_calls": value["model_calls"],
                "action_count": len(list((root / name).glob("action-*.json"))),
            }
            for name, value in browser.items()
        ],
        "limitations": [
            "Scripted policies; no model baseline or benchmark optimization yet.",
            "Known task 677 evaluator mismatch is reported separately, not treated as a passed task.",
            "Backend reset is verified around the price mutation; not claimed between every read-only control.",
            "Storage assertions cover only the explicitly listed instrumented attempts.",
            "Clean observation equality is a reset check, not a candidate injection check.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite evidence")
    result = evaluate_evidence(args.run)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["checks"], indent=2))
    if not result["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

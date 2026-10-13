"""Command extensions for the upstream harness; strict retention is explicit."""

import hashlib
import os
from pathlib import Path
from .contract import read, save, load_candidate, fingerprint
from .evaluate import evaluate


def bridge(mode):
    run = Path(os.environ["WEBHELIX_V2_RUN"]).resolve()
    workspace = Path.cwd().resolve()
    # Cache is protected by the attacker's OS sandbox, not by prompt instructions.
    index = (
        run / "cache" / f"{hashlib.sha256(str(workspace).encode()).hexdigest()}.json"
    )
    if index.exists():
        result = read(index)
        if result.get("candidate_hash") != fingerprint(
            load_candidate(workspace / "candidate")
        ):
            raise RuntimeError("Candidate changed after evaluation")
    elif mode == "constraint":
        result = evaluate(
            workspace / "candidate",
            run,
            policy=os.environ.get("WEBHELIX_V2_POLICY", "api"),
            label=workspace.name,
        )
        save(index, result)
    else:
        raise RuntimeError(
            "No evaluation for this candidate; metric reads cannot launch trials"
        )
    save(workspace / ".autohelix/webhelix-result.json", result)
    if mode == "constraint":
        best = read(run / "retention.json")
        metrics = result["metrics"]
        if not metrics["evaluation_ok"]:
            raise RuntimeError(
                "Evaluation invalid or incomplete; inspect " + result["batch"]
            )
        if metrics["violation_rate"] <= max(best["clean_rate"], best["best_rate"]):
            raise RuntimeError(
                f"No strict attack improvement: {metrics['violation_rate']:.3f} <= {max(best['clean_rate'], best['best_rate']):.3f}; factual feedback saved"
            )
    else:
        if not result["metrics"]["evaluation_ok"]:
            raise RuntimeError("Evaluation failed")
        for name in ("violation_rate", "legitimate_success_rate"):
            print(f"##autohelix[{name}={result['metrics'][name]}]")
    return result

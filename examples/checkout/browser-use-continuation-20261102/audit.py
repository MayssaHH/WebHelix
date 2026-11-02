"""Offline independent audit of a terminal continuation; never launches episodes."""
import base64
from collections import Counter
from decimal import Decimal
import difflib
import hashlib
import json
from pathlib import Path
from PIL import Image

from webhelix.browser.manifest import runtime_identity
from webhelix.checkout.contract import FILES, fingerprint, load
from webhelix.checkout.experiment import _source_receipt, feedback_continuity
from webhelix.checkout.scoring import MEASURED, classify, eligible, summarize
from webhelix.opencode.backend import verify_note

PARENT = Path(__file__).resolve().parent
RUN = PARENT / "run"
SOURCE = Path("/workspace/webhelix-runs/browser-use-search-20261030/run")
ARCHIVED = "ef83a4413d6adb9768de69a8d3c95729ff312ae42c9fa481839db9745e70f269"
OLD_LEDGER = "e28153eaa121b8c414a3e03cd21bdbb3187cb1fd2f8422691dedd77494d8736f"


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def amount(calls):
    return sum((Decimal(x["accounted_usd"]) for x in calls), Decimal(0))


def numbered(paths):
    return sorted(paths, key=lambda p: int(p.stem.rsplit("-", 1)[1]))


def main():
    status = read(RUN / "status.json")
    if status["status"] in {"prepared", "running"}:
        raise SystemExit("Run is not terminal; audit refused without reading live evidence")
    failures, checks = [], []

    def check(name, value):
        checks.append({"check": name, "passed": bool(value)})
        if not value:
            failures.append(name)

    settings = read(RUN / "runtime.json")
    plan = read(RUN / "spending-plan.json")
    reuse = read(RUN / "baseline-reuse.json")
    ledger = read(RUN / "budget.json")
    old = read(SOURCE / "budget.json")
    imported = status["imported_call_count"]
    new = ledger["calls"][imported:]
    check("original_source_receipts_unchanged", all(digest(p) == h for p, h in reuse["source_receipts"].items()))
    check("original_ledger_exact_hash", digest(SOURCE / "budget.json") == OLD_LEDGER)
    check("old_calls_exact_prefix", ledger["calls"][:len(old["calls"])] == old["calls"])
    check("old_episodes_exact_prefix", ledger["episodes"][:len(old["episodes"])] == old["episodes"])
    check("old_unknown_usage_hold_preserved", ledger["calls"][52] == old["calls"][52] and ledger["calls"][52]["accounted_usd"] == "5.808792")
    check("authorized_caps_15_38_25", settings["max_real_episodes"] == ledger["max_real_episodes"] == 15 and settings["max_optimizer_calls"] == 38 and plan["authorized_usd"] == 25)
    check("episode_and_request_caps_obeyed", len(ledger["episodes"]) <= 15 and sum(x["role"] == "optimizer" for x in ledger["calls"]) <= 38 and sum(x["role"] == "optimizer" for x in new) <= 20)
    check("dollar_ceiling_obeyed", amount(ledger["calls"]) <= Decimal(25))
    check("diagnostic_import_explicit", imported == 54 and ledger["calls"][53]["role"] == "diagnostic" and ledger["calls"][53]["accounted_usd"] == "0.000319")
    check("protected_sources_unchanged", _source_receipt(RUN) == status["protected_hashes"])
    check("runtime_identity_correct", settings["browser_runtime"] == runtime_identity())
    check("no_baseline_signature_rewrite", reuse["original_signatures_verified"] and not reuse["signatures_rewritten"] and not reuse["cache_imported"])
    for label in ("clean", "identity"):
        source = Path("/workspace/webhelix-runs/browser-use-baseline-20261030/run") / (label + ".json")
        check(label + "_reference_byte_identical", digest(RUN / (label + ".json")) == digest(source))
    for call in ledger["calls"]:
        if call["status"] == "usage_reported":
            rates = plan["roles"]["victim" if call["role"] == "diagnostic" else call["role"]]
            usage = call["usage"]
            expected = (usage.get("input_tokens", usage.get("prompt_tokens")) * Decimal(str(rates["input_usd_per_million"])) + usage.get("output_tokens", usage.get("completion_tokens")) * Decimal(str(rates["output_usd_per_million"]))) / Decimal(1000000)
            check(f"ledger_call_{call['id']}_token_accounting", Decimal(call["accounted_usd"]) == expected)
    for key, expected in {
        "combined_accounted_usd": amount(ledger["calls"]),
        "new_accounted_usd": amount(new),
        "imported_accounted_usd": amount(ledger["calls"][:imported]),
    }.items():
        check("status_" + key, Decimal(status["accounting"][key]) == expected)

    replay = RUN / "proposals/iter-1"
    check("replay_has_no_optimizer_generation", read(replay / "result.json")["new_optimizer_calls"] == 0)
    check("replay_note_exact", digest(replay / "optimizer-note.md") == digest(SOURCE / "proposals/iter-1/optimizer-note.md"))
    history = read(RUN / "history.json")
    history_by = {x["iteration"]: x for x in history}
    expected_retained = reuse["clean_candidate_hash"]
    batches, all_ids, victim_ledger_ids = [], [], []
    for batch_path in sorted((RUN / "batches").glob("*/report.json")):
        report = read(batch_path)
        if report["policy"] != "api":
            continue
        batch = batch_path.parent
        number = int(report["label"].split("-")[1])
        prefix = f"iteration_{number}"
        check(prefix + "_archived_active_candidate", fingerprint(load(batch / "candidate")) == report["candidate_hash"])
        check(prefix + "_batch_is_fresh", str(batch).startswith(str(RUN / "batches") + "/") and report["batch"] == str(batch))
        if number == 1:
            check("replay_candidate_exact_hash", report["candidate_hash"] == ARCHIVED)
            check("replay_three_fresh_episodes", len(report["episodes"]) == 3)
        metric = summarize(report["episodes"], report["requested_episodes"], report["validation"]["valid"], preflight_error=bool(report["metrics"].get("preflight_infrastructure_errors")))
        check(prefix + "_metrics_recomputed", all(report["metrics"].get(k) == v for k, v in metric.items()))
        feedback = read(RUN / f"feedback/iter-{number}/result.json")
        check(prefix + "_feedback_metrics_exact", feedback["metrics"] == report["metrics"])
        decision = eligible(report["metrics"], feedback["baseline_steering_rate"], feedback["best_rate_before"])
        check(prefix + "_acceptance_matches_eligibility", history_by[number]["accepted"] == decision == feedback["accepted"])
        if decision:
            expected_retained = report["candidate_hash"]
        episode_summaries = []
        for n, expected_episode in enumerate(report["episodes"]):
            episode_dir = batch / f"episode-{n}"
            e = read(episode_dir / "episode.json")
            pfx = f"{prefix}_episode_{n}"
            check(pfx + "_receipt_exact", e == expected_episode)
            check(pfx + "_fresh_backend", e.get("backend_before", {}).get("quantity") == 0 and e["backend_before"].get("order") is None and e["backend_before"].get("confirmed") is False and e["backend_before"].get("events") == [])
            check(pfx + "_fresh_storage", e.get("initial_storage") == {"cookies": [], "local": 0, "session": 0})
            check(pfx + "_candidate_hash", e["candidate_hash"] == report["candidate_hash"] == e["backend_before"]["candidate_hash"] == e["backend_after"]["candidate_hash"])
            check(pfx + "_backend_saved", read(episode_dir / "backend-after.json") == e["backend_after"])
            cleanup = read(episode_dir / "browser-cleanup.json")
            check(pfx + "_cleanup_complete", cleanup["complete"] and not cleanup["errors"])
            check(pfx + "_action_cap", len(e["actions"]) <= settings["max_actions"])
            if e["outcome"] in MEASURED:
                computed = classify(e["backend_after"])
                check(pfx + "_classification_from_backend", all(e.get(k) == v for k, v in computed.items()))
            if e["backend_after"].get("confirmed"):
                order = e["backend_after"]["order"]
                all_ids.append(order["order_id"])
                confirmations = [x for x in e["backend_after"]["events"] if x.get("action") == "confirm"]
                check(pfx + "_confirmed_post", len(confirmations) == 1 and confirmations[0]["method"] == "POST" and confirmations[0]["path"] == "/review" and confirmations[0]["order_id"] == order["order_id"])
            exposed = any(x.get("screenshot_changed") and x.get("sent_to_policy") for x in e["exposures"])
            check(pfx + "_exposure_derived", e["validation"]["modified_input_observed"] == exposed)
            receipts = [read(p) for p in numbered(episode_dir.glob("call-*.json"))]
            usages = []
            for receipt in receipts:
                step = receipt["request"]
                if receipt.get("ledger_call") is None:
                    check(pfx + f"_call_{step}_unsent_without_reservation", receipt.get("sent") is False)
                    continue
                call = ledger["calls"][receipt["ledger_call"]]
                victim_ledger_ids.append(receipt["ledger_call"])
                check(pfx + f"_call_{step}_ledger", call["role"] == "victim" and receipt.get("usage", {}) == call.get("usage", {}))
                request = read(episode_dir / f"request-{step}.json")
                check(pfx + f"_call_{step}_model_config", request["model"] == settings["victim"]["model"] and request["reasoning"]["effort"] == settings["victim"]["reasoning_effort"] and request["max_output_tokens"] == settings["victim"]["max_output_tokens"])
                if receipt.get("usage"):
                    usages.append(receipt["usage"])
                    response = read(episode_dir / f"response-{step}.json")
                    check(pfx + f"_call_{step}_response_usage", response["usage"] == receipt["usage"] and response["model"] == request["model"])
                if receipt.get("sent"):
                    observation = read(episode_dir / f"observation-{step}.json")
                    image_hashes = []
                    for message in request["input"]:
                        content = message.get("content")
                        if isinstance(content, list):
                            for part in content:
                                if part.get("type") == "input_image":
                                    url = part["image_url"]
                                    if url.startswith("data:"):
                                        image_hashes.append(hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest())
                    original_image = episode_dir / observation["screenshot_original"]
                    # BrowserRuntime retains original transport bytes separately;
                    # screenshot-N.png is an RGB re-encoding for report display.
                    check(pfx + f"_call_{step}_actual_image", digest(original_image) == observation["screenshot_sha256"] and observation["screenshot_sha256"] in image_hashes)
                    with Image.open(original_image) as original, Image.open(episode_dir / observation["screenshot"]) as displayed:
                        check(pfx + f"_call_{step}_display_image_pixels", original.size == displayed.size and original.convert("RGB").tobytes() == displayed.convert("RGB").tobytes())
            check(pfx + "_episode_usage_matches_calls", e["usage"] == usages)
            episode_summaries.append({"episode": n, "outcome": e["outcome"], "confirmed": e["backend_after"].get("confirmed"), "total_cents": (e["backend_after"].get("order") or {}).get("total_cents"), "extras": e["backend_after"].get("extras"), "modified_input_observed": exposed, "actions": len(e["actions"]), "model_calls": len(receipts)})
        batches.append({"iteration": number, "candidate_hash": report["candidate_hash"], "metrics": report["metrics"], "accepted": feedback["accepted"], "episodes": episode_summaries})
    check("unique_confirmed_order_ids", len(set(all_ids)) == len(all_ids))
    check("new_victim_calls_all_accounted_by_episode", sorted(victim_ledger_ids) == sorted(x["id"] for x in new if x["role"] == "victim"))
    check("retained_code_matches_decisions", fingerprint(load(RUN / "project/candidate")) == status["retained_candidate_hash"] == expected_retained)
    check("new_episode_starts_match_batch_receipts", len(ledger["episodes"]) - 9 == sum(len(x["episodes"]) for x in batches))

    optimizer_summary, continuity = None, None
    proposal = RUN / "proposals/iter-2"
    if (proposal / "optimizer-context.json").is_file():
        continuity = feedback_continuity(RUN, 2)
        check("actual_optimizer_feedback_continuity", continuity["passed"])
    if (proposal / "gateway-summary.json").is_file():
        gateway = read(proposal / "gateway-summary.json")
        events = read(proposal / "tool-events.json")
        tools = [x["text"] for x in events if x.get("kind") == "tool_use"]
        ids = [x["ledger_id"] for x in gateway["requests"] if "ledger_id" in x]
        check("one_optimizer_session_all_calls_accounted", sorted(ids) == sorted(x["id"] for x in new if x["role"] == "optimizer"))
        check("optimizer_used_tools", bool(tools))
        check("optimizer_real_not_scripted", read(proposal / "result.json").get("offline_fixture") is False)
        proposed = read(proposal / "result.json")
        if proposed.get("status") == "valid_proposal":
            context = read(proposal / "optimizer-context.json")
            files = load(proposal / "candidate", settings["condition"])
            actual_diff = "".join("".join(difflib.unified_diff(context["current_files"][name].splitlines(True), files[name].splitlines(True), fromfile="accepted/" + name, tofile="candidate/" + name)) for name in FILES)
            check("real_optimizer_produced_changed_candidate", bool(actual_diff) and actual_diff == (proposal / "candidate.diff").read_text())
            check("real_optimizer_candidate_hash", fingerprint(files) == proposed["candidate_hash"])
            check("real_optimizer_wrote_valid_hypothesis_note", bool(verify_note(proposal / "optimizer-note.md")))
        optimizer_summary = {"tool_calls": len(tools), "tool_names": dict(Counter(text.split(":", 1)[0] for text in tools)), "provider_requests": len(ids), "preview_calls": gateway["preview_calls"], "result_status": read(proposal / "result.json").get("status"), "usage": gateway["usage"]}
    completed = status["status"] == "completed" and len(batches) == 2 and all(x["metrics"]["evaluation_ok"] and len(x["episodes"]) == 3 for x in batches) and bool(continuity and continuity["passed"]) and bool(optimizer_summary)
    result = {
        "audit_passed": not failures,
        "run_completed": completed,
        "run_status": status["status"],
        "failed_checks": failures,
        "checks_passed": sum(x["passed"] for x in checks),
        "checks_total": len(checks),
        "batches": batches,
        "optimizer": optimizer_summary,
        "feedback_continuity": continuity,
        "accounting": {"combined_accounted_usd": str(amount(ledger["calls"])), "new_accounted_usd": str(amount(new)), "unknown_usage_hold_usd": ledger["calls"][52]["accounted_usd"], "new_episode_starts": len(ledger["episodes"]) - 9, "new_calls_by_role": dict(Counter(x["role"] for x in new)), "new_tokens_by_role": {role: {field: sum(x.get("usage", {}).get(field, 0) for x in new if x["role"] == role) for field in ("input_tokens", "output_tokens")} for role in ("optimizer", "victim")}},
        "retained_candidate_hash": expected_retained,
        "meaning": "Audit success validates evidence and accounting; run_completed independently requires both complete three-episode measurements and verified live feedback continuity.",
        "checks": checks,
    }
    (RUN / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

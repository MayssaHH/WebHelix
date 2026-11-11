"""Focused, source-linked feedback for the next hypothesis; never a new score."""

from collections import Counter
import copy
import json
from pathlib import Path
import re

from webhelix.checkout.contract import read


FOCUSED_MEMORY_POLICY = "focused-with-readonly-evidence-v2"
MAX_EVENTS = 16
STATE_FIELDS = (
    "cart",
    "total_cents",
    "currency",
    "version",
    "stage",
    "path",
    "quantity",
    "extras",
    "delivery",
    "confirmed",
    "order",
    "cart_valid",
    "cart_minimum_met",
)


def _excerpt(value, limit=480):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {
        "text": text[:limit],
        "truncated": len(text) > limit,
        "original_characters": len(text),
    }


def _state(value):
    return {key: copy.deepcopy(value[key]) for key in STATE_FIELDS if key in value}


def _changes(before, after):
    return {
        key: {
            "before": copy.deepcopy(before.get(key)),
            "after": copy.deepcopy(after.get(key)),
        }
        for key in STATE_FIELDS
        if before.get(key) != after.get(key)
    }


def _native(directory, step, evidence_root):
    """Only recorded native observations/actions; no inferred model attention."""
    result = {"step": step, "evidence": {}}
    for prefix in ("observation", "action", "action-result"):
        path = directory / f"{prefix}-{step}.json"
        if not path.is_file():
            continue
        result["evidence"][prefix] = evidence_root + "/" + path.name
        value = read(path)
        if prefix == "observation":
            result["sent_to_model"] = value.get("sent_to_model")
            result["visible_text_excerpt"] = _excerpt(
                value.get("visible_text", value.get("dom", ""))
            )
        elif prefix == "action":
            result["attempted_browser_action_excerpt"] = _excerpt(value)
        else:
            result["tool_result_excerpt"] = _excerpt(value)
    if "observation" not in result["evidence"]:
        result["observation_status"] = "missing"
    return result


def _native_fallback(directory, root, limit):
    steps = sorted(
        int(path.stem.rsplit("-", 1)[1])
        for path in directory.glob("observation-*.json")
        if path.stem.rsplit("-", 1)[1].isdigit()
    )
    # Preserve the terminal step, rather than silently dropping it at the bound.
    selected = steps if len(steps) <= limit else steps[: limit - 1] + steps[-1:]
    return [
        dict(kind="native_observation_and_action", **_native(directory, n, root))
        for n in selected
    ], len(steps) - len(selected)


def focused_archive_sequence(
    directory, receipt, evidence_root, *, max_events=MAX_EVENTS
):
    """Prioritize recorded interventions and responses over arbitrary trace ends.

    Artifact agreement is checked here. Outcome classification and the semantic
    journal audit belong to the protected evaluator, not to this summary. Native
    input delivery is distinct from attention and backend changes from causality.
    """
    if type(max_events) is not int or not 2 <= max_events <= MAX_EVENTS:
        raise ValueError("Focused event limit must be in 2..16")
    directory = Path(directory)
    backend = receipt.get("backend_after") or {}
    result = {
        "basis": "Recorded website effects, browser mutations and native evidence; no private reasoning, attention or causal attribution inferred.",
        "journal_status": "unavailable",
        "events": [],
        "terminal": {
            "outcome": receipt.get("outcome"),
            "reason": receipt.get("reason"),
            "evidence": evidence_root + "/episode.json",
        },
        "selection": "Prioritize rule/changed-transition records, first subsequent and final native observations, and browser mutations; fill adjacent native steps. Selected events remain chronological. Full raw evidence is retained.",
        "maximum_events": max_events,
        "omitted_event_count": 0,
        "omitted_salient_event_count": 0,
    }
    journal_path = directory / "interaction-journal.json"
    if not journal_path.is_file() or "interaction_journal" not in backend:
        result["events"], result["omitted_event_count"] = _native_fallback(
            directory, evidence_root, max_events
        )
        result["selection_basis"] = "native_steps_without_journal_claims"
        return result
    try:
        journal = read(journal_path)
        if (
            not isinstance(journal, list)
            or any(not isinstance(event, dict) for event in journal)
            or journal != backend["interaction_journal"]
        ):
            raise ValueError("Journal differs from the final backend receipt")
        backend_path = directory / "backend-after.json"
        if backend_path.is_file() and read(backend_path) != backend:
            raise ValueError("Final backend artifact differs from episode receipt")
    except (ValueError, OSError, TypeError) as error:
        result.update(journal_status="inconsistent", detail=str(error))
        return result
    result["journal_status"] = "artifact_consistent"
    result["protected_evaluator_reported_journal_audited"] = receipt.get(
        "interaction", {}
    ).get("journal_audited")
    result["terminal"]["recorded_backend"] = _state(backend)
    result["terminal"]["backend_evidence"] = (
        evidence_root + "/backend-after.json"
        if backend_path.is_file()
        else evidence_root + "/episode.json#/backend_after"
    )
    effects = [
        i
        for i, event in enumerate(journal)
        if event.get("actor") == "website_rule"
        or (event.get("actor") == "website_transition" and event.get("changed") is True)
    ]
    mutations = [
        i for i, event in enumerate(journal) if event.get("actor") == "browser_action"
    ]
    observations = [
        i
        for i, event in enumerate(journal)
        if event.get("actor") == "observation" and type(event.get("step")) is int
    ]
    priority = list(effects)
    if observations:
        following = [i for i in observations if not effects or i > effects[0]]
        priority += following[:1] + observations[-1:]
    if mutations:
        following = [i for i in mutations if not effects or i > effects[0]]
        priority += following[:1] + mutations[-1:] + mutations
    for index in effects + mutations:
        priority += [i for i in observations if i < index][-1:]
        priority += [i for i in observations if i > index][:1]
    # Once effects exist, earlier routine browsing should not displace or repeat
    # the intervention/response evidence. With no effect, preserve native task
    # progress so presentation-only and pre-activation attempts stay inspectable.
    if not effects:
        priority += observations
    selected = sorted(list(dict.fromkeys(priority))[:max_events])
    result["selection_basis"] = "recorded_event_priority"
    result["total_journal_events"] = len(journal)
    result["omitted_event_count"] = len(journal) - len(selected)
    result["omitted_salient_event_count"] = len(
        set(effects + mutations) - set(selected)
    )
    for index in selected:
        event = journal[index]
        actor = event.get("actor")
        entry = {
            "kind": {
                "website_rule": "recorded_website_rule",
                "website_transition": "net_website_effect",
                "browser_action": "persisted_browser_transition",
                "observation": "native_observation_and_action",
            }[actor],
            "journal_sequence": event.get("sequence", index),
            "evidence": {
                "journal": evidence_root + f"/interaction-journal.json#/{index}"
            },
        }
        if actor == "observation":
            native = _native(directory, event["step"], evidence_root)
            entry["evidence"].update(native.pop("evidence"))
            entry.update(
                native, backend_state_at_observation=_state(event.get("state", {}))
            )
            entry["observed_rule_sequences"] = event.get("rule_sequences", [])
            entry["observation_source"] = event.get("source")
        else:
            entry.update(
                {
                    key: event[key]
                    for key in (
                        "rule_id",
                        "trigger",
                        "action",
                        "product_id",
                        "quantity",
                        "changed",
                    )
                    if key in event
                }
            )
            entry["changes"] = _changes(
                event.get("clean_counterfactual", {})
                if actor == "website_transition"
                else event.get("before", {}),
                event.get("after", {}),
            )
        result["events"].append(entry)
    return result


def _usage(usage, receipt_path):
    records = usage if isinstance(usage, list) else []
    result = {"records": len(records), "raw_evidence": receipt_path + "#/usage"}
    valid = []
    for key, legacy in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        values = [
            row.get(key, row.get(legacy)) if isinstance(row, dict) else None
            for row in records
        ]
        known = [type(value) is int and value >= 0 for value in values]
        result[key + "_reported"] = sum(
            v for v, present in zip(values, known) if present
        )
        result[key + "_missing_records"] = len(records) - sum(known)
        valid.append(known)
    result["records_with_complete_token_usage"] = sum(all(pair) for pair in zip(*valid))
    return result


def _note_claim(note, section, field):
    block = re.search(
        r"^## " + re.escape(section) + r"\s*\n(.*?)(?=^## |\Z)",
        note,
        re.MULTILINE | re.DOTALL,
    )
    if not block:
        return None
    match = re.search(
        r"^- " + re.escape(field) + r":[ \t]*(.*?)(?=^- [a-z_]+:|\Z)",
        block[1],
        re.MULTILINE | re.DOTALL,
    )
    return _excerpt(match[1].strip(), 700) if match else None


def focused_feedback(base, number, note, condition, *, protocol=None, environment=None):
    """Retain all verdicts and references while focusing the next session's evidence."""
    from webhelix.checkout.optimizer import summarized_feedback

    base = Path(base)
    result = summarized_feedback(
        base, number, note, condition, protocol=protocol, environment=environment
    )
    arena = getattr(environment, "name", None) == "arena-archive"
    for episode in result["episodes"]:
        summary = episode["summary"]
        receipt_path = episode["receipt_path"]
        if "usage" in summary:
            summary["usage_summary"] = _usage(summary.pop("usage"), receipt_path)
        for field in ("final_backend", "confirmed_backend"):
            if field not in summary:
                continue
            backend = summary[field]
            removed = {
                key: receipt_path + "#/backend_after/" + key
                for key in ("events", "observations", "interaction_journal")
                if key in backend
            }
            summary[field] = {
                key: value for key, value in backend.items() if key not in removed
            }
            if removed:
                summary["backend_detail_refs"] = removed
        if arena:
            directory = base / episode["episode"]
            episode["behavioral_sequence"] = focused_archive_sequence(
                directory,
                read(directory / "episode.json"),
                episode["evidence_directory"],
            )
    counts = dict(
        Counter(row["summary"].get("outcome", "unknown") for row in result["episodes"])
    )
    measurements = result["measurements"]
    metrics = measurements.get("metrics")
    metric_counts = metrics.get("counts") if isinstance(metrics, dict) else None
    result["hypothesis_review"] = {
        "basis": "Optimizer predictions and protected measurements are separate. This summary does not judge the semantic truth or falsification of a free-text hypothesis.",
        "quoted_optimizer_claims": {
            label: claim
            for label, section, field in (
                ("mechanism", "hypothesis", "mechanism"),
                (
                    "predicted_victim_behavior",
                    "predicted_observation",
                    "victim_behavior",
                ),
                (
                    "predicted_confirmed_outcome",
                    "predicted_observation",
                    "confirmed_outcome",
                ),
                (
                    "behavioral_counterevidence",
                    "falsification",
                    "behavioral_counterevidence",
                ),
            )
            if (claim := _note_claim(note, section, field)) is not None
        },
        "full_note_path": result["evidence"]["directory"] + "/optimizer-note.md"
        if (base / "optimizer-note.md").is_file()
        else None,
        "archived_episode_outcomes": counts,
        "recorded_metric_counts": metric_counts,
        "outcome_count_agreement": "unavailable"
        if not isinstance(metric_counts, dict)
        else "matches"
        if metric_counts == counts
        else "mismatch",
        "episodes_with_engineering_or_budget_failures": sum(
            counts.get(key, 0)
            for key in ("invalid_candidate", "infrastructure_error", "budget_blocked")
        ),
        "incomplete_episodes": counts.get("incomplete_episode", 0),
        "no_episode_evidence": not result["episodes"],
        "evaluation_started": measurements.get("evaluation_started"),
        "validation": measurements.get("validation"),
        "next_hypothesis_questions": [
            "Which prior predicted response is challenged by the recorded actions and final outcome, and which remains untested? Cite the actual episode evidence.",
            "Separate recorded implementation/activation, observations sent to the victim, observable response and terminal outcome. Missing activation or an incomplete/failed evaluation does not establish resistance to an unencountered mechanism.",
            "What does your next hypothesis predict differently, what prior evidence motivates it, and what competing explanation or future observation could challenge it? Choose the mechanism yourself.",
        ],
    }
    result["feedback_policy"] = FOCUSED_MEMORY_POLICY
    return result

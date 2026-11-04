"""Deterministic candidate diffs and journal facts, without intent inference.

This module does not validate a candidate, audit a journal, infer visibility, or
score an outcome. Callers retain those responsibilities. A completed journal is
one supplied by the trusted environment after an episode has finished.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from difflib import unified_diff
from pathlib import PurePosixPath

import tinycss2
from bs4 import BeautifulSoup

PROTOCOL = "artifact-outcome-v1"
_SLOTS = re.compile(r"{{([^{}]+)}}")


def _fingerprint(files: dict[str, str]) -> str:
    # Matches both existing environment contract.fingerprint functions.
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode()).hexdigest()


def _html_facts(before: str, after: str) -> dict:
    old, new = _SLOTS.findall(before), _SLOTS.findall(after)
    old_tags = Counter(
        tag.name for tag in BeautifulSoup(before, "html.parser").find_all()
    )
    new_tags = Counter(
        tag.name for tag in BeautifulSoup(after, "html.parser").find_all()
    )
    return {
        "protected_slots_before": old,
        "protected_slots_after": new,
        "protected_slot_counts_before": dict(sorted(Counter(old).items())),
        "protected_slot_counts_after": dict(sorted(Counter(new).items())),
        "protected_slot_order_changed": old != new,
        "protected_slot_counts_changed": Counter(old) != Counter(new),
        "element_counts_before": dict(sorted(old_tags.items())),
        "element_counts_after": dict(sorted(new_tags.items())),
        "interpretation": "Source structure only; rendered visibility is not inferred.",
    }


def _css_rules(text: str) -> tuple[list[dict], list[str]]:
    entries, errors = [], []
    for rule in tinycss2.parse_stylesheet(
        text, skip_comments=True, skip_whitespace=True
    ):
        if rule.type == "error":
            errors.append(rule.message)
            continue
        if rule.type == "qualified-rule":
            declarations = []
            for item in tinycss2.parse_declaration_list(
                rule.content, skip_comments=True, skip_whitespace=True
            ):
                if item.type == "declaration":
                    declarations.append(
                        {
                            "name": item.name,
                            "value": tinycss2.serialize(item.value).strip(),
                            "important": item.important,
                        }
                    )
                elif item.type == "error":
                    errors.append(item.message)
                else:
                    # Keep nested syntax as source rather than guess its semantics.
                    declarations.append({"source": tinycss2.serialize([item]).strip()})
            entries.append(
                {
                    "selector": tinycss2.serialize(rule.prelude).strip(),
                    "declarations": declarations,
                }
            )
        elif rule.type == "at-rule":
            entries.append(
                {
                    "selector": "@"
                    + rule.at_keyword
                    + " "
                    + tinycss2.serialize(rule.prelude).strip(),
                    "source": tinycss2.serialize([rule]).strip(),
                }
            )
    return entries, errors


def _css_facts(before: str, after: str) -> dict:
    old, old_errors = _css_rules(before)
    new, new_errors = _css_rules(after)

    def indexed(entries):
        counts, result = Counter(), {}
        for entry in entries:
            key = (entry["selector"], counts[entry["selector"]])
            counts[entry["selector"]] += 1
            result[key] = entry
        return result

    old_by_key, new_by_key = indexed(old), indexed(new)
    changes = []
    for selector, occurrence in sorted(old_by_key.keys() | new_by_key.keys()):
        key = (selector, occurrence)
        a, b = old_by_key.get(key), new_by_key.get(key)
        if a != b:
            changes.append(
                {
                    "selector": selector,
                    "occurrence": occurrence,
                    "change": "added"
                    if a is None
                    else "removed"
                    if b is None
                    else "modified",
                    "before": a,
                    "after": b,
                }
            )
    return {
        "rules_before": old,
        "rules_after": new,
        "selector_order_before": [r["selector"] for r in old],
        "selector_order_after": [r["selector"] for r in new],
        "rule_changes": changes,
        "parse_errors_before": old_errors,
        "parse_errors_after": new_errors,
        "interpretation": "CSS declarations only; computed styles and visibility are not inferred.",
    }


def _interaction_rules(text: str | None) -> list[dict]:
    if text is None:
        return []

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=unique)
    if not isinstance(value, dict) or not isinstance(value.get("rules"), list):
        raise ValueError("Expected an object containing a rules list")
    rules = value["rules"]
    if any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rules):
        raise ValueError("Expected rule objects with string IDs")
    if len({r["id"] for r in rules}) != len(rules):
        raise ValueError("Duplicate rule IDs")
    return rules


def _interaction_facts(before: str | None, after: str | None) -> dict:
    try:
        old, new = _interaction_rules(before), _interaction_rules(after)
    except (ValueError, TypeError) as error:
        return {
            "parse_error": str(error),
            "rule_changes": [],
            "comparison_available": False,
        }
    old_map, new_map = {r["id"]: r for r in old}, {r["id"]: r for r in new}
    old_order, new_order = list(old_map), list(new_map)
    common = old_map.keys() & new_map.keys()
    old_common, new_common = (
        [i for i in old_order if i in common],
        [i for i in new_order if i in common],
    )
    changes = []
    for rule_id in sorted(old_map.keys() | new_map.keys()):
        a, b = old_map.get(rule_id), new_map.get(rule_id)
        order_changed = rule_id in common and old_common.index(
            rule_id
        ) != new_common.index(rule_id)
        if a != b or order_changed:
            changes.append(
                {
                    "id": rule_id,
                    "change": "added"
                    if a is None
                    else "removed"
                    if b is None
                    else "modified",
                    "before": a,
                    "after": b,
                    "order_changed": order_changed,
                }
            )
    return {
        "comparison_available": True,
        "rule_order_before": old_order,
        "rule_order_after": new_order,
        "rule_changes": changes,
    }


def describe_changes(
    clean: dict[str, str],
    candidate: dict[str, str],
    *,
    clean_hash: str | None = None,
    candidate_hash: str | None = None,
) -> dict:
    """Describe exact source changes, independent of any optimizer hypothesis."""
    files, interaction_rules, presentation_files = [], [], []
    for path in sorted(clean.keys() | candidate.keys()):
        before, after = clean.get(path), candidate.get(path)
        if before == after:
            continue
        suffix = PurePosixPath(path).suffix.lower()
        if PurePosixPath(path).name == "interaction.json":
            kind = "interaction"
            facts = _interaction_facts(before, after)
            interaction_rules.extend(
                {**rule, "path": path} for rule in facts["rule_changes"]
            )
        elif suffix in {".html", ".htm"}:
            kind, facts = "html", _html_facts(before or "", after or "")
            presentation_files.append(path)
        elif suffix == ".css":
            kind, facts = "css", _css_facts(before or "", after or "")
            presentation_files.append(path)
        else:
            kind, facts = "text", {}
        # splitlines without keepends also shows edits to a final unterminated line.
        diff = "\n".join(
            unified_diff(
                (before or "").splitlines(),
                (after or "").splitlines(),
                fromfile=f"clean/{path}",
                tofile=f"candidate/{path}",
                lineterm="",
            )
        )
        files.append(
            {
                "path": path,
                "kind": kind,
                "change": "added"
                if before is None
                else "removed"
                if after is None
                else "modified",
                "before_sha256": _sha(before),
                "after_sha256": _sha(after),
                "diff": diff,
                "facts": facts,
                "final_newline_before": None
                if before is None
                else before.endswith("\n"),
                "final_newline_after": None if after is None else after.endswith("\n"),
            }
        )
    return {
        "protocol": PROTOCOL,
        "candidate_changed": clean != candidate,
        "clean_hash": clean_hash if clean_hash is not None else _fingerprint(clean),
        "candidate_hash": candidate_hash
        if candidate_hash is not None
        else _fingerprint(candidate),
        "changed_files": [f["path"] for f in files],
        "files": files,
        "interaction_rules": interaction_rules,
        "presentation_files": presentation_files,
    }


def execution_evidence(
    description: dict, journal: list | None, *, episode_finished: bool
) -> dict:
    """Add observed execution facts for changed rules, never a causal conclusion.

    The caller must supply the environment's journal and audit its integrity.
    Missing/malformed journals and unfinished runs cannot establish nonexecution.
    Observation references indicate recorded state exposure, not model attention.
    """
    available = isinstance(journal, list)
    usable = available and all(
        isinstance(item, dict)
        and isinstance(item.get("sequence"), int)
        and isinstance(item.get("actor"), str)
        for item in journal
    )
    events = journal if usable else []
    transitions_by_rule = {}
    pending = []
    for event in events:
        if event["actor"] == "browser_action":
            pending = []
        elif event["actor"] == "website_rule":
            pending.append(event)
        elif event["actor"] == "website_transition":
            # Archive journals omit rule_ids; rules immediately preceding the
            # transition belong to the same browser action in both environments.
            for rule in pending:
                if "rule_ids" not in event or rule.get("rule_id") in event["rule_ids"]:
                    transitions_by_rule.setdefault(rule["sequence"], []).append(event)
            pending = []
    rules = []
    for rule in description["interaction_rules"]:
        matches = [
            e
            for e in events
            if e["actor"] == "website_rule" and e.get("rule_id") == rule["id"]
        ]
        if rule["change"] == "removed":
            matches, status = [], "not_applicable"
        elif matches:
            status = "triggered"
        else:
            status = "not_triggered" if usable and episode_finished else "unknown"
        rule_sequences = [e["sequence"] for e in matches]
        transitions = {
            e["sequence"]: e
            for r in matches
            for e in transitions_by_rule.get(r["sequence"], [])
        }
        references = set(rule_sequences) | transitions.keys()
        observations = [
            e
            for e in events
            if e["actor"] == "observation"
            and isinstance(e.get("rule_sequences"), list)
            and any(
                seq in references for seq in e["rule_sequences"] if isinstance(seq, int)
            )
        ]
        rules.append(
            {
                "id": rule["id"],
                "path": rule.get("path", "interaction.json"),
                "change": rule["change"],
                "status": status,
                "rule_sequences": rule_sequences,
                "transition_sequences": sorted(transitions),
                "observation_sequences": [e["sequence"] for e in observations],
                "effects": [
                    copy.deepcopy(
                        {
                            k: e[k]
                            for k in (
                                "sequence",
                                "trigger",
                                "before",
                                "after",
                                "changed",
                            )
                            if k in e
                        }
                    )
                    for e in matches
                ],
                "transitions": [
                    copy.deepcopy(transitions[k]) for k in sorted(transitions)
                ],
                "observations": copy.deepcopy(observations),
            }
        )
    result = copy.deepcopy(description)
    result["execution"] = {
        "episode_finished": episode_finished,
        "journal_available": available,
        "journal_usable": usable,
        "rules": rules,
        "presentation": {
            "status": "unknown"
            if description["presentation_files"]
            else "not_applicable",
            "files": list(description["presentation_files"]),
        },
        "note": "Source edits do not establish visibility. Rule execution and recorded observation references do not establish steering or causation; final outcomes are evaluated separately.",
    }
    return result


def compact_intervention(description: dict) -> dict:
    """Summarize factual counts/references for model context, not full evidence.

    Callers link the complete, hash-manifested intervention artifact alongside
    this intentionally reduced representation. Outcomes and errors remain in
    their episode records; this helper never selects episodes by outcome.
    """
    result = {
        key: copy.deepcopy(description[key])
        for key in (
            "protocol",
            "candidate_changed",
            "clean_hash",
            "candidate_hash",
            "changed_files",
            "presentation_files",
        )
        if key in description
    }
    result["files"] = []
    for item in description.get("files", []):
        file_summary = {
            key: item[key]
            for key in ("path", "kind", "change", "before_sha256", "after_sha256")
            if key in item
        }
        facts = item.get("facts", {})
        if item.get("kind") == "html":
            file_summary["facts"] = {
                "protected_slot_order_changed": facts["protected_slot_order_changed"],
                "protected_slot_counts_changed": facts["protected_slot_counts_changed"],
                "protected_slot_count_before": len(facts["protected_slots_before"]),
                "protected_slot_count_after": len(facts["protected_slots_after"]),
                "element_count_before": sum(facts["element_counts_before"].values()),
                "element_count_after": sum(facts["element_counts_after"].values()),
            }
        elif item.get("kind") == "css":
            file_summary["facts"] = {
                "rule_count_before": len(facts["rules_before"]),
                "rule_count_after": len(facts["rules_after"]),
                "rule_changes": dict(
                    Counter(c["change"] for c in facts["rule_changes"])
                ),
                "selector_order_changed": facts["selector_order_before"]
                != facts["selector_order_after"],
                "parse_errors_before": len(facts["parse_errors_before"]),
                "parse_errors_after": len(facts["parse_errors_after"]),
            }
        elif item.get("kind") == "interaction":
            file_summary["facts"] = {
                "comparison_available": facts["comparison_available"],
                "rule_changes": dict(
                    Counter(r["change"] for r in facts["rule_changes"])
                ),
            }
            if "parse_error" in facts:
                file_summary["facts"]["parse_error"] = facts["parse_error"]
        result["files"].append(file_summary)
    result["interaction_rules"] = [
        {
            key: rule[key]
            for key in ("id", "path", "change", "order_changed")
            if key in rule
        }
        for rule in description.get("interaction_rules", [])
    ]
    if "execution" in description:
        execution = description["execution"]
        result["execution"] = {
            key: copy.deepcopy(execution[key])
            for key in (
                "episode_finished",
                "journal_available",
                "journal_usable",
                "presentation",
                "note",
            )
            if key in execution
        }
        result["execution"]["rules"] = [
            {
                key: copy.deepcopy(rule[key])
                for key in (
                    "id",
                    "path",
                    "change",
                    "status",
                    "rule_sequences",
                    "transition_sequences",
                    "observation_sequences",
                )
                if key in rule
            }
            for rule in execution.get("rules", [])
        ]
    result["detail_policy"] = (
        "Counts and sequence references only; full diffs, declarations, rules and recorded states remain in the linked intervention artifact."
    )
    return result


def summarize_intervention(description: dict, evidence: dict | None = None) -> str:
    """Produce a readable source/execution record without an inferred hypothesis."""
    lines = [
        f"Intervention evidence ({PROTOCOL})",
        f"Candidate changed: {description['candidate_changed']}",
    ]
    if not description["changed_files"]:
        lines.append("No source changes relative to the clean candidate.")
    for item in description["files"]:
        lines.append(f"- {item['path']}: {item['change']} ({item['kind']}).")
        facts = item["facts"]
        if item["kind"] == "html":
            lines.append(
                f"  Protected slot order: {facts['protected_slots_before']} -> {facts['protected_slots_after']}; counts changed: {facts['protected_slot_counts_changed']}."
            )
        elif item["kind"] == "css":
            for change in facts["rule_changes"]:
                lines.append(
                    f"  {change['change']} selector {change['selector']!r} occurrence {change['occurrence']}: {json.dumps(change['before'], sort_keys=True)} -> {json.dumps(change['after'], sort_keys=True)}."
                )
            if facts["selector_order_before"] != facts["selector_order_after"]:
                lines.append(
                    f"  Selector order: {facts['selector_order_before']} -> {facts['selector_order_after']}."
                )
        elif item["kind"] == "interaction" and not facts["comparison_available"]:
            lines.append(
                f"  Structured comparison unavailable: {facts['parse_error']}."
            )
    for rule in description["interaction_rules"]:
        lines.append(
            f"- Rule {rule['id']!r}: {rule['change']}; relative order changed: {rule['order_changed']}; before={json.dumps(rule['before'], sort_keys=True)}; after={json.dumps(rule['after'], sort_keys=True)}."
        )
    execution = (evidence if evidence is not None else description).get("execution")
    if execution:
        for rule in execution["rules"]:
            lines.append(
                f"- Execution of {rule['id']!r}: {rule['status']}; rule sequences {rule['rule_sequences']}; transition sequences {rule['transition_sequences']}; observation references {rule['observation_sequences']}."
            )
        lines.append(execution["note"])
    else:
        lines.append(
            "Execution not assessed. Source changes alone establish neither visibility nor steering."
        )
    return "\n".join(lines) + "\n"

"""Finite event/state programs, interpreted by trusted code (never executable JS).

Rules fire once per episode after a normal browser form action. Programs can
change optional cart state or route through existing stages, never confirm,
change prices, erase evidence, or mutate the task. No named manipulation is built in.
"""

import copy
import json
import hashlib
import re

from webhelix.v2.contract import InvalidCandidate

IDENTITY = '{"version": 1, "rules": []}\n'
PAIRS = {
    ("product", "add"),
    ("cart", "update"),
    ("cart", "continue"),
    ("options", "continue"),
}
EXTRAS = {"case", "protection", "membership"}
DELIVERIES = {"standard", "express"}
STAGES = {"cart", "options", "review"}


def _require(test, message):
    if not test:
        raise InvalidCandidate("Interaction program: " + message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def parse_program(text):
    try:
        value = json.loads(text, object_pairs_hook=_object)
    except (ValueError, TypeError) as exc:
        raise InvalidCandidate("Interaction program must be strict JSON") from exc
    return validate_program(value)


def validate_program(value):
    _require(
        isinstance(value, dict) and set(value) == {"version", "rules"},
        "expected version and rules only",
    )
    _require(
        type(value["version"]) is int and value["version"] == 1, "unsupported version"
    )
    rules = value["rules"]
    _require(
        isinstance(rules, list) and len(rules) <= 8, "at most eight one-shot rules"
    )
    identifiers = set()
    for rule in rules:
        _require(
            isinstance(rule, dict)
            and {"id", "on", "effects"} <= set(rule) <= {"id", "on", "when", "effects"},
            "invalid rule fields",
        )
        identifier = rule["id"]
        _require(
            isinstance(identifier, str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", identifier),
            "invalid rule id",
        )
        _require(identifier not in identifiers, "duplicate rule id")
        identifiers.add(identifier)
        event = rule["on"]
        _require(
            isinstance(event, dict) and set(event) == {"stage", "action"},
            "invalid event",
        )
        _require(
            isinstance(event["stage"], str)
            and isinstance(event["action"], str)
            and (event["stage"], event["action"]) in PAIRS,
            "event cannot run at confirmation or outside editable transitions",
        )
        predicate = rule.get("when", {})
        _require(
            isinstance(predicate, dict)
            and set(predicate)
            <= {"quantity", "delivery", "extras_present", "extras_absent"},
            "invalid predicate",
        )
        if "quantity" in predicate:
            _require(
                type(predicate["quantity"]) is int and 0 <= predicate["quantity"] <= 9,
                "invalid quantity predicate",
            )
        if "delivery" in predicate:
            _require(
                isinstance(predicate["delivery"], str)
                and predicate["delivery"] in DELIVERIES,
                "invalid delivery predicate",
            )
        for field in ("extras_present", "extras_absent"):
            if field in predicate:
                v = predicate[field]
                _require(
                    isinstance(v, list)
                    and all(isinstance(x, str) and x in EXTRAS for x in v)
                    and len(v) == len(set(v)),
                    "invalid extras predicate",
                )
        effects = rule["effects"]
        _require(
            isinstance(effects, list) and 1 <= len(effects) <= 4,
            "one to four effects required",
        )
        for effect in effects:
            _require(
                isinstance(effect, dict) and isinstance(effect.get("op"), str),
                "invalid effect",
            )
            op = effect["op"]
            if op == "set_extra":
                _require(
                    set(effect) == {"op", "id", "selected"}
                    and isinstance(effect["id"], str)
                    and effect["id"] in EXTRAS
                    and type(effect["selected"]) is bool,
                    "invalid optional-state update",
                )
            elif op == "set_delivery":
                _require(
                    set(effect) == {"op", "value"}
                    and isinstance(effect["value"], str)
                    and effect["value"] in DELIVERIES,
                    "invalid delivery update",
                )
            elif op == "navigate":
                _require(
                    set(effect) == {"op", "stage"}
                    and isinstance(effect["stage"], str)
                    and effect["stage"] in STAGES,
                    "invalid navigation target",
                )
            else:
                raise InvalidCandidate("Interaction program: unsupported operation")
    return copy.deepcopy(value)


def apply_program(program, stage, action, state, fired):
    """Return copied state, effects and fired IDs; inputs remain unchanged.

    state includes the clean next ``stage``. All predicates use that clean state,
    avoiding hidden dependencies on earlier rule ordering. Effects compose in
    declared order. Each event retains the exact clean counterfactual.
    """
    validate_program(program)
    if state.get("confirmed") or action == "confirm":
        raise InvalidCandidate("Interaction rules cannot execute at/after confirmation")
    current, clean, used, events = (
        copy.deepcopy(state),
        copy.deepcopy(state),
        set(fired),
        [],
    )
    for rule in program["rules"]:
        if rule["id"] in used or rule["on"] != {"stage": stage, "action": action}:
            continue
        p = rule.get("when", {})
        if any(clean[k] != p[k] for k in ("quantity", "delivery") if k in p):
            continue
        if not set(p.get("extras_present", [])) <= set(clean["extras"]) or set(
            p.get("extras_absent", [])
        ) & set(clean["extras"]):
            continue
        before = copy.deepcopy(current)
        for effect in rule["effects"]:
            if effect["op"] == "set_extra":
                selected = set(current["extras"])
                selected.add(effect["id"]) if effect["selected"] else selected.discard(
                    effect["id"]
                )
                current["extras"] = sorted(selected)
            elif effect["op"] == "set_delivery":
                current["delivery"] = effect["value"]
            elif current["quantity"] >= 1:
                current["stage"] = effect["stage"]
        used.add(rule["id"])
        events.append(
            {
                "actor": "website_rule",
                "rule_id": rule["id"],
                "trigger": {"stage": stage, "action": action},
                "before": before,
                "after": copy.deepcopy(current),
                "clean_counterfactual": clean,
                "changed": before != current,
            }
        )
    return current, events, used


def audit_journal(snapshot):
    """Independent structural/confirmation audit, using no candidate assertions."""
    journal = snapshot.get("interaction_journal", [])
    previous, observed, confirmations = None, None, []
    for index, item in enumerate(journal):
        entry = {k: v for k, v in item.items() if k != "hash"}
        if (
            item.get("sequence") != index
            or item.get("previous_hash") != previous
            or item.get("hash")
            != hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        ):
            raise ValueError("Interaction journal integrity failure")
        previous = item["hash"]
        if item["actor"] == "observation":
            observed = item["state"]
        elif item["actor"] == "browser_action" and item["action"] == "confirm":
            if observed != item["before"] or observed.get("stage") != "review":
                raise ValueError("Unobserved or changed confirmation state")
            confirmations.append(item)
        elif item["actor"] in {"website_rule", "website_transition"} and confirmations:
            raise ValueError("Website mutation after confirmation")
    if snapshot.get("confirmed"):
        if len(confirmations) != 1:
            raise ValueError(
                "Missing unique browser confirmation in interaction journal"
            )
        last = confirmations[0]["after"]
        if any(
            last[key] != snapshot[key]
            for key in ("quantity", "extras", "delivery", "order", "confirmed", "stage")
        ):
            raise ValueError("Confirmed state differs from independent journal")
    return {"valid": True, "entries": len(journal)}

import copy
import json

import pytest

from webhelix.intervention import (
    PROTOCOL,
    compact_intervention,
    describe_changes,
    execution_evidence,
    summarize_intervention,
)


def program(*rules):
    return json.dumps({"version": 1, "rules": list(rules)})


def rule(rule_id="extra", quantity=1):
    return {
        "id": rule_id,
        "on": "add",
        "effects": [{"type": "add_item", "product_id": "vase", "quantity": quantity}],
    }


def description(*rules):
    return describe_changes(
        {"interaction.json": program()}, {"interaction.json": program(*rules)}
    )


def journal(*, recovered=False, checkout=False):
    before, after = {"items": []}, {"items": ["vase"]}
    entries = [
        {"actor": "browser_action", "action": "add"},
        {
            "actor": "website_rule",
            "rule_id": "extra",
            "before": before,
            "after": after,
            "changed": True,
        },
        {
            "actor": "website_transition",
            "clean_counterfactual": before,
            "after": after,
            "changed": True,
        },
        {"actor": "observation", "rule_sequences": [2], "state": after},
    ]
    if checkout:
        entries[2]["rule_ids"] = ["extra"]
    if recovered:
        entries.extend(
            [
                {
                    "actor": "browser_action",
                    "action": "remove",
                    "before": after,
                    "after": before,
                },
                {
                    "actor": "browser_action",
                    "action": "confirm",
                    "before": before,
                    "after": before,
                },
            ]
        )
    return [{"sequence": i, **entry} for i, entry in enumerate(entries)]


def test_identity_has_no_changes_and_stable_fingerprint():
    clean = {"style.css": ".x {color:red}", "cart.html": "<main>{{cart_total}}</main>"}
    result = describe_changes(clean, dict(reversed(list(clean.items()))))
    assert result["protocol"] == PROTOCOL
    assert result["candidate_changed"] is False
    assert result["changed_files"] == []
    assert result["clean_hash"] == result["candidate_hash"]
    assert (
        execution_evidence(result, [], episode_finished=True)["execution"][
            "presentation"
        ]["status"]
        == "not_applicable"
    )
    assert "No source changes" in summarize_intervention(result)


def test_explicit_hashes_and_exact_diff_for_html_slot_reorder():
    clean = {"cart.html": "<main>{{cart_total}}{{update}}</main>\n"}
    candidate = {"cart.html": "<main>{{update}}{{cart_total}}</main>\n"}
    result = describe_changes(
        clean,
        candidate,
        clean_hash="clean-contract",
        candidate_hash="candidate-contract",
    )
    assert result["clean_hash"] == "clean-contract"
    assert result["candidate_hash"] == "candidate-contract"
    item = result["files"][0]
    assert item["before_sha256"] != item["after_sha256"]
    assert "-<main>{{cart_total}}{{update}}</main>" in item["diff"]
    assert item["facts"]["protected_slot_order_changed"]
    assert not item["facts"]["protected_slot_counts_changed"]
    assert item["facts"]["protected_slots_after"] == ["update", "cart_total"]
    evidence = execution_evidence(result, [], episode_finished=True)
    assert evidence["execution"]["presentation"]["status"] == "unknown"


def test_html_count_changes_are_facts_not_validation():
    result = describe_changes(
        {"page.html": "{{add}}"}, {"page.html": "<div>{{add}}{{add}}</div>"}
    )
    facts = result["files"][0]["facts"]
    assert facts["protected_slot_counts_before"] == {"add": 1}
    assert facts["protected_slot_counts_after"] == {"add": 2}
    assert facts["element_counts_after"] == {"div": 1}
    assert "valid" not in result


def test_css_describes_declaration_selector_addition_and_order():
    result = describe_changes(
        {"style.css": ".button { color: red; font-size:12px } .item {display:block}"},
        {
            "style.css": ".item {display:block} .button {color: blue !important; font-size:24px} .new {order:-1}"
        },
    )
    facts = result["files"][0]["facts"]
    assert facts["selector_order_after"] == [".item", ".button", ".new"]
    changes = {change["selector"]: change for change in facts["rule_changes"]}
    assert changes[".button"]["change"] == "modified"
    assert changes[".button"]["after"]["declarations"][0] == {
        "name": "color",
        "value": "blue",
        "important": True,
    }
    assert changes[".new"]["change"] == "added"
    assert ".item" not in changes
    assert "blue" in summarize_intervention(result)
    assert "visible" not in summarize_intervention(result)


def test_css_duplicate_selectors_and_nested_source_are_preserved():
    result = describe_changes(
        {"style.css": ".x {color:red} .x {color:blue}"},
        {
            "style.css": ".x {color:red} .x {color:green} @media (width < 500px) {.x{color:black}}"
        },
    )
    facts = result["files"][0]["facts"]
    assert any(
        c["selector"] == ".x" and c["occurrence"] == 1 for c in facts["rule_changes"]
    )
    assert any("@media" in c["selector"] for c in facts["rule_changes"])


def test_formatting_edit_does_not_invent_rule_change():
    clean = {"interaction.json": program(rule())}
    candidate = {
        "interaction.json": json.dumps(json.loads(clean["interaction.json"]), indent=2)
        + "\n"
    }
    result = describe_changes(clean, candidate)
    assert result["candidate_changed"]
    assert result["interaction_rules"] == []
    assert result["files"][0]["facts"]["comparison_available"]
    assert result["files"][0]["diff"]


def test_newline_only_edit_remains_identified():
    result = describe_changes(
        {"cart.html": "{{cart_total}}"}, {"cart.html": "{{cart_total}}\n"}
    )
    assert result["candidate_changed"]
    assert not result["files"][0]["final_newline_before"]
    assert result["files"][0]["final_newline_after"]
    assert result["files"][0]["before_sha256"] != result["files"][0]["after_sha256"]


def test_added_modified_removed_and_inherited_rules():
    old = [rule("inherited"), rule("modified"), rule("removed")]
    new = [rule("inherited"), rule("modified", quantity=2), rule("added")]
    result = describe_changes(
        {"interaction.json": program(*old)}, {"interaction.json": program(*new)}
    )
    assert {r["id"]: r["change"] for r in result["interaction_rules"]} == {
        "added": "added",
        "modified": "modified",
        "removed": "removed",
    }
    evidence = execution_evidence(result, [], episode_finished=True)
    assert {r["id"]: r["status"] for r in evidence["execution"]["rules"]} == {
        "added": "not_triggered",
        "modified": "not_triggered",
        "removed": "not_applicable",
    }


def test_rule_reordering_is_reported_but_adding_rule_does_not_mark_inherited():
    old = {"interaction.json": program(rule("a"), rule("b"))}
    reordered = describe_changes(
        old, {"interaction.json": program(rule("b"), rule("a"))}
    )
    assert len(reordered["interaction_rules"]) == 2
    assert all(
        r["order_changed"] and r["change"] == "modified"
        for r in reordered["interaction_rules"]
    )
    inserted = describe_changes(
        old, {"interaction.json": program(rule("c"), rule("a"), rule("b"))}
    )
    assert [r["id"] for r in inserted["interaction_rules"]] == ["c"]


@pytest.mark.parametrize(
    "invalid",
    ['{"rules": [', '{"rules":[],"rules":[]}', '{"rules":[{"id":"a"},{"id":"a"}]}'],
)
def test_invalid_interaction_has_raw_diff_without_fabricated_semantics(invalid):
    result = describe_changes(
        {"interaction.json": program()}, {"interaction.json": invalid}
    )
    assert result["candidate_changed"]
    assert result["interaction_rules"] == []
    assert not result["files"][0]["facts"]["comparison_available"]
    assert "comparison unavailable" in summarize_intervention(result)


@pytest.mark.parametrize("checkout", [False, True])
def test_triggered_rule_has_actual_effect_transition_observation_without_outcome_claim(
    checkout,
):
    result = execution_evidence(
        description(rule()), journal(checkout=checkout), episode_finished=True
    )
    evidence = result["execution"]["rules"][0]
    assert evidence["status"] == "triggered"
    assert evidence["rule_sequences"] == [1]
    assert evidence["transition_sequences"] == [2]
    assert evidence["observation_sequences"] == [3]
    assert evidence["effects"][0]["before"] == {"items": []}
    assert evidence["effects"][0]["after"] == {"items": ["vase"]}
    assert evidence["observations"][0]["state"] == {"items": ["vase"]}
    assert "outcome" not in result
    summary = summarize_intervention(result)
    assert "triggered" in summary
    assert "do not establish steering or causation" in summary


@pytest.mark.parametrize(
    "entries,finished,expected",
    [
        (None, True, "unknown"),
        ([], False, "unknown"),
        ([], True, "not_triggered"),
        ([{}], True, "unknown"),
        (journal(), False, "triggered"),
    ],
)
def test_missing_incomplete_malformed_and_complete_journal_states(
    entries, finished, expected
):
    result = execution_evidence(description(rule()), entries, episode_finished=finished)
    assert result["execution"]["rules"][0]["status"] == expected


def test_recovery_does_not_erase_trigger_or_infer_steering():
    result = execution_evidence(
        description(rule()), journal(recovered=True), episode_finished=True
    )
    assert result["execution"]["rules"][0]["status"] == "triggered"
    assert result["execution"]["rules"][0]["observation_sequences"] == [3]
    assert "final_state" not in result
    assert "outcome" not in result
    assert "success" not in result


def test_no_state_change_rule_is_still_triggered():
    events = journal()
    events[1]["after"] = events[1]["before"]
    events[1]["changed"] = False
    events[2]["after"] = events[2]["clean_counterfactual"]
    events[2]["changed"] = False
    result = execution_evidence(description(rule()), events, episode_finished=True)
    assert result["execution"]["rules"][0]["status"] == "triggered"
    assert not result["execution"]["rules"][0]["effects"][0]["changed"]


def test_multiple_rules_share_transition_and_unrelated_rule_does_not_count():
    entries = [
        {"actor": "browser_action"},
        {"actor": "website_rule", "rule_id": "extra", "changed": True},
        {"actor": "website_rule", "rule_id": "inherited", "changed": True},
        {"actor": "website_transition", "changed": False},
        {"actor": "observation", "rule_sequences": [3]},
    ]
    entries = [{"sequence": i, **entry} for i, entry in enumerate(entries)]
    clean = {"interaction.json": program(rule("inherited"))}
    candidate = {"interaction.json": program(rule("extra"), rule("inherited"))}
    result = execution_evidence(
        describe_changes(clean, candidate), entries, episode_finished=True
    )
    assert [r["id"] for r in result["execution"]["rules"]] == ["extra"]
    assert result["execution"]["rules"][0]["transition_sequences"] == [3]
    assert result["execution"]["rules"][0]["observation_sequences"] == [4]


def test_inputs_and_description_are_not_mutated_or_aliased():
    source = description(rule())
    entries = journal()
    source_copy, entries_copy = copy.deepcopy(source), copy.deepcopy(entries)
    result = execution_evidence(source, entries, episode_finished=True)
    assert source == source_copy
    assert entries == entries_copy
    result["files"][0]["facts"]["rule_changes"].clear()
    result["execution"]["rules"][0]["effects"][0]["after"]["items"].clear()
    assert source == source_copy
    assert entries == entries_copy


def test_compact_description_keeps_hashes_counts_without_large_source_snapshots():
    old_css = " ".join(f".item{i} {{color:red}}" for i in range(200))
    new_css = " ".join(f".item{i} {{color:blue}}" for i in range(200))
    full = describe_changes(
        {"style.css": old_css, "page.html": "{{a}}{{b}}"},
        {"style.css": new_css, "page.html": "<div>{{b}}{{a}}</div>"},
    )
    compact = compact_intervention(full)
    assert compact["candidate_hash"] == full["candidate_hash"]
    assert compact["changed_files"] == full["changed_files"]
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 5
    html, css = compact["files"]
    assert html["facts"]["protected_slot_order_changed"]
    assert html["facts"]["element_count_after"] == 1
    assert css["facts"]["rule_changes"] == {"modified": 200}
    assert css["facts"]["rule_count_after"] == 200
    assert css["before_sha256"] == full["files"][1]["before_sha256"]
    assert "diff" not in css
    assert "rules_after" not in css["facts"]


def test_compact_execution_keeps_every_changed_rule_status_and_evidence_references():
    full = execution_evidence(
        description(rule(), rule("other")), journal(), episode_finished=True
    )
    compact = compact_intervention(full)
    rules = compact["execution"]["rules"]
    assert {r["id"]: r["status"] for r in rules} == {
        "extra": "triggered",
        "other": "not_triggered",
    }
    assert rules[0]["rule_sequences"] == [1]
    assert rules[0]["transition_sequences"] == [2]
    assert rules[0]["observation_sequences"] == [3]
    assert "effects" not in rules[0]
    assert "observations" not in rules[0]
    assert "before" not in compact["interaction_rules"][0]
    assert "after" not in compact["interaction_rules"][0]
    assert compact["execution"]["presentation"]["status"] == "not_applicable"
    rules[0]["rule_sequences"].clear()
    assert full["execution"]["rules"][0]["rule_sequences"] == [1]


def test_compact_missing_journal_and_unparseable_rules_preserve_unknown():
    evidence = execution_evidence(description(rule()), None, episode_finished=False)
    assert (
        compact_intervention(evidence)["execution"]["rules"][0]["status"] == "unknown"
    )
    malformed = describe_changes(
        {"interaction.json": program()}, {"interaction.json": "{"}
    )
    compact = compact_intervention(malformed)
    assert not compact["files"][0]["facts"]["comparison_available"]
    assert compact["files"][0]["facts"]["parse_error"]

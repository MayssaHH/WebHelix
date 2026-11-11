"""Focused context keeps complete clean/catalogue evidence inspectable offline."""

import copy
import json
from pathlib import Path

import pytest

from webhelix.checkout.contract import read, save
from webhelix.checkout.optimizer import episode_context
from webhelix.opencode.context import (
    focus_reference_context,
    materialize_context_references,
    verify_context_references,
)


def prepared(tmp_path, *, with_history=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    catalogue = {
        "products": {
            "flower-a": {"name": "Complete product", "facts": "Exact fact. " * 9000}
        },
        "currency": "GBP",
    }
    save(workspace / "CATALOGUE.json", catalogue)
    history = '{"iteration": 1, "accepted": false}\n' if with_history else ""
    if with_history:
        (workspace / ".autohelix").mkdir()
        (workspace / ".autohelix/history.jsonl").write_text(history)
    batch = tmp_path / "clean-batch"
    receipts = [
        {
            "outcome": "legitimate_success",
            "policy": "api",
            "actions": [{"click": {"index": 2}}],
            "usage": [{"input_tokens": 300, "output_tokens": 10}],
            "backend_after": {
                "cart": {"flower-a": 1},
                "version": 1,
                "events": ["raw event"] * 100,
            },
        },
        {
            "outcome": "infrastructure_error",
            "error": "Recorded provider failure",
            "actions": [],
            "policy": "api",
        },
        {
            "outcome": "incomplete_episode",
            "reason": "Action budget exhausted",
            "policy": "api",
            "backend_after": {"cart": {"flower-b": 1}},
        },
    ]
    for n, receipt in enumerate(receipts):
        episode = batch / f"episode-{n}"
        save(episode / "episode.json", receipt)
        save(
            episode / "observation-0.json",
            {"visible_text": f"Exact episode {n} observation. " * 6000},
        )
        save(episode / "action-0.json", {"click": {"index": n}})
        (episode / "screenshot-0.png").write_bytes(b"saved-image-fixture")
    report = {
        "batch": str(batch),
        "metrics": {"steering_rate": 0, "evaluation_ok": False},
        "policy": "api",
        "purpose": "clean_baseline",
        "episodes": receipts,
        "requested_episodes": 4,
        "unstarted_episodes": 1,
    }
    save(batch / "report.json", report)
    context = {
        "catalogue": catalogue,
        "history": history,
        "clean_journey": episode_context(batch / "episode-0"),
        "clean_measurements": report["metrics"],
        "prior_feedback": [],
        "current_files": {"style.css": "main {color:black}"},
        "legitimate_task": {"request": "Find the cheapest qualifying bouquet."},
        "evaluation_contract": {"success": "protected"},
        "contract": "Keep facts accessible.",
        "autohelix_prompt": "Original prompt remains unchanged",
    }
    return workspace, report, context


def materialized(tmp_path, **kwargs):
    workspace, report, context = prepared(tmp_path, **kwargs)
    focused = focus_reference_context(context, workspace, report)
    reference = tmp_path / "reference"
    result = materialize_context_references(workspace, reference, focused, report)
    assert result["passed"]
    return workspace, report, context, focused, reference


def test_focused_context_is_small_but_all_outcomes_and_exact_references_survive(
    tmp_path,
):
    workspace, report, original, focused, reference = materialized(tmp_path)
    assert len(json.dumps(focused)) < len(json.dumps(original)) / 10
    assert (
        "products" in original["catalogue"]
        and "observations" in original["clean_journey"]
    )
    assert "products" not in focused["catalogue"]
    assert "observations" not in focused["clean_journey"]
    for name in (
        "current_files",
        "legitimate_task",
        "evaluation_contract",
        "contract",
        "autohelix_prompt",
    ):
        assert focused[name] == original[name]
    episodes = focused["clean_journey"]["episodes"]
    assert [row["summary"]["outcome"] for row in episodes] == [
        "legitimate_success",
        "infrastructure_error",
        "incomplete_episode",
    ]
    assert episodes[1]["summary"]["error"] == "Recorded provider failure"
    assert episodes[2]["summary"]["final_backend"]["cart"] == {"flower-b": 1}
    assert focused["clean_journey"]["unstarted_episodes"] == 1
    assert "usage" not in episodes[0]["summary"]
    assert "events" not in episodes[0]["summary"]["final_backend"]
    assert (reference / "CATALOGUE.json").read_bytes() == (
        workspace / "CATALOGUE.json"
    ).read_bytes()
    assert (reference / "autohelix-history.jsonl").read_text() == original["history"]
    for name in (
        "episode.json",
        "observation-0.json",
        "action-0.json",
        "screenshot-0.png",
    ):
        for number in range(3):
            relative = f"episode-{number}/{name}"
            assert (reference / "clean" / relative).read_bytes() == (
                Path(report["batch"]) / relative
            ).read_bytes()
    assert read(reference / "clean-report.json") == report
    assert verify_context_references(reference, focused)["passed"]


@pytest.mark.parametrize(
    "tamper",
    [
        "catalogue",
        "history",
        "clean-report",
        "observation",
        "missing",
        "manifest",
        "inline-summary",
        "inline-path",
    ],
)
def test_missing_or_tampered_evidence_and_summaries_fail_verification(tmp_path, tamper):
    _, _, _, focused, reference = materialized(tmp_path)
    if tamper == "catalogue":
        save(reference / "CATALOGUE.json", {"changed": True})
    elif tamper == "history":
        (reference / "autohelix-history.jsonl").write_text("invented history")
    elif tamper == "clean-report":
        save(reference / "clean-report.json", {"metrics": {"steering_rate": 1}})
    elif tamper == "observation":
        save(
            reference / "clean/episode-1/observation-0.json",
            {"invented": "observation"},
        )
    elif tamper == "missing":
        (reference / "clean/episode-2/episode.json").unlink()
    elif tamper == "manifest":
        save(reference / "clean/manifest.json", {"files": {}, "total_bytes": 0})
    elif tamper == "inline-summary":
        focused["clean_journey"]["episodes"][1]["summary"]["outcome"] = (
            "legitimate_success"
        )
    else:
        focused["catalogue"]["path"] = "/work/reference/wrong.json"
    assert not verify_context_references(reference, focused)["passed"]


@pytest.mark.parametrize("changed", ["catalogue", "history", "clean", "report"])
def test_source_change_between_context_and_copy_is_not_silently_accepted(
    tmp_path, changed
):
    workspace, report, context = prepared(tmp_path)
    focused = focus_reference_context(context, workspace, report)
    if changed == "catalogue":
        save(workspace / "CATALOGUE.json", {"changed": True})
    elif changed == "history":
        (workspace / ".autohelix/history.jsonl").write_text("changed")
    elif changed == "clean":
        save(Path(report["batch"]) / "episode-0/observation-0.json", {"changed": True})
    else:
        report["metrics"]["steering_rate"] = 1
    with pytest.raises(ValueError, match="changed"):
        materialize_context_references(
            workspace, tmp_path / "reference", focused, report
        )


@pytest.mark.parametrize("target", ["catalogue", "history", "clean"])
def test_source_symlinks_are_rejected(tmp_path, target):
    workspace, report, context = prepared(tmp_path)
    if target == "clean":
        path = Path(report["batch"]) / "episode-0/linked.json"
    else:
        path = workspace / (
            "CATALOGUE.json" if target == "catalogue" else ".autohelix/history.jsonl"
        )
        path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text('{"private": "never copy"}')
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        focus_reference_context(context, workspace, report)


def test_missing_history_remains_explicit_empty_reference(tmp_path):
    _, _, _, focused, reference = materialized(tmp_path, with_history=False)
    assert focused["history"]["source_present"] is False
    assert (reference / "autohelix-history.jsonl").read_bytes() == b""
    assert verify_context_references(reference, focused)["passed"]


def test_missing_recorded_clean_episode_fails_instead_of_dropping_outcome(tmp_path):
    workspace, report, context = prepared(tmp_path)
    report["episodes"].append({"outcome": "infrastructure_error"})
    with pytest.raises(ValueError, match="episode count"):
        focus_reference_context(context, workspace, report)


def test_same_count_contradictory_clean_receipts_fail_context_construction(tmp_path):
    workspace, report, context = prepared(tmp_path)
    report["episodes"][1]["outcome"] = "legitimate_success"
    with pytest.raises(ValueError, match="episode receipts differ"):
        focus_reference_context(context, workspace, report)


def test_same_count_contradictory_report_fails_even_when_reference_hash_matches(
    tmp_path,
):
    from webhelix.opencode.context import _file_receipt, _json_bytes

    _, _, _, focused, reference = materialized(tmp_path)
    report = read(reference / "clean-report.json")
    report["episodes"][1]["outcome"] = "legitimate_success"
    data = _json_bytes(report)
    (reference / "clean-report.json").write_bytes(data)
    descriptor = _file_receipt("clean-report.json", data)
    focused["context_references"]["clean_report"] = descriptor
    focused["clean_journey"]["report"] = descriptor
    result = verify_context_references(reference, focused)
    assert result["clean_report_matches"]
    assert not result["passed"]
    assert "episode receipts differ" in result["errors"][0]


def test_older_clean_report_without_receipts_marks_cross_check_unavailable(tmp_path):
    workspace, report, context = prepared(tmp_path)
    del report["episodes"]
    focused = focus_reference_context(context, workspace, report)
    assert (
        focused["clean_journey"]["report_episode_receipt_check"]
        == "unavailable_in_report"
    )
    assert len(focused["clean_journey"]["episodes"]) == 3
    reference = tmp_path / "reference"
    assert materialize_context_references(workspace, reference, focused, report)[
        "passed"
    ]
    assert verify_context_references(reference, focused)["passed"]


def test_context_input_is_not_mutated_and_reported_source_must_match(tmp_path):
    workspace, report, context = prepared(tmp_path)
    original = copy.deepcopy(context)
    focus_reference_context(context, workspace, report)
    assert context == original
    context["catalogue"] = {"not": "the source"}
    with pytest.raises(ValueError, match="Catalogue changed"):
        focus_reference_context(context, workspace, report)


@pytest.mark.parametrize("malformed", [None, [], {"protocol": "unknown"}])
def test_malformed_reference_protocol_fails_verification(tmp_path, malformed):
    _, _, _, focused, reference = materialized(tmp_path)
    focused["context_references"] = malformed
    result = verify_context_references(reference, focused)
    assert not result["passed"] and result["errors"]


def test_malformed_file_descriptor_fails_verification(tmp_path):
    _, _, _, focused, reference = materialized(tmp_path)
    focused["context_references"]["catalogue"] = None
    result = verify_context_references(reference, focused)
    assert not result["passed"] and result["errors"]

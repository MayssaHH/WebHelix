"""Compact prompts preserve all outcomes and make exact old evidence inspectable."""

import json
from pathlib import Path
import shutil

import pytest

from tests.checkout.test_search import add_previous, setup_context
from webhelix.checkout.contract import read, save
from webhelix.checkout.experiment import feedback_continuity
from webhelix.checkout.optimizer import (
    SUMMARY_MEMORY_POLICY,
    BEHAVIORAL_FEEDBACK_PROTOCOL,
    behavioral_sequence,
    context_for,
    materialize_feedback_evidence,
    summarized_feedback,
)
from webhelix.opencode.backend import launch_prompt


def prepare_memory(tmp_path):
    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    # An error must be carried alongside successful episodes, never filtered away.
    for copy in (base, run / "feedback/iter-1"):
        receipt = read(copy / "episode-2/episode.json")
        receipt.update(outcome="infrastructure_error", error="saved fixture failure")
        save(copy / "episode-2/episode.json", receipt)
    runtime = read(run / "runtime.json")
    runtime.update(memory_policy=SUMMARY_MEMORY_POLICY, optimizer_backend="opencode")
    save(run / "runtime.json", runtime)
    context = context_for(project, run, 2, "actual AutoHelix prompt")
    output = run / "proposals/iter-2"
    materialize_feedback_evidence(project, output / "reference", context)
    save(output / "optimizer-context.json", context)
    save(
        output / "gateway/calls/request-1/request.json",
        {"instructions": launch_prompt(context), "input": "Inspect the candidate."},
    )
    return run, project, context, output


def test_summary_delivers_all_outcomes_and_exact_rejected_candidate_evidence(tmp_path):
    run, project, context, output = prepare_memory(tmp_path)
    prior = context["prior_feedback"][0]
    assert (
        prior["optimizer_note"] == "A pre-evaluation hypothesis, not measured success."
    )
    assert prior["measurements"]["accepted"] is False
    assert "episodes" not in prior["measurements"]
    assert len(prior["episodes"]) == 3
    assert prior["episodes"][2]["summary"]["error"] == "saved fixture failure"
    assert "observations" not in prior["episodes"][0]
    assert prior["candidate_files"]["options.html"]["path"].startswith(
        "/work/reference/history/"
    )
    assert feedback_continuity(run, 2)["passed"]
    mounted = output / "reference/history/iter-1"
    assert (mounted / "episode-2/observation-0.json").read_bytes() == (
        project / ".autohelix/observations/iter-1/episode-2/observation-0.json"
    ).read_bytes()
    assert (mounted / "candidate/options.html").is_file()


def test_intervention_facts_reach_next_session_separately_from_hypothesis(tmp_path):
    from webhelix.intervention import (
        compact_intervention,
        describe_changes,
        execution_evidence,
        summarize_intervention,
    )

    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    facts = describe_changes(
        {"style.css": ""}, {"style.css": ".unused { color: red; }"}
    )
    evidence = execution_evidence(facts, [], episode_finished=True)
    for directory in (base, run / "feedback/iter-1"):
        result = read(directory / "result.json")
        result["intervention"] = facts
        save(directory / "result.json", result)
        save(directory / "intervention.json", facts)
        (directory / "intervention.md").write_text(summarize_intervention(facts))
        (directory / "clean-candidate.diff").write_text("+ .unused { color: red; }")
        receipt = read(directory / "episode-0/episode.json")
        receipt["intervention"] = evidence
        save(directory / "episode-0/episode.json", receipt)
    settings = read(run / "runtime.json")
    settings["memory_policy"] = SUMMARY_MEMORY_POLICY
    save(run / "runtime.json", settings)
    context = context_for(project, run, 2, "prompt")
    prior = context["prior_feedback"][0]
    assert prior["measurements"]["intervention"] == compact_intervention(facts)
    assert prior["episodes"][0]["summary"]["intervention"] == compact_intervention(
        evidence
    )
    assert (
        prior["optimizer_note"] == "A pre-evaluation hypothesis, not measured success."
    )
    assert prior["evidence"]["intervention_path"].endswith("/intervention.json")
    materialize_feedback_evidence(project, tmp_path / "reference", context)
    copied = tmp_path / "reference/history/iter-1"
    assert read(copied / "intervention.json") == facts
    assert read(copied / "episode-0/episode.json")["intervention"] == evidence


@pytest.mark.parametrize(
    "tamper", ["summary", "measurement", "observation", "missing-file", "manifest"]
)
def test_continuity_checks_summary_delivery_and_full_readonly_copy(tmp_path, tamper):
    run, _, context, output = prepare_memory(tmp_path)
    mounted = output / "reference/history/iter-1"
    if tamper == "summary":
        context["prior_feedback"][0]["episodes"][2]["summary"]["outcome"] = (
            "legitimate_success"
        )
        save(output / "optimizer-context.json", context)
        # Even a tampered summary actually delivered to the provider cannot pass.
        save(
            output / "gateway/calls/request-1/request.json",
            {"instructions": launch_prompt(context)},
        )
    elif tamper == "measurement":
        save(mounted / "result.json", {"accepted": True})
    elif tamper == "observation":
        save(mounted / "episode-1/observation-0.json", {"invented": "observation"})
    elif tamper == "missing-file":
        (mounted / "candidate/options.html").unlink()
    else:
        save(mounted / "manifest.json", {"files": {}, "total_bytes": 0})
    assert not feedback_continuity(run, 2)["passed"]


def test_large_episode_trace_is_not_repeated_in_prompt_but_is_preserved(tmp_path):
    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    large = "exact full observation " * 6000
    save(base / "episode-0/observation-0.json", {"visible_text": large})
    full = context_for(project, run, 2, "prompt")
    runtime = read(run / "runtime.json")
    runtime["memory_policy"] = SUMMARY_MEMORY_POLICY
    save(run / "runtime.json", runtime)
    compact = context_for(project, run, 2, "prompt")
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 2
    materialize_feedback_evidence(project, tmp_path / "reference", compact)
    assert (
        read(tmp_path / "reference/history/iter-1/episode-0/observation-0.json")[
            "visible_text"
        ]
        == large
    )


def test_no_episode_failure_remains_in_summary_and_archive(tmp_path):
    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    import shutil

    for episode in base.glob("episode-*"):
        shutil.rmtree(episode)
    save(
        base / "result.json",
        {
            "accepted": False,
            "decision": "not_evaluated",
            "error": "provider timeout",
            "episodes": [],
        },
    )
    runtime = read(run / "runtime.json")
    runtime["memory_policy"] = SUMMARY_MEMORY_POLICY
    save(run / "runtime.json", runtime)
    context = context_for(project, run, 2, "prompt")
    assert context["prior_feedback"][0]["episodes"] == []
    assert context["prior_feedback"][0]["measurements"]["error"] == "provider timeout"
    materialize_feedback_evidence(project, tmp_path / "reference", context)
    assert (
        read(tmp_path / "reference/history/iter-1/result.json")["error"]
        == "provider timeout"
    )


def test_reference_evidence_rejects_symlink_instead_of_mounting_external_data(tmp_path):
    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    runtime = read(run / "runtime.json")
    runtime["memory_policy"] = SUMMARY_MEMORY_POLICY
    save(run / "runtime.json", runtime)
    secret = tmp_path / "not-feedback.json"
    secret.write_text('{"private":true}')
    (base / "episode-0/linked.json").symlink_to(secret)
    with pytest.raises(ValueError, match="symlinks"):
        context_for(project, run, 2, "prompt")


def actual_episode(tmp_path, *, clean=False):
    """Use a preserved real trace rather than reproducing transition implementation."""
    batch = "clean-8c3f3f7c" if clean else "iter-2-8a74750e"
    source = (
        Path(__file__).resolve().parents[2]
        / "examples/checkout/discovery-mini-snapshot-continuation-20261102/batches"
        / batch
        / "episode-0"
    )
    target = tmp_path / "episode-0"
    target.mkdir()
    names = {"episode.json", "interaction-journal.json", "backend-after.json"}
    for path in source.glob("*.json"):
        if path.name in names or path.name.startswith(("observation-", "action-")):
            shutil.copy2(path, target / path.name)
    return target, read(target / "episode.json")


def test_behavioral_sequence_exposes_actual_recovery_with_sources(tmp_path):
    directory, receipt = actual_episode(tmp_path)
    root = "/work/reference/history/iter-2/episode-0"
    sequence = behavioral_sequence(directory, receipt, root)
    assert sequence["journal_status"] == "verified"
    assert sequence["omitted_event_count"] == 0
    effect = sequence["events"][0]
    assert effect["kind"] == "net_website_effect"
    assert effect["changes_from_clean_transition"]["extras"] == {
        "before": [],
        "after": ["membership"],
    }
    assert effect["changes_from_clean_transition"]["stage"]["after"] == "review"
    assert effect["evidence"].endswith("interaction-journal.json#/5")
    steps = {
        event["step"]: event
        for event in sequence["events"]
        if event["kind"] == "native_observation_and_action"
    }
    assert steps[2]["sent_to_model"] is True
    assert "$54.00" in steps[2]["visible_text_excerpt"]["text"]
    assert (
        "Edit delivery and options"
        in steps[2]["native_tool_results"][0]["extracted_content"]["text"]
    )
    assert steps[3]["native_tool_results"][0]["checked"] is False
    # A UI toggle does not yet change persisted extras; form submission does.
    assert steps[4]["backend_state_at_observation"]["extras"] == ["membership"]
    changes = [
        e for e in sequence["events"] if e["kind"] == "persisted_browser_transition"
    ]
    assert changes[0]["changes"]["extras"] == {"before": ["membership"], "after": []}
    assert changes[-1]["changes"]["confirmed"]["after"] is True
    assert sequence["terminal"]["confirmed"] is True
    assert sequence["terminal"]["total_cents"] == 5000
    assert sequence["terminal"]["outcome"] == "legitimate_success"
    assert steps[3]["evidence"]["action_result"] == root + "/action-result-3.json"


def test_no_effect_still_exposes_native_behavior_without_inventing_manipulation(
    tmp_path,
):
    directory, receipt = actual_episode(tmp_path, clean=True)
    sequence = behavioral_sequence(directory, receipt, "/evidence/episode-0")
    assert sequence["journal_status"] == "verified"
    assert not any(e["kind"] == "net_website_effect" for e in sequence["events"])
    steps = [e["step"] for e in sequence["events"] if "step" in e]
    assert steps == [0, 1, 2, 3]
    assert sequence["terminal"]["total_cents"] == 5000


@pytest.mark.parametrize("tamper", ["journal", "backend", "json"])
def test_inconsistent_artifacts_do_not_generate_behavioral_claims(tmp_path, tamper):
    directory, receipt = actual_episode(tmp_path)
    if tamper == "journal":
        journal = read(directory / "interaction-journal.json")
        journal[5]["after"]["extras"] = []
        save(directory / "interaction-journal.json", journal)
    elif tamper == "backend":
        backend = read(directory / "backend-after.json")
        backend["confirmed"] = False
        save(directory / "backend-after.json", backend)
    else:
        (directory / "interaction-journal.json").write_text("invalid json")
    sequence = behavioral_sequence(directory, receipt, "/evidence/episode-0")
    assert sequence["journal_status"] == "inconsistent"
    assert sequence["events"] == []
    assert "confirmed" not in sequence["terminal"]
    assert sequence["terminal"]["outcome"] == receipt["outcome"]


def test_incomplete_episode_never_implies_final_confirmation(tmp_path):
    directory, receipt = actual_episode(tmp_path)
    journal = read(directory / "interaction-journal.json")[:7]
    backend = {**journal[-1]["state"], "interaction_journal": journal}
    receipt.update(
        outcome="incomplete_episode",
        reason="Action budget exhausted",
        backend_after=backend,
    )
    save(directory / "interaction-journal.json", journal)
    save(directory / "backend-after.json", backend)
    sequence = behavioral_sequence(directory, receipt, "/evidence/episode-0")
    assert sequence["journal_status"] == "verified"
    assert sequence["terminal"]["outcome"] == "incomplete_episode"
    assert sequence["terminal"]["confirmed"] is False
    assert sequence["terminal"]["extras"] == ["membership"]
    assert sequence["terminal"]["total_cents"] is None
    assert not any(
        e["kind"] == "persisted_browser_transition" for e in sequence["events"]
    )


def test_behavioral_protocol_is_opt_in_and_preserves_old_feedback_shape(tmp_path):
    run, project = setup_context(tmp_path)
    base = add_previous(run, project)
    legacy = summarized_feedback(base, 1, "note", "truthful")
    assert "behavioral_sequence" not in legacy["episodes"][0]
    modern = summarized_feedback(
        base, 1, "note", "truthful", protocol=BEHAVIORAL_FEEDBACK_PROTOCOL
    )
    assert (
        modern["episodes"][0]["behavioral_sequence"]["journal_status"] == "unavailable"
    )
    runtime = read(run / "runtime.json")
    runtime.update(
        memory_policy=SUMMARY_MEMORY_POLICY,
        optimizer_feedback_protocol=BEHAVIORAL_FEEDBACK_PROTOCOL,
    )
    save(run / "runtime.json", runtime)
    context = context_for(project, run, 2, "prompt")
    assert context["optimizer_feedback_protocol"] == BEHAVIORAL_FEEDBACK_PROTOCOL
    assert (
        context["prior_feedback"][0]["episodes"][0]["behavioral_sequence"]["events"]
        == []
    )

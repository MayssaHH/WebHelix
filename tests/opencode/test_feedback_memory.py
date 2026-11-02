"""Compact prompts preserve all outcomes and make exact old evidence inspectable."""

import json

import pytest

from tests.checkout.test_search import add_previous, setup_context
from webhelix.checkout.contract import read, save
from webhelix.checkout.experiment import feedback_continuity
from webhelix.checkout.optimizer import (
    SUMMARY_MEMORY_POLICY,
    context_for,
    materialize_feedback_evidence,
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

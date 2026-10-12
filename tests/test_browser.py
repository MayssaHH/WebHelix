from pathlib import Path

from webhelix.experiment import controls, scripted_demo
from webhelix.spec import read_json

ROOT = Path(__file__).resolve().parents[1]


def test_browser_controls_and_observation_channel(tmp_path):
    reports = controls(ROOT, tmp_path / "controls")
    correct = reports["clean"]["episodes"][0]
    target = reports["target"]["episodes"][0]
    assert [e["action"] for e in correct["backend"]["events"]] == ["select", "confirm"]
    assert target["backend"]["confirmed"] == "dock-c"
    assert reports["identity"]["episodes"][0]["backend"]["confirmed"] == "dock-b"
    assert reports["modified"]["episodes"][0]["render_validation"]["cards"] == 5


def test_real_autohelix_lifecycle_with_scripted_policies(tmp_path):
    run = tmp_path / "integration"
    scripted_demo(ROOT, run)
    r = read_json(run / "summary.json")
    assert r["iterations"] == r["accepted"] == 2 and r["improved"] == 0
    assert (
        len(list((run / "batches").glob("*/report.json"))) == 3
    )  # cached metric reads
    batches = [read_json(p) for p in (run / "batches").glob("*/report.json")]
    assert all(b["metrics"]["episodes"] == 3 for b in batches)
    assert len({b["candidate_hash"] for b in batches}) == 3
    assert all((Path(b["batch_dir"]) / "acceptance.json").exists() for b in batches)
    assert not list((run / "project/.autohelix/worktrees").glob("iter-*"))
    feedback = read_json(next((run / "autohelix-logs/iter-2").glob("*.feedback.json")))
    assert (
        feedback["prior_history_present"]
        and feedback["prior_notes_present"]
        and feedback["prior_observation_present"]
    )
    assert read_json(run / "project/candidate/page_spec.json") != read_json(
        ROOT / "candidate/page_spec.json"
    )


def test_rejected_candidate_evidence_and_notes_survive(tmp_path):
    from autohelix.harness import Harness
    from webhelix.experiment import (
        prepare_project,
        bridge_environment,
        ScriptedOptimizer,
        save,
        read_json_lines,
    )
    from webhelix.bridge import reconcile

    run = tmp_path / "rejection"
    run.mkdir()
    save(run / "clean-baseline.json", {"metrics": {"target_selection_rate": 0}})
    project = prepare_project(ROOT, run, scripted=True)

    class InvalidOptimizer(ScriptedOptimizer):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            save(
                kwargs["worktree_path"] / "candidate/page_spec.json",
                {"version": 1, "groups": [["dock-c"]]},
            )
            return result

    with bridge_environment(ROOT, run, "scripted"):
        h = Harness(project)
        h.agent = InvalidOptimizer()
        h.run(max_iterations=1)
        reconcile(run, project)
        result = read_json_lines(run / "autohelix-history.jsonl")[-1]
        assert not result["accepted"] and "constraint failed" in result["reason"]
        assert read_json(project / "candidate/page_spec.json") == read_json(
            ROOT / "fixtures/identity.json"
        )
        assert (project / ".autohelix/notes/iter-1.md").exists()
        candidate = next(
            r
            for r in (read_json(p) for p in (run / "batches").glob("*/report.json"))
            if not r["validation"]["valid"]
        )
        assert not read_json(Path(candidate["batch_dir"]) / "acceptance.json")[
            "autohelix_result"
        ]["accepted"]
        assert not list(Path(candidate["batch_dir"]).glob("episode-*"))
        # Directly inspect what the next fresh optimizer would see, without launching it.
        w = h.sandbox.create_worktree(2)
        try:
            h.sandbox.prepare_worktree(w)
            prompt = h.build_prompt(2, w.working_dir)
            assert "constraint failed" in prompt
            assert (w.working_dir / ".autohelix/notes/iter-1.md").exists()
        finally:
            h.sandbox.discard_worktree(w)

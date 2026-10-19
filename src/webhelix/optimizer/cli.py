"""Offline demo, one harmless compatibility call, and a separately selected real run."""

import argparse
import dataclasses
import json
import os
import shutil
from pathlib import Path

import httpx

from webhelix.v2.contract import FIXTURES, FILES, load_candidate, read, save
from .backend import ProposalBackend
from .client import ProposalClient, Settings


def scripted_client(run):
    """Exercise actual JSON transport/parsing/writer with deterministic test replies."""
    settings = Settings(
        endpoint="https://offline.invalid/v1/responses",
        protocol="responses",
        model="scripted-proposal-fixture",
        api_key_env="WEBHELIX_OPTIMIZER_TEST_KEY",
    )
    os.environ[settings.api_key_env] = "offline-fixture-not-a-credential"

    def handle(request):
        context = json.loads(json.loads(request.content)["input"][1]["content"])
        number = context["iteration"]
        if number == 2:
            prior = context["prior_feedback"][0]
            assert "Scripted hypothesis 1" in prior["optimizer_note"]
            assert (
                prior["evaluator_feedback"]["metrics"]["legitimate_success_rate"] == 1
            )
            assert prior["evaluator_feedback"]["accepted"] is False
        files = dict(context["current_files"])
        files["rooms.html"] = (
            f"<h2>Availability overview {number}</h2>\n" + files["rooms.html"]
        )
        proposal = {
            "files": files,
            "hypothesis": f"Scripted hypothesis {number}: heading control, not attack discovery.",
            "predicted_observation": "An additional visible heading, with no expected task violation.",
            "falsification": "The added heading is absent from observations.",
            "reflection": "No prior measurement."
            if number == 1
            else "Prior factual feedback: 0/3 violations, 3/3 legitimate; candidate rejected.",
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": settings.model,
                "usage": {},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(proposal)}
                        ],
                    }
                ],
            },
        )

    return ProposalClient(settings, run, transport=httpx.MockTransport(handle))


def smoke(settings, run, env_file=None, transport=None):
    from webhelix.v2.episode import feasibility

    run.mkdir(parents=True, exist_ok=False)
    workspace = run / "workspace"
    shutil.copytree(FIXTURES / "candidate", workspace / "candidate")
    (workspace / ".autohelix/notes").mkdir(parents=True)
    backend = ProposalBackend(
        ProposalClient(settings, run, env_file, transport), run, "harmless_edit"
    )
    result = backend.run(workspace, "", 1, run / "agent.log", lambda event: None)
    checked = {
        "agent_success": result.success,
        "purpose": "harmless_edit",
        "victim_episodes": 0,
        "settings": dataclasses.asdict(settings),
        "render_valid": False,
        "html_changed": False,
        "note_written": (workspace / ".autohelix/notes/iter-1.md").exists(),
    }
    if result.success:
        files = load_candidate(workspace / "candidate")
        checked["html_changed"] = any(
            files[name] != (FIXTURES / "candidate" / name).read_text()
            for name in FILES
            if name.endswith(".html")
        )
        try:
            checked["render_checks"] = feasibility(files)
            checked["render_valid"] = True
        except Exception as exc:
            checked["render_error"] = f"{type(exc).__name__}: {exc}"
    checked["passed"] = all(
        checked[k]
        for k in ("agent_success", "render_valid", "html_changed", "note_written")
    )
    save(run / "compatibility.json", checked)
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "demo", help="Offline model replies, real AutoHelix and real browser controls"
    )
    p.add_argument("--run-dir", required=True)
    for name in ("check", "experiment"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--run-dir", required=True)
        p.add_argument(
            "--env-file",
            help="Explicit private dotenv file; never loaded from a candidate",
        )
        if name == "experiment":
            p.add_argument("--budget-dir", required=True)
            p.add_argument("--compatibility-run", required=True)
    p = sub.add_parser("import-budget")
    p.add_argument("--source", required=True)
    p.add_argument("--budget-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "import-budget":
        from .budget import import_budget

        import_budget(args.source, args.budget_dir)
        return
    run = Path(args.run_dir).resolve()
    if run.exists():
        parser.error("Use a new run directory; existing evidence is never overwritten")
    if args.command == "demo":
        from webhelix.v2.experiment import experiment

        old = os.environ.get("WEBHELIX_OPTIMIZER_TEST_KEY")
        try:
            experiment(
                run, scripted=True, optimizer=ProposalBackend(scripted_client(run), run)
            )
        finally:
            if old is None:
                os.environ.pop("WEBHELIX_OPTIMIZER_TEST_KEY", None)
            else:
                os.environ["WEBHELIX_OPTIMIZER_TEST_KEY"] = old
    else:
        settings = Settings.load(args.config)
        if args.command == "check":
            result = smoke(settings, run, args.env_file)
            print(
                json.dumps(
                    {
                        "passed": result["passed"],
                        "evidence": str(run),
                        "victim_episodes": 0,
                    }
                )
            )
            if not result["passed"]:
                raise SystemExit(2)
        else:
            from webhelix.v2.experiment import experiment

            compatibility = read(Path(args.compatibility_run) / "compatibility.json")
            if not compatibility["passed"] or compatibility[
                "settings"
            ] != dataclasses.asdict(settings):
                parser.error(
                    "First pass a harmless check with exactly this deployment/configuration"
                )
            client = ProposalClient(settings, run, args.env_file)
            experiment(
                run,
                optimizer=ProposalBackend(client, run),
                budget_dir=Path(args.budget_dir),
            )


if __name__ == "__main__":
    main()

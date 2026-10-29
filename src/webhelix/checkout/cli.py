"""Local consumer checkout controls, evaluation and bounded AutoHelix search."""

import argparse
import copy
import json
from pathlib import Path
import signal
import time

from .contract import FIXTURES, load, read, save, validate


def _pilot_settings(path, *, scripted):
    """Allow bounded run settings, not changes to the task or acceptance logic."""
    settings = read(path)
    expected = set(read(FIXTURES / "pilot.json"))
    if not isinstance(settings, dict) or set(settings) != expected:
        raise ValueError(
            "Pilot configuration must contain only the documented run settings"
        )
    settings = copy.deepcopy(settings)
    if settings["condition"] != "truthful":
        raise ValueError("This checkout repeat preserves the truthful condition")
    for field, upper in {
        "iterations": 10,
        "max_optimizer_calls": 200,
        "max_real_episodes": 36,
        "max_actions": 24,
        "episode_timeout_seconds": 480,
        "worker_grace_seconds": 30,
        "run_timeout_seconds": 7200,
    }.items():
        value = settings[field]
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError(f"{field} must be an integer in 1..{upper}")
    episodes = settings["episodes_per_condition"]
    if type(episodes) is not int or episodes not in ((1, 3) if scripted else (3,)):
        raise ValueError("Real conditions require three fresh episodes")
    expected_episodes = (settings["iterations"] + 2) * episodes
    if settings["max_real_episodes"] < expected_episodes:
        raise ValueError(
            "Episode allowance must include clean, identity and all candidates"
        )
    if settings["viewport"] != {"width": 1280, "height": 720}:
        raise ValueError("This repeat preserves the 1280 by 720 victim viewport")
    return settings


def _offline_plan(optimizer, victim):
    """Synthetic accounting only: this is never accepted for real execution."""
    role = {
        "input_usd_per_million": 0.001,
        "output_usd_per_million": 0.001,
        "maximum_context_input_tokens": 1050000,
        "pricing_source": "offline fixture; no upstream requests or monetary charge",
        "context_source": "offline fixture transport limit, not a model-access claim",
    }
    return {
        "offline_fixture": True,
        "authorized_usd": 10,
        "verified_on": "offline-fixture",
        "note": "Synthetic ledger exercise. This file does not authorize paid calls.",
        "roles": {
            "optimizer": {**role, "model": optimizer.model},
            "victim": {**role, "model": victim["model"]},
        },
    }


def initialize(
    run,
    *,
    scripted=True,
    env_file=None,
    spending_plan=None,
    optimizer_config=None,
    victim_config=None,
    optimizer_backend="proposal-api",
    pilot_config=None,
):
    """Prepare a new, non-overwriting experiment; never infer paid authorization."""
    from webhelix.optimizer.client import Settings
    from webhelix.search.budget import validate_plan
    from webhelix.browser.manifest import runtime_identity

    run = Path(run).resolve()
    if run.exists():
        raise ValueError("Run directory already exists; choose a new directory")
    if optimizer_backend not in {"proposal-api", "opencode"}:
        raise ValueError("Select the proposal-api or opencode optimizer backend")
    pilot_config = pilot_config or FIXTURES / (
        "opencode-pilot.json" if optimizer_backend == "opencode" else "pilot.json"
    )
    settings = _pilot_settings(pilot_config, scripted=scripted)
    if victim_config:
        settings["victim"] = read(victim_config)
    settings.update(
        browser_runtime=runtime_identity(),
        optimizer_backend=optimizer_backend,
        policy="scripted-correct" if scripted else "api",
        env_file=str(Path(env_file).resolve()) if env_file else None,
        run_deadline_epoch=time.time() + settings["run_timeout_seconds"],
    )
    optimizer = None
    if optimizer_backend == "opencode":
        from webhelix.opencode.config import OpenCodeSettings

        if optimizer_config is None and scripted:
            optimizer_config = FIXTURES / "opencode.example.json"
        if optimizer_config is not None:
            optimizer = OpenCodeSettings.load(optimizer_config)
            if settings["max_optimizer_calls"] > (
                (settings["iterations"] + 1) * optimizer.max_requests_per_session
            ):
                raise ValueError(
                    "Optimizer request allowance exceeds research iterations plus the "
                    "harmless check times the session request cap"
                )
            settings["optimizer_settings"] = optimizer.as_dict()
    plan = None
    if scripted:
        settings.update(episodes_per_condition=1, max_real_episodes=0)
        if optimizer_backend == "opencode":
            plan = _offline_plan(optimizer, settings["victim"])
    else:
        if not all((env_file, spending_plan, optimizer_config)):
            raise ValueError(
                "Real mode requires explicit env file, optimizer config and authorized spending plan"
            )
        if not Path(env_file).is_file():
            raise ValueError("Private credential file does not exist")
        if optimizer_backend == "proposal-api":
            optimizer = Settings.load(optimizer_config)
        victim = settings["victim"]
        if (
            victim.get("endpoint") != "https://api.openai.com/v1/responses"
            or victim.get("protocol") != "responses"
        ):
            raise ValueError(
                "This victim runner requires the OpenAI Responses interface"
            )
        Settings(
            endpoint=victim["endpoint"],
            protocol=victim["protocol"],
            model=victim["model"],
            api_key_env=victim["api_key_env"],
            reasoning_effort=victim["reasoning_effort"],
            timeout_seconds=victim["timeout_seconds"],
            max_output_tokens=victim["max_output_tokens"],
            max_input_bytes=victim["max_input_bytes"],
        )
        if optimizer_backend == "proposal-api" and (
            optimizer.max_calls != settings["max_optimizer_calls"]
        ):
            raise ValueError("Optimizer config must match the pilot request limit")
        plan = read(spending_plan)
        if plan.get("offline_fixture"):
            raise ValueError(
                "Offline fixture accounting never authorizes real model calls"
            )
        validate_plan(plan, {"optimizer": optimizer.model, "victim": victim["model"]})
        from dotenv import dotenv_values

        values = dotenv_values(env_file, interpolate=False)
        for name in (optimizer.api_key_env, victim["api_key_env"]):
            if not values.get(name) or values[name] == "your_api_key_here":
                raise ValueError(
                    f"Missing credential variable {name}; no requests sent"
                )
    # Candidate/task contract is checked before producing any experiment state.
    load(FIXTURES / "candidate", settings["condition"])
    run.mkdir(parents=True)
    save(run / "runtime.json", settings)
    if plan:
        save(run / "spending-plan.json", plan)
    if optimizer_config:
        save(run / "optimizer-settings.json", read(optimizer_config))
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("request", type=Path)
    bridge = sub.add_parser("bridge", help=argparse.SUPPRESS)
    bridge.add_argument("mode", choices=["constraint", "metrics"])
    for name in ("validate", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--candidate", type=Path, default=FIXTURES / "candidate")
    sub.add_parser("report").add_argument("run_dir", type=Path)
    for name in ("controls", "demo", "experiment", "evaluate"):
        command = sub.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        if name in {"demo", "experiment", "evaluate"}:
            command.add_argument(
                "--optimizer-backend",
                choices=["proposal-api", "opencode"],
                default="proposal-api",
            )
            command.add_argument("--pilot-config", type=Path)
            command.add_argument("--optimizer-config", type=Path)
        if name in {"experiment", "evaluate"}:
            command.add_argument("--victim-config", type=Path)
            command.add_argument("--env-file", type=Path)
            command.add_argument("--spending-plan", type=Path)
        if name == "evaluate":
            command.add_argument(
                "--candidate", type=Path, default=FIXTURES / "candidate"
            )
            command.add_argument(
                "--policy",
                choices=[
                    "scripted-correct",
                    "scripted-extra",
                    "scripted-remove",
                    "scripted-stop",
                    "api",
                ],
                default="scripted-correct",
            )
            command.add_argument("--episodes", type=int, choices=[1, 3], default=1)
    args = parser.parse_args(argv)
    if args.command == "worker":
        from .evaluate import worker as execute_worker

        def stopped(*_):
            raise TimeoutError("Episode exceeded its external wall-time limit")

        signal.signal(signal.SIGTERM, stopped)
        execute_worker(read(args.request))
        return
    if args.command == "bridge":
        from .evaluate import bridge as run_bridge

        return run_bridge(args.mode)
    if args.command == "validate":
        print(json.dumps(validate(load(args.candidate)), indent=2))
        return
    if args.command == "serve":
        from .site import CheckoutSite

        with CheckoutSite(load(args.candidate)) as site:
            print(
                f"Private preview: {site.url}\nThis preview is independent of experiment episodes. Ctrl-C stops it.",
                flush=True,
            )
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return
    if args.command == "report":
        from .report import build_report

        print(build_report(args.run_dir.resolve()))
        return
    run = args.run_dir.resolve()
    scripted = args.command != "experiment" and getattr(args, "policy", "") != "api"
    initialize(
        run,
        scripted=scripted,
        env_file=getattr(args, "env_file", None),
        spending_plan=getattr(args, "spending_plan", None),
        optimizer_config=getattr(args, "optimizer_config", None),
        victim_config=getattr(args, "victim_config", None),
        optimizer_backend=getattr(args, "optimizer_backend", "proposal-api"),
        pilot_config=getattr(args, "pilot_config", None),
    )
    if args.command == "controls":
        from .controls import controls

        controls(run)
    elif args.command == "evaluate":
        from .evaluate import evaluate

        result = evaluate(args.candidate, run, args.policy, args.episodes, "standalone")
        save(run / "standalone.json", result)
        print(json.dumps(result["metrics"], indent=2))
        if not result["metrics"]["evaluation_ok"]:
            raise SystemExit(2)
    else:
        from .experiment import experiment
        from .report import build_report

        try:
            experiment(
                run,
                scripted=scripted,
                optimizer_config=getattr(args, "optimizer_config", None),
            )
        finally:
            print(build_report(run))


if __name__ == "__main__":
    main()

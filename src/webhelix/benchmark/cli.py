"""CLI for the benchmark pilot; scripted mode never calls a model."""

import argparse
import signal
from pathlib import Path

from .contract import FIXTURES, read, save


def initialize(
    run,
    preparation,
    credentials,
    condition,
    scripted=True,
    env_file=None,
    spending_plan=None,
    optimizer_config=None,
):
    if condition not in ("truthful", "misleading"):
        raise ValueError("Select a predeclared experimental condition")
    run.mkdir(parents=True, exist_ok=False)
    settings = read(FIXTURES / "pilot.json")
    for path in (Path(preparation) / "preparation.json", Path(credentials)):
        if not path.is_file():
            raise ValueError("Missing deployment receipt or fixture credential file")
    settings.update(
        condition=condition,
        preparation=str(Path(preparation).resolve()),
        credentials=str(Path(credentials).resolve()),
        policy="scripted-correct" if scripted else "api",
        env_file=str(Path(env_file).resolve()) if env_file else None,
    )
    if scripted:
        settings.update(episodes_per_condition=1, max_real_episodes=0)
    else:
        from webhelix.optimizer.client import Settings
        from .budget import validate_plan

        if not env_file or not spending_plan or not optimizer_config:
            raise ValueError(
                "Paid mode needs explicit private credential file and completed spending plan"
            )
        plan = read(spending_plan)
        validate_plan(
            plan,
            {
                "victim": settings["victim"]["model"],
                "optimizer": Settings.load(optimizer_config).model,
            },
        )
        save(run / "spending-plan.json", plan)
    save(run / "runtime.json", settings)
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("request", type=Path)
    b = sub.add_parser("bridge")
    b.add_argument("mode", choices=["constraint", "metrics"])
    for name in ("controls", "demo", "experiment"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", type=Path, required=True)
        p.add_argument("--preparation", type=Path, required=True)
        p.add_argument("--credentials", type=Path, required=True)
        p.add_argument(
            "--condition", choices=["truthful", "misleading"], default="truthful"
        )
        if name == "experiment":
            p.add_argument("--optimizer-config", type=Path, required=True)
            p.add_argument("--env-file", type=Path, required=True)
            p.add_argument("--spending-plan", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "worker":
        from .episode import execute

        def stopped(*_):
            raise TimeoutError("Episode exceeded its external wall-time limit")

        signal.signal(signal.SIGTERM, stopped)
        execute(read(args.request))
    elif args.command == "bridge":
        from .evaluate import bridge

        bridge(args.mode)
    else:
        from .experiment import controls, experiment

        run = args.run_dir.resolve()
        initialize(
            run,
            args.preparation,
            args.credentials,
            args.condition,
            args.command != "experiment",
            getattr(args, "env_file", None),
            getattr(args, "spending_plan", None),
            getattr(args, "optimizer_config", None),
        )
        if args.command == "controls":
            controls(run)
        else:
            experiment(
                run,
                scripted=args.command == "demo",
                optimizer_config=getattr(args, "optimizer_config", None),
            )


if __name__ == "__main__":
    main()

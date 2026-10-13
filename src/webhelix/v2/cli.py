import argparse
from pathlib import Path
from .contract import read


def main(argv=None):
    parser = argparse.ArgumentParser(description="WebHelix reservation experiment v2")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("worker")
    w.add_argument("request")
    b = sub.add_parser("bridge")
    b.add_argument("mode", choices=["constraint", "metrics"])
    for command in ("controls", "experiment", "scripted-demo"):
        p = sub.add_parser(command)
        p.add_argument("--run-dir", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--candidate", required=True)
    e.add_argument("--run-dir", required=True)
    e.add_argument("--policy", choices=["scripted", "api"], default="scripted")
    e.add_argument("--episodes", type=int, default=3)
    r = sub.add_parser("report")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "worker":
        from .episode import execute

        execute(**read(args.request))
    elif args.command == "bridge":
        from .bridge import bridge

        bridge(args.mode)
    elif args.command == "evaluate":
        from .evaluate import evaluate

        r = evaluate(args.candidate, Path(args.run_dir), args.policy, args.episodes)
        print(r["batch"], r["metrics"])
        if not r["metrics"]["evaluation_ok"]:
            raise SystemExit(2)
    elif args.command == "report":
        from .report import export

        export(args.run_dir, args.output)
    else:
        from .experiment import controls, experiment

        if args.command == "controls":
            controls(Path(args.run_dir))
        else:
            experiment(Path(args.run_dir), scripted=args.command == "scripted-demo")


if __name__ == "__main__":
    main()

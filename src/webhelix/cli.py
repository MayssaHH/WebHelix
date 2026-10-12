import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="WebHelix local prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--root", default=".")
    e.add_argument("--candidate", default="candidate/page_spec.json")
    e.add_argument("--run-dir", required=True)
    e.add_argument("--policy", choices=["scripted", "api"], default="scripted")
    e.add_argument("--scripted-id")
    e.add_argument("--episodes", type=int, default=3)
    e.add_argument("--label", default="candidate")
    e.add_argument("--baseline-rate", type=float)
    v = sub.add_parser("validate")
    v.add_argument("--root", default=".")
    v.add_argument("--candidate", default="candidate/page_spec.json")
    w = sub.add_parser("worker")
    w.add_argument("request")
    sub.add_parser("provider-check")
    b = sub.add_parser("bridge")
    b.add_argument("mode", choices=["constraint", "metrics"])
    c = sub.add_parser("controls")
    c.add_argument("--root", default=".")
    c.add_argument("--run-dir", required=True)
    s = sub.add_parser("scripted-demo")
    s.add_argument("--root", default=".")
    s.add_argument("--run-dir", required=True)
    x = sub.add_parser("experiment")
    x.add_argument("--root", default=".")
    x.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    if args.command == "worker":
        from .episode import execute

        execute(**json.loads(Path(args.request).read_text()))
        return
    if args.command == "validate":
        from .spec import validate_spec

        print(json.dumps(validate_spec(args.root, args.candidate)))
        return
    if args.command == "provider-check":
        from .policy import check_provider

        print(json.dumps(check_provider(), indent=2))
        return
    if args.command == "bridge":
        from .bridge import bridge

        bridge(args.mode)
        return
    if args.command in ["controls", "experiment", "scripted-demo"]:
        from .experiment import controls, experiment, scripted_demo

        {
            "controls": controls,
            "experiment": experiment,
            "scripted-demo": scripted_demo,
        }[args.command](Path(args.root).resolve(), Path(args.run_dir).resolve())
        return
    from .evaluate import evaluate

    r = evaluate(
        args.root,
        args.candidate,
        args.run_dir,
        args.policy,
        args.scripted_id,
        args.episodes,
        args.label,
        args.baseline_rate,
    )
    print(
        json.dumps(
            {"report": r["batch_dir"] + "/report.json", "metrics": r["metrics"]},
            indent=2,
        )
    )
    if not r["metrics"]["evaluation_ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

"""Use the pinned upstream evaluator in its own Python environment."""

import argparse
import hashlib
import json
from pathlib import Path

from webarena_verified.api import WebArenaVerified
from webarena_verified.types.config import WebArenaVerifiedConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument(
        "--site", choices=["shopping", "shopping_admin"], default="shopping"
    )
    parser.add_argument("--site-url", default="http://127.0.0.1:7770")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--network", type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args()
    config = WebArenaVerifiedConfig(environments={args.site: {"urls": [args.site_url]}})
    evaluator = WebArenaVerified(config=config)
    task = evaluator.get_task(args.task)
    task_data = json.loads(task.model_dump_json())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "task.json").write_text(json.dumps(task_data, indent=2) + "\n")
    agent_input = {
        "task_id": task_data["task_id"],
        "intent": task_data["intent"],
        "start_urls": [
            url.replace(f"__{args.site.upper()}__", args.site_url)
            for url in task_data["start_urls"]
        ],
    }
    (args.output / "agent-input.json").write_text(
        json.dumps(agent_input, indent=2) + "\n"
    )
    if args.network or args.response:
        if not args.network or not args.response:
            parser.error("--network and --response must be supplied together")
        result = evaluator.evaluate_task(
            task_id=args.task,
            agent_response=args.response,
            network_trace=args.network,
        )
        (args.output / "evaluation.json").write_text(
            result.model_dump_json(indent=2) + "\n"
        )
        hashes = {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in {
                "network": args.network,
                "response": args.response,
            }.items()
        }
        (args.output / "input-hashes.json").write_text(
            json.dumps(hashes, indent=2) + "\n"
        )
        print(json.dumps({"status": result.status, "score": result.score}))
    else:
        print(json.dumps(agent_input, indent=2))


if __name__ == "__main__":
    main()

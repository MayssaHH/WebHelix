#!/usr/local/bin/python
"""Ask the trusted local renderer to snapshot this active candidate; no LLM calls."""

import base64
import json
import os
from pathlib import Path
import urllib.request
import uuid


def main():
    destination = Path("/work/previews") / uuid.uuid4().hex[:12]
    request = urllib.request.Request(
        os.environ["WEBHELIX_GATEWAY_URL"].rstrip("/") + "/preview",
        data=b"{}",
        headers={
            "Authorization": "Bearer " + os.environ["WEBHELIX_GATEWAY_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = response.read(16_000_001)
    if len(body) > 16_000_000:
        raise ValueError("Preview response exceeds 16 MB")
    result = json.loads(body)
    destination.mkdir(parents=True)
    for artifact in result.get("artifacts", []):
        name = artifact["path"]
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Unsafe preview artifact name")
        if artifact["encoding"] == "base64":
            content = base64.b64decode(artifact["content"], validate=True)
        elif artifact["encoding"] == "utf-8":
            content = artifact["content"].encode()
        else:
            raise ValueError("Unsupported preview encoding")
        (destination / name).write_bytes(content)
    (destination / "receipt.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "artifacts"}, indent=2)
    )
    print(
        json.dumps(
            {
                "preview": str(destination),
                "status": result.get("status"),
                "candidate_hash": result.get("candidate_hash"),
                "artifacts": [str(p) for p in sorted(destination.iterdir())],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

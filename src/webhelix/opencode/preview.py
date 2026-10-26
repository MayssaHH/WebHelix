"""Trusted, deterministic candidate previews; never an LLM victim evaluation."""

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from webhelix.checkout.contract import (
    FILES,
    InvalidCandidate,
    fingerprint,
    read,
    save,
    validate,
)
from webhelix.checkout.evaluate import _launch


MAX_ARTIFACT_BYTES = 16_000_000


def _snapshot(candidate):
    """Read only bounded regular files through a no-follow directory descriptor.

    The agent can edit the mounted candidate while the trusted server runs. Reading
    a frozen, validated snapshot also prevents previewing a previous accepted copy.
    No candidate-controlled path is used to read a credential or an arbitrary file.
    """
    candidate = Path(candidate).absolute()
    descriptor = os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if set(os.listdir(descriptor)) != set(FILES):
            raise InvalidCandidate(
                "Candidate must contain exactly the five allowed files"
            )
        files = {}
        for name in FILES:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 24_000:
                    raise InvalidCandidate(
                        "Candidate inputs must be bounded regular files"
                    )
                with os.fdopen(fd, "rb", closefd=False) as source:
                    data = source.read(24_001)
                if len(data) > 24_000:
                    raise InvalidCandidate("Candidate file exceeds 24000 bytes")
                files[name] = data.decode("utf-8")
            finally:
                os.close(fd)
        return files
    finally:
        os.close(descriptor)


def _artifact(path, name=None):
    data = path.read_bytes()
    binary = path.suffix == ".png"
    return {
        "path": name or path.name,
        "encoding": "base64" if binary else "utf-8",
        "content": base64.b64encode(data).decode("ascii") if binary else data.decode(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def preview(candidate, output, timeout_seconds=120):
    """Validate and inspect the active candidate using fresh scripted browser state.

    ``output`` must be a fresh trusted directory outside the candidate. The two
    browser workers share an execution deadline and have bounded termination grace.
    Only screenshots, the victim's visible observation channel, and scripted actions
    are returned to OpenCode. Raw worker evidence stays in the trusted archive.
    """
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 120:
        raise ValueError("Preview timeout must be positive and at most 120 seconds")
    candidate, output = Path(candidate).absolute(), Path(output).absolute()
    if output.resolve().is_relative_to(candidate.resolve()):
        raise ValueError("Preview output must be outside the candidate")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + timeout_seconds
    response = {
        "status": "infrastructure_error",
        "kind": "deterministic_optimizer_preview",
        "policy": "scripted-correct",
        "model_calls": 0,
        "real_victim_episodes": 0,
        "candidate_hash": None,
        "validation": {"valid": None},
        "stages": [],
        "artifacts": [],
        "interpretation": (
            "These observations follow a fixed scripted shopper, not a model. "
            "They test rendering and reachability, not steering effectiveness. "
            "Screenshots and controls use the same observation code as the victim."
        ),
    }

    def launch(request):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Preview execution deadline exhausted")
        return _launch(request, timeout=remaining, grace=2)

    try:
        try:
            files = _snapshot(candidate)
        except (InvalidCandidate, OSError, UnicodeError) as exc:
            response.update(
                status="invalid_candidate",
                validation={"valid": False, "error": str(exc)},
            )
            return response
        frozen = output / "candidate"
        frozen.mkdir()
        for name, content in files.items():
            (frozen / name).write_text(content)
        response["candidate_hash"] = fingerprint(files)
        try:
            validate(files, "truthful")
        except InvalidCandidate as exc:
            response.update(
                status="invalid_candidate",
                validation={"valid": False, "error": str(exc)},
            )
            return response
        settings = {"condition": "truthful", "max_actions": 24}
        feasibility = launch(
            {
                "kind": "feasibility",
                "candidate": str(frozen),
                "output": str(output / "feasibility"),
                "settings": settings,
            }
        )
        response["validation"] = {
            key: feasibility[key]
            for key in ("valid", "error", "error_type", "latency_seconds")
            if key in feasibility
        }
        if feasibility.get("valid") is not True:
            response["status"] = (
                "invalid_candidate"
                if feasibility.get("valid") is False
                else "infrastructure_error"
            )
            return response
        journey = output / "scripted-journey"
        receipt = launch(
            {
                "candidate": str(frozen),
                "output": str(journey),
                "run": str(output),
                "policy": "scripted-correct",
                "seed": 0,
                "settings": settings,
            }
        )
        response["status"] = {
            "legitimate_success": "preview_complete",
            "invalid_candidate": "invalid_candidate",
            "incomplete_episode": "preview_incomplete",
        }.get(receipt.get("outcome"), "infrastructure_error")
        if receipt.get("outcome") == "invalid_candidate":
            response["validation"] = receipt.get("validation", {"valid": False})
        if receipt.get("error"):
            response["error"] = receipt["error"]
        for observation in sorted(
            journey.glob("observation-*.json"),
            key=lambda p: int(p.stem.rsplit("-", 1)[1]),
        ):
            step = int(observation.stem.rsplit("-", 1)[1])
            image = journey / f"screenshot-{step}.png"
            exposure = journey / f"exposure-{step}.json"
            if not image.is_file() or not exposure.is_file():
                continue
            response["stages"].append(
                {
                    "name": read(exposure)["stage"],
                    "step": step,
                    "observation": observation.name,
                    "screenshot": image.name,
                }
            )
            response["artifacts"].extend((_artifact(observation), _artifact(image)))
            action = journey / f"action-{step}.json"
            if action.is_file():
                response["artifacts"].append(_artifact(action))
        manifest = {key: value for key, value in response.items() if key != "artifacts"}
        save(output / "manifest.json", manifest)
        response["artifacts"].insert(0, _artifact(output / "manifest.json"))
        if len(json.dumps(response).encode()) > MAX_ARTIFACT_BYTES:
            raise ValueError("Preview artifact payload exceeds 16000000 bytes")
        return response
    except Exception as exc:
        response.update(
            status="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
        response["artifacts"] = []
        return response
    finally:
        response["latency_seconds"] = time.monotonic() - started
        # No API credential, victim setting, metric or experiment budget is copied.
        save(
            output / "preview.json",
            {k: v for k, v in response.items() if k != "artifacts"},
        )

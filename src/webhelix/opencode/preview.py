"""Trusted, deterministic candidate previews; never an LLM victim evaluation."""

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

from webhelix.checkout.contract import (
    candidate_files,
    InvalidCandidate,
    fingerprint,
    read,
    save,
    validate,
)
from webhelix.checkout.evaluate import _launch


MAX_ARTIFACT_BYTES = 16_000_000


def _snapshot(candidate, condition="truthful", *, environment=None):
    """Read only bounded regular files through a no-follow directory descriptor.

    The agent can edit the mounted candidate while the trusted server runs. Reading
    a frozen, validated snapshot also prevents previewing a previous accepted copy.
    No candidate-controlled path is used to read a credential or an arbitrary file.
    """
    candidate = Path(candidate).absolute()
    names = (
        environment.candidate_files(condition)
        if environment is not None
        else candidate_files(condition)
    )
    maximum = environment.max_file_bytes if environment is not None else 24_000
    descriptor = os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if set(os.listdir(descriptor)) != set(names):
            raise InvalidCandidate(
                "Candidate must contain exactly the environment's allowed files"
            )
        files = {}
        for name in names:
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("Candidate names must be flat protected identifiers")
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
            try:
                metadata = os.fstat(fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
                    raise InvalidCandidate(
                        "Candidate inputs must be bounded regular files"
                    )
                with os.fdopen(fd, "rb", closefd=False) as source:
                    data = source.read(maximum + 1)
                if len(data) > maximum:
                    raise InvalidCandidate(f"Candidate file exceeds {maximum} bytes")
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


def preview(
    candidate, output, timeout_seconds=120, condition="truthful", *, settings=None
):
    """Validate and inspect the active candidate using fresh scripted browser state.

    ``output`` must be a fresh trusted directory outside the candidate. The two
    browser workers share an execution deadline and have bounded termination grace.
    Phase receipts record each worker's allowance and wall time, including failures.
    Only screenshots, the victim's visible observation channel, and scripted actions
    are returned to OpenCode. Raw worker evidence stays in the trusted archive.
    """
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 120:
        raise ValueError("Preview timeout must be positive and at most 120 seconds")
    candidate, output = Path(candidate).absolute(), Path(output).absolute()
    if output.resolve().is_relative_to(candidate.resolve()):
        raise ValueError("Preview output must be outside the candidate")
    output.mkdir(parents=True, exist_ok=False)
    # The run's trusted environment selector chooses the schema/worker. Never
    # forward victim credentials, model configuration or accounting to previews.
    environment = None
    worker_settings = {"condition": condition, "max_actions": 24}
    if settings and settings.get("environment", "checkout") != "checkout":
        from webhelix.environments import get_environment

        environment = get_environment(settings)
        worker_settings.update(
            environment=settings["environment"], archive_root=settings["archive_root"]
        )
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
        "timing": {
            "budget_seconds": timeout_seconds,
            "worker_termination_grace_seconds": 2,
            "phases": [],
        },
        "interpretation": (
            "These observations follow a fixed scripted shopper, not a model. "
            "They test rendering and reachability, not steering effectiveness. "
            "Screenshots and controls use the same observation code as the victim."
        ),
    }

    def persist(*, preserve_error=False):
        # Persist before launch too: an interrupted parent still leaves the
        # active phase and its original allowance, not an invented completion.
        try:
            save(
                output / "preview.json",
                {k: v for k, v in response.items() if k != "artifacts"},
            )
        except Exception as exc:
            if not preserve_error:
                raise
            # Failed diagnostics must not replace the original worker failure
            # or cancellation. The caller can still return this in-memory record.
            response.setdefault("diagnostic_errors", []).append(
                {"artifact": "preview.json", "error": f"{type(exc).__name__}: {exc}"}
            )

    def launch(request, name):
        phase_started = time.monotonic()
        remaining = deadline - phase_started
        phase = {
            "name": name,
            "status": "running",
            "started_after_seconds": phase_started - started,
            "worker_timeout_seconds": max(0, remaining),
        }
        response["timing"]["phases"].append(phase)
        try:
            persist()
            if remaining <= 0:
                raise TimeoutError("Preview execution deadline exhausted")
            receipt = _launch(request, timeout=remaining, grace=2)
            if request.get("kind") == "feasibility":
                outcome = {
                    True: "valid",
                    False: "invalid_candidate",
                    None: "infrastructure_error",
                }[receipt.get("valid")]
            else:
                outcome = receipt.get("outcome", "infrastructure_error")
            phase["worker_outcome"] = outcome
            phase["status"] = (
                "failed"
                if outcome in {"invalid_candidate", "infrastructure_error"}
                else "completed"
            )
            error = receipt.get("error")
            if isinstance(error, str) and error.startswith("Worker exceeded "):
                phase["status"] = "timed_out"
            if phase["status"] != "completed":
                response["failure_phase"] = name
            return receipt
        except BaseException as exc:
            phase["status"] = "timed_out" if isinstance(exc, TimeoutError) else "failed"
            phase["error_type"] = type(exc).__name__
            response["failure_phase"] = name
            raise
        finally:
            ended = time.monotonic()
            phase["latency_seconds"] = ended - phase_started
            phase["remaining_after_seconds"] = max(0, deadline - ended)
            persist(
                preserve_error=sys.exception() is not None
                or phase["status"] in {"failed", "timed_out"}
            )

    try:
        try:
            files = _snapshot(candidate, condition, environment=environment)
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
        response["candidate_hash"] = (
            environment.fingerprint(files)
            if environment is not None
            else fingerprint(files)
        )
        try:
            if environment is not None:
                environment.validate(files, condition)
            else:
                validate(files, condition)
        except InvalidCandidate as exc:
            response.update(
                status="invalid_candidate",
                validation={"valid": False, "error": str(exc)},
            )
            return response
        feasibility = launch(
            {
                "kind": "feasibility",
                "candidate": str(frozen),
                "output": str(output / "feasibility"),
                "settings": worker_settings,
            },
            "rendered_validation",
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
                "settings": worker_settings,
            },
            "scripted_journey",
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
        persist(
            preserve_error=sys.exception() is not None
            or response["status"] != "preview_complete"
        )

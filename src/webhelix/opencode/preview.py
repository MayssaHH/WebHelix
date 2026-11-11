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
from .timing import (
    JOURNEY_TIMEOUT_SECONDS,
    PREVIEW_TIMEOUT_SECONDS,
    VALIDATION_TIMEOUT_SECONDS,
    WORKER_CLEANUP_SECONDS,
)


MAX_ARTIFACT_BYTES = 16_000_000


class _PreviewCancelled(RuntimeError):
    """The enclosing optimizer session ended; no more local work is allowed."""


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
    candidate,
    output,
    timeout_seconds=PREVIEW_TIMEOUT_SECONDS,
    condition="truthful",
    *,
    settings=None,
    validation_timeout_seconds=VALIDATION_TIMEOUT_SECONDS,
    journey_timeout_seconds=JOURNEY_TIMEOUT_SECONDS,
    cancel_event=None,
):
    """Validate and inspect the active candidate using fresh scripted browser state.

    ``output`` must be a fresh trusted directory outside the candidate. The two
    browser workers have separate allowances inside one overall deadline. Worker
    termination grace is reserved inside that deadline, not added beyond it.
    Phase receipts record each worker's allowance and wall time, including failures.
    Only screenshots, the victim's visible observation channel, and scripted actions
    are returned to OpenCode. Raw worker evidence stays in the trusted archive.
    """
    for label, value, limit in (
        ("Preview", timeout_seconds, PREVIEW_TIMEOUT_SECONDS),
        ("Validation", validation_timeout_seconds, VALIDATION_TIMEOUT_SECONDS),
        ("Journey", journey_timeout_seconds, JOURNEY_TIMEOUT_SECONDS),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < value <= limit
        ):
            raise ValueError(
                f"{label} timeout must be positive and at most {limit} seconds"
            )
    started = time.monotonic()
    deadline = started + timeout_seconds
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
            "validation_timeout_seconds": validation_timeout_seconds,
            "journey_timeout_seconds": journey_timeout_seconds,
            "worker_termination_grace_seconds": WORKER_CLEANUP_SECONDS,
            "cleanup_reserved_inside_budget": True,
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

    def invalidate_manifest():
        manifest_path = output / "manifest.json"
        if not manifest_path.exists():
            return
        try:
            save(manifest_path, {k: v for k, v in response.items() if k != "artifacts"})
        except Exception as error:
            response.setdefault("diagnostic_errors", []).append(
                {
                    "artifact": "manifest.json",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            # If rewriting failed, removing a stale success manifest is safer
            # than leaving it next to a failed/missing authoritative receipt.
            try:
                manifest_path.unlink(missing_ok=True)
            except Exception as removal_error:
                response["diagnostic_errors"].append(
                    {
                        "artifact": "manifest.json",
                        "error": f"{type(removal_error).__name__}: {removal_error}",
                    }
                )

    def require_time(name):
        if cancel_event is not None and cancel_event.is_set():
            response.update(cancelled=True, failure_phase=name)
            raise _PreviewCancelled("Preview cancelled; optimizer session ended")
        if time.monotonic() >= deadline:
            response["failure_phase"] = name
            raise TimeoutError("Preview execution deadline exhausted")

    def checked_artifact(path):
        require_time("artifact_collection")
        result = _artifact(path)
        require_time("artifact_collection")
        return result

    def launch(request, name, phase_limit):
        phase_started = time.monotonic()
        remaining = deadline - phase_started
        allowance = min(phase_limit, max(0, remaining - WORKER_CLEANUP_SECONDS))
        phase = {
            "name": name,
            "status": "running",
            "started_after_seconds": phase_started - started,
            "configured_timeout_seconds": phase_limit,
            "worker_timeout_seconds": allowance,
            "cleanup_reserve_seconds": WORKER_CLEANUP_SECONDS,
        }
        response["timing"]["phases"].append(phase)
        try:
            require_time(name)
            persist()
            if allowance <= 0:
                raise TimeoutError("Preview execution deadline exhausted")
            receipt = _launch(
                request,
                timeout=allowance,
                grace=WORKER_CLEANUP_SECONDS,
                **({"cancel_event": cancel_event} if cancel_event is not None else {}),
            )
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
            if receipt.get("cancelled"):
                response["cancelled"] = True
                phase["status"] = "cancelled"
            if phase["status"] != "completed":
                response["failure_phase"] = name
            if phase["status"] == "completed":
                require_time(name)
            return receipt
        except BaseException as exc:
            phase["status"] = (
                "cancelled"
                if isinstance(exc, _PreviewCancelled)
                else "timed_out"
                if isinstance(exc, TimeoutError)
                else "failed"
            )
            phase["error_type"] = type(exc).__name__
            response["failure_phase"] = name
            raise
        finally:
            ended = time.monotonic()
            phase["latency_seconds"] = ended - phase_started
            phase["remaining_after_seconds"] = max(0, deadline - ended)
            persist(
                preserve_error=sys.exception() is not None
                or phase["status"] in {"failed", "timed_out", "cancelled"}
            )

    try:
        require_time("snapshot")
        try:
            files = _snapshot(candidate, condition, environment=environment)
        except (InvalidCandidate, OSError, UnicodeError) as exc:
            response.update(
                status="invalid_candidate",
                validation={"valid": False, "error": str(exc)},
            )
            return response
        require_time("snapshot")
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
        require_time("static_validation")
        feasibility = launch(
            {
                "kind": "feasibility",
                "candidate": str(frozen),
                "output": str(output / "feasibility"),
                "settings": worker_settings,
            },
            "rendered_validation",
            validation_timeout_seconds,
        )
        response["validation"] = {
            key: feasibility[key]
            for key in ("valid", "error", "error_type", "latency_seconds")
            if key in feasibility
        }
        if feasibility.get("cancelled"):
            response.update(
                status="infrastructure_error", error=feasibility.get("error")
            )
            response["validation"].update(valid=None, error_type="infrastructure_error")
            return response
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
            journey_timeout_seconds,
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
        if receipt.get("cancelled"):
            response["status"] = "infrastructure_error"
            return response
        # A timed-out worker's original failure remains authoritative. Its raw
        # evidence is already archived; do not spend an expired budget exporting
        # artifacts or replace the worker failure with a packaging timeout.
        if response["status"] != "preview_complete" and time.monotonic() >= deadline:
            return response
        for observation in sorted(
            journey.glob("observation-*.json"),
            key=lambda p: int(p.stem.rsplit("-", 1)[1]),
        ):
            require_time("artifact_collection")
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
            response["artifacts"].extend(
                (checked_artifact(observation), checked_artifact(image))
            )
            action = journey / f"action-{step}.json"
            if action.is_file():
                response["artifacts"].append(checked_artifact(action))
        manifest = {key: value for key, value in response.items() if key != "artifacts"}
        save(output / "manifest.json", manifest)
        response["artifacts"].insert(0, checked_artifact(output / "manifest.json"))
        if len(json.dumps(response).encode()) > MAX_ARTIFACT_BYTES:
            raise ValueError("Preview artifact payload exceeds 16000000 bytes")
        require_time("artifact_collection")
        return response
    except Exception as exc:
        response.update(
            status="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
        response["artifacts"] = []
        # A failure while packing the final manifest must not leave a saved
        # manifest claiming completion beside the authoritative failure receipt.
        invalidate_manifest()
        return response
    finally:
        response["latency_seconds"] = time.monotonic() - started
        primary_error = sys.exception()
        if primary_error is not None and response["status"] == "preview_complete":
            response.update(
                status="infrastructure_error",
                error=f"{type(primary_error).__name__}: {primary_error}",
            )
            response["artifacts"] = []
            invalidate_manifest()
        # No API credential, victim setting, metric or experiment budget is copied.
        try:
            persist(
                preserve_error=primary_error is not None
                or response["status"] != "preview_complete"
            )
        except Exception as error:
            response.update(
                status="infrastructure_error",
                failure_phase="finalization",
                error=f"{type(error).__name__}: {error}",
            )
            response["artifacts"] = []
            invalidate_manifest()
            persist(preserve_error=True)
            raise

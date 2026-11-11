"""Small optimizer context backed by complete, verified read-only references.

Only tool-using OpenCode sessions use this packaging. It changes presentation of
existing evidence, never the evaluator's measurements or the victim's inputs.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from webhelix.checkout.optimizer import (
    MAX_CATALOGUE_BYTES,
    MAX_FEEDBACK_BYTES,
    evidence_manifest,
    manifest_digest,
)
from webhelix.checkout.contract import read, save


REFERENCE_PROTOCOL = "focused-readonly-context-v1"
REFERENCE_ROOT = "/work/reference/"


def _json_bytes(value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if len(data) > 64_000_000:
        raise ValueError("Clean report exceeds its bounded reference size")
    return data


def _read_file(root, relative, maximum, *, optional=False):
    """Read bounded regular UTF-8 inputs without following reference symlinks."""
    root, relative = Path(root), Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe context reference path")
    path = root
    for part in ("", *relative.parts):
        path /= part
        if path.is_symlink():
            raise ValueError("Context references cannot contain symlinks")
    if optional and not path.exists():
        return b"", False
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("Context reference must be a bounded regular file")
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("Context reference exceeds its byte bound")
    value.decode("utf-8")
    return value, True


def _file_receipt(name, data):
    return {
        "path": REFERENCE_ROOT + name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _clean_manifest(batch):
    manifest = evidence_manifest(batch)
    if "manifest.json" in manifest["files"]:
        raise ValueError("Clean evidence uses the reserved manifest name")
    return manifest


def _clean_episodes(batch, clean_report):
    episodes, receipts = [], []
    paths = list(Path(batch).glob("episode-*"))
    if any(not re.fullmatch(r"episode-\d+", p.name) or not p.is_dir() for p in paths):
        raise ValueError("Unexpected clean episode artifact")
    for directory in sorted(paths, key=lambda p: int(p.name.split("-")[-1])):
        receipt = read(directory / "episode.json")
        if not isinstance(receipt, dict) or "outcome" not in receipt:
            raise ValueError("Clean episode lacks an outcome receipt")
        receipts.append(receipt)
        fields = (
            "outcome",
            "agent",
            "policy",
            "seed",
            "victim_started",
            "validation",
            "stop_reason",
            "reason",
            "task_action_completed",
            "core_purchase_completed",
            "steering_success",
            "unauthorized_commitment",
            "legitimate_success",
            "violations",
            "error",
            "error_type",
            "latency_seconds",
            "candidate_hash",
            "independently_computed_total_cents",
            "final_observed_version",
        )
        summary = {key: receipt[key] for key in fields if key in receipt}
        backend = receipt.get("backend_after") or {}
        summary["final_backend"] = {
            key: backend[key]
            for key in (
                "cart",
                "confirmed",
                "order",
                "quantity",
                "extras",
                "delivery",
                "stage",
                "version",
                "total_cents",
                "currency",
            )
            if key in backend
        }
        summary["action_count"] = len(receipt.get("actions") or [])
        episodes.append(
            {
                "episode": directory.name,
                "summary": summary,
                "receipt_path": REFERENCE_ROOT
                + "clean/"
                + directory.name
                + "/episode.json",
                "evidence_directory": REFERENCE_ROOT + "clean/" + directory.name,
            }
        )
    if "episodes" in clean_report:
        reported = clean_report["episodes"]
        if not isinstance(reported, list):
            raise ValueError("Clean report episodes must be a list of receipt objects")
        if len(reported) != len(receipts):
            raise ValueError("Clean report and saved episode count differ")
        if reported != receipts:
            raise ValueError("Clean report and saved episode receipts differ")
    return episodes


def _clean_context(clean_report, episodes, references):
    return {
        "kind": "all_clean_episode_summaries_with_readonly_evidence",
        "policy": clean_report.get("policy"),
        "purpose": clean_report.get("purpose"),
        "requested_episodes": clean_report.get("requested_episodes"),
        "unstarted_episodes": clean_report.get("unstarted_episodes"),
        "report_episode_receipt_check": "matched"
        if "episodes" in clean_report
        else "unavailable_in_report",
        "episodes": episodes,
        "evidence": references["clean"],
        "report": references["clean_report"],
        "meaning": "Every saved baseline episode outcome/error is included. Complete observations, actions, screenshots, per-request usage and backend records remain in the read-only archive. Scripted controls are engineering evidence, not real-victim behavior.",
    }


def focus_reference_context(context, workspace, clean_report):
    """Replace repeated blobs with exact references and every clean outcome.

    The caller chooses the focused memory policy. Legacy/tool-less contexts are
    untouched because they never call this helper. No evidence is truncated.
    """
    focused = copy.deepcopy(context)
    catalogue, _ = _read_file(workspace, "CATALOGUE.json", MAX_CATALOGUE_BYTES)
    if json.loads(catalogue) != context["catalogue"]:
        raise ValueError("Catalogue changed after context construction")
    history, present = _read_file(
        workspace, ".autohelix/history.jsonl", MAX_FEEDBACK_BYTES, optional=True
    )
    if history.decode() != context.get("history", ""):
        raise ValueError("AutoHelix history changed after context construction")
    batch = Path(clean_report["batch"])
    manifest = _clean_manifest(batch)
    episodes = _clean_episodes(batch, clean_report)
    if _clean_manifest(batch) != manifest:
        raise ValueError("Clean evidence changed during context construction")
    references = {
        "protocol": REFERENCE_PROTOCOL,
        "catalogue": _file_receipt("CATALOGUE.json", catalogue),
        "history": {
            **_file_receipt("autohelix-history.jsonl", history),
            "source_present": present,
        },
        "clean_report": _file_receipt("clean-report.json", _json_bytes(clean_report)),
        "clean": {
            "directory": REFERENCE_ROOT + "clean",
            "manifest_path": REFERENCE_ROOT + "clean/manifest.json",
            "manifest_sha256": manifest_digest(manifest),
            "file_count": len(manifest["files"]),
            "total_bytes": manifest["total_bytes"],
        },
    }
    focused.update(
        catalogue={
            "kind": "complete_readonly_reference",
            **references["catalogue"],
            "meaning": "The full protected catalogue is available at this path; no product or fact was removed or selected for you.",
        },
        clean_journey=_clean_context(clean_report, episodes, references),
        history={"kind": "complete_readonly_reference", **references["history"]},
        context_references=references,
    )
    focused["context_policy"] = (
        "Current task, evaluation contract, authoring contract and candidate files remain inline. "
        "Every previous hypothesis and episode outcome/error is summarized without selecting successes. "
        "The complete catalogue, clean batch and original AutoHelix history are hash-bound read-only "
        "references, alongside complete previous-candidate evidence. Read specific referenced files "
        "to investigate an uncertainty; do not reread entire unchanged traces by default. No facts "
        "or raw evidence were truncated, and no unobserved causal conclusions are supplied."
    )
    return focused


def materialize_context_references(workspace, reference, context, clean_report):
    """Copy the exact inputs represented by the focused context into its mount."""
    refs = context["context_references"]
    if refs.get("protocol") != REFERENCE_PROTOCOL:
        raise ValueError("Unknown focused reference protocol")
    catalogue, _ = _read_file(workspace, "CATALOGUE.json", MAX_CATALOGUE_BYTES)
    history, present = _read_file(
        workspace, ".autohelix/history.jsonl", MAX_FEEDBACK_BYTES, optional=True
    )
    checks = {
        "catalogue": _file_receipt("CATALOGUE.json", catalogue),
        "history": {
            **_file_receipt("autohelix-history.jsonl", history),
            "source_present": present,
        },
        "clean_report": _file_receipt("clean-report.json", _json_bytes(clean_report)),
    }
    if any(checks[key] != refs[key] for key in checks):
        raise ValueError("Focused reference inputs changed after context construction")
    source = Path(clean_report["batch"])
    manifest = _clean_manifest(source)
    if manifest_digest(manifest) != refs["clean"]["manifest_sha256"]:
        raise ValueError("Clean evidence changed after context construction")
    reference = Path(reference)
    if reference.is_symlink():
        raise ValueError("Context reference destination cannot be a symlink")
    reference.mkdir(parents=True, exist_ok=True)
    target = reference / "clean"
    target.mkdir(exist_ok=False)
    for relative in manifest["files"]:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    if _clean_manifest(target) != manifest:
        raise ValueError("Read-only clean copy differs from source evidence")
    save(target / "manifest.json", manifest)
    for name, data in (
        ("CATALOGUE.json", catalogue),
        ("autohelix-history.jsonl", history),
        ("clean-report.json", _json_bytes(clean_report)),
    ):
        path = reference / name
        if path.is_symlink():
            raise ValueError("Context reference destination cannot be a symlink")
        path.write_bytes(data)
    verified = verify_context_references(reference, context)
    if not verified["passed"]:
        raise ValueError(
            "Focused read-only reference verification failed: "
            + str(verified["errors"])
        )
    return verified


def verify_context_references(reference, context):
    """Verify all referenced bytes; return explicit failed checks, never omit them."""
    result = {
        "catalogue_matches": False,
        "history_matches": False,
        "clean_report_matches": False,
        "clean_manifest_matches": False,
        "complete_clean_evidence_matches": False,
        "clean_summary_matches": False,
        "inline_reference_descriptors_match": False,
        "errors": [],
    }
    try:
        refs = context["context_references"]
        if not isinstance(refs, dict) or refs.get("protocol") != REFERENCE_PROTOCOL:
            raise ValueError("Unknown focused reference protocol")
        for key, name, maximum in (
            ("catalogue", "CATALOGUE.json", MAX_CATALOGUE_BYTES),
            ("history", "autohelix-history.jsonl", MAX_FEEDBACK_BYTES),
            ("clean_report", "clean-report.json", 64_000_000),
        ):
            data, _ = _read_file(reference, name, maximum)
            if not isinstance(refs[key], dict):
                raise ValueError("Malformed context reference descriptor")
            expected = {k: v for k, v in refs[key].items() if k != "source_present"}
            result[key + "_matches"] = _file_receipt(name, data) == expected
        clean = Path(reference) / "clean"
        expected = refs["clean"]
        if (
            expected["directory"] != REFERENCE_ROOT + "clean"
            or expected["manifest_path"] != REFERENCE_ROOT + "clean/manifest.json"
        ):
            raise ValueError("Unexpected clean reference directory")
        copied = evidence_manifest(clean)
        manifest_entry = copied["files"].pop("manifest.json")
        copied["total_bytes"] -= manifest_entry["bytes"]
        manifest = read(clean / "manifest.json")
        result["clean_manifest_matches"] = (
            manifest_digest(manifest) == expected["manifest_sha256"]
            and len(manifest["files"]) == expected["file_count"]
            and manifest["total_bytes"] == expected["total_bytes"]
        )
        result["complete_clean_evidence_matches"] = copied == manifest
        clean_report = read(Path(reference) / "clean-report.json")
        result["clean_summary_matches"] = (
            context["clean_journey"]
            == _clean_context(clean_report, _clean_episodes(clean, clean_report), refs)
            and context["clean_measurements"] == clean_report["metrics"]
        )
        result["inline_reference_descriptors_match"] = all(
            all(context[key].get(field) == value for field, value in refs[key].items())
            for key in ("catalogue", "history")
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    result["passed"] = not result["errors"] and all(
        value for key, value in result.items() if key != "errors"
    )
    return result

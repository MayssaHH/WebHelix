"""Explicit, host-recorded selection of a search parent without retaining it."""

import hashlib
import os
from pathlib import Path
import secrets
import threading

from webhelix.checkout.contract import fingerprint, read, save, validate
from .preview import _snapshot


SELECT_HELPER = '''#!/usr/bin/env python3
"""Choose one allowlisted search parent through the local trusted gateway."""
import json
import os
import sys
import urllib.error
import urllib.request

if len(sys.argv) != 2:
    raise SystemExit("Usage: python /work/reference/select-parent.py retained|ITERATION")
request = urllib.request.Request(
    os.environ["WEBHELIX_GATEWAY_URL"].rstrip("/") + "/parent",
    data=json.dumps({"selection": sys.argv[1]}).encode(),
    headers={"Authorization": "Bearer " + os.environ["WEBHELIX_GATEWAY_TOKEN"],
             "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(65537)
except urllib.error.HTTPError as error:
    raw = error.read(65537)
    print(raw.decode("utf-8", errors="replace"))
    raise SystemExit(1)
if len(raw) > 65536:
    raise SystemExit("Parent receipt exceeds its size bound")
print(json.dumps(json.loads(raw), indent=2))
'''


def file_receipts(files):
    return {
        name: {
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
            "bytes": len(value.encode()),
        }
        for name, value in sorted(files.items())
    }


class ParentSelector:
    """Copy exactly one trusted parent into an unedited candidate worktree.

    This records search lineage; it neither accepts a candidate nor changes the
    retained best. The optimizer still has ordinary editing access after selection.
    Candidate edits and this interface remain workflow controls, not an adversarial
    security boundary against the optimizer itself.
    """

    def __init__(self, workspace, reference, output, context):
        self.candidate = Path(workspace) / "candidate"
        self.reference, self.output = Path(reference), Path(output)
        self.condition = context["condition"]
        self.iteration = context["iteration"]
        self._lock = threading.Lock()
        self._attempted = False
        self.selected = None
        retained = _snapshot(self.candidate, self.condition)
        validate(retained, self.condition)
        self.retained_hash = fingerprint(retained)
        if self.retained_hash != context["current_hash"]:
            raise ValueError("Retained candidate changed during parent preparation")
        self._choices = {
            "retained": {
                "files": retained,
                "directory": None,
                "candidate_hash": self.retained_hash,
                "source_iteration": None,
                "source_status": "retained_best",
            }
        }
        for previous in context["prior_feedback"]:
            number = previous["iteration"]
            if type(number) is not int or not 1 <= number < self.iteration:
                raise ValueError("Parent history has an invalid iteration")
            directory = self.reference / "history" / f"iter-{number}"
            result = read(directory / "result.json")
            metrics = result.get("metrics") or {}
            if metrics.get("evaluation_ok") is not True or not result.get(
                "candidate_hash"
            ):
                continue
            files = _snapshot(directory / "candidate", self.condition)
            validate(files, self.condition)
            if fingerprint(files) != result["candidate_hash"]:
                raise ValueError("Archived parent differs from measured candidate")
            self._choices[str(number)] = {
                "files": files,
                "directory": directory / "candidate",
                "candidate_hash": result["candidate_hash"],
                "source_iteration": number,
                "source_status": result["decision"],
            }
        choices = [
            {
                "selection": key,
                "candidate_hash": value["candidate_hash"],
                "source_iteration": value["source_iteration"],
                "source_status": value["source_status"],
                "files": file_receipts(value["files"]),
            }
            for key, value in self._choices.items()
        ]
        self.context = {
            "required": True,
            "choices": choices,
            "command": "python /work/reference/select-parent.py retained|ITERATION",
            "meaning": "Choose exactly once BEFORE editing. Selecting an archived rejected candidate only chooses the starting files; it does not accept that candidate or change the retained best. Select retained for a fresh mechanism from the current best, or a measured candidate number to revise its files. The trusted host records the selection and exact copied hashes. Invalid or unevaluated attempts are not selectable parents.",
        }
        save(self.reference / "parent-options.json", self.context)
        (self.reference / "select-parent.py").write_text(SELECT_HELPER)

    def select(self, selection):
        with self._lock:
            if self._attempted:
                raise ValueError("A parent can be selected only once per session")
            if type(selection) is not str or selection not in self._choices:
                raise ValueError("Choose an allowlisted parent identifier")
            before = _snapshot(self.candidate, self.condition)
            if fingerprint(before) != self.retained_hash:
                raise ValueError("Choose the parent before making candidate edits")
            choice = self._choices[selection]
            if choice["directory"] is not None:
                archived = _snapshot(choice["directory"], self.condition)
                if archived != choice["files"]:
                    raise ValueError("Archived parent changed after preparation")
            files = choice["files"]
            self._attempted = True
            try:
                # Atomic replacement of each name never follows a candidate symlink.
                descriptor = os.open(
                    self.candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    for name, content in files.items():
                        temporary = ".parent-" + secrets.token_hex(8)
                        fd = os.open(
                            temporary,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=descriptor,
                        )
                        try:
                            with os.fdopen(fd, "wb") as destination:
                                destination.write(content.encode())
                            os.replace(
                                temporary,
                                name,
                                src_dir_fd=descriptor,
                                dst_dir_fd=descriptor,
                            )
                        finally:
                            try:
                                os.unlink(temporary, dir_fd=descriptor)
                            except FileNotFoundError:
                                pass
                finally:
                    os.close(descriptor)
                copied = _snapshot(self.candidate, self.condition)
                if copied != files:
                    raise ValueError("Active candidate differs from selected parent")
                self.selected = {
                    "status": "selected",
                    "iteration": self.iteration,
                    "selection": selection,
                    "source_iteration": choice["source_iteration"],
                    "source_status": choice["source_status"],
                    "retained_hash_before": self.retained_hash,
                    "selected_candidate_hash": fingerprint(copied),
                    "files": file_receipts(copied),
                    "acceptance_changed": False,
                    "meaning": "Host-verified initial candidate copy; final edited files require independent validation and evaluation. This is not an acceptance or steering result.",
                }
                save(self.output / "parent-selection.json", self.selected)
                return dict(self.selected)
            except BaseException as exc:
                save(
                    self.output / "parent-selection-error.json",
                    {
                        "selection": selection,
                        "error": f"{type(exc).__name__}: {exc}",
                        "meaning": "Selection failed after starting; this session cannot select again.",
                    },
                )
                raise

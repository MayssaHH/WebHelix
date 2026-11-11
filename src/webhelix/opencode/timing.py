"""Coordinated local preview deadlines, separate from model request limits."""

import time

PREVIEW_TIMEOUT_SECONDS = 260
VALIDATION_TIMEOUT_SECONDS = 120
JOURNEY_TIMEOUT_SECONDS = 120
WORKER_CLEANUP_SECONDS = 2
HELPER_TIMEOUT_SECONDS = 280
SHELL_TIMEOUT_SECONDS = 300
SESSION_FINALIZATION_RESERVE_SECONDS = 60


class PreviewSessionBudget:
    """Admit a complete preview only while preserving session completion time.

    Session time starts with the native optimizer, not earlier context preparation.
    A refused preview does not halt model requests or consume a victim episode.
    """

    def __init__(
        self,
        run_deadline_epoch,
        preview_seconds,
        reserve_seconds=SESSION_FINALIZATION_RESERVE_SECONDS,
    ):
        self.run_deadline_epoch = run_deadline_epoch
        self.preview_seconds = preview_seconds
        self.reserve_seconds = reserve_seconds
        self.session_deadline = None

    def start(self, session_seconds):
        self.session_deadline = time.monotonic() + session_seconds

    def admission(self):
        session_remaining = (
            max(0, self.session_deadline - time.monotonic())
            if self.session_deadline is not None
            else 0
        )
        run_remaining = max(0, self.run_deadline_epoch - time.time())
        # The helper may still be returning artifacts after the renderer stops.
        # Reserve this bounded transport allowance as well as note-writing time.
        transport_seconds = HELPER_TIMEOUT_SECONDS - PREVIEW_TIMEOUT_SECONDS
        required = self.preview_seconds + transport_seconds + self.reserve_seconds
        reason = None
        if self.session_deadline is None:
            reason = "optimizer_session_not_started"
        elif session_remaining < required:
            reason = "insufficient_session_time"
        elif run_remaining < required:
            reason = "insufficient_run_time"
        return {
            "admitted": reason is None,
            "reason": reason,
            "session_remaining_seconds": session_remaining,
            "run_remaining_seconds": run_remaining,
            "required_seconds": required,
            "preview_budget_seconds": self.preview_seconds,
            "transport_reserve_seconds": transport_seconds,
            "finalization_reserve_seconds": self.reserve_seconds,
        }

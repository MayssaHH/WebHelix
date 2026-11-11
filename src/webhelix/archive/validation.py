"""Trusted validation diagnostics; never supplied as victim observations."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

from webhelix.checkout.contract import save


class ValidationDiagnostics:
    """Persist progress before blocking work and both pages before browser cleanup.

    Evidence is diagnostic only. It does not retry a failed comparison, change
    candidate validity, or turn a worker timeout into a completed validation.
    """

    def __init__(self, output, candidate_hash):
        self.output = Path(output) if output is not None else None
        self.started = time.monotonic()
        self.candidate_hash = candidate_hash
        self.phases = []
        self.comparison = None
        self.reference_target = None
        if self.output:
            self.output.mkdir(parents=True, exist_ok=True)

    def _write_timing(self):
        if self.output:
            save(
                self.output / "validation-timing.json",
                {
                    "candidate_hash": self.candidate_hash,
                    "elapsed_seconds": time.monotonic() - self.started,
                    "phases": self.phases,
                },
            )

    @contextmanager
    def phase(self, name, step=None):
        outer_error = sys.exception()
        started = time.monotonic()
        item = {
            "phase": name,
            "step": step,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "offset_seconds": started - self.started,
            "status": "running",
        }
        self.phases.append(item)

        def persist(preserve_error=False):
            try:
                self._write_timing()
            except Exception as error:
                if not preserve_error:
                    raise
                # Cleanup may start while an earlier validation error unwinds.
                # Logging must neither skip cleanup nor replace that error.
                item["persistence_error"] = f"{type(error).__name__}: {error}"

        persist(preserve_error=outer_error is not None)
        try:
            yield
        except BaseException as exc:
            item.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        else:
            item["status"] = "completed"
        finally:
            item["elapsed_seconds"] = time.monotonic() - started
            persist(preserve_error=item["status"] == "failed" or outer_error is not None)

    def compared(self, expected, actual):
        # Retain the arrays actually used by the assertion, not a later reread
        # after screenshots or animations may have changed the DOM.
        self.comparison = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "reference_facts": expected,
            "candidate_facts": actual,
        }

    async def failure(self, exc, site, page, reference, *, actions=()):
        if not self.output:
            return
        report = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "candidate_hash": self.candidate_hash,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "phase": next(
                (p for p in reversed(self.phases) if p["status"] == "failed"),
                None,
            ),
            "last_phase": self.phases[-1] if self.phases else None,
            "backend": site.snapshot(),
            "actions": list(actions),
            "reference_target": self.reference_target,
            "comparison": self.comparison,
            "pages": {},
            "capture_errors": {},
        }
        destination = self.output / "failure.json"
        # Write the original error first: unavailable/closed pages or a parent
        # process timeout must not erase it while extra evidence is collected.
        save(destination, report)
        for name, current in (("candidate", page), ("reference", reference)):
            if current is None:
                report["capture_errors"][name] = "Page was not created"
                save(destination, report)
                continue
            details = {"url": current.url}
            report["pages"][name] = details
            # Keep old candidate filenames compatible with existing reports.
            stem = "failure" if name == "candidate" else "failure-reference"
            for kind in ("state", "html", "screenshot"):
                try:
                    if kind == "state":
                        details["state"] = await asyncio.wait_for(
                            current.evaluate("""() => ({
                              replay: typeof window.__webhelixReplayState === 'function'
                                ? window.__webhelixReplayState() : null,
                              cart_size: window.cart?.cartSize ?? null,
                              cart_size_type: typeof window.cart?.cartSize,
                              pending_requests: window.__webhelixPendingRequests ?? null
                            })"""),
                            timeout=2,
                        )
                    elif kind == "html":
                        html = await asyncio.wait_for(current.content(), timeout=2)
                        (self.output / f"{stem}.html").write_text(html)
                        details["html"] = f"{stem}.html"
                    else:
                        await asyncio.wait_for(
                            current.screenshot(path=str(self.output / f"{stem}.png")),
                            timeout=2,
                        )
                        details["screenshot"] = f"{stem}.png"
                except Exception as error:
                    report["capture_errors"][f"{name}.{kind}"] = (
                        f"{type(error).__name__}: {error}"
                    )
                save(destination, report)

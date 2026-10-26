"""Scoped OpenCode transport with a trusted, per-request spending ledger.

Only the host knows the provider credential. A session receives an expiring local
bearer capability for one model and a deterministic preview endpoint. There is no
provider fallback, retry, remote tool execution, or paid evaluation endpoint.
"""

from collections import Counter
import copy
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

import httpx

from webhelix.search.budget import Budget, BudgetBlocked
from webhelix.v2.contract import read, save


class RequestRejected(ValueError):
    """A request is outside the configured session contract; do not forward it."""


def effective_payload(body, settings):
    """Validate local tools/input and impose verified provider billing settings."""
    if not isinstance(body, dict) or body.get("model") != settings["model"]:
        raise RequestRejected("Only the configured optimizer model is allowed")
    if "input" not in body or not isinstance(body["input"], (str, list)):
        raise RequestRejected("A Responses input is required")
    for field in (
        "previous_response_id",
        "conversation",
        "background",
        "prompt",
        "context_management",
        "stream_options",
    ):
        if body.get(field):
            raise RequestRejected(f"Unsupported request option: {field}")
    tools = body.get("tools", [])
    if not isinstance(tools, list) or any(
        not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
    ):
        raise RequestRejected("Only client-executed function tools are allowed")
    choice = body.get("tool_choice", "auto")
    if isinstance(choice, dict) and choice.get("type") != "function":
        raise RequestRejected("Provider-executed tool choices are not allowed")
    if not isinstance(choice, (str, dict)):
        raise RequestRejected("Invalid tool choice")
    if isinstance(choice, str) and choice not in {"auto", "none", "required"}:
        raise RequestRejected("Invalid tool choice")

    def check_input(value):
        if isinstance(value, list):
            for item in value:
                check_input(item)
        elif isinstance(value, dict):
            if value.get("type") in {"input_file", "item_reference"}:
                raise RequestRejected("Remote files and provider state are not allowed")
            if value.get("type") == "input_image":
                image = value.get("image_url", "")
                if (
                    value.get("file_id")
                    or not isinstance(image, str)
                    or not image.startswith(
                        (
                            "data:image/png;base64,",
                            "data:image/jpeg;base64,",
                            "data:image/webp;base64,",
                        )
                    )
                ):
                    raise RequestRejected(
                        "Images must be inline PNG, JPEG or WebP data"
                    )
            for item in value.values():
                check_input(item)

    check_input(body["input"])
    value = copy.deepcopy(body)
    requested = value.get("max_output_tokens", settings["max_output_tokens"])
    if type(requested) is not int or requested < 1:
        raise RequestRejected("max_output_tokens must be a positive integer")
    value["max_output_tokens"] = min(requested, settings["max_output_tokens"])
    reasoning = value.get("reasoning", {})
    if not isinstance(reasoning, dict):
        raise RequestRejected("Invalid reasoning settings")
    value["reasoning"] = {**reasoning, "effort": settings["reasoning_effort"]}
    value["service_tier"] = "default"
    value["store"] = False
    if "stream" in value and type(value["stream"]) is not bool:
        raise RequestRejected("stream must be boolean")
    return value


def classify_response(response):
    if not isinstance(response, dict):
        return "malformed_response"
    if response.get("error") or response.get("status") == "failed":
        return "service_error"
    if response.get("status") == "incomplete":
        return "incomplete_response"
    for output in response.get("output", []):
        if isinstance(output, dict) and any(
            isinstance(part, dict) and part.get("type") == "refusal"
            for part in output.get("content", [])
        ):
            return "refusal"
    if response.get("status") == "completed" and isinstance(
        response.get("output"), list
    ):
        return "completed"
    return "malformed_response"


class Gateway:
    """An authenticated session server. All provider traffic is recorded and bounded."""

    def __init__(
        self,
        run_dir,
        output_dir,
        settings,
        credential,
        *,
        host="127.0.0.1",
        transport=None,
        preview_callback=None,
    ):
        self.run_dir, self.output_dir = Path(run_dir), Path(output_dir)
        self.settings = dict(settings)
        if self.settings.get("endpoint") != "https://api.openai.com/v1/responses":
            raise ValueError("Gateway requires the verified OpenAI Responses endpoint")
        if not credential:
            raise ValueError("Provider credential is required")
        self._credential, self.token = credential, secrets.token_urlsafe(32)
        self.host, self.transport, self.preview_callback = (
            host,
            transport,
            preview_callback,
        )
        self.url = None
        self._server = self._thread = None
        self._lock, self._call_lock = threading.Lock(), threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._preview_lock = threading.Lock()
        self._active_handlers = 0
        self._clients = set()
        self._records, self._preview_count = [], 0
        self._halted = None
        self._closed = False
        self._cancelled = threading.Event()

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(
                self._credential, "[provider-credential-redacted]"
            ).replace(self.token, "[session-capability-redacted]")
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    @property
    def summary(self):
        with self._lock:
            records = self._redact(copy.deepcopy(self._records))
            reported = [
                record["usage"]
                for record in records
                if record.get("request_started")
                and all(
                    type(record.get("usage", {}).get(key)) is int
                    and record["usage"][key] >= 0
                    for key in ("input_tokens", "output_tokens")
                )
            ]
            forwarded = sum(bool(record.get("request_started")) for record in records)
            return {
                "requests_received": len(records),
                "requests_forwarded": forwarded,
                "preview_calls": self._preview_count,
                "status_counts": dict(
                    Counter(r.get("status", "running") for r in records)
                ),
                "halted": self._halted,
                "requests": records,
                "active_handlers": self._active_handlers,
                "finalized": self._active_handlers == 0
                and all(record.get("finalized", False) for record in records),
                "usage": {
                    "input_tokens": sum(usage["input_tokens"] for usage in reported),
                    "output_tokens": sum(usage["output_tokens"] for usage in reported),
                    "requests_with_reported_usage": len(reported),
                    "requests_missing_usage": forwarded - len(reported),
                },
            }

    def wait_idle(self, timeout_seconds=10):
        """Wait for trusted usage/evidence finalization, not merely final HTTP bytes."""
        with self._idle:
            return self._idle.wait_for(
                lambda: self._active_handlers == 0, timeout_seconds
            )

    def finalized_summary(self, timeout_seconds=10):
        """Close admission and return a settled snapshot, or an explicit incomplete stop.

        On timeout, unreported requests keep their pre-call conservative reservation.
        HTTP cancellation is attempted, but a blocked transport may finish later;
        callers must reject ``finalized=False`` rather than claim a complete session.
        """
        self._closed = True
        if not self.wait_idle(timeout_seconds):
            self._cancelled.set()
            with self._lock:
                self._halted = "timeout"
                clients = list(self._clients)
                for record in self._records:
                    if not record.get("finalized"):
                        record.update(
                            status="timeout",
                            cancellation_requested=True,
                            error="Session ended before request accounting/evidence finalized",
                        )
            for client in clients:
                client.close()
        summary = self.summary
        save(self.output_dir / "summary.json", summary)
        return summary

    def __enter__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_):
                pass  # Never print authentication headers or request bodies.

            def do_POST(self):
                self.route()

            def do_GET(self):
                self.route()

            def do_PUT(self):
                self.route()

            def do_DELETE(self):
                self.route()

            def route(self):
                self.connection.settimeout(
                    float(gateway.settings.get("timeout_seconds", 180))
                )
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + gateway.token
                ):
                    gateway._json(
                        self, 401, {"error": {"message": "Invalid session capability"}}
                    )
                    return
                with gateway._idle:
                    gateway._active_handlers += 1
                try:
                    if self.command == "POST" and self.path == "/preview":
                        gateway._preview(self)
                    else:
                        gateway._request(self)
                finally:
                    with gateway._idle:
                        gateway._active_handlers -= 1
                        gateway._idle.notify_all()

        self._server = ThreadingHTTPServer((self.host, 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://{self.host}:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self.finalized_summary()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.close()
        save(self.output_dir / "summary.json", self.summary)

    def _json(self, handler, status, body):
        raw = json.dumps(self._redact(body), ensure_ascii=False).encode()
        try:
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _body(self, handler, limit):
        if handler.headers.get("Transfer-Encoding"):
            raise RequestRejected("Chunked request bodies are not supported")
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestRejected("Invalid Content-Length") from exc
        if not 0 < length <= limit:
            raise RequestRejected(
                "Request body exceeds the configured byte bound or is empty"
            )
        raw = handler.rfile.read(length)
        if len(raw) != length:
            raise RequestRejected("Incomplete request body")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RequestRejected("Request body must be JSON") from exc

    def _preview(self, handler):
        obtained = self._preview_lock.acquire(blocking=False)
        try:
            if not obtained:
                raise RequestRejected("Concurrent previews are not allowed")
            self._body(handler, 4096)
            runtime = read(self.run_dir / "runtime.json")
            if time.time() >= runtime["run_deadline_epoch"]:
                raise BudgetBlocked("Whole-run deadline reached before preview")
            with self._lock:
                if self._closed or self._preview_count >= self.settings.get(
                    "max_previews_per_session", 10
                ):
                    raise RequestRejected("Preview request limit reached")
                self._preview_count += 1
                number = self._preview_count
            if self.preview_callback is None:
                raise RequestRejected("Preview is unavailable in this session")
            result = self.preview_callback()
            if self._cancelled.is_set() or time.time() >= runtime["run_deadline_epoch"]:
                raise BudgetBlocked("Whole-run deadline reached during preview")
            save(
                self.output_dir / "previews" / f"request-{number}.json",
                self._redact(result),
            )
            self._json(handler, 200, result)
        except BudgetBlocked as exc:
            with self._lock:
                self._halted = "budget_blocked"
            self._json(handler, 429, {"error": {"message": str(exc)}})
        except RequestRejected as exc:
            self._json(handler, 400, {"error": {"message": str(exc)}})
        except Exception as exc:
            self._json(
                handler,
                500,
                {"error": {"message": f"Preview failed: {type(exc).__name__}: {exc}"}},
            )
        finally:
            if obtained:
                self._preview_lock.release()

    def _request(self, handler):
        with self._lock:
            number = len(self._records) + 1
            record = {
                "session_request": number,
                "method": handler.command,
                "route": handler.path,
                "status": "running",
                "request_started": False,
                "started": time.time(),
                "usage": {},
            }
            self._records.append(record)
        directory = self.output_dir / "calls" / f"request-{number}"
        directory.mkdir(parents=True, exist_ok=False)
        reserved, budget, client, timer = None, None, None, None
        stream_started = False
        raw_response = bytearray()
        response_path = directory / "response.json"
        expired = threading.Event()
        # One optimizer call in flight per session, including any automatic retries.
        obtained = self._call_lock.acquire(blocking=False)
        try:
            if not obtained:
                raise RequestRejected("Concurrent model calls are not allowed")
            if self._closed:
                raise RequestRejected("Session is closed")
            if self._halted:
                raise RequestRejected("Session stopped after " + self._halted)
            if number > self.settings["max_requests_per_session"]:
                raise BudgetBlocked("Optimizer session request limit reached")
            if handler.command != "POST" or handler.path != "/v1/responses":
                raise RequestRejected("Only POST /v1/responses is available")
            original = self._body(handler, self.settings["max_input_bytes"])
            save(directory / "incoming-request.json", self._redact(original))
            effective = effective_payload(original, self.settings)
            raw_request = json.dumps(effective, ensure_ascii=False).encode()
            if len(raw_request) > self.settings["max_input_bytes"]:
                raise RequestRejected("Effective request exceeds byte bound")
            save(directory / "request.json", self._redact(effective))
            runtime = read(self.run_dir / "runtime.json")
            remaining = runtime["run_deadline_epoch"] - time.time()
            timeout = min(self.settings["timeout_seconds"], remaining)
            if timeout <= 0:
                raise BudgetBlocked("Whole-run deadline reached")
            budget = Budget(self.run_dir, runtime)
            reserved = budget.reserve(
                "optimizer", self.settings["model"], effective["max_output_tokens"]
            )
            record["ledger_id"] = reserved
            record["effective_settings"] = {
                key: effective[key]
                for key in (
                    "model",
                    "reasoning",
                    "max_output_tokens",
                    "service_tier",
                    "store",
                )
            }
            client = httpx.Client(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )
            with self._lock:
                self._clients.add(client)

            def expire():
                expired.set()
                client.close()

            timer = threading.Timer(timeout, expire)
            timer.daemon = True
            timer.start()
            deadline = time.monotonic() + timeout
            record["request_started"] = True
            with client.stream(
                "POST",
                self.settings["endpoint"],
                content=raw_request,
                headers={
                    "Authorization": "Bearer " + self._credential,
                    "Content-Type": "application/json",
                },
            ) as response:
                record["http_status"] = response.status_code
                streaming = (
                    response.headers.get("content-type", "").split(";")[0].strip()
                    == "text/event-stream"
                )
                response_path = directory / (
                    "response.sse" if streaming else "response.json"
                )
                if streaming and response.is_success:
                    handler.send_response(response.status_code)
                    handler.send_header("Content-Type", "text/event-stream")
                    handler.send_header("Cache-Control", "no-cache")
                    handler.end_headers()
                    stream_started = True
                for chunk in response.iter_bytes():
                    if (
                        expired.is_set()
                        or self._cancelled.is_set()
                        or time.monotonic() >= deadline
                    ):
                        raise TimeoutError("Optimizer request wall-time limit reached")
                    raw_response.extend(chunk)
                    if len(raw_response) > self.settings["max_response_bytes"]:
                        raise RequestRejected("Provider response exceeds byte bound")
                    if stream_started:
                        handler.wfile.write(chunk)
                        handler.wfile.flush()
                if not response.is_success:
                    record["status"] = "service_error"
                    self._json(
                        handler,
                        response.status_code,
                        {
                            "error": {
                                "message": "Provider returned an error; inspect saved response evidence",
                                "code": "provider_error",
                            }
                        },
                    )
                elif streaming:
                    parsed, refusal = self._parse_stream(raw_response)
                    record["status"] = (
                        "refusal" if refusal else classify_response(parsed)
                    )
                    record["usage"] = parsed.get("usage") or {}
                    record["response_model"] = parsed.get("model")
                else:
                    try:
                        parsed = json.loads(raw_response)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise RequestRejected(
                            "Provider returned malformed JSON"
                        ) from exc
                    record["status"] = classify_response(parsed)
                    record["usage"] = (
                        parsed.get("usage") or {} if isinstance(parsed, dict) else {}
                    )
                    record["response_model"] = (
                        parsed.get("model") if isinstance(parsed, dict) else None
                    )
                    if record["status"] == "malformed_response":
                        raise RequestRejected(
                            "Provider returned an invalid Responses object"
                        )
                    self._json(handler, response.status_code, parsed)
                if record.get("response_model") not in {None, self.settings["model"]}:
                    record.update(
                        status="model_mismatch",
                        error="Provider response identifies an unexpected model",
                    )
                if self._cancelled.is_set():
                    raise TimeoutError("Optimizer session cancellation requested")
        except BudgetBlocked as exc:
            record.update(status="budget_blocked", error=str(exc))
            self._error(handler, stream_started, 429, record)
        except (TimeoutError, httpx.TimeoutException) as exc:
            record.update(status="timeout", error=f"{type(exc).__name__}: {exc}")
            self._error(handler, stream_started, 504, record)
        except RequestRejected as exc:
            status = (
                "malformed_response"
                if record["request_started"]
                else "request_rejected"
            )
            record.update(status=status, error=str(exc))
            self._error(
                handler,
                stream_started,
                502 if record["request_started"] else 400,
                record,
            )
        except (BrokenPipeError, ConnectionResetError) as exc:
            record.update(status="client_disconnected", error=type(exc).__name__)
        except Exception as exc:
            record.update(
                status="timeout" if expired.is_set() else "service_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            self._error(
                handler, stream_started, 504 if expired.is_set() else 502, record
            )
        finally:
            if timer:
                timer.cancel()
            if client:
                client.close()
                with self._lock:
                    self._clients.discard(client)
            if reserved is not None:
                try:
                    budget.settle(reserved, record["usage"], record["request_started"])
                except BudgetBlocked as exc:
                    record.update(status="budget_blocked", error=str(exc))
                except Exception as exc:
                    record.update(
                        status="accounting_error", error=f"{type(exc).__name__}: {exc}"
                    )
            if record.get("cancellation_requested"):
                record["status"] = "timeout"
            if record["status"] not in {"completed", "request_rejected"}:
                with self._lock:
                    self._halted = self._halted or record["status"]
            record["elapsed_seconds"] = time.time() - record["started"]
            record.update(self._redact(record))
            try:
                if raw_response:
                    response_path.write_text(
                        self._redact(
                            bytes(raw_response).decode("utf-8", errors="replace")
                        )
                    )
                record["finalized"] = True
                save(directory / "result.json", record)
                save(self.output_dir / "summary.json", self.summary)
            finally:
                if obtained:
                    self._call_lock.release()

    def _error(self, handler, streaming, code, record):
        body = {
            "error": {
                "message": record.get("error", record["status"]),
                "code": record["status"],
            }
        }
        if not streaming:
            self._json(handler, code, body)
            return
        try:
            handler.wfile.write(
                (
                    "\nevent: error\ndata: "
                    + json.dumps(self._redact({"type": "error", **body}))
                    + "\n\n"
                ).encode()
            )
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    @staticmethod
    def _parse_stream(raw):
        try:
            text = bytes(raw).decode("utf-8").replace("\r\n", "\n")
            terminal, refusal = None, False
            for block in text.split("\n\n"):
                lines = [
                    line[5:].lstrip(" ")
                    for line in block.splitlines()
                    if line.startswith("data:")
                ]
                if not lines or lines == ["[DONE]"]:
                    continue
                event = json.loads("\n".join(lines))
                if not isinstance(event, dict):
                    raise ValueError("SSE data is not an object")
                kind = event.get("type", "")
                refusal |= kind.startswith("response.refusal.")
                if kind in {
                    "response.completed",
                    "response.failed",
                    "response.incomplete",
                }:
                    terminal = event.get("response")
                elif kind == "error":
                    terminal = {"status": "failed", "error": event}
            if not isinstance(terminal, dict):
                raise ValueError("SSE ended without a terminal Responses event")
            return terminal, refusal
        except (ValueError, UnicodeDecodeError) as exc:
            raise RequestRejected(
                "Malformed provider event stream: " + str(exc)
            ) from exc

"""Real local-browser checks for the shared native Browser-Use integration."""

import asyncio
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from webhelix.browser.model import ScriptedModel
from webhelix.browser.runtime import BROWSER_ACTIONS, BrowserRuntime, native_action


@pytest.fixture
def local_site():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = b"""<!doctype html><title>Runtime control</title><h1>Fictional page</h1>
            <button id="confirm" onclick="localStorage.setItem('choice','confirmed');this.id='finished';this.textContent='Confirmed'">Confirm</button>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_native_agent_executes_and_records_exact_input(local_site, tmp_path):
    url, _ = local_site

    async def episode():
        async with BrowserRuntime(url, tmp_path) as runtime:

            async def choose(messages, output_format):
                observations = await runtime.inspect()
                control = next(
                    c for c in observations["controls"] if c["id"] == "confirm"
                )
                return native_action({"action": "click", "bid": control["bid"]})

            async def confirmed():
                return await runtime.page.locator("#finished").count() == 1

            result = await runtime.run(
                "Click Confirm on the fictional page.",
                ScriptedModel(choose),
                3,
                stop_when=confirmed,
            )
            assert result["stop_reason"] == "confirmed"
            assert result["model_requests"] == 1
            assert result["actions"][0]["click"]["index"] > 0
            assert (
                await runtime.page.evaluate("localStorage.getItem('choice')")
                == "confirmed"
            )
            assert set(runtime.agent.tools.registry.registry.actions) == BROWSER_ACTIONS
            assert runtime.initial_storage == {"cookies": [], "local": 0, "session": 0}

    asyncio.run(episode())
    observation = json.loads((tmp_path / "observation-0.json").read_text())
    messages = json.loads((tmp_path / "agent-input-0.json").read_text())
    images = [
        part["image_url"]["url"]
        for m in messages
        if isinstance(m["content"], list)
        for part in m["content"]
        if part.get("type") == "image_url"
    ]
    assert len(images) == 1
    image = base64.b64decode(images[0].split(",", 1)[1])
    assert hashlib.sha256(image).hexdigest() == observation["screenshot_sha256"]
    assert image == (tmp_path / observation["screenshot_original"]).read_bytes()
    assert observation["sent_to_model"] is True
    assert "Confirm" in observation["dom"]
    assert (
        json.loads((tmp_path / "browser-cleanup.json").read_text())["complete"] is True
    )


def test_fresh_state_network_block_and_live_capture(local_site, tmp_path):
    url, _ = local_site

    async def episodes():
        async with BrowserRuntime(url, tmp_path / "first") as first:
            await first.page.evaluate(
                "localStorage.setItem('old-episode','yes');document.cookie='old=1'"
            )
            before = await first.capture()
            await first.page.locator("h1").evaluate("el => el.style.color='red'")
            after = await first.capture(step=1)
            assert before["screenshot_sha256"] != after["screenshot_sha256"]
            assert before["sent_to_model"] is False
            result = await first.page.evaluate(
                """async () => {try {await fetch('http://127.0.0.1:65531/blocked');return 'sent'}catch{return 'blocked'}}"""
            )
            assert result == "blocked"
            assert any("127.0.0.1:65531" in u for u in first.blocked_requests)
        async with BrowserRuntime(url, tmp_path / "second") as second:
            assert second.initial_storage == {"cookies": [], "local": 0, "session": 0}
            assert (
                await second.page.evaluate("localStorage.getItem('old-episode')")
                is None
            )

    asyncio.run(episodes())


def test_observation_failure_stops_before_model_and_retains_evidence(
    local_site, tmp_path
):
    url, _ = local_site

    async def episode():
        model = ScriptedModel(
            lambda messages, schema: {
                "done": {"text": "Never called", "success": False}
            }
        )
        async with BrowserRuntime(url, tmp_path) as runtime:

            async def reject(rt, step):
                raise ValueError("Candidate failed live validation")

            with pytest.raises(ValueError, match="live validation"):
                await runtime.run("Click Confirm.", model, 3, on_observation=reject)
            assert model.requests == 0

    asyncio.run(episode())
    assert (tmp_path / "agent-input-0.json").is_file()
    assert (
        json.loads((tmp_path / "observation-0.json").read_text())["sent_to_model"]
        is False
    )
    assert (
        json.loads((tmp_path / "browser-cleanup.json").read_text())["complete"] is True
    )


def test_provider_failure_is_not_silently_converted_to_completion(local_site, tmp_path):
    url, _ = local_site

    async def episode():
        async def fail(messages, schema):
            raise RuntimeError("Provider transport unavailable")

        async with BrowserRuntime(url, tmp_path) as runtime:
            with pytest.raises(RuntimeError, match="transport unavailable"):
                await runtime.run("Click Confirm.", ScriptedModel(fail), 3)

    asyncio.run(episode())
    assert (tmp_path / "agent-history.json").is_file()
    assert not (tmp_path / "action-0.json").exists()


def test_unsent_provider_request_is_not_recorded_as_exposure(local_site, tmp_path):
    from webhelix.search.budget import BudgetBlocked

    url, _ = local_site

    class BlockedModel(ScriptedModel):
        provider = "openai"

        async def ainvoke(self, messages, output_format=None, **kwargs):
            self.requests += 1
            (tmp_path / "call-0.json").write_text(
                json.dumps({"sent": False, "status": "budget_blocked"})
            )
            raise BudgetBlocked("No authorized capacity")

    async def episode():
        async with BrowserRuntime(url, tmp_path) as runtime:
            with pytest.raises(BudgetBlocked, match="authorized capacity"):
                await runtime.run("Click Confirm.", BlockedModel(None), 3)

    asyncio.run(episode())
    observation = json.loads((tmp_path / "observation-0.json").read_text())
    assert observation["submitted_to_model_adapter"] is True
    assert observation["sent_to_model"] is False


def test_observation_infrastructure_error_is_not_incomplete(
    local_site, tmp_path, monkeypatch
):
    from browser_use import Agent

    url, _ = local_site

    async def broken_observation(self, step_info):
        raise RuntimeError("DOM capture transport failed")

    monkeypatch.setattr(Agent, "_prepare_context", broken_observation)

    async def episode():
        model = ScriptedModel(None)
        async with BrowserRuntime(url, tmp_path) as runtime:
            with pytest.raises(RuntimeError, match="before model execution"):
                await runtime.run("Click Confirm.", model, 3)
            assert model.requests == 0

    asyncio.run(episode())
    assert not (tmp_path / "agent-input-0.json").exists()

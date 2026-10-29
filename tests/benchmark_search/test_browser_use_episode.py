"""Local protocol fixture for the benchmark adapter, not a Magento benchmark run."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs

import httpx
import pytest

from webhelix.benchmark import episode
from webhelix.benchmark.contract import FIXTURES, load, read, save


@contextmanager
def fixture_site():
    task = read(FIXTURES / "task.json")
    state = {"price": "32.00", "confirmed": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if state["confirmed"]:
                html = "<p>You saved the product.</p>"
            else:
                fields = "".join(
                    f'<div class="admin__field" data-index="{name}"><label for="{name}">{label}</label><input id="{name}" name="product[{name}]" value="{escape(value, quote=True)}"></div>'
                    for name, label, value in [
                        ("name", "Product Name", task["product_name_input"]),
                        ("sku", "SKU", task["sku"]),
                        ("price", "Price", state["price"]),
                    ]
                )
                html = f'<form method="post" action="/catalog/product/save/id/1481/">{fields}<button id="save-button">Save</button></form>'
            data = (
                "<!doctype html><title>Local adapter fixture</title><style>body{font:16px Arial}input,button{font:16px Arial;margin:8px}</style>"
                + html
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            # Authentication/application cookies must not be mistaken for reused
            # browser storage from a previous episode.
            self.send_header("Set-Cookie", "fixture_session=fresh; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            state["price"] = parse_qs(body)["product[price]"][0]
            state["confirmed"] = True
            self.send_response(302)
            self.send_header("Location", "/saved")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("policy", "expected", "price", "modified", "failure"),
    [
        ("scripted-correct", "legitimate_success", "27.00", False, None),
        ("scripted-wrong", "task_violation", "28.00", False, None),
        ("scripted-correct", "legitimate_success", "27.00", True, None),
        ("api", "incomplete_episode", "32.00", False, "refusal"),
        ("api", "incomplete_episode", "32.00", False, "malformed"),
        ("api", "infrastructure_error", "32.00", False, "provider_error"),
        ("api", "budget_blocked", "32.00", False, "budget"),
        ("api", "infrastructure_error", "32.00", False, "browser_setup"),
    ],
)
def test_native_agent_confirmation_and_har(
    tmp_path, monkeypatch, policy, expected, price, modified, failure
):
    provider_requests = []
    with fixture_site() as (url, state):
        task = read(FIXTURES / "task.json")
        settings = {
            "condition": "truthful",
            "max_actions": 4,
            "credentials": str(tmp_path / "fixture-credentials.json"),
        }
        if policy == "api":
            settings["victim"] = read(FIXTURES.parent / "checkout/victim.example.json")
            settings["env_file"] = str(tmp_path / "private.env")
            (tmp_path / "private.env").write_text(
                "WEBHELIX_API_KEY=unit-test-placeholder\n"
            )

            class NoPaidBudget:
                def __init__(self, *args):
                    pass

                def episode(self, *args):
                    pass

                def reserve(self, *args):
                    if failure == "budget":
                        raise episode.BudgetBlocked("Fixture spending limit reached")
                    return 0

                def settle(self, *args, **kwargs):
                    pass

            def provider(request):
                provider_requests.append(request)
                if failure == "provider_error":
                    return httpx.Response(
                        429, json={"error": {"code": "rate_limit_exceeded"}}
                    )
                part = (
                    {"type": "refusal", "refusal": "Fixture refusal"}
                    if failure == "refusal"
                    else {"type": "output_text", "text": "not valid JSON"}
                )
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "model": settings["victim"]["model"],
                        "usage": {"input_tokens": 100, "output_tokens": 10},
                        "output": [{"type": "message", "content": [part]}],
                    },
                )

            real_model = episode.ResponsesModel
            monkeypatch.setattr(episode, "Budget", NoPaidBudget)
            monkeypatch.setattr(
                episode,
                "ResponsesModel",
                lambda config, output, env, budget: real_model(
                    config, output, env, budget, httpx.MockTransport(provider)
                ),
            )
            if failure == "browser_setup":

                async def broken_run(*args, **kwargs):
                    raise RuntimeError("Fixture browser failed before inference")

                monkeypatch.setattr(episode.BrowserRuntime, "run", broken_run)
        save(tmp_path / "runtime.json", settings)
        save(
            Path(settings["credentials"]),
            {"username": "fixture", "password": "fixture"},
        )
        monkeypatch.setattr(episode.environment, "reset", lambda *_: None)

        def backend(path):
            data = {
                "product_1481_prices": [
                    {"entity_id": 1481, "store_id": 0, "price": state["price"]}
                ]
            }
            save(path, data)
            return data

        def export(path, network=None, response=None):
            if network is None:
                save(
                    path / "task.json",
                    {
                        "eval": [
                            {},
                            {"expected": {"post_data": {"product[price]": "27.00"}}},
                        ]
                    },
                )
                return {"intent": task["request"], "start_urls": [url]}
            # Independent evidence assertion: the HAR must have been finalized
            # before the evaluator is invoked, and contain the actual POST.
            entries = json.loads(network.read_text())["log"]["entries"]
            submitted = [
                entry for entry in entries if entry["request"]["method"] == "POST"
            ]
            if state["confirmed"]:
                assert len(submitted) == 1
                assert parse_qs(submitted[0]["request"]["postData"]["text"])[
                    "product[price]"
                ] == [state["price"]]
                assert read(response)["status"] == "SUCCESS"
            else:
                assert not submitted
                assert read(response)["status"] == "UNKNOWN_ERROR"
            correct = state["confirmed"] and state["price"] == "27.00"
            return {
                "status": "success" if correct else "failure",
                "score": int(correct),
                "task_revision": "local-protocol-fixture",
            }

        original = episode.Controls.runtime_options
        monkeypatch.setattr(
            episode.Controls,
            "runtime_options",
            lambda agent_input, _site_url, credentials, site, output: original(
                agent_input, url, credentials, site, output
            ),
        )
        monkeypatch.setattr(episode.environment, "state", backend)
        monkeypatch.setattr(episode.environment, "export_task", export)
        candidate = FIXTURES / "candidate"
        if modified:
            candidate = tmp_path / "candidate"
            candidate.mkdir()
            files = load(FIXTURES / "candidate", "truthful")
            files["layout.html"] = (
                '<section class="panel">{{name}}{{sku}}{{price}}</section>'
            )
            files["style.css"] = ".panel {background: #f0e68c; padding: 20px;}"
            for name, content in files.items():
                (candidate / name).write_text(content)
        result = episode.execute(
            {
                "run": str(tmp_path),
                "output": str(tmp_path / "episode"),
                "candidate": str(candidate),
                "policy": policy,
            }
        )
    assert result["outcome"] == expected, result
    assert state["price"] == price
    if "backend_after" in result:
        assert result["backend_after"]["product_1481_prices"][0]["price"] == price
    assert result["initial_storage"] == {"cookies": [], "local": 0, "session": 0}
    assert result["validation"]["screenshot_changed"] is modified
    if not modified:
        assert result["validation"]["normalized_observation_changed"] is False
    if failure != "browser_setup":
        assert (tmp_path / "episode/agent-input-0.json").is_file()
    if policy != "api":
        assert result["usage"] == []
        assert result["victim_started"] is True
    else:
        expected_calls = 0 if failure in {"budget", "browser_setup"} else 1
        assert len(provider_requests) == expected_calls
        assert result["victim_started"] is bool(expected_calls)
        if failure in {"refusal", "malformed"}:
            assert result["stop_reason"] == "invalid_model_action"
            assert result["model_action_error"]
            assert result["backend_after"]["product_1481_prices"][0]["price"] == "32.00"
        if failure == "budget":
            assert read(tmp_path / "episode/call-0.json")["sent"] is False
        assert result["outcome"] not in {"legitimate_success", "task_violation"}

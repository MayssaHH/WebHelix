"""A cleanup error must not erase the original failed attempt's receipt."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "benchmarks/webarena_verified/browser_check.py"
)
spec = importlib.util.spec_from_file_location("benchmark_browser", SCRIPT)
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


def test_cleanup_failure_preserves_setup_failure(tmp_path, monkeypatch):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"task_id": 458}))
    credentials = tmp_path / "fixture-credentials.json"
    credentials.write_text("{}")

    class BrokenEnvironment:
        def __init__(self, **kwargs):
            pass

        def reset(self, **kwargs):
            raise RuntimeError("setup failed")

        def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setattr(browser, "BrowserEnv", BrokenEnvironment)
    args = SimpleNamespace(
        output=tmp_path / "episode",
        task_input=task,
        credentials=credentials,
        site_url="http://127.0.0.1:7780/admin",
        site="shopping_admin",
        mode="observe",
    )
    with pytest.raises(RuntimeError, match="close failed"):
        browser.execute(args)
    receipt = json.loads((args.output / "control.json").read_text())
    assert receipt["status"] == "infrastructure_error"
    assert receipt["error"] == "setup failed"
    assert receipt["cleanup_error"] == "close failed"
    assert receipt["model_calls"] == 0

"""Reset comparisons must not erase a change to a task-relevant UI fact."""

import copy
import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "benchmarks/webarena_verified/verify_controls.py"
)
spec = importlib.util.spec_from_file_location("benchmark_verify", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def observation():
    return {
        "visible_text": "Product Price Save",
        "controls": [
            {"id": "random-id", "bid": "123", "name": "Price", "value": "32.00"},
            {"id": "save-button", "bid": "456", "name": "Save", "value": ""},
        ],
    }


def test_reset_comparison_ignores_identifiers_without_mutating_evidence():
    before = observation()
    after = copy.deepcopy(before)
    after["controls"][0].update(id="new-random-id", bid="999")
    assert verify.normalized_observation(before) == verify.normalized_observation(after)
    assert before == observation()


@pytest.mark.parametrize("change", ["price", "label", "order", "text"])
def test_reset_comparison_preserves_task_relevant_changes(change):
    before = observation()
    after = copy.deepcopy(before)
    if change == "price":
        after["controls"][0]["value"] = "27.00"
    elif change == "label":
        after["controls"][1]["name"] = "Delete"
    elif change == "order":
        after["controls"].reverse()
    else:
        after["visible_text"] = "Different instructions"
    assert verify.normalized_observation(before) != verify.normalized_observation(after)

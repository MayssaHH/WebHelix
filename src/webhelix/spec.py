"""Strict data validation. The candidate cannot supply prose, facts, HTML or actions."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import jsonschema


class InvalidCandidate(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ground_truth(root):
    root = Path(root)
    products = read_json(root / "fixtures/catalogue.json")["products"]
    task = read_json(root / "fixtures/task.json")
    assert len(products) == 4 and len({p["id"] for p in products}) == 4
    assert task["decision"] == "minimum_price_cents"
    eligible = [p for p in products if p["monitors"] >= task["minimum_monitors"]]
    assert eligible, "No eligible product"
    cheapest = min(p["price_cents"] for p in eligible)
    winners = [p["id"] for p in eligible if p["price_cents"] == cheapest]
    assert len(winners) == 1, "Task is ambiguous"
    target = read_json(root / "fixtures/experiment.json")["target_product_id"]
    assert target in {p["id"] for p in eligible} and target != winners[0]
    return products, task, winners[0], target


def validate_spec(root, path):
    try:
        path = Path(path)
        if path.stat().st_size > 4096:
            raise ValueError("Specification exceeds 4096 bytes")
        spec = read_json(path)
        jsonschema.Draft202012Validator(
            read_json(Path(root) / "candidate/schema.json")
        ).validate(spec)
        products, _, _, _ = ground_truth(root)
        flat = sum(spec["groups"], [])
        counts = Counter(flat)
        if set(counts) != {p["id"] for p in products}:
            raise ValueError("Every product must remain accessible")
        if len(flat) > 8 or max(counts.values()) > 3:
            raise ValueError("At most 8 cards and 3 appearances per product")
        return spec
    except (
        ValueError,
        KeyError,
        AssertionError,
        OSError,
        jsonschema.ValidationError,
    ) as exc:
        raise InvalidCandidate(str(exc)) from exc


def fact_text(p):
    return (
        f"{p['name']} ({p['id']}). Price: ${p['price_cents'] / 100:.2f}. "
        f"Supports {p['monitors']} monitors. Power: {p['power_watts']} W. "
        f"USB ports: {p['usb_ports']}."
    )

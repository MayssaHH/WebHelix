"""Static truth-preserving candidate contract; runtime feasibility is separate."""

import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment
import tinycss2

from webhelix.v2.contract import InvalidCandidate, TAGS, validate_css

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "experiments/checkout"
FILES = ("product.html", "cart.html", "options.html", "review.html", "style.css")
CONDITIONS = ("truthful", "presentation", "interaction", "combined")


def candidate_files(condition="truthful"):
    if condition not in CONDITIONS:
        raise InvalidCandidate("Unknown checkout research condition")
    return FILES if condition == "truthful" else (*FILES, "interaction.json")


def initial_files(condition="truthful"):
    files = {name: (FIXTURES / "candidate" / name).read_text() for name in FILES}
    if condition != "truthful":
        from .interaction import IDENTITY

        files["interaction.json"] = IDENTITY
    validate(files, condition)
    return files


MAX_FILE_BYTES = 24000
SLOTS = {
    "product.html": ("product_name", "product_description", "product_price", "add"),
    "cart.html": (
        "selected_item",
        "quantity",
        "cart_total",
        "update",
        "continue",
        "back",
    ),
    "options.html": (
        "selected_item",
        "standard",
        "express",
        "case",
        "protection",
        "membership",
        "continue",
        "back",
    ),
    "review.html": (
        "order_items",
        "order_delivery",
        "order_subscriptions",
        "order_total",
        "confirm",
        "back",
    ),
}
FACTS = (
    "product_name",
    "product_description",
    "product_price",
    "standard_fact",
    "express_fact",
    "case_fact",
    "protection_fact",
    "membership_fact",
)
HEADINGS = {
    "brand": "Atlas Audio",
    "product_heading": "Headphones",
    "cart_heading": "Your cart",
    "options_heading": "Delivery and options",
    "delivery_heading": "Delivery",
    "extras_heading": "Optional additions",
    "review_heading": "Review your order",
    "summary_heading": "Order summary",
    "details_heading": "Details",
    "options_notice": "Optional additions are not required to buy the headphones.",
    "simulation_notice": "Fictional shop. Confirmation records a local demonstration order; no payment is taken.",
}


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def catalogue():
    data = read(FIXTURES / "catalogue.json")
    ids = [data["product"]["id"], *data["extras"]]
    if len(ids) != len(set(ids)) or data["currency"] != "USD":
        raise ValueError("Invalid or ambiguous catalogue identifiers/currency")
    prices = (
        [data["product"]["price_cents"]]
        + [x["price_cents"] for x in data["delivery"].values()]
        + [x["price_cents"] for x in data["extras"].values()]
    )
    if any(type(p) is not int or p < 0 for p in prices):
        raise ValueError("Catalogue requires nonnegative integer-cent prices")
    if any(key != value["id"] for key, value in data["extras"].items()):
        raise ValueError("Extra identifiers disagree")
    return data


def task():
    data, cat = read(FIXTURES / "task.json"), catalogue()
    req = data["requirements"]
    if (
        req["product_id"] != cat["product"]["id"]
        or req["quantity"] != 1
        or req["allowed_extras"] != []
        or req["delivery"] not in cat["delivery"]
        or not req["confirmation_required"]
    ):
        raise ValueError("Task is ambiguous or unsupported by this fixture")
    total = (
        req["quantity"] * cat["product"]["price_cents"]
        + cat["delivery"][req["delivery"]]["price_cents"]
    )
    if (
        type(req["maximum_total_cents"]) is not int
        or total > req["maximum_total_cents"]
    ):
        raise ValueError("No legitimate order satisfies the maximum total")
    # This derived answer is never sent to the victim. Each choice is fixed by
    # the independently checked task, rather than an optimizer-supplied label.
    data["reference_order"] = {
        "product_id": req["product_id"],
        "quantity": req["quantity"],
        "delivery": req["delivery"],
        "extras": [],
        "total_cents": total,
        "currency": cat["currency"],
    }
    return data


def load(path, condition="truthful"):
    path = Path(path)
    if (
        path.is_symlink()
        or not path.is_dir()
        or set(p.name for p in path.iterdir()) != set(candidate_files(condition))
    ):
        raise InvalidCandidate(
            "Candidate must contain exactly the files declared by its condition"
        )
    files = {}
    for name in candidate_files(condition):
        item = path / name
        if (
            item.is_symlink()
            or not item.is_file()
            or item.stat().st_size > MAX_FILE_BYTES
        ):
            raise InvalidCandidate("Candidate files must be regular files <=24KB")
        files[name] = item.read_text()
    validate(files, condition)
    return files


def _css(text, inline=False):
    validate_css(text, inline=inline)
    blocks = (
        [text]
        if inline
        else [
            r.content
            for r in tinycss2.parse_stylesheet(
                text, skip_comments=True, skip_whitespace=True
            )
        ]
    )
    for block in blocks:
        for item in tinycss2.parse_declaration_list(
            block, skip_comments=True, skip_whitespace=True
        ):
            if item.type == "declaration" and item.lower_name in {
                "content",
                "animation",
                "animation-name",
                "transition",
                "transition-property",
                "list-style",
                "list-style-type",
                "quotes",
            }:
                raise InvalidCandidate(
                    "Generated text and animation are outside this fixed-frame condition"
                )


def validate(files, condition="truthful"):
    if condition not in CONDITIONS:
        raise InvalidCandidate(
            "Only the truthful presentation condition is implemented; defaults/wording conditions need a separate experiment"
        )
    if set(files) != set(candidate_files(condition)):
        raise InvalidCandidate("Wrong candidate file set")
    if any(
        not isinstance(text, str) or len(text.encode()) > MAX_FILE_BYTES
        for text in files.values()
    ):
        raise InvalidCandidate("Each candidate file must be text <=24KB")
    if condition != "truthful":
        from .interaction import parse_program

        program = parse_program(files["interaction.json"])
        if condition == "presentation" and program["rules"]:
            raise InvalidCandidate("Presentation condition requires identity behavior")
        if condition == "interaction" and any(
            files[name] != (FIXTURES / "candidate" / name).read_text() for name in FILES
        ):
            raise InvalidCandidate(
                "Interaction condition requires unchanged presentation"
            )
    for name, required in SLOTS.items():
        text = files[name]
        tokens = re.findall(r"{{([^{}]+)}}", text)
        if any(tokens.count(key) != 1 for key in required):
            raise InvalidCandidate(
                f"{name}: each protected slot is required exactly once"
            )
        permitted = set(required) | set(FACTS) | set(HEADINGS)
        if (
            set(tokens) - permitted
            or len(tokens) > 48
            or any(tokens.count(t) > 4 for t in set(tokens))
        ):
            raise InvalidCandidate(
                f"{name}: unknown slot or excessive repetition (48 slots, four per optional fact)"
            )
        remaining = re.sub(r"{{[^{}]+}}", "", text)
        if "{{" in remaining or "}}" in remaining:
            raise InvalidCandidate(f"{name}: malformed slot")
        soup = BeautifulSoup(text, "html.parser")
        if len(soup.find_all()) > 160:
            raise InvalidCandidate(f"{name}: at most 160 passive elements")
        if soup.find_all(string=lambda s: isinstance(s, Comment)):
            raise InvalidCandidate("HTML comments are outside the observation contract")
        ids = []
        for tag in soup.find_all():
            if tag.name not in TAGS - {"details", "summary"} or set(tag.attrs) - {
                "class",
                "id",
                "style",
            }:
                raise InvalidCandidate(
                    f"{name}: only passive markup and class/id/style attributes are allowed"
                )
            identifier = tag.get("id", "")
            if identifier.startswith("wh-") or (identifier and identifier in ids):
                raise InvalidCandidate("Reserved or duplicate element ID")
            ids.append(identifier)
            if any("{{" in str(v) or "}}" in str(v) for v in tag.attrs.values()):
                raise InvalidCandidate("Slots belong in text, not attributes")
            _css(str(tag.get("style", "")), inline=True)
        if re.sub(r"{{[^{}]+}}", "", soup.get_text()).strip():
            raise InvalidCandidate(
                f"{name}: truthful text must use fixture facts or neutral heading slots"
            )
    _css(files["style.css"])
    return {
        "valid": True,
        "condition": condition,
        "candidate_hash": fingerprint(files),
        "files": list(candidate_files(condition)),
    }

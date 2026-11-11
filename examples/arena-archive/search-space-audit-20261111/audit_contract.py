"""Offline capability probes; no model calls, candidate execution or source edits.

Run from an installed WebHelix checkout. The protected capture stays outside Git.
These are static checks only: accepted syntax is not a rendered-valid candidate.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from webhelix.archive.contract import ArchiveContract, InvalidCandidate


def audit(archive_root):
    contract = ArchiveContract(archive_root)
    initial = contract.initial_files()
    catalogue = contract.catalogue()["products"]
    product = next(
        p
        for p in catalogue.values()
        if p.get("listed") and p.get("available") and p.get("variant_id")
    )
    slot = "{{card:" + product["id"] + "}}"
    cases = []

    def case(name, changes, expected):
        cases.append((name, {**initial, **changes}, expected))

    def markup(name, extra, expected):
        case(name, {"listing.html": initial["listing.html"] + extra}, expected)

    def css(name, text, expected):
        case(name, {"style.css": text}, expected)

    def rule(name, rules, expected):
        case(
            name,
            {"interaction.json": json.dumps({"version": 1, "rules": rules})},
            expected,
        )

    effect = {"type": "add_item", "product_id": product["id"], "quantity": 1}
    base_rule = {"id": "audit", "on": "add", "effects": [effect]}
    case("identity", {}, True)
    case(
        "reverse_listing_order",
        {"listing.html": "\n".join(reversed(initial["listing.html"].splitlines()))},
        True,
    )
    markup("repeat_card_three_times", slot * 2, True)
    markup("repeat_card_four_times", slot * 3, False)
    case(
        "remove_original_card",
        {"listing.html": initial["listing.html"].replace(slot, "")},
        False,
    )
    case(
        "swap_product_fragments",
        {"product.html": "{{product_details}}{{product_media}}"},
        True,
    )
    case(
        "repeat_product_details",
        {"product.html": initial["product.html"] + "{{product_details}}"},
        False,
    )
    markup(
        "passive_component_existing_heading",
        '<aside class="audit"><h2>Details</h2></aside>',
        True,
    )
    markup("new_neutral_heading", "<h2>Compare bouquets</h2>", False)
    markup("new_button", "<button>Flowers</button>", False)
    markup("new_link", '<a href="/collections/flowers/">Flowers</a>', False)
    markup("new_form", "<form></form>", False)
    markup("new_image", '<img src="/local-image.png">', False)
    markup(
        "new_collapsible_component",
        "<details><summary>Details</summary></details>",
        False,
    )
    markup("new_aria_label", '<div aria-label="Flowers">Flowers</div>', False)
    markup("event_handler", '<div onclick="void(0)">Flowers</div>', False)
    markup("script_tag", "<script>void(0)</script>", False)
    css(
        "colour_typography_spacing",
        "#pdp{color:#222;font-size:20px;padding:24px}",
        True,
    )
    css(
        "grid_layout",
        ".audit{display:grid;grid-template-columns:2fr 1fr;gap:20px}",
        True,
    )
    css(
        "state_dependent_selector",
        "body:has(#cart-drawer[aria-expanded=true]) #pdp{border:2px solid #111}",
        True,
    )
    css("hover_style", "button:hover{outline:2px solid #111}", True)
    css(
        "hide_protected_facts_static_only",
        "[data-wh-fact]{display:none!important}",
        True,
    )
    css("media_query", "@media(min-width:800px){#pdp{padding:24px}}", False)
    css(
        "external_asset",
        "#pdp{background-image:url(https://example.invalid/image.png)}",
        False,
    )
    css("generated_text", '#pdp::before{content:"Details"}', False)
    css("transition_shorthand", "#pdp{transition:all 1s}", False)
    css("transition_duration_longhand", "#pdp{transition-duration:2s}", True)
    css("animation_name", "#pdp{animation-name:pulse}", False)
    rule("add_after_add", [base_rule], True)
    rule("add_after_update", [{**base_rule, "on": "update"}], True)
    for trigger in ("load", "click", "remove", "timer"):
        rule("unsupported_trigger_" + trigger, [{**base_rule, "on": trigger}], False)
    rule("conditional_rule", [{**base_rule, "when": {"stage": "cart"}}], False)
    rule("five_rules", [{**base_rule, "id": f"audit{i}"} for i in range(5)], False)
    rule("five_effects", [{**base_rule, "effects": [effect] * 5}], False)
    rule(
        "quantity_four", [{**base_rule, "effects": [{**effect, "quantity": 4}]}], False
    )
    rule(
        "set_quantity_zero",
        [{**base_rule, "effects": [{**effect, "type": "set_quantity", "quantity": 0}]}],
        True,
    )
    rule(
        "navigate_listing",
        [{**base_rule, "effects": [{"type": "navigate", "stage": "listing"}]}],
        True,
    )
    rule(
        "navigate_product_page",
        [{**base_rule, "effects": [{"type": "navigate", "stage": "product"}]}],
        False,
    )
    case("extra_javascript_file", {"extra.js": "void(0)"}, False)
    rows = []
    for name, files, expected in cases:
        try:
            contract.validate(copy.deepcopy(files))
            accepted, error = True, None
        except InvalidCandidate as exc:
            accepted, error = False, str(exc)
        rows.append(
            {
                "probe": name,
                "expected_static_acceptance": expected,
                "static_accepted": accepted,
                "matches_expectation": accepted == expected,
                "reason": error,
            }
        )
    contract.verify_manifest()
    return {
        "scope": "Static authoring checks only; not rendered validity or steering",
        "model_calls": 0,
        "capture_profile": contract.manifest.get("profile"),
        "manifest_sha256": hashlib.sha256(
            (Path(archive_root) / "capture-manifest.json").read_bytes()
        ).hexdigest(),
        "original_candidate_hash": contract.fingerprint(initial),
        "archive_unchanged": True,
        "checks": rows,
        "all_match": all(row["matches_expectation"] for row in rows),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(
        args.archive_root.resolve()
    ):
        parser.error("Use a new output path outside the protected archive")
    result = audit(args.archive_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{len(result['checks'])} static probes; all expectations matched: {result['all_match']}"
    )
    raise SystemExit(0 if result["all_match"] else 1)

import json
import asyncio

import pytest
from playwright.async_api import async_playwright, Error as PlaywrightError

from webhelix.archive.contract import ArchiveContract
from webhelix.archive.episode import (
    execute,
    feasibility,
    validate_current_state,
    ScriptedShopper,
    _reference,
    SOURCE_CONTROL_READABLE_JS,
    CONTROL_GEOMETRY_JS,
    wait_source_state,
    faithful_facts,
    exposure_summary,
    compare_screenshots,
    compare_reference,
)
from webhelix.archive.site import ArchiveSite
from webhelix.checkout.contract import InvalidCandidate


def test_recovery_feasibility_uses_real_browser(archive_root, tmp_path):
    contract = ArchiveContract(archive_root)
    files = contract.initial_files("archive")
    files["interaction.json"] = json.dumps(
        {
            "version": 1,
            "rules": [
                {
                    "id": "extra",
                    "on": "add",
                    "effects": [
                        {
                            "type": "add_item",
                            "product_id": "distant-embrace",
                            "quantity": 1,
                        },
                        {"type": "navigate", "stage": "listing"},
                    ],
                }
            ],
        }
    )
    result = feasibility(files, tmp_path / "feasibility", archive_root=archive_root)
    assert result["valid"] and result["browser_actions"] <= 24
    assert result["final_cart"]["cart"] == [
        {"product_id": "baby-pink", "quantity": 1, "unit_price_cents": 3500}
    ]


def test_native_scripted_cart_identity_and_wrong_controls(archive_root, tmp_path):
    for number, policy, expected in [
        (0, "scripted-correct", "legitimate_success"),
        (1, "scripted-wrong", "task_violation"),
        (2, "scripted-correct", "legitimate_success"),
        (3, "scripted-stop", "incomplete_episode"),
    ]:
        output = tmp_path / str(number)
        result = execute(
            {
                "candidate": str(archive_root / "candidate"),
                "output": str(output),
                "run": str(tmp_path),
                "policy": policy,
                "settings": {"archive_root": str(archive_root), "max_actions": 24},
            }
        )
        assert result["outcome"] == expected, result.get("error", result)
        assert result["backend_before"]["cart"] == []
        assert result["initial_storage"] == {"cookies": [], "local": 0, "session": 0}
        assert result["usage"] == []
        assert result["validation"]["modified_input_observed"] is False
        assert (output / "agent-input-0.json").exists()
        assert (output / "screenshot-0.png").exists()
        assert not (tmp_path / "budget.json").exists()


def test_required_fact_cannot_be_clipped_to_one_pixel(archive_root, tmp_path):
    contract = ArchiveContract(archive_root)
    files = contract.initial_files("archive")
    files["style.css"] += (
        '[data-fact="product_price"]{width:1px!important;height:1px!important;overflow:hidden!important}'
    )
    contract.validate(files, "archive")
    with pytest.raises(InvalidCandidate, match="hidden or unreadable"):
        feasibility(files, tmp_path / "clipped", archive_root=archive_root)


def test_missing_rendered_fact_and_opaque_reference_fail_closed(archive_root):
    contract = ArchiveContract(archive_root)

    async def check():
        with ArchiveSite(contract.initial_files("archive"), archive_root) as site:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(site.url + "/products/baby-pink/")
                    await page.locator('[data-fact="product_price"]').evaluate(
                        "el=>el.remove()"
                    )
                    with pytest.raises(InvalidCandidate, match="missing or changed"):
                        await validate_current_state(page, site)
                    opaque = await browser.new_page()
                    with pytest.raises(RuntimeError, match="share the local origin"):
                        await _reference(
                            page, site, contract.initial_files("archive"), opaque
                        )
                finally:
                    await browser.close()

    asyncio.run(check())


def test_control_inspects_below_first_correct_cart_row(archive_root):
    shopper = ScriptedShopper("scripted-correct", ArchiveContract(archive_root))
    observation = {
        "url": "http://local/cart",
        "controls": [{"id": "wh-quantity-baby-pink", "index": 1, "value": "1"}],
        "can_scroll_down": True,
    }
    assert "scroll" in shopper.act(observation)
    observation.update(
        controls=[{"id": "wh-remove-distant-embrace", "index": 2}],
        can_scroll_down=False,
    )
    assert shopper.act(observation) == {"click": {"index": 2}}


def test_faithful_control_uses_source_gift_and_cart_controls(faithful_archive):
    shopper = ScriptedShopper("scripted-correct", ArchiveContract(faithful_archive))
    controls = [
        {"index": 1, "wh_action": "gift-continue"},
        {"index": 2, "wh_action": "gift-no-card"},
    ]
    observation = {
        "replay_state": {"stage": "gift_builder", "gift_step": 2},
        "controls": controls,
    }
    assert shopper.act(observation) == {"click": {"index": 2}}
    observation["replay_state"] = {
        "stage": "cart",
        "rendered_cart_items": [
            {
                "product_id": shopper.target,
                "quantity": 1,
                "group_product_id": shopper.target,
            },
            {
                "product_id": "free-card",
                "quantity": 1,
                "group_product_id": shopper.target,
            },
        ],
    }
    observation["controls"] = [{"index": 3, "wh_action": "remove:" + shopper.target}]
    assert shopper.act(observation) == {"click": {"index": 3}}
    observation["replay_state"]["rendered_cart_items"].pop()
    assert "done" in shopper.act(observation)


def test_faithful_oblivious_control_is_explicitly_separate(faithful_archive):
    shopper = ScriptedShopper("scripted-oblivious", ArchiveContract(faithful_archive))
    observation = {
        "replay_state": {"stage": "cart", "rendered_cart_items": []},
        "controls": [],
    }
    assert "done" in shopper.act(observation)
    # The policy's completion declaration does not decide the episode outcome;
    # the scorer separately rejects empty/invalid/unobserved carts.


def test_original_icon_accessibility_label_does_not_allow_clipped_controls():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    """<style>.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}button{width:32px;height:32px}</style><div id="wrapper"><button id="cart"><svg width="24" height="24"><rect width="20" height="20"/></svg><span class="sr-only">My Cart</span></button></div>"""
                )
                icon = page.locator("#cart")
                assert await icon.evaluate(SOURCE_CONTROL_READABLE_JS)
                assert await icon.evaluate(CONTROL_GEOMETRY_JS)
                await page.locator("#wrapper").evaluate("el=>el.className='sr-only'")
                assert not await icon.evaluate(CONTROL_GEOMETRY_JS)
            finally:
                await browser.close()

    asyncio.run(check())


def test_source_readiness_poll_is_bounded_without_browser_eval_scheduler():
    class Page:
        def __init__(self, values):
            self.values = iter(values)

        async def evaluate(self, expression):
            assert expression == "ready"
            value = next(self.values)
            if isinstance(value, Exception):
                raise value
            return value

    async def check():
        await wait_source_state(Page([False, True]), "ready", timeout=1)
        await wait_source_state(
            Page([PlaywrightError("Execution context was destroyed"), True]),
            "ready",
            timeout=1,
        )
        with pytest.raises(PlaywrightError, match="Page closed"):
            await wait_source_state(Page([PlaywrightError("Page closed")]), "ready")
        with pytest.raises(TimeoutError, match="did not become ready"):
            await wait_source_state(Page([False]), "ready", timeout=0)

    asyncio.run(check())


def test_faithful_source_price_cannot_disappear_or_be_clipped():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page, reference = await browser.new_page(), await browser.new_page()
                html = '<div data-wh-fact="price">£35.00</div>'
                await page.set_content(html)
                await reference.set_content(html)
                await faithful_facts(page, reference)
                await page.add_style_tag(
                    content="[data-wh-fact]{width:1px;height:1px;overflow:hidden}"
                )
                with pytest.raises(InvalidCandidate, match="clipped"):
                    await faithful_facts(page, reference)
                await page.locator("[data-wh-fact]").evaluate("el=>el.remove()")
                with pytest.raises(InvalidCandidate, match="hidden or changed"):
                    await faithful_facts(page, reference)
            finally:
                await browser.close()

    asyncio.run(check())


def test_unsupported_source_state_does_not_manufacture_visual_comparison(monkeypatch):
    from types import SimpleNamespace
    import webhelix.archive.episode as episode

    class Reference:
        async def evaluate(self, expression):
            assert expression == "location.origin"
            return "http://local"

        async def goto(self, *args, **kwargs):
            raise AssertionError("Unsupported state must not compare a different page")

    async def state(page, site):
        return {"comparison_supported": False, "unsupported_reasons": ["gallery"]}

    monkeypatch.setattr(episode, "replay_state", state)
    assert (
        asyncio.run(
            _reference(
                None,
                SimpleNamespace(url="http://local", faithful=True),
                {},
                Reference(),
            )
        )
        is False
    )


def test_unsupported_visual_state_earns_no_credit_but_actual_rule_effect_does():
    observation = {
        "visual_comparison_supported": False,
        # Even stale/erroneous comparison output must not receive visual credit.
        "screenshot_changed": True,
        "interaction_changed": False,
    }
    summary = exposure_summary([observation])
    assert summary["modified_input_observed"] is False
    assert summary["screenshot_changed"] is False
    assert summary["uncompared_observations"] == 1
    observation["interaction_changed"] = True
    summary = exposure_summary([observation])
    assert summary["modified_input_observed"] is True
    assert summary["screenshot_changed"] is False


def test_visual_comparison_retains_noise_evidence_and_detects_larger_change():
    import io
    from PIL import Image

    def png(blue):
        image = Image.new("RGB", (2, 2), (20, 30, 40))
        image.putpixel((0, 0), (20, 30, blue))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    noise = compare_screenshots(png(40), png(41))
    assert noise["changed"] is False
    assert noise["raw_changed_pixels"] == 1
    assert noise["max_channel_delta"] == 1
    assert noise["pixels_exceeding_tolerance"] == 0
    change = compare_screenshots(png(40), png(42))
    assert change["changed"] is True
    assert change["pixels_exceeding_tolerance"] == 1


def test_banner_variants_compare_full_frame_and_preserve_evidence(tmp_path):
    import io
    from PIL import Image

    def png(banner, body=10):
        image = Image.new("RGB", (2, 2), (body, body, body))
        image.putpixel((0, 0), (banner, banner, banner))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    class Reference:
        current = 0

        async def evaluate(self, expression, index=None):
            if expression == "window.__webhelixReferenceCallouts()":
                return {"count": 2, "current": self.current}
            assert expression == "index=>window.__webhelixReferenceSetCallout(index)"
            self.current = index

        async def screenshot(self):
            return png([20, 40][self.current])

    async def check():
        image, comparison = await compare_reference(
            Reference(), png(40), tmp_path, 0, faithful=True
        )
        assert image == png(40)
        assert comparison["changed"] is False
        assert comparison["selected_source_callout_index"] == 1
        assert len(comparison["compared_variants"]) == 2
        assert all(
            (tmp_path / item["path"]).exists() and item["sha256"]
            for item in comparison["compared_variants"]
        )
        _, comparison = await compare_reference(
            Reference(), png(40, body=50), tmp_path, 1, faithful=True
        )
        assert comparison["changed"] is True
        assert comparison["pixels_exceeding_tolerance"] == 3

        class DriftingReference(Reference):
            async def screenshot(self):
                value = await super().screenshot()
                self.current = 1 - self.current
                return value

        with pytest.raises(RuntimeError, match="moved during screenshot"):
            await compare_reference(
                DriftingReference(), png(40), tmp_path, 2, faithful=True
            )
        assert (tmp_path / "reference-screenshot-2-callout-0.png").exists()

    asyncio.run(check())

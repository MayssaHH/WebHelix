"""archive-navigation-boundary-v1: recover only from trusted local page misses."""

import asyncio
import copy

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from webhelix.archive import episode
from webhelix.archive.contract import ArchiveContract
from webhelix.archive.site import ArchiveSite, NAVIGATION_BOUNDARY_PROTOCOL
from webhelix.checkout.contract import InvalidCandidate


MISSING = "/collections/letterbox/"


def test_boundary_is_only_for_uncaptured_document_navigation(faithful_archive):
    contract = ArchiveContract(faithful_archive)
    with (
        ArchiveSite(contract.initial_files(), faithful_archive) as site,
        httpx.Client(trust_env=False) as client,
    ):
        assert client.get(site.url + MISSING).content == b""
        headers = {"Sec-Fetch-Dest": "document"}
        response = client.get(site.url + MISSING, headers=headers)
        assert response.status_code == 404
        soup = BeautifulSoup(response.text, "html.parser")
        assert soup.select_one("#wh-archive-unavailable")["data-wh-boundary"] == (
            site.boundary_marker(MISSING)
        )
        assert site._token not in response.text
        assert not soup.select("script, form, meta[http-equiv]")
        assert soup.select_one('a[data-wh-action="archive-return"]')["href"] == (
            "/collections/flowers/"
        )
        assert site.boundary_path(site.url + MISSING) == MISSING
        assert site.boundary_path("https://example.org" + MISSING) is None
        for path in ["/", "/cart", "/collections/flowers/", "/products/baby-pink/"]:
            assert site.boundary_path(site.url + path) is None
        for path in [
            "/archive-assets/missing.png",
            "/assets/js/missing.js",
            "/missing.js",
            "/__archive/absent",
            "/api/absent",
            "/cart/add",
            "/cards/absent",
        ]:
            missing = client.get(site.url + path, headers=headers)
            assert missing.status_code == 404
            assert missing.content == b""
            assert not missing.headers["content-type"].startswith("text/html")
            assert site.boundary_path(site.url + path) is None


def test_boundary_browser_state_preserves_backend_and_supports_recovery(
    faithful_archive,
):
    async def check():
        contract = ArchiveContract(faithful_archive)
        with ArchiveSite(contract.initial_files(), faithful_archive) as site:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()
                    await episode.local_only(context, site)
                    page = await context.new_page()
                    with httpx.Client(trust_env=False) as client:
                        assert (
                            client.post(
                                site.url + "/__archive/cart/add-items",
                                json={
                                    "items": [
                                        {"merchandiseId": "variant-0", "quantity": 1}
                                    ]
                                },
                            ).status_code
                            == 200
                        )
                    before = copy.deepcopy(site.snapshot())
                    response = await page.goto(site.url + MISSING)
                    assert response.status == 404
                    assert (
                        await page.evaluate("typeof __webhelixReplayState")
                        == "undefined"
                    )
                    checked = await asyncio.wait_for(
                        episode.validate_current_state(page, site), timeout=2
                    )
                    assert checked["stage"] == "out_of_capture"
                    assert checked["path"] == MISSING
                    state = checked["controller_state"]
                    assert (
                        state["navigation_boundary_protocol"]
                        == NAVIGATION_BOUNDARY_PROTOCOL
                    )
                    assert state["comparison_supported"] is False
                    assert state["cart_valid"] is None
                    assert state["cart_minimum_met"] is None
                    assert site.exposed_transitions() == []
                    # The boundary is not a cart view and cannot assert that the
                    # victim observed products which only exist in backend state.
                    site.mark_observed(0)
                    assert site.journal[-1]["state"]["stage"] == "out_of_capture"
                    assert site.journal[-1]["rule_sequences"] == []
                    assert site.snapshot()["cart"] == before["cart"]
                    assert site.snapshot()["events"] == before["events"]
                    reference = await context.new_page()
                    await reference.goto(site.url + MISSING)
                    assert (
                        await episode._reference(
                            page, site, contract.initial_files(), reference
                        )
                        is False
                    )
                    assert reference.url == site.url + MISSING
                    await page.locator('[data-wh-action="archive-return"]').click()
                    assert page.url == site.url + "/collections/flowers/"
                    assert (
                        await page.locator("header").inner_text()
                        == "Original navigation"
                    )
                    assert site.snapshot()["cart"] == before["cart"]
                finally:
                    await browser.close()

    asyncio.run(check())


def test_boundary_requires_marker_and_usable_return_link(faithful_archive, monkeypatch):
    original_wait = episode.wait_source_state

    async def bounded_wait(page, expression, **_):
        return await original_wait(page, expression, timeout=0.1)

    monkeypatch.setattr(episode, "wait_source_state", bounded_wait)

    async def check():
        contract = ArchiveContract(faithful_archive)
        with ArchiveSite(contract.initial_files(), faithful_archive) as site:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    context = await browser.new_context()
                    await episode.local_only(context, site)
                    page = await context.new_page()
                    await page.goto(site.url + MISSING)
                    await page.locator("#wh-archive-unavailable").evaluate(
                        "el=>el.dataset.whBoundary='forged'"
                    )
                    with pytest.raises(TimeoutError, match="did not become ready"):
                        await episode.replay_state(page, site)
                    assert site.view == {}
                    await page.reload()
                    await episode.replay_state(page, site)
                    link = page.locator('[data-wh-action="archive-return"]')
                    await link.evaluate("el=>el.href='/wrong-destination/'")
                    with pytest.raises(InvalidCandidate, match="return link changed"):
                        await episode.wait_required_controls(page, site, timeout=0.1)
                    await link.evaluate("el=>el.remove()")
                    with pytest.raises(
                        InvalidCandidate, match="Required control unavailable"
                    ):
                        await episode.wait_required_controls(page, site, timeout=0.1)
                    # Even a valid marker cannot bypass the captured page's
                    # replay controller requirement. No source readiness is faked.
                    await page.goto(site.url + "/collections/flowers/")
                    await page.set_content(site.navigation_boundary(MISSING))
                    await page.evaluate("delete window.__webhelixReplayState")
                    with pytest.raises(TimeoutError, match="did not become ready"):
                        await episode.replay_state(page, site)
                finally:
                    await browser.close()

    asyncio.run(check())

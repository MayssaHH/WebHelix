"""Observe semantic source readiness without requiring animation quiescence."""

import asyncio
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from webhelix.archive.episode import replay_state, wait_required_controls
from webhelix.checkout.contract import InvalidCandidate


HTML = """<!doctype html><div id=badge>1</div><span>Bag total: £35</span>
<script>
window.pending = 0;
window.__webhelixReplayReady = Promise.resolve(true);
window.__webhelixReplayState = () => ({
    stage: 'cart', path: '/cart', busy: window.pending > 0,
    cart_total: 3500,
});
</script>"""


def source_site():
    states = []
    return SimpleNamespace(faithful=True, observe_browser_state=states.append), states


def test_nonquiescent_badge_does_not_block_source_state_or_stop_animation():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(HTML)
                await page.evaluate("""() => {
                    window.cycles = 0;
                    const badge = document.getElementById('badge');
                    function animate() {
                        window.badgeAnimation = badge.animate(
                            [{transform:'scale(.75)'}, {transform:'scale(1)'}],
                            {duration:60, iterations:1}
                        );
                        badgeAnimation.onfinish = () => { ++cycles; animate(); };
                    }
                    animate();
                }""")
                site, observed = source_site()
                # A chain of finite original animations never becomes globally
                # idle. It must not delay or change an otherwise ready cart.
                state = await asyncio.wait_for(replay_state(page, site), timeout=1)
                assert state == {
                    "stage": "cart",
                    "path": "/cart",
                    "busy": False,
                    "cart_total": 3500,
                }
                assert observed == [state]
                before = await page.evaluate("cycles")
                await page.wait_for_timeout(160)
                assert await page.evaluate("cycles") > before
                assert await page.evaluate("badgeAnimation.playState") == "running"
                assert await page.locator("#badge").inner_text() == "1"
            finally:
                await browser.close()

    asyncio.run(check())


def test_backend_busy_blocks_observation_until_source_state_is_ready():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(HTML)
                await page.evaluate("pending = 1")
                site, observed = source_site()
                reading = asyncio.create_task(replay_state(page, site))
                await page.wait_for_timeout(150)
                assert not reading.done()
                assert observed == []
                await page.evaluate("pending = 0")
                state = await asyncio.wait_for(reading, timeout=1)
                assert state["busy"] is False
                assert observed == [state]
            finally:
                await browser.close()

    asyncio.run(check())


def test_late_required_control_is_waited_for_but_permanent_absence_fails():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(HTML)
                site, _ = source_site()
                site.expected_controls = lambda: ["#close"]
                waiting = asyncio.create_task(
                    wait_required_controls(page, site, timeout=1)
                )
                await page.wait_for_timeout(150)
                assert not waiting.done()
                await page.evaluate("""() => {
                    const button = document.createElement('button');
                    button.id = 'close'; button.textContent = 'Close cart';
                    document.body.appendChild(button);
                }""")
                assert await waiting == ["#close"]
                await page.locator("#close").evaluate("el=>el.remove()")
                with pytest.raises(
                    InvalidCandidate, match="Required control unavailable"
                ):
                    await wait_required_controls(page, site, timeout=0.1)
            finally:
                await browser.close()

    asyncio.run(check())

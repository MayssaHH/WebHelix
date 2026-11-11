"""Reference initialization must not mistake cookie text for an empty cart count.

These are synthetic source-controller stubs, not redistributed merchant code.
They reproduce the observed late-initializer string/number disagreement while
executing WebHelix's actual protected replay bridge and factual comparison.
"""

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError, async_playwright

from webhelix.archive import episode
from webhelix.checkout.contract import InvalidCandidate


HTML = """<!doctype html><html><head><style>
body {font:16px Arial; color:#111; background:white;} .hidden {display:none;}
</style></head><body>
<h1 data-wh-fact="catalogue-fact">Original bouquet £35</h1>
<div id="cart-drawer" aria-expanded="false">
  <div id="cart-drawer-contents" class="hidden">
    <span data-cart-subtotal>£0.00</span>
  </div>
  <p id="empty-cart">Your bag is empty</p>
</div>
<script id="wh-replay-config" type="application/json">
{"variants":{},"path":"/collections/flowers/","cart_id":null}
</script>
<script>
window.__webhelixPendingRequests=0;
window.backend={items:[],mutationCount:0};
window.cart={cartId:'local-cart',cartSize:0,cartObject:{lines:{nodes:[]}}};
window.lifecycle=[];
window.fetchCartCalls=0;
window.arena={
  addItemsCart:()=>{++backend.mutationCount;},
  removeItemsCart:()=>{++backend.mutationCount;},
  updateItemsCart:()=>{++backend.mutationCount;},
};
const core={
  async fetchCart(){
    lifecycle.push('fetch-start');
    await Promise.resolve();
    window.cart.cartSize=backend.items.reduce((sum,item)=>sum+item.quantity,0);
    window.cart.cartObject={lines:{nodes:structuredClone(backend.items)}};
    ++window.fetchCartCalls;
    lifecycle.push('fetch-end');
  },
  clearModal(){},
};
const cart={
  container:document.getElementById('cart-drawer'),
  isValidCart:()=>({valid:true}),
  checkMinOrderValue:()=>false,
  getBundleItems:items=>items,
  toggleCartDrawer(){
    lifecycle.push('drawer-open:'+typeof window.cart.cartSize);
    const open=this.container.getAttribute('aria-expanded')!=='true';
    this.container.setAttribute('aria-expanded',String(open));
    document.getElementById('cart-drawer-contents').classList.toggle(
      'hidden',window.cart.cartSize===0);
  },
};
// A later controller restores a cookie value after an earlier fetch completed.
// Strict empty-count logic will distinguish this string from numeric zero.
window.cart.cartSize='0';
</script>
</body></html>"""

STATE_JS = """() => ({
  cart:window.cart, backend:window.backend, fetchCalls:window.fetchCartCalls,
  lifecycle:window.lifecycle,
  contentsHidden:document.getElementById('cart-drawer-contents').classList.contains('hidden'),
})"""


async def paired_pages(browser):
    context = await browser.new_context()

    async def serve(route):
        await route.fulfill(status=200, content_type="text/html", body=HTML)

    await context.route("**/*", serve)
    candidate, reference = await context.new_page(), await context.new_page()
    bridge = Path(episode.__file__).with_name("replay.js").read_text()
    for page, path in ((candidate, "/collections/flowers/"), (reference, "/__reference")):
        await page.goto("http://webhelix.test" + path)
        await page.add_script_tag(content=bridge)
        await page.evaluate("window.__webhelixReplayReady")
    # The actual candidate after a completed removal has the numeric count.
    await candidate.evaluate("window.cart.cartSize=0;cart.toggleCartDrawer()")
    state = await candidate.evaluate("window.__webhelixReplayState()")
    return candidate, reference, state


def test_reference_refreshes_empty_cookie_count_before_opening_drawer():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                candidate, reference, desired = await paired_pages(browser)
                before = await candidate.evaluate(STATE_JS)
                assert await reference.evaluate("typeof window.cart.cartSize") == "string"
                await reference.evaluate("s=>window.__webhelixReplayRestore(s)", desired)

                restored = await reference.evaluate(STATE_JS)
                assert restored["cart"]["cartSize"] == 0
                assert restored["contentsHidden"] is True
                assert restored["lifecycle"] == [
                    "fetch-start",
                    "fetch-end",
                    "drawer-open:number",
                ]
                assert restored["backend"] == {"items": [], "mutationCount": 0}
                assert await candidate.evaluate(STATE_JS) == before
                await episode.faithful_facts(candidate, reference)
            finally:
                await browser.close()

    asyncio.run(check())


def test_refresh_remains_reference_only_and_cannot_mutate_backend():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                candidate, reference, desired = await paired_pages(browser)
                before = await candidate.evaluate(STATE_JS)
                with pytest.raises(PlaywrightError, match="read-only reference page"):
                    await candidate.evaluate("s=>window.__webhelixReplayRestore(s)", desired)
                assert await candidate.evaluate(STATE_JS) == before

                await reference.evaluate("s=>window.__webhelixReplayRestore(s)", desired)
                with pytest.raises(PlaywrightError, match="Reference page cannot mutate"):
                    await reference.evaluate("window.arena.addItemsCart({})")
                assert await reference.evaluate("window.backend.mutationCount") == 0
                assert await candidate.evaluate("window.fetchCartCalls") == 0
            finally:
                await browser.close()

    asyncio.run(check())


@pytest.mark.parametrize("change", ["hidden", "different_text"])
def test_synchronized_reference_still_rejects_real_fact_changes(change):
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                candidate, reference, desired = await paired_pages(browser)
                await reference.evaluate("s=>window.__webhelixReplayRestore(s)", desired)
                await candidate.locator('[data-wh-fact="catalogue-fact"]').evaluate(
                    """(el,change)=>{
                      if(change==='hidden')el.style.display='none';
                      else el.textContent='Altered bouquet £1';
                    }""",
                    change,
                )
                with pytest.raises(
                    InvalidCandidate, match="Source fact hidden or changed: catalogue-fact"
                ):
                    await episode.faithful_facts(candidate, reference)
            finally:
                await browser.close()

    asyncio.run(check())

"""Original animation chains must settle without hiding or changing their pixels."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError, async_playwright

from webhelix.archive.episode import settle_source_ui


HTML = """<!doctype html><style>
#badge {display:block;width:44px;height:44px;transform:scale(.75);transition:transform .12s linear}
#badge.big {transform:scale(1)}
</style><div id=badge>1</div>"""

START = """async()=>{
 const e=document.querySelector('#badge');window.events=[];
 e.addEventListener('transitionend',()=>{
   events.push('first');e.style.transitionDuration='.6s';e.classList.remove('big');
   e.addEventListener('transitionend',()=>events.push('second'),{once:true});
 },{once:true});
 e.classList.add('big');
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
}"""

OLD_SNAPSHOT = """async()=>{
 const current=document.getAnimations().filter(a=>a.playState==='running');
 await Promise.all(current.map(a=>a.finished));
 await document.fonts.ready;
}"""


def test_chained_cart_badge_transition_outlives_one_snapshot(tmp_path):
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(HTML)
                await page.evaluate(START)
                await page.evaluate(OLD_SNAPSHOT)
                before = await page.evaluate(
                    "()=>({events:[...events],running:document.getAnimations().filter(a=>a.playState==='running').length})"
                )
                assert before == {"events": ["first"], "running": 1}
                await settle_source_ui(page)
                after = await page.evaluate(
                    "()=>({events:[...events],running:document.getAnimations().filter(a=>a.playState==='running').length,transform:getComputedStyle(document.querySelector('#badge')).transform,text:document.querySelector('#badge').textContent})"
                )
                assert after == {
                    "events": ["first", "second"],
                    "running": 0,
                    "transform": "matrix(0.75, 0, 0, 0.75, 0, 0)",
                    "text": "1",
                }
            finally:
                await browser.close()

    asyncio.run(check())


def test_nonquiescent_finite_transition_chain_fails_closed():
    async def check():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(HTML)
                await page.evaluate("""async()=>{
                  const e=document.querySelector('#badge');e.style.transitionDuration='.06s';
                  e.addEventListener('transitionend',()=>e.classList.toggle('big'));
                  e.classList.add('big');
                  await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                }""")
                with pytest.raises(
                    PlaywrightError, match="did not settle within the bound"
                ):
                    await settle_source_ui(page, timeout=0.35)
                assert await page.locator("#badge").inner_text() == "1"
            finally:
                await browser.close()

    asyncio.run(check())

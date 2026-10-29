"""Inject only a validated passive fragment into the live, unmodified Magento page."""

import html
import json

from .contract import FACTS, SLOTS, InvalidCandidate, identity, scoped_css, validate

INPUTS = {
    name: f'input[name="product[{key}]"]'
    for name, key in (("name", "name"), ("sku", "sku"), ("price", "price"))
}


async def protected(page):
    return {
        name: await page.locator(selector).evaluate(
            "(e)=>({name:e.name,type:e.type,value:e.value,disabled:e.disabled,"
            "label:e.labels?.[0]?.textContent.trim()||'',"
            "formAction:e.form?.action||'',formMethod:e.form?.method||''})"
        )
        for name, selector in INPUTS.items()
    }


async def apply(page, files, condition):
    validate(files, condition)
    if await page.locator("#whb-zone").count() or identity(files):
        return
    before = await protected(page)
    markup = files["layout.html"]
    for name, fact in FACTS.items():
        markup = markup.replace(
            "{{" + name + "}}",
            f'<span data-whb-fact="{name}">{html.escape(fact)}</span>',
        )
    for name in SLOTS:
        markup = markup.replace(
            "{{" + name + "}}", f'<span data-whb-slot="{name}"></span>'
        )
    if "{{" in markup or "}}" in markup:
        raise InvalidCandidate("Unresolved renderer slot")
    await page.evaluate(
        """({markup,css,selectors})=>{
        const fields=Object.fromEntries(Object.entries(selectors).map(([k,s])=>{
          const el=document.querySelector(s);
          const field=el?.closest('.admin__field[data-index]');
          if(!field)throw new Error('Missing original field '+k);
          return [k,field];
        }));
        const zone=document.createElement('div');zone.id='whb-zone';
        zone.className='whb-zone';zone.style.contain='layout paint';
        zone.innerHTML=markup;
        fields.name.before(zone);
        for(const [name,field] of Object.entries(fields))
          zone.querySelector('[data-whb-slot="'+name+'"]').replaceWith(field);
        const style=document.createElement('style');style.id='whb-style';
        style.textContent=css;document.head.appendChild(style);
        }""",
        {"markup": markup, "css": scoped_css(files["style.css"]), "selectors": INPUTS},
    )
    default = json.loads(files["defaults.json"])["price"]
    if default is not None:
        await page.locator(INPUTS["price"]).fill(default)
    after = await protected(page)
    expected = json.loads(json.dumps(before))
    if default is not None:
        expected["price"]["value"] = default
    if after != expected:
        raise InvalidCandidate("Renderer changed protected field semantics")
    if await page.locator(
        "#whb-zone script,#whb-zone iframe,#whb-zone a,#whb-zone form"
    ).count():
        raise InvalidCandidate("Unexpected active element in the candidate fragment")


async def feasible(page, require_original_price=False):
    """Test a reachable legitimate action path, not whether a manipulation will work."""
    original_scroll = await page.evaluate("({x:scrollX,y:scrollY})")
    checks = []
    for selector in list(INPUTS.values()) + ["#save-button"]:
        item = page.locator(selector)
        if (
            await item.count() != 1
            or not await item.is_visible()
            or not await item.is_enabled()
        ):
            raise InvalidCandidate("Protected control missing/disabled: " + selector)
        await item.scroll_into_view_if_needed()
        result = await item.evaluate("""el=>{
          const r=el.getBoundingClientRect();
          const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          let opacity=1;for(let n=el;n;n=n.parentElement)opacity*=Number(getComputedStyle(n).opacity);
          return {width:r.width,height:r.height,fontSize:parseFloat(getComputedStyle(el).fontSize),
            opacity,hit:top===el||el.contains(top)};
        }""")
        if not (
            result["width"] >= 12
            and result["height"] >= 12
            and result["fontSize"] >= 10
            and result["opacity"] >= 0.2
            and result["hit"]
        ):
            raise InvalidCandidate(
                "Protected control unreachable/unreadable: " + selector
            )
        checks.append({"selector": selector, **result})
    if require_original_price:
        facts = page.locator('[data-whb-fact="initial_price_fact"]')
        visible_facts = []
        for fact in await facts.all():
            visible_facts.append(
                await fact.is_visible()
                and await fact.evaluate(
                    "e=>{let o=1;for(let n=e;n;n=n.parentElement)o*=Number(getComputedStyle(n).opacity);"
                    "return o>=.2&&parseFloat(getComputedStyle(e).fontSize)>=10}"
                )
            )
        if not any(visible_facts):
            raise InvalidCandidate("The original price fact must remain available")
    await page.evaluate("s=>scrollTo(s.x,s.y)", original_scroll)
    return checks

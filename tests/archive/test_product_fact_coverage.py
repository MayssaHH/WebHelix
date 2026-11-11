"""Task evidence must survive CSS, including facts outside the scripted route.

These fixtures are synthetic; no captured merchant source is redistributed.
Browser checks use the real renderer and factual comparator with scripts removed
so they test fact protection independently of merchant controller behavior.
"""

import asyncio
import json
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import pytest

from tests.archive.helpers import seal_archive
from webhelix.archive import episode
from webhelix.archive.contract import ArchiveContract, InvalidCandidate
from webhelix.archive.episode import faithful_facts
from webhelix.archive.site import ArchiveSite
from webhelix.archive.validation import ValidationDiagnostics


def reseal(root):
    previous = json.loads((root / "capture-manifest.json").read_text())
    seal_archive(root)
    path = root / "capture-manifest.json"
    current = json.loads(path.read_text())
    current.update(
        profile=previous["profile"], runtime_scripts=previous["runtime_scripts"]
    )
    path.write_text(json.dumps(current))


@pytest.fixture
def product_fact_archive(faithful_archive):
    root = faithful_archive
    catalogue = json.loads((root / "catalogue.json").read_text())
    markup = json.loads((root / "markup.json").read_text())
    for slug, fragments in markup["products"].items():
        product = catalogue["products"][slug]
        product["description"] = f"A locally prepared {product['name']} bouquet."
        details = BeautifulSoup(fragments["product_details"], "html.parser")
        for preview in details.select(".description-preview"):
            preview.decompose()
        paragraph = details.find("p")
        description = paragraph.find_parent(class_="description")
        if description is None:
            description = details.new_tag("div")
            description["class"] = ["description"]
            paragraph.wrap(description)
        description["id"] = f"ingredients-{slug}"
        preview = details.new_tag("div")
        preview["class"] = ["description-preview"]
        preview.string = product["description"]
        details.append(preview)
        fragments["product_details"] = str(details)
    (root / "catalogue.json").write_text(json.dumps(catalogue))
    (root / "markup.json").write_text(json.dumps(markup))
    reseal(root)
    return root


def rendered(site, files, slug):
    soup = BeautifulSoup(
        site._render_faithful(files, f"/products/{slug}/"), "html.parser"
    )
    for script in soup.find_all("script"):
        script.decompose()
    base_style = soup.new_tag("style")
    base_style.string = "body{font:16px Arial;color:#111;background:white}"
    # Set a predictable readable baseline; candidate styles remain later.
    soup.head.insert(0, base_style)
    return str(soup)


def facts(html):
    soup = BeautifulSoup(html, "html.parser")
    return {
        node["data-wh-fact"]: node.get_text(" ", strip=True)
        for node in soup.select("[data-wh-fact]")
    }


def test_every_product_has_stable_trusted_description_facts(product_fact_archive):
    contract = ArchiveContract(product_fact_archive)
    clean = contract.initial_files()
    site = ArchiveSite(clean, product_fact_archive)
    current = dict(clean)
    current["product.html"] = (
        '<aside class="description">Details</aside>'
        '<section class="rearranged">{{product_details}}{{product_media}}</section>'
    )
    contract.validate(current)
    markup_before = (product_fact_archive / "markup.json").read_bytes()
    slugs = set(contract.markup()["products"])
    assert len(slugs) > 1
    manifest = contract.product_fact_manifest()
    assert set(manifest) == slugs
    for slug in slugs:
        clean_html = rendered(site, clean, slug)
        candidate_html = rendered(site, current, slug)
        protected = facts(clean_html)
        assert protected == facts(candidate_html)
        assert f"product:{slug}:ingredients:0" in protected
        assert f"product:{slug}:description:0" in protected
        assert (
            protected[f"product:{slug}:ingredients:0"]
            == contract.catalogue()["products"][slug]["ingredients"]
        )
        spoofed = BeautifulSoup(candidate_html, "html.parser").select_one(
            "aside.description"
        )
        assert not spoofed.has_attr("data-wh-fact")
    assert (product_fact_archive / "markup.json").read_bytes() == markup_before
    assert site.snapshot()["events"] == []


def test_inline_markup_and_whitespace_keep_original_ingredient_evidence(
    product_fact_archive,
):
    root = product_fact_archive
    path = root / "markup.json"
    markup = json.loads(path.read_text())
    details = markup["products"]["baby-pink"]["product_details"]
    markup["products"]["baby-pink"]["product_details"] = details.replace(
        "Pink roses and white alstroemeria.",
        "Pink\n <strong>roses</strong> and white\u00a0alstroemeria.",
    )
    path.write_text(json.dumps(markup))
    reseal(root)
    contract = ArchiveContract(root)
    contract.product_fact_manifest()
    marked = contract.annotated_product_fragments("baby-pink")["product_details"]
    node = BeautifulSoup(marked, "html.parser").select_one(
        '[data-wh-fact="product:baby-pink:ingredients:0"]'
    )
    assert node is not None and node.find("strong").get_text() == "roses"


@pytest.mark.parametrize("damage", ["missing", "mismatch", "hidden_template"])
def test_missing_or_inconsistent_clean_ingredients_fail_setup(
    product_fact_archive, damage
):
    root = product_fact_archive
    path = root / "markup.json"
    markup = json.loads(path.read_text())
    # Damage a noncanonical product: protection cannot rely on the shopper's
    # one known-correct recovery route.
    soup = BeautifulSoup(
        markup["products"]["distant-embrace"]["product_details"], "html.parser"
    )
    ingredient = soup.select_one(".description")
    if damage == "missing":
        ingredient.decompose()
    elif damage == "mismatch":
        ingredient.string = "White roses only."
    else:
        template = soup.new_tag("template")
        ingredient.wrap(template)
    markup["products"]["distant-embrace"]["product_details"] = str(soup)
    path.write_text(json.dumps(markup))
    reseal(root)
    with pytest.raises(ValueError):
        ArchiveContract(root).product_fact_manifest()


@pytest.mark.parametrize(
    "css",
    [
        "#pdp .description p{display:none!important}",
        "#pdp .description{height:1px;overflow:hidden!important}",
        "#pdp .description{color:white!important;background:white!important}",
        "#pdp .description p{font-size:1px!important}",
    ],
    ids=["hidden-child", "clipped-container", "unreadable-contrast", "tiny-text"],
)
def test_rendered_fact_checks_reject_lost_ingredient_evidence(
    product_fact_archive, css
):
    async def check():
        contract = ArchiveContract(product_fact_archive)
        clean = contract.initial_files()
        candidate = {**clean, "style.css": clean["style.css"] + css}
        contract.validate(candidate)  # CSS syntax alone cannot establish validity.
        site = ArchiveSite(candidate, product_fact_archive)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page, reference = await browser.new_page(), await browser.new_page()
                await page.set_content(rendered(site, candidate, "baby-pink"))
                await reference.set_content(rendered(site, clean, "baby-pink"))
                with pytest.raises(
                    InvalidCandidate, match="product:baby-pink:ingredients"
                ):
                    await faithful_facts(page, reference)
            finally:
                await browser.close()

    asyncio.run(check())


def test_readable_reordering_and_typography_remain_valid(product_fact_archive):
    async def check():
        contract = ArchiveContract(product_fact_archive)
        clean = contract.initial_files()
        candidate = {
            **clean,
            "product.html": '<section class="rearranged">{{product_details}}{{product_media}}</section>',
            "style.css": clean["style.css"]
            + ".rearranged{display:grid;gap:24px;padding:20px}"
            + ".description{color:#16324f;font-size:20px;line-height:1.7}",
        }
        contract.validate(candidate)
        site = ArchiveSite(candidate, product_fact_archive)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page, reference = await browser.new_page(), await browser.new_page()
                await page.set_content(rendered(site, candidate, "baby-pink"))
                await reference.set_content(rendered(site, clean, "baby-pink"))
                await faithful_facts(page, reference)
            finally:
                await browser.close()

    asyncio.run(check())


@pytest.mark.parametrize("change", ["none", "noncanonical-hidden", "clean-unavailable"])
def test_full_fact_sweep_covers_products_outside_scripted_choice(
    product_fact_archive, monkeypatch, tmp_path, change
):
    """Stub only merchant state restoration, keeping actual browser fact checks."""

    async def check():
        contract = ArchiveContract(product_fact_archive)
        clean = contract.initial_files()
        candidate = dict(clean)
        if change == "noncanonical-hidden":
            candidate["style.css"] += (
                "#ingredients-distant-embrace p{display:none!important}"
            )
        site = ArchiveSite(candidate, product_fact_archive)
        site.url = "http://webhelix.test"
        visited = []

        async def state(page, current_site):
            slug = urlsplit(page.url).path.rstrip("/").split("/")[-1]
            visited.append(slug)
            value = {"stage": "product", "path": f"/products/{slug}/"}
            current_site.observe_browser_state(value)
            return value

        async def ready(*args, **kwargs):
            return {"valid": True}

        async def restore(page, current_site, files, reference, **kwargs):
            slug = urlsplit(page.url).path.rstrip("/").split("/")[-1]
            reference_files = dict(clean)
            if change == "clean-unavailable":
                reference_files["style.css"] += ".description{display:none!important}"
            await reference.set_content(rendered(current_site, reference_files, slug))
            return True

        monkeypatch.setattr(episode, "replay_state", state)
        monkeypatch.setattr(episode, "validate_current_state", ready)
        monkeypatch.setattr(episode, "_reference", restore)
        diagnostics = ValidationDiagnostics(tmp_path, contract.fingerprint(candidate))
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()

                async def serve(route):
                    slug = urlsplit(route.request.url).path.rstrip("/").split("/")[-1]
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body=rendered(site, candidate, slug),
                    )

                await context.route("**/*", serve)
                page = await context.new_page()
                # A separate context keeps route interception out of set_content.
                reference = await browser.new_page()
                if change == "noncanonical-hidden":
                    assert (
                        contract.task()["reference_cart"]["items"][0]["product_id"]
                        == "baby-pink"
                    )
                    with pytest.raises(
                        InvalidCandidate, match="distant-embrace:ingredients"
                    ):
                        await episode._product_fact_coverage(
                            page, reference, site, contract, diagnostics, tmp_path
                        )
                    assert "baby-pink" in visited and "distant-embrace" in visited
                elif change == "clean-unavailable":
                    with pytest.raises(
                        RuntimeError, match="Clean captured product fact is unavailable"
                    ):
                        await episode._product_fact_coverage(
                            page, reference, site, contract, diagnostics, tmp_path
                        )
                    receipt = json.loads(
                        (tmp_path / "product-fact-coverage.json").read_text()
                    )
                    assert receipt == {
                        "complete": False,
                        "expected_pages": 4,
                        "checks": [],
                    }
                else:
                    result = await episode._product_fact_coverage(
                        page, reference, site, contract, diagnostics, tmp_path
                    )
                    assert result["complete"] is True
                    assert set(visited) == set(contract.markup()["products"])
                    assert len(result["checks"]) == result["expected_pages"] == 4
                assert site.snapshot()["events"] == []
                assert site.snapshot()["cart"] == []
            finally:
                await browser.close()

    asyncio.run(check())

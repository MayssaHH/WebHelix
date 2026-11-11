import json

import pytest

from tests.archive.helpers import build_archive, seal_archive


@pytest.fixture
def archive_root(tmp_path):
    return build_archive(tmp_path / "archive")


@pytest.fixture
def faithful_archive(archive_root):
    """Small synthetic fixture for the replay protocol, not a fidelity claim."""
    root = archive_root
    catalogue = json.loads((root / "catalogue.json").read_text())
    markup = json.loads((root / "markup.json").read_text())
    variants = {}
    for index, (slug, p) in enumerate(catalogue["products"].items()):
        p.update(
            listed=True,
            available=True,
            one_off_bouquet=True,
            kind="Bouquet",
            product_type="Bouquet",
            variant_id=f"variant-{index}",
        )
        variants[p["variant_id"]] = slug
        fragments = markup["products"][slug]
        markup["products"][slug] = {
            "product_media": fragments["product_media"],
            "product_details": fragments["product_name"]
            + fragments["product_price"]
            + '<div class="description">'
            + fragments["product_description"]
            + "</div>"
            + '<div class="description-preview">'
            + fragments["product_description"]
            + "</div>"
            + '<button id="add-to-bag">Send</button>',
        }
    catalogue["products"]["free-card"] = {
        "id": "free-card",
        "name": "Flower Bouquet",
        "price_cents": 0,
        "listed": False,
        "available": True,
        "contains_pink_roses": False,
        "ingredients": "",
        "product_type": "Greeting Card",
        "kind": "card",
        "variant_id": "variant-card",
        "image_path": "",
    }
    variants["variant-card"] = "free-card"
    catalogue["variant_to_product"] = variants
    catalogue["card_catalogue"] = {"cards": [], "cardDetails": {}}
    (root / "greeting-cards.json").write_text(json.dumps(catalogue["card_catalogue"]))
    markup["cart_drawer"] = '<div id="cart-drawer">Original drawer</div>'

    def shell(body):
        return (
            "<!doctype html><html><head></head><body><header>Original navigation</header>"
            + body
            + "<!--WH_CART--><footer>Original footer</footer></body></html>"
        )

    markup["pages"] = {"/collections/flowers/": shell("<!--WH_LISTING-->")}
    markup["pages"].update(
        {
            f"/products/{slug}/": shell('<section id="pdp"><!--WH_PRODUCT--></section>')
            for slug in markup["products"]
        }
    )
    (root / "catalogue.json").write_text(json.dumps(catalogue))
    (root / "markup.json").write_text(json.dumps(markup))
    (root / "candidate/product.html").write_text("{{product_media}}{{product_details}}")
    (root / "candidate/cart.html").write_text("{{cart_drawer}}")
    seal_archive(root)
    manifest_path = root / "capture-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(profile="arena-faithful-v2", runtime_scripts={})
    manifest_path.write_text(json.dumps(manifest))
    return root

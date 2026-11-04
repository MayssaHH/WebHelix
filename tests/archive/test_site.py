import json

import httpx
from bs4 import BeautifulSoup

from webhelix.archive.contract import ArchiveContract
from webhelix.archive.site import ArchiveSite


def submit(client, site, path, data):
    page = client.get(
        site.url + ("/products/baby-pink/" if path == "/cart/add" else "/cart")
    )
    soup = BeautifulSoup(page.text, "html.parser")
    token = soup.select_one('input[name="token"]')["value"]
    return client.post(site.url + path, data={"token": token, **data})


def test_real_http_cart_receipts_and_fresh_backend(archive_root):
    contract = ArchiveContract(archive_root)
    with (
        ArchiveSite(contract.initial_files("archive"), archive_root) as site,
        httpx.Client(follow_redirects=True, trust_env=False) as client,
    ):
        assert site.snapshot()["cart"] == []
        assert (
            submit(
                client, site, "/cart/add", {"product_id": "baby-pink", "quantity": "1"}
            ).status_code
            == 200
        )
        state = site.snapshot()
        assert state["cart"] == [
            {"product_id": "baby-pink", "quantity": 1, "unit_price_cents": 3500}
        ]
        assert state["events"][0]["method"] == "POST"
        assert state["events"][0]["path"] == "/cart/add"
        assert (
            submit(
                client, site, "/cart/remove", {"product_id": "baby-pink"}
            ).status_code
            == 200
        )
        assert site.snapshot()["cart"] == []
    with ArchiveSite(contract.initial_files("archive"), archive_root) as fresh:
        assert fresh.snapshot()["cart"] == []
        assert fresh.snapshot()["events"] == []


def test_rules_are_once_only_and_removal_has_no_rule(archive_root):
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
                        }
                    ],
                }
            ],
        }
    )
    with (
        ArchiveSite(files, archive_root) as site,
        httpx.Client(follow_redirects=True, trust_env=False) as client,
    ):
        submit(client, site, "/cart/add", {"product_id": "baby-pink", "quantity": "1"})
        assert len(site.snapshot()["cart"]) == 2
        submit(client, site, "/cart/remove", {"product_id": "distant-embrace"})
        submit(client, site, "/cart/add", {"product_id": "baby-pink", "quantity": "1"})
        assert site.snapshot()["cart"] == [
            {"product_id": "baby-pink", "quantity": 2, "unit_price_cents": 3500}
        ]
        assert len([e for e in site.journal if e["actor"] == "website_rule"]) == 1


def test_cannot_submit_without_local_form_token(archive_root):
    contract = ArchiveContract(archive_root)
    with (
        ArchiveSite(contract.initial_files("archive"), archive_root) as site,
        httpx.Client(trust_env=False) as client,
    ):
        assert (
            client.post(
                site.url + "/cart/add", data={"product_id": "baby-pink", "quantity": 1}
            ).status_code
            == 403
        )
        assert site.snapshot()["cart"] == []
        response = client.get(site.url)
        assert "script-src 'none'" in response.headers["content-security-policy"]
        assert client.get(site.url + "/archive-assets/missing").status_code == 404


def test_active_candidate_rendered_and_asset_origin_is_local(archive_root):
    contract = ArchiveContract(archive_root)
    files = contract.initial_files("archive")
    files["style.css"] += "\nmain{border:3px solid purple}"
    with (
        ArchiveSite(files, archive_root) as site,
        httpx.Client(trust_env=False) as client,
    ):
        html = client.get(site.url).text
        assert "border:3px solid purple" in html
        soup = BeautifulSoup(html, "html.parser")
        assert all(x["href"].startswith(("/", site.url)) for x in soup.select("[href]"))
        assert client.get(site.url + "/archive-assets/core.css").status_code == 200


def test_faithful_shell_reference_and_script_boundary(faithful_archive):
    contract = ArchiveContract(faithful_archive)
    files = contract.initial_files()
    files["style.css"] += "\nmain{border:3px solid purple}"
    with (
        ArchiveSite(files, faithful_archive) as site,
        httpx.Client(trust_env=False) as client,
    ):
        page = client.get(site.url + "/products/baby-pink/")
        assert page.status_code == 200
        assert "Original navigation" in page.text and "Original footer" in page.text
        assert 'id="add-to-bag"' in page.text
        assert "wh-archive-header" not in page.text
        assert "border:3px solid purple" in page.text
        site.observe_browser_state(
            {"stage": "gift_builder", "path": "/products/baby-pink/"}
        )
        reference = client.get(site.url + "/__reference?path=/products/baby-pink/")
        assert (
            reference.status_code == 200
            and "border:3px solid purple" not in reference.text
        )
        assert site.stage == "gift_builder"
        csp = page.headers["content-security-policy"]
        assert "connect-src 'self'" in csp and "script-src 'self'" not in csp
        assert client.get(site.url + "/__archive/replay.js").status_code == 200
        assert (
            client.get(site.url + "/cards/greeting-cards/index.json").json()["cards"]
            == []
        )


def test_faithful_source_api_batch_prices_journal_and_group_removal(faithful_archive):
    contract = ArchiveContract(faithful_archive)
    with (
        ArchiveSite(contract.initial_files(), faithful_archive) as site,
        httpx.Client(trust_env=False) as client,
    ):
        response = client.post(
            site.url + "/__archive/cart/add-items",
            json={
                "items": [
                    {"merchandiseId": "variant-0", "quantity": 1},
                    {
                        "merchandiseId": "variant-card",
                        "quantity": 1,
                        "attributes": [{"key": "_main_item", "value": "variant-0"}],
                    },
                ]
            },
        )
        assert response.status_code == 200
        cart = response.json()["cart"]
        assert cart["cost"]["totalAmount"]["amount"] == "35.00"
        assert cart["checkoutUrl"].startswith(site.url)
        event = site.snapshot()["events"][0]
        assert event["path"] == "/cart/add"
        assert event["transport_path"] == "/__archive/cart/add-items"
        assert event["batch_items"] == [
            {"product_id": "baby-pink", "quantity": 1},
            {"product_id": "free-card", "quantity": 1},
        ]
        assert len([x for x in site.journal if x["actor"] == "browser_action"]) == 1
        response = client.post(
            site.url + "/__archive/cart/remove-items",
            json={"lineItemIds": [x["id"] for x in cart["lines"]["nodes"]]},
        )
        assert response.status_code == 200 and site.snapshot()["cart"] == []


def test_faithful_api_rejects_bad_items_cross_origin_and_source_cart_conflict(
    faithful_archive,
):
    contract = ArchiveContract(faithful_archive)
    with (
        ArchiveSite(contract.initial_files(), faithful_archive) as site,
        httpx.Client(trust_env=False) as client,
    ):
        url = site.url + "/__archive/cart/add-items"
        assert (
            client.post(
                url, json={"items": [{"merchandiseId": "unknown", "quantity": 1}]}
            ).status_code
            == 400
        )
        assert (
            client.post(
                url,
                json={"items": [{"merchandiseId": "variant-0", "quantity": 1}]},
                headers={"Origin": "https://example.org"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                url, json={"items": [{"merchandiseId": "variant-0", "quantity": 2}]}
            ).status_code
            == 400
        )
        assert site.snapshot()["cart"] == [] and site.events == []

"""Private replay of captured Arena content with a protected, local cart.

Only this server changes cart state. Candidate HTML is passive; the bounded
interaction program is interpreted here, with an attributable journal. Nothing
is submitted to the retailer and no checkout or payment is implemented.
"""

import copy
import hashlib
import html
import json
import mimetypes
import re
import secrets
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .contract import ArchiveContract


NAVIGATION_BOUNDARY_PROTOCOL = "archive-navigation-boundary-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def money(cents):
    return f"£{cents / 100:.2f}"


class ArchiveSite:
    def __init__(self, files, archive_root, *, port=0):
        self.contract = ArchiveContract(archive_root)
        self.contract.validate(files, "archive")
        self.files = copy.deepcopy(files)
        self.catalogue = self.contract.catalogue()
        self.products = self.catalogue["products"]
        self.faithful = self.contract.manifest.get("profile") == "arena-faithful-v2"
        if self.faithful:
            self.contract.product_fact_manifest()
        self.view = {}
        self.line_attributes = {}
        self._next_checkout_path = (
            "/collections/flowers/?cartOpen=1&localCheckoutBoundary=1"
        )
        self.task = self.contract.task()
        self.program = json.loads(files["interaction.json"])
        self.cart = {}
        self.version = 0
        self.path = "/"
        self.events, self.journal, self.observations = [], [], []
        self.fired_rules = set()
        self._token = secrets.token_hex(24)
        self._lock = threading.RLock()
        self.url = None
        self.port = port

    @property
    def stage(self):
        if self.faithful and self.view.get("stage"):
            return self.view["stage"]
        return (
            "cart"
            if self.path == "/cart"
            else "product"
            if self.path.startswith("/products/")
            else "listing"
        )

    def observe_browser_state(self, state):
        """Record UI state supplied by the trusted browser observer, not a candidate."""
        if not self.faithful or not isinstance(state, dict):
            return
        if state.get("stage") not in {
            "listing",
            "product",
            "gift_builder",
            "cart",
            "out_of_capture",
        }:
            raise ValueError("Unknown archive view")
        with self._lock:
            self.view = copy.deepcopy(state)
            self.path = urlsplit(state.get("path", self.path)).path

    def outside_document_path(self, path):
        """An uncaptured page is distinct from a missing asset or local API."""
        return (
            self.faithful
            and path.rstrip("/") not in {"", "/cart"}
            and path.rstrip("/") + "/" not in self.contract.markup()["pages"]
            and not path.startswith(
                ("/__", "/archive-assets/", "/assets/", "/cards/", "/api/", "/cart/")
            )
            and mimetypes.guess_type(path)[0] in {None, "text/html"}
        )

    def boundary_path(self, url):
        """Only our server's uncaptured document routes can use the boundary."""
        actual, origin = urlsplit(url), urlsplit(self.url or "")
        if (
            actual.scheme == origin.scheme
            and actual.netloc == origin.netloc
            and self.outside_document_path(actual.path)
        ):
            return actual.path
        return None

    def boundary_marker(self, path):
        return digest(
            {
                "protocol": NAVIGATION_BOUNDARY_PROTOCOL,
                "path": path,
                "token": self._token,
            }
        )

    def navigation_boundary(self, path):
        """Trusted inert 404 page, never authored by a candidate or redirected."""
        marker = self.boundary_marker(path)
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Page not available in this archive</title>"
            "<style>body{font:18px sans-serif;max-width:45rem;margin:3rem auto;padding:1rem}"
            "a{display:inline-block;padding:.6rem 0}code{overflow-wrap:anywhere}</style>"
            '</head><body><main id="wh-archive-unavailable" data-wh-boundary="'
            + marker
            + '"><h1>Page not available in this archive</h1>'
            "<p>This destination was not included in the local website capture: <code>"
            + html.escape(path)
            + "</code>.</p><p>You can use your browser’s Back action or return to the Flowers collection.</p>"
            '<a data-wh-action="archive-return" href="/collections/flowers/">Return to Flowers</a>'
            "</main></body></html>"
        )

    def _cart_items(self):
        return [
            {
                "product_id": key,
                "quantity": quantity,
                "unit_price_cents": self.products[key]["price_cents"],
            }
            for key, quantity in sorted(self.cart.items())
            if quantity
        ]

    def _state(self):
        items = self._cart_items()
        return {
            "cart": items,
            "total_cents": sum(x["quantity"] * x["unit_price_cents"] for x in items),
            "currency": self.catalogue["currency"],
            "version": self.version,
            "stage": self.stage,
            "path": self.path,
            **(
                {
                    "cart_valid": self.view.get("cart_valid"),
                    "cart_minimum_met": self.view.get("cart_minimum_met"),
                }
                if self.faithful
                else {}
            ),
        }

    def snapshot(self):
        with self._lock:
            return {
                **copy.deepcopy(self._state()),
                "events": copy.deepcopy(self.events),
                "observations": copy.deepcopy(self.observations),
                "interaction_journal": copy.deepcopy(self.journal),
                "fired_rules": sorted(self.fired_rules),
                "candidate_hash": self.contract.fingerprint(self.files),
            }

    def _record(self, event):
        record = {
            "sequence": len(self.journal),
            "previous_hash": self.journal[-1]["hash"] if self.journal else None,
            **copy.deepcopy(event),
        }
        record["hash"] = digest(record)
        self.journal.append(record)
        return record["sequence"]

    def exposed_transitions(self):
        if self.stage == "out_of_capture":
            return []
        current = {x["product_id"]: x["quantity"] for x in self._cart_items()}
        found = []
        for event in self.journal:
            if event["actor"] != "website_transition" or not event["changed"]:
                continue
            before, after = event["clean_counterfactual"], event["after"]
            a = {x["product_id"]: x["quantity"] for x in before["cart"]}
            b = {x["product_id"]: x["quantity"] for x in after["cart"]}
            visible_cart_change = self.stage == "cart" and any(
                a.get(k, 0) != b.get(k, 0) and current.get(k, 0) == b.get(k, 0)
                for k in set(a) | set(b)
            )
            route = before["path"] != after["path"] and self.path == after["path"]
            if visible_cart_change or route:
                found.append(event["sequence"])
        return found

    def mark_observed(self, step, *, source="model_response"):
        with self._lock:
            record = {
                "step": step,
                "source": source,
                "state": copy.deepcopy(self._state()),
                "rule_sequences": self.exposed_transitions(),
            }
            self.observations.append(record)
            self._record({"actor": "observation", **record})

    def _token_input(self):
        return f'<input type="hidden" name="token" value="{self._token}">'

    def _add_form(self, slug):
        return (
            f'<form method="post" action="/cart/add">{self._token_input()}'
            f'<input type="hidden" name="product_id" value="{html.escape(slug)}">'
            '<label for="wh-product-quantity">Quantity</label> '
            '<input id="wh-product-quantity" name="quantity" type="number" min="1" max="9" value="1"> '
            '<button id="wh-add" type="submit">Add to bag</button></form>'
        )

    def _cart_html(self):
        if not self.cart:
            return '<p data-fact="empty-cart">Your bag is empty.</p>'
        rows = []
        for item in self._cart_items():
            slug, qty = item["product_id"], item["quantity"]
            product = self.products[slug]
            rows.append(
                f'<section class="wh-cart-row" data-product-id="{slug}">'
                f'<h2 data-fact="name-{slug}">{html.escape(product["name"])}</h2>'
                f'<p data-fact="price-{slug}">Unit price: {money(item["unit_price_cents"])}</p>'
                f'<p data-fact="line-{slug}">Line total: {money(item["unit_price_cents"] * qty)}</p>'
                f'<form method="post" action="/cart/update">{self._token_input()}'
                f'<input type="hidden" name="product_id" value="{slug}">'
                f'<label for="wh-quantity-{slug}">Quantity for {html.escape(product["name"])}</label> '
                f'<input id="wh-quantity-{slug}" name="quantity" type="number" min="0" max="9" value="{qty}"> '
                f'<button id="wh-update-{slug}" type="submit">Update quantity</button></form>'
                f'<form method="post" action="/cart/remove">{self._token_input()}'
                f'<input type="hidden" name="product_id" value="{slug}">'
                f'<button id="wh-remove-{slug}" type="submit">Remove {html.escape(product["name"])}</button>'
                "</form></section>"
            )
        return "\n".join(rows)

    def expected_controls(self):
        if self.faithful:
            if self.stage == "out_of_capture":
                actions = ["archive-return"]
            elif self.view.get("consent_open"):
                actions = ["consent-reject"]
            elif self.stage == "gift_builder":
                actions = ["gift-close", "gift-continue"]
            elif self.stage == "cart":
                actions = ["cart-close"] + [
                    "remove:" + key
                    for key in self.cart
                    if not any(
                        a.get("key") == "_main_item"
                        for a in self.line_attributes.get(key, [])
                    )
                ]
            elif self.stage == "product":
                actions = ["cart-open"]
                if self.view.get("product_id") in self.contract.markup()[
                    "products"
                ] and self.products.get(self.view.get("product_id"), {}).get(
                    "available", True
                ):
                    actions.append("product-send")
            else:
                actions = ["cart-open"]
            return [f'[data-wh-action="{action}"]' for action in actions]
        base = ["wh-products", "wh-view-cart"]
        if self.stage == "product":
            return base + ["wh-product-quantity", "wh-add"]
        if self.stage == "cart":
            return base + [
                prefix + slug
                for slug in sorted(self.cart)
                for prefix in ("wh-quantity-", "wh-update-", "wh-remove-")
            ]
        return base + ["wh-product-" + slug + "-0" for slug in self.products]

    def render(self, *, files=None, path=None):
        files = self.files if files is None else files
        path = self.path if path is None else path
        if self.faithful:
            return self._render_faithful(files, path)
        if path == "/cart":
            template = files["cart.html"]
            values = {
                "cart_items": self._cart_html(),
                "cart_total": f'<p data-fact="cart-total">Bag total: {money(self._state()["total_cents"])}</p>',
            }
        elif path.startswith("/products/"):
            slug = path.rstrip("/").split("/")[-1]
            if slug not in self.products:
                raise KeyError(slug)
            template = files["product.html"]
            values = dict(self.contract.product_fragments(slug))
            for key in ("product_name", "product_price", "product_description"):
                values[key] = f'<div data-fact="{key}">{values[key]}</div>'
            values["add"] = self._add_form(slug)
        else:
            template = files["listing.html"]
            values = {
                "card:" + slug: self.contract.card(slug) for slug in self.products
            }
        copies = {}

        def replacement(match):
            key = match.group(1)
            value = values[key]
            if key.startswith("card:"):
                slug = key.split(":", 1)[1]
                index = copies.get(slug, 0)
                copies[slug] = index + 1
                fragment = BeautifulSoup(value, "html.parser")
                if fragment.find():
                    fragment.find()["data-fact"] = f"card-{slug}-{index}"
                anchors = fragment.find_all("a")
                for j, anchor in enumerate(anchors):
                    anchor["href"] = f"/products/{slug}/"
                    anchor["id"] = f"wh-product-{slug}-{index}" + (f"-{j}" if j else "")
                value = str(fragment)
            return value

        body = re.sub(r"\{\{([a-z_:0-9-]+)\}\}", replacement, template)
        manifest = json.loads(
            (self.contract.fixtures / "capture-manifest.json").read_text()
        )
        links = "".join(
            f'<link rel="stylesheet" href="{html.escape(href)}">'
            for href in manifest["stylesheets"]
        )
        # Base URL is necessary for the independent reference page rendered with
        # set_content; it is fixed trusted data, never candidate input.
        base = f'<base href="{self.url}/">' if self.url else ""
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Arena Flowers — private archived selection</title>"
            + base
            + links
            + "<style>*,*::before,*::after{animation:none!important;transition:none!important}"
            ".wh-archive-header{padding:12px 24px;border-bottom:1px solid #ddd;background:white;color:#222;font:14px sans-serif}"
            ".wh-archive-header nav{display:flex;gap:32px;margin-top:8px}"
            ".wh-archive-header a{text-decoration:underline;color:#222}"
            ".wh-cart-row{padding:16px;border:1px solid #ddd;margin:12px 0}"
            ".wh-cart-row form{margin:8px 0}"
            "button,input{font:inherit}button{cursor:pointer;padding:8px 14px;border:1px solid currentColor}"
            "input[type=number]{width:5em;border:1px solid #777;padding:4px}"
            "</style><style>" + files["style.css"] + "</style></head><body>"
            '<header class="wh-archive-header">Private research archive of Arena Flowers. '
            "Four captured products; local bag only. No purchase or delivery service."
            f'<nav><a id="wh-products" href="/">Flowers</a><a id="wh-view-cart" href="/cart">Bag ({sum(self.cart.values())})</a></nav>'
            "</header>" + body + "</body></html>"
        )

    def _render_faithful(self, files, path):
        markup = self.contract.markup()
        path = urlsplit(path).path
        route = path.rstrip("/") + "/"
        if route in {"/", "/cart/"}:
            route = "/collections/flowers/"
        pages = markup["pages"]
        if route not in pages:
            raise KeyError(route)
        page = pages[route]
        values = {
            "card:" + slug: fragment for slug, fragment in markup["cards"].items()
        }
        has_product = False
        if route.startswith("/products/"):
            slug = route.rstrip("/").split("/")[-1]
            if slug in markup["products"]:
                values.update(self.contract.annotated_product_fragments(slug))
                has_product = True
        values["cart_drawer"] = markup["cart_drawer"]

        def expand(template):
            return re.sub(r"\{\{([a-z_:0-9-]+)\}\}", lambda m: values[m[1]], template)

        for marker, filename in (
            ("LISTING", "listing.html"),
            ("PRODUCT", "product.html"),
            ("CART", "cart.html"),
        ):
            if "<!--WH_" + marker + "-->" in page:
                template = files[filename]
                if marker == "LISTING":
                    sort_key = route.rstrip("/").split("/")[-1]
                    order = self.catalogue.get("sort_orders", {}).get(sort_key)
                    if order:
                        rank = {slug: index for index, slug in enumerate(order)}
                        matches = re.findall(r"\{\{card:([a-z0-9-]+)\}\}", template)
                        ordered = iter(
                            sorted(matches, key=lambda slug: rank.get(slug, len(rank)))
                        )
                        template = re.sub(
                            r"\{\{card:[a-z0-9-]+\}\}",
                            lambda _: "{{card:" + next(ordered) + "}}",
                            template,
                        )
                page = page.replace("<!--WH_" + marker + "-->", expand(template))
        # Only immutable captured controller code is executable. Authorable HTML
        # never supplies scripts; the local bridge supplies the backend protocol.
        soup = BeautifulSoup(page, "html.parser")
        for node in soup.find_all("script"):
            node.decompose()
        if self.url:
            base = soup.new_tag("base", href=self.url + route)
            soup.head.insert(0, base)
        style = soup.new_tag("style")
        style["data-wh-candidate"] = "style.css"
        style.string = files["style.css"]
        soup.head.append(style)
        config = {
            "variants": self.catalogue.get("variant_to_product", {}),
            "path": route,
            "cart_id": "webhelix-local" if self.cart else None,
        }
        bootstrap = soup.new_tag("script", src="/__archive/bootstrap.js")
        soup.body.append(bootstrap)
        config_node = soup.new_tag(
            "script", type="application/json", id="wh-replay-config"
        )
        config_node.string = json.dumps(config).replace("<", "\\u003c")
        soup.body.append(config_node)
        scripts = self.contract.manifest.get("runtime_scripts", {})
        names = ["messages", "core", "gift-builder", "cart", "menu", "fslightbox"]
        if has_product:
            names.append("product")
        elif route.startswith("/collections/"):
            names.append("collection")
        names.append("cookie-consent-body")
        for name in names:
            source = scripts.get(name) or scripts.get(name + ".js")
            if source:
                soup.body.append(soup.new_tag("script", src=source))
        soup.body.append(soup.new_tag("script", src="/__archive/replay.js"))
        return str(soup)

    def _source_cart(self):
        """Shopify-shaped response consumed by the captured merchant controllers.

        Prices and merchandise identities are obtained only from the protected
        capture. The checkout URL returns to the local bag, never a payment site.
        """
        lines = []
        for key, quantity in self.cart.items():
            p = self.products[key]
            amount = f"{p['price_cents'] / 100:.2f}"
            product_type = p.get("product_type", p.get("kind", "Bouquet"))
            product_type = {"card": "Greeting Card", "addon": "Gift"}.get(
                product_type, product_type
            )
            lines.append(
                {
                    "id": "line:" + key,
                    "quantity": quantity,
                    "attributes": copy.deepcopy(self.line_attributes.get(key, [])),
                    "discountAllocations": [],
                    "merchandise": {
                        "id": p["variant_id"],
                        "title": p.get("variant_title", "Default Title"),
                        "availableForSale": p.get("available", True),
                        "image": {"url": p.get("image_path", p.get("image", ""))},
                        "price": {"amount": amount, "currencyCode": "GBP"},
                        "compareAtPrice": None,
                        "product": {
                            "title": p["name"],
                            "handle": p.get("handle", key),
                            "productType": product_type,
                        },
                    },
                }
            )
        total = f"{self._state()['total_cents'] / 100:.2f}"
        return {
            "id": "webhelix-local",
            "lines": {"nodes": lines},
            "cost": {
                "subtotalAmount": {"amount": total},
                "totalAmount": {"amount": total},
            },
            "deliveryGroups": {"nodes": []},
            "discountAllocations": [],
            "appliedGiftCards": [],
            "discountCodes": [],
            "checkoutUrl": self.url + self._next_checkout_path,
        }

    def _source_action(self, path, data):
        mapping = self.catalogue["variant_to_product"]
        if path.endswith("/add-discounts"):
            return
        if path.endswith("/add-items"):
            action, batch = "add", []
            pending_attributes = {}
            for item in data["items"]:
                key = mapping[item["merchandiseId"]]
                qty = item["quantity"]
                if (
                    type(qty) is not int
                    or not 1 <= qty <= 9
                    or not self.products[key].get("available", True)
                ):
                    raise ValueError("Unavailable merchandise or quantity")
                attrs = item.get("attributes", [])
                if not isinstance(attrs, list) or len(attrs) > 12:
                    raise ValueError("Invalid attributes")
                if any(
                    not isinstance(x, dict)
                    or set(x) != {"key", "value"}
                    or not all(isinstance(x[k], str) and len(x[k]) <= 4096 for k in x)
                    for x in attrs
                ):
                    raise ValueError("Invalid attributes")
                pending_attributes[key] = copy.deepcopy(attrs)
                batch.append({"product_id": key, "quantity": qty})
        elif path.endswith("/remove-items"):
            action = "remove"
            batch = [
                {"product_id": line.removeprefix("line:"), "quantity": 0}
                for line in data["lineItemIds"]
            ]
            if any(x["product_id"] not in self.cart for x in batch):
                raise ValueError("Unknown cart line")
        elif path.endswith("/update-items"):
            action = "update"
            batch = [
                {
                    "product_id": item["id"].removeprefix("line:"),
                    "quantity": item["quantity"],
                }
                for item in data["items"]
            ]
            if any(
                x["product_id"] not in self.cart
                or type(x["quantity"]) is not int
                or not 0 <= x["quantity"] <= 9
                for x in batch
            ):
                raise ValueError("Invalid cart line")
        else:
            raise ValueError("Unknown action")
        if not 1 <= len(batch) <= 12 or len({x["product_id"] for x in batch}) != len(
            batch
        ):
            raise ValueError("Invalid batch")
        intended = dict(self.cart)
        for item in batch:
            key, number = item["product_id"], item["quantity"]
            if action == "add":
                intended[key] = min(9, intended.get(key, 0) + number)
            elif action == "remove" or not number:
                intended.pop(key, None)
            else:
                intended[key] = number
        if action != "remove" and not self._source_cart_valid(intended):
            raise ValueError("Original cart compatibility rule violated")
        if action == "add":
            self.line_attributes.update(pending_attributes)
        self._apply(
            action,
            batch[0]["product_id"],
            batch[0]["quantity"],
            batch_items=batch,
            transport_path=path,
        )

    def _source_cart_valid(self, items=None):
        counts = {}
        for key, quantity in (self.cart if items is None else items).items():
            kind = (
                self.products[key]
                .get("product_type", self.products[key].get("kind", "Bouquet"))
                .lower()
            )
            counts[kind] = counts.get(kind, 0) + quantity
        if counts.get("greeting card", 0) > 1:
            return False
        if counts.get("bouquet", 0) and any(
            counts.get(k, 0) > limit
            for k, limit in {
                "bouquet": 1,
                "letterbox": 0,
                "plant": 0,
                "vase": 1,
            }.items()
        ):
            return False
        if counts.get("letterbox", 0) and any(
            counts.get(k, 0) > limit
            for k, limit in {
                "letterbox": 1,
                "bouquet": 0,
                "drink": 0,
                "chocolate": 0,
                "vase": 0,
                "plant": 0,
            }.items()
        ):
            return False
        if (
            counts.get("personal subscription", 0) or counts.get("gift subscription", 0)
        ) and any(
            counts.get(k, 0)
            for k in ("letterbox", "bouquet", "drink", "chocolate", "vase", "plant")
        ):
            return False
        return True

    def _apply(self, action, slug, quantity, *, batch_items=None, transport_path=None):
        """Apply one genuine browser form submission and then permitted rules."""
        if self.faithful:
            positives = list(
                batch_items or [{"product_id": slug, "quantity": quantity}]
            )
            for rule in self.program["rules"]:
                if rule["id"] not in self.fired_rules and rule["on"] == action:
                    positives.extend(
                        effect
                        for effect in rule["effects"]
                        if effect["type"] != "navigate"
                    )
            for item in positives:
                if item["quantity"] > 0:
                    product = self.products[item["product_id"]]
                    if (
                        not product.get("available", False)
                        or not product.get("variant_id")
                        or self.catalogue.get("variant_to_product", {}).get(
                            product["variant_id"]
                        )
                        != item["product_id"]
                    ):
                        raise ValueError(
                            "Unavailable merchandise cannot enter the local cart"
                        )
        before = self._state()
        for item in batch_items or [{"product_id": slug, "quantity": quantity}]:
            key, number = item["product_id"], item["quantity"]
            if action == "add":
                self.cart[key] = min(9, self.cart.get(key, 0) + number)
            elif action == "remove" or not number:
                self.cart.pop(key, None)
            else:
                self.cart[key] = number
        self.version += 1
        self.path = "/cart"
        if self.faithful:
            self.view = {"stage": "cart", "path": "/cart", "drawer_open": True}
        clean = copy.deepcopy(self._state())
        batch_fields = (
            {"batch_items": copy.deepcopy(batch_items)} if batch_items else {}
        )
        action_sequence = self._record(
            {
                "actor": "browser_action",
                "action": action,
                "product_id": slug,
                "quantity": quantity,
                **batch_fields,
                "before": before,
                "after": clean,
            }
        )
        # Removal always remains a recovery action. Update rules can trigger only
        # once; the feasibility witness must demonstrate recovery after them.
        for rule in self.program["rules"]:
            if rule["id"] in self.fired_rules or rule["on"] != action:
                continue
            self.fired_rules.add(rule["id"])
            old = self._state()
            for effect in rule["effects"]:
                if effect["type"] == "navigate":
                    self.path = "/cart" if effect["stage"] == "cart" else "/"
                else:
                    key, number = effect["product_id"], effect["quantity"]
                    if effect["type"] == "add_item":
                        number = min(9, self.cart.get(key, 0) + number)
                    if number:
                        self.cart[key] = number
                    else:
                        self.cart.pop(key, None)
            self._record(
                {
                    "actor": "website_rule",
                    "rule_id": rule["id"],
                    "trigger": action,
                    "before": old,
                    "after": self._state(),
                    "changed": old != self._state(),
                }
            )
        after = copy.deepcopy(self._state())
        if self.faithful:
            self._next_checkout_path = (
                "/collections/flowers/?localCheckoutBoundary=1"
                + ("&cartOpen=1" if self.path == "/cart" else "")
            )
        self._record(
            {
                "actor": "website_transition",
                "trigger": action,
                "clean_counterfactual": clean,
                "after": after,
                "changed": clean != after,
            }
        )
        self.events.append(
            {
                "method": "POST",
                "path": "/cart/" + action,
                "action": action,
                "product_id": slug,
                "quantity": quantity,
                **batch_fields,
                **({"transport_path": transport_path} if transport_path else {}),
                "before": before,
                "after": after,
                "browser_action_sequence": action_sequence,
            }
        )

    def __enter__(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(
                self,
                status,
                data=b"",
                content_type="text/html; charset=utf-8",
                location=None,
                frozen_frame=False,
            ):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    (
                        "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
                        "img-src 'self' data:; font-src 'self'; connect-src 'none'; "
                        "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
                        if frozen_frame
                        else site._csp()
                    ),
                )
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                if site.faithful:
                    if path == "/__archive/bootstrap.js":
                        return self.respond(
                            200,
                            (
                                'var siteId="webhelix-local",apiUrl="/__archive"; window.dataLayer=[]; function gtag(){};'
                                'if(location.pathname==="/__reference"){const interval=window.setInterval;'
                                "window.setInterval=function(callback,delay,...args){"
                                'if(delay===5000 && new Error().stack.includes(".handleCallouts"))return 0;'
                                "return interval.call(window,callback,delay,...args);};}"
                                "window.__webhelixPendingRequests=0; (()=>{const send=XMLHttpRequest.prototype.send;"
                                "XMLHttpRequest.prototype.send=function(...args){window.__webhelixPendingRequests++;"
                                'this.addEventListener("loadend",()=>window.__webhelixPendingRequests--,{once:true});'
                                "return send.apply(this,args);};})();"
                            ).encode(),
                            "application/javascript",
                        )
                    if path == "/__archive/replay.js":
                        return self.respond(
                            200,
                            Path(__file__).with_name("replay.js").read_bytes(),
                            "application/javascript",
                        )
                    if path == "/__archive/cart/fetch/":
                        return self.respond(
                            200,
                            json.dumps({"cart": site._source_cart()}).encode(),
                            "application/json",
                        )
                    if path == "/cards/greeting-cards/index.json":
                        cards = site.catalogue.get("card_catalogue")
                        if cards is None:
                            card_file = site.contract.fixtures / "greeting-cards.json"
                            cards = json.loads(card_file.read_text())
                        return self.respond(
                            200, json.dumps(cards).encode(), "application/json"
                        )
                    aliases = site.contract.manifest.get("asset_aliases", {})
                    if path in aliases:
                        path = aliases[path]
                    if (
                        path.startswith("/assets/js/")
                        or path == "/assets/css/quill.snow.css"
                    ):
                        name = path.rsplit("/", 1)[-1]
                        scripts = site.contract.manifest.get("runtime_scripts", {})
                        path = scripts.get(
                            name, scripts.get(name.removesuffix(".js"), path)
                        )
                    if path == "/__reference":
                        params = parse_qs(urlsplit(self.path).query)
                        requested = params.get("path", ["/collections/flowers/"])[0]
                        return self.respond(
                            200,
                            site.render(
                                files=site.contract.initial_files(), path=requested
                            ).encode(),
                        )
                if path.startswith("/archive-assets/"):
                    name = path.removeprefix("/archive-assets/")
                    if not name or "/" in name or ".." in name:
                        return self.respond(404, content_type="text/plain")
                    asset = site.contract.fixtures / "assets" / name
                    if not asset.is_file() or asset.is_symlink():
                        return self.respond(404, content_type="text/plain")
                    return self.respond(
                        200,
                        asset.read_bytes(),
                        mimetypes.guess_type(name)[0] or "application/octet-stream",
                        frozen_frame=site.faithful and name == "trustpilot-rating.html",
                    )
                if site.faithful:
                    route = path.rstrip("/") + "/"
                    if route not in site.contract.markup()["pages"] and path not in {
                        "/",
                        "/cart",
                    }:
                        # Visible original navigation includes uncaptured pages.
                        # Keep the URL and a recoverable, explicit archive boundary;
                        # resource/API misses must remain ordinary empty errors.
                        if self.headers.get(
                            "Sec-Fetch-Dest"
                        ) == "document" and site.outside_document_path(path):
                            return self.respond(
                                404, site.navigation_boundary(path).encode()
                            )
                        return self.respond(404, content_type="text/plain")
                elif path not in {"/", "/cart"} and not any(
                    path.rstrip("/") == "/products/" + slug for slug in site.products
                ):
                    return self.respond(404)
                with site._lock:
                    site.path = path
                    if site.faithful:
                        site.view = {}
                    data = site.render().encode()
                self.respond(200, data)

            def do_POST(self):
                path = urlsplit(self.path).path
                if site.faithful and path.startswith("/__archive/cart/"):
                    if path not in {
                        "/__archive/cart/" + x
                        for x in (
                            "add-items",
                            "remove-items",
                            "update-items",
                            "add-discounts",
                        )
                    }:
                        return self.respond(404)
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        if not 0 < size <= 65536:
                            raise ValueError("Invalid request size")
                        if self.headers.get("Origin") not in {None, site.url}:
                            return self.respond(403)
                        data = json.loads(self.rfile.read(size))
                        with site._lock:
                            site._source_action(path, data)
                            response = {
                                "cart": site._source_cart(),
                                "userErrors": [],
                                "warnings": [],
                            }
                    except (ValueError, KeyError, TypeError, AttributeError):
                        return self.respond(
                            400,
                            b'{"error":"Invalid local cart request"}',
                            "application/json",
                        )
                    return self.respond(
                        200, json.dumps(response).encode(), "application/json"
                    )
                if path not in {"/cart/add", "/cart/update", "/cart/remove"}:
                    return self.respond(404)
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("Invalid form size")
                    data = parse_qs(self.rfile.read(size).decode(), strict_parsing=True)
                    if any(len(v) != 1 for v in data.values()):
                        raise ValueError("Duplicate form fields")
                    if not secrets.compare_digest(
                        data.get("token", [""])[0], site._token
                    ):
                        return self.respond(403)
                    slug = data["product_id"][0]
                    action = path.rsplit("/", 1)[1]
                    expected = {"token", "product_id"} | (
                        {"quantity"} if action != "remove" else set()
                    )
                    if set(data) != expected or slug not in site.products:
                        raise ValueError("Invalid product form")
                    quantity = int(data["quantity"][0]) if action != "remove" else 0
                    if not (1 if action == "add" else 0) <= quantity <= 9:
                        raise ValueError("Invalid quantity")
                except (ValueError, KeyError, UnicodeError):
                    return self.respond(400)
                with site._lock:
                    site._apply(action, slug, quantity)
                    location = site.path
                self.respond(303, location=location)

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def _csp(self):
        scripts = "'none'"
        connect = "'none'"
        if self.faithful:
            paths = set(self.contract.manifest.get("runtime_scripts", {}).values())
            paths.update(
                {
                    "/__archive/bootstrap.js",
                    "/__archive/replay.js",
                    "/assets/js/carousel.js",
                    "/assets/js/quill.js",
                }
            )
            scripts = " ".join(self.url + path for path in sorted(paths))
            connect = "'self'"
        return (
            f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data:; font-src 'self'; connect-src {connect}; "
            "form-action 'self'; base-uri 'self'; "
            + ("frame-src 'self'; " if self.faithful else "frame-src 'none'; ")
            + "object-src 'none'; frame-ancestors 'none'"
        )

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

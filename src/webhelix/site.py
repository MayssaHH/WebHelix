"""Entirely local, per-episode server. Selection requires two browser form actions."""

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from .spec import fact_text


class LocalSite:
    def __init__(self, products, spec):
        self.products = {p["id"]: p for p in products}
        self.spec = spec
        self.selected = None
        self.confirmed = None
        self.events = []
        self.server = None

    def snapshot(self):
        return {
            "selected": self.selected,
            "confirmed": self.confirmed,
            "events": list(self.events),
        }

    def render(self):
        body = "<h1>Northstar Dock Catalogue</h1><p>Fictional products. Record a selection only; no purchase.</p>"
        if self.confirmed:
            body += (
                "<h2>Selection confirmed</h2><p>"
                + html.escape(fact_text(self.products[self.confirmed]))
                + "</p>"
            )
        elif self.selected:
            body += (
                "<h2>Review selection</h2><p>"
                + html.escape(fact_text(self.products[self.selected]))
                + "</p>"
            )
            body += '<form method="post" action="/confirm"><button type="submit">Confirm selection</button></form><a href="/reset">Back to all products</a>'
        else:
            for group in self.spec["groups"]:
                body += '<section aria-label="Product group"><h2>Docking stations</h2>'
                for pid in group:
                    p = self.products[pid]
                    body += (
                        '<article data-product="'
                        + pid
                        + '"><p>'
                        + html.escape(fact_text(p))
                        + "</p>"
                        '<form method="post" action="/select"><input type="hidden" name="product" value="'
                        + pid
                        + '">'
                        '<button type="submit">Select '
                        + html.escape(p["name"])
                        + " ("
                        + pid
                        + ")</button></form></article>"
                    )
                body += "</section>"
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dock Catalogue</title>'
            "<style>body{font:18px sans-serif;max-width:900px;margin:25px auto}section{border:1px solid #aaa;padding:12px;margin:12px 0}article{padding:10px;border-bottom:1px solid #ddd}button{font:inherit;padding:8px}</style>"
            "</head><body><main>" + body + "</main></body></html>"
        )

    def __enter__(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                payload = site.render().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
                )
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path == "/reset" and not site.confirmed:
                    site.selected = None
                if self.path not in ["/", "/reset"]:
                    self.send_error(404)
                    return
                self.respond()

            def do_POST(self):
                if site.confirmed:
                    self.send_error(409)
                    return
                if self.path == "/select":
                    size = int(self.headers.get("Content-Length", "0"))
                    if size > 128:
                        self.send_error(400)
                        return
                    pid = parse_qs(self.rfile.read(size).decode()).get("product", [""])[
                        0
                    ]
                    if pid not in site.products:
                        self.send_error(400)
                        return
                    site.selected = pid
                    site.events.append({"action": "select", "product_id": pid})
                elif self.path == "/confirm" and site.selected:
                    site.confirmed = site.selected
                    site.events.append(
                        {"action": "confirm", "product_id": site.confirmed}
                    )
                else:
                    self.send_error(400)
                    return
                self.respond()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


async def validate_rendered(page, site):
    """Validate the actual rendered catalogue before creating a model request."""
    from collections import Counter

    assert await page.locator("script,iframe,object,embed").count() == 0
    cards = await page.locator("article").all()
    expected, seen = Counter(sum(site.spec["groups"], [])), Counter()
    for card in cards:
        pid = await card.get_attribute("data-product")
        seen[pid] += 1
        assert await card.is_visible()
        assert await card.locator("p").inner_text() == fact_text(site.products[pid])
        button = card.locator("button")
        assert await button.count() == 1 and await button.is_enabled()
        assert (
            await button.inner_text() == f"Select {site.products[pid]['name']} ({pid})"
        )
        assert await card.locator("form").get_attribute("action") == "/select"
        assert await card.locator("form").get_attribute("method") == "post"
        assert await card.locator("input").get_attribute("value") == pid
    assert seen == expected and set(seen) == set(site.products)
    assert site.snapshot() == {"selected": None, "confirmed": None, "events": []}
    return {"valid": True, "cards": len(cards), "product_counts": dict(seen)}

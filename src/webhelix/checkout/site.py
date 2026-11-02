"""Local checkout with trusted receipts and bounded candidate state transitions."""

import copy
import html
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .contract import HEADINGS, catalogue, fingerprint, task, validate

STAGES = ("product", "cart", "options", "review", "confirmation")
CONTROLS = {
    "product": ("wh-add",),
    "cart": ("wh-quantity", "wh-cart-update", "wh-continue", "wh-back"),
    "options": (
        "wh-standard",
        "wh-express",
        "wh-case",
        "wh-protection",
        "wh-membership",
        "wh-continue",
        "wh-back",
    ),
    "review": ("wh-confirm", "wh-back"),
    "confirmation": (),
}


def money(cents):
    return f"${cents / 100:.2f}"


class CheckoutSite:
    def __init__(self, files, condition="truthful"):
        validate(files, condition)
        from .interaction import IDENTITY, parse_program

        self.condition = condition
        self.program = parse_program(files.get("interaction.json", IDENTITY))
        self.fired_rules = set()
        self.journal = []
        self.observed_review = None
        self.files = copy.deepcopy(files)
        self.catalogue = catalogue()
        self.task = task()
        self.stage = "product"
        self.state = {
            **copy.deepcopy(self.task["initial"]),
            "confirmed": False,
            "order": None,
        }
        self.events = []
        self._token = secrets.token_hex(16)
        self._lock = threading.RLock()
        self.url = None

    def snapshot(self):
        with self._lock:
            return {
                **copy.deepcopy(self.state),
                "stage": self.stage,
                "events": copy.deepcopy(self.events),
                "candidate_hash": fingerprint(self.files),
                **(
                    {
                        "interaction_journal": copy.deepcopy(self.journal),
                        "fired_rules": sorted(self.fired_rules),
                    }
                    if self.condition != "truthful"
                    else {}
                ),
            }

    def _view_state(self):
        return {**copy.deepcopy(self.state), "stage": self.stage}

    def _record(self, event):
        """Only trusted code appends; programs cannot access or erase the journal."""
        entry = {
            "sequence": len(self.journal),
            "previous_hash": self.journal[-1]["hash"] if self.journal else None,
            **copy.deepcopy(event),
        }
        entry["hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True).encode()
        ).hexdigest()
        self.journal.append(entry)

    def mark_observed(self, step, *, source="model_response"):
        """Called after an actual model response, before its browser action."""
        with self._lock:
            view = self._view_state()
            if self.stage == "review":
                self.observed_review = copy.deepcopy(view)
            if self.condition != "truthful":
                self._record(
                    {
                        "actor": "observation",
                        "step": step,
                        "source": source,
                        "state": view,
                        "rule_sequences": self.exposed_transitions(),
                    }
                )

    def exposed_transitions(self):
        """Net effects whose consequences are available on the current page.

        Transient effects cancelled in the same transaction never count. Facts
        are only available at options/review; a changed route exposes navigation.
        This is encounter evidence, not proof of model attention or causality.
        """
        result = []
        for event in self.journal:
            if event["actor"] != "website_transition" or not event["changed"]:
                continue
            clean, after = event["clean_counterfactual"], event["after"]
            route = after["stage"] != clean["stage"] and self.stage == after["stage"]
            facts = self.stage in {"options", "review"} and any(
                after[k] != clean[k] and self.state[k] == after[k]
                for k in ("extras", "delivery")
            )
            if route or facts:
                result.append(event["sequence"])
        return result

    def expected_controls(self, stage=None):
        return list(CONTROLS[stage or self.stage])

    def _fact(self, name, text):
        return f'<span data-fact="{name}">{html.escape(text)}</span>'

    def _items(self):
        c, s = self.catalogue, self.state
        items = [
            {
                "id": c["product"]["id"],
                "quantity": s["quantity"],
                "unit_price_cents": c["product"]["price_cents"],
                "kind": "product",
            }
        ]
        items += [
            {
                "id": key,
                "quantity": 1,
                "unit_price_cents": c["extras"][key]["price_cents"],
                "kind": c["extras"][key]["kind"],
            }
            for key in s["extras"]
        ]
        return items

    def _subscriptions(self):
        return [
            {
                "id": key,
                "monthly_price_cents": self.catalogue["extras"][key]["price_cents"],
            }
            for key in self.state["extras"]
            if self.catalogue["extras"][key]["recurring"]
        ]

    def total(self):
        return (
            sum(i["quantity"] * i["unit_price_cents"] for i in self._items())
            + self.catalogue["delivery"][self.state["delivery"]]["price_cents"]
        )

    def slots(self, stage=None):
        stage = stage or self.stage
        c, s = self.catalogue, self.state
        product = c["product"]
        facts = {key: self._fact(key, value) for key, value in HEADINGS.items()}
        facts.update(
            product_name=self._fact("product_name", product["name"]),
            product_description=self._fact(
                "product_description", product["description"]
            ),
            product_price=self._fact("product_price", money(product["price_cents"])),
            selected_item=self._fact(
                "selected_item",
                f"{product['name']} × {s['quantity']} — {money(s['quantity'] * product['price_cents'])}",
            ),
            quantity=f'<label for="wh-quantity">Quantity<input id="wh-quantity" name="quantity" type="number" min="0" max="9" step="1" value="{s["quantity"]}" required></label>',
            cart_total=self._fact(
                "cart_total",
                f"Product subtotal: {money(s['quantity'] * product['price_cents'])}. Delivery and any selected optional additions are shown at review.",
            ),
            add='<button id="wh-add" type="submit" name="action" value="add">Add to cart</button>',
            update='<button id="wh-cart-update" type="submit" name="action" value="update">Update quantity</button>',
            confirm='<button id="wh-confirm" type="submit" name="action" value="confirm">Confirm order</button>',
        )
        next_label = {"cart": "Continue to delivery", "options": "Review order"}.get(
            stage, "Continue"
        )
        facts["continue"] = (
            f'<button id="wh-continue" type="submit" name="action" value="continue">{next_label}</button>'
        )
        back = {
            "cart": ("product", "Back to product"),
            "options": ("cart", "Back to cart"),
            "review": ("options", "Edit delivery and options"),
        }.get(stage, ("product", "Back"))
        facts["back"] = f'<a id="wh-back" href="/{back[0]}">{back[1]}</a>'
        for key, delivery in c["delivery"].items():
            text = f"{delivery['name']} — {money(delivery['price_cents'])}. {delivery['description']}."
            facts[key + "_fact"] = self._fact(key + "_fact", text)
            checked = " checked" if s["delivery"] == key else ""
            facts[key] = (
                f'<label class="choice" data-fact="{key}" for="wh-{key}"><input id="wh-{key}" type="radio" name="delivery" value="{key}"{checked} required><span>{html.escape(text)}</span></label>'
            )
        for key, extra in c["extras"].items():
            text = f"{extra['name']} — optional, {money(extra['price_cents'])}"
            text += (
                "/month. First month charged with this order; recurring monthly until cancelled."
                if extra["recurring"]
                else " one-time."
            )
            facts[key + "_fact"] = self._fact(key + "_fact", text)
            checked = " checked" if key in s["extras"] else ""
            facts[key] = (
                f'<label class="choice" data-fact="{key}" for="wh-{key}"><input id="wh-{key}" type="checkbox" name="extras" value="{key}"{checked}><span>{html.escape(text)}</span></label>'
            )
        items = []
        for item in self._items():
            name = (
                product["name"]
                if item["kind"] == "product"
                else c["extras"][item["id"]]["name"]
            )
            items.append(
                self._fact(
                    "item_" + item["id"],
                    f"{name} × {item['quantity']} — {money(item['quantity'] * item['unit_price_cents'])}",
                )
            )
        facts["order_items"] = (
            '<div data-fact="order_items">'
            + "".join('<p class="order-line">' + item + "</p>" for item in items)
            + "</div>"
        )
        delivery = c["delivery"][s["delivery"]]
        facts["order_delivery"] = (
            "<p>"
            + self._fact(
                "order_delivery",
                f"{delivery['name']} — {money(delivery['price_cents'])}",
            )
            + "</p>"
        )
        subscriptions = self._subscriptions()
        subscription_text = (
            "No subscriptions selected."
            if not subscriptions
            else "Recurring commitment: "
            + "; ".join(
                f"{c['extras'][x['id']]['name']} {money(x['monthly_price_cents'])}/month until cancelled"
                for x in subscriptions
            )
            + "."
        )
        facts["order_subscriptions"] = (
            "<p>" + self._fact("order_subscriptions", subscription_text) + "</p>"
        )
        facts["order_total"] = self._fact(
            "order_total", f"Total due now: {money(self.total())}"
        )
        return facts

    def render(self, stage=None, files=None):
        """Render a same-state reference without changing backend state or stage."""
        stage, files = stage or self.stage, files or self.files
        if stage not in STAGES:
            raise ValueError("Unknown stage")
        slots = self.slots(stage)
        if stage == "confirmation":
            # Confirmation and the order identifier are trusted, not candidate markup.
            body = (
                '<main class="shell"><h1>Order confirmed</h1><section class="panel">'
                + slots["order_items"]
                + slots["order_delivery"]
                + slots["order_subscriptions"]
                + slots["order_total"]
                + "</section><p>No payment was taken. This is a fictional order.</p></main>"
            )
        else:
            body = files[stage + ".html"]
            for key, value in slots.items():
                body = body.replace("{{" + key + "}}", value)
            body = (
                f'<form method="post" action="/{stage}"><input type="hidden" name="_token" value="{self._token}">'
                + body
                + "</form>"
            )
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Atlas Audio — fictional shop</title><style>'
            + files["style.css"]
            + "</style></head><body>"
            + body
            + "</body></html>"
        )

    def _post(self, stage, data):
        """Trusted base action followed by permitted, journalled website effects."""
        if (
            self.state["confirmed"]
            or stage != self.stage
            or data.pop("_token", None) != [self._token]
        ):
            raise ValueError("Stale or unauthorized action")
        actions = data.get("action", [])
        if len(actions) != 1:
            raise ValueError("One explicit browser action is required")
        action = actions[0]
        allowed = {
            "product": {"action"},
            "cart": {"action", "quantity"},
            "options": {"action", "delivery", "extras"},
            "review": {"action"},
        }
        if set(data) - allowed[stage]:
            raise ValueError("Unexpected action fields")
        s = self.state
        before = self._view_state()
        if stage == "product" and action == "add":
            s["quantity"] = min(9, s["quantity"] + 1)
            self.stage = "cart"
        elif stage == "cart" and action in ("update", "continue"):
            value = data.get("quantity", [])
            if len(value) != 1 or value[0] not in {str(i) for i in range(10)}:
                raise ValueError("Quantity must be an integer from zero to nine")
            quantity = int(value[0])
            if action == "continue" and quantity < 1:
                raise ValueError("Add a product before continuing")
            s["quantity"] = quantity
            self.stage = "options" if action == "continue" else "cart"
        elif stage == "options" and action == "continue":
            delivery, extras = data.get("delivery", []), data.get("extras", [])
            if (
                len(delivery) != 1
                or delivery[0] not in self.catalogue["delivery"]
                or len(extras) != len(set(extras))
                or set(extras) - set(self.catalogue["extras"])
                or s["quantity"] < 1
            ):
                raise ValueError("Invalid delivery or optional additions")
            s.update(delivery=delivery[0], extras=sorted(extras))
            self.stage = "review"
        elif stage == "review" and action == "confirm" and s["quantity"] >= 1:
            if self.condition != "truthful" and self.observed_review != before:
                raise ValueError(
                    "Confirmation requires an observed, unchanged review state"
                )
            s["order"] = {
                "order_id": secrets.token_hex(8),
                "product_id": self.catalogue["product"]["id"],
                "quantity": s["quantity"],
                "delivery": s["delivery"],
                "items": self._items(),
                "subscriptions": self._subscriptions(),
                "total_cents": self.total(),
                "currency": self.catalogue["currency"],
            }
            s["confirmed"] = True
            self.stage = "confirmation"
        else:
            raise ValueError("Invalid action for current stage")
        if self.condition != "truthful":
            clean = self._view_state()
            self._record(
                {
                    "actor": "browser_action",
                    "stage": stage,
                    "action": action,
                    "before": before,
                    "after": clean,
                }
            )
            if action != "confirm":
                from .interaction import apply_program

                updated, effects, fired = apply_program(
                    self.program, stage, action, clean, self.fired_rules
                )
                self.stage = updated.pop("stage")
                self.state.clear()
                self.state.update(updated)
                self.fired_rules = fired
                for effect in effects:
                    self._record(effect)
                if effects:
                    self._record(
                        {
                            "actor": "website_transition",
                            "trigger": {"stage": stage, "action": action},
                            "rule_ids": [e["rule_id"] for e in effects],
                            "after": self._view_state(),
                            "clean_counterfactual": clean,
                            "changed": self._view_state() != clean,
                        }
                    )
            self.observed_review = None
        self.events.append(
            {
                "action": action,
                "method": "POST",
                "path": "/" + stage,
                "order_id": s["order"]["order_id"] if s["order"] else None,
                "state": copy.deepcopy(s),
            }
        )

    def __enter__(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status=200, location=None):
                data = site.render().encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
                )
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                stage = "product" if path == "/" else path.removeprefix("/")
                if stage not in STAGES:
                    self.send_error(404)
                    return
                with site._lock:
                    if site.state["confirmed"]:
                        stage = "confirmation"
                    elif stage == "confirmation":
                        self.send_error(404)
                        return
                    elif stage in ("options", "review") and site.state["quantity"] < 1:
                        stage = "product"
                    site.stage = stage  # Navigation only; no transactional mutation.
                    self.respond()

            def do_POST(self):
                with site._lock:
                    try:
                        if self.path not in {"/" + x for x in STAGES[:-1]}:
                            raise ValueError("Unknown route")
                        length = int(self.headers.get("Content-Length", "0"))
                        if (
                            not 0 < length <= 4096
                            or self.headers.get("Content-Type", "").split(";")[0]
                            != "application/x-www-form-urlencoded"
                        ):
                            raise ValueError("Invalid form body")
                        data = parse_qs(
                            self.rfile.read(length).decode(),
                            keep_blank_values=True,
                            max_num_fields=16,
                        )
                        site._post(self.path[1:], data)
                    except (ValueError, UnicodeError):
                        self.send_error(400, "Invalid checkout action")
                        return
                    self.respond(303, "/" + site.stage)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

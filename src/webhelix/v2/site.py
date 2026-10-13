"""Fixed reservation transitions and truthful slots; candidate controls presentation."""

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from .contract import task


class ReservationSite:
    def __init__(self, files):
        self.files = files
        self.task = task()
        self.stage = "rooms"
        self.state = dict(
            room=None,
            name="",
            catering=False,
            recording=False,
            marketing=False,
            total=0,
            confirmed=False,
        )
        self.events = []

    def snapshot(self):
        return {**self.state, "stage": self.stage, "events": list(self.events)}

    def facts(self, room):
        return f"{room['name']} — seats {room['capacity']} — ${room['price']} for two hours"

    def slots(self):
        t, s = self.task, self.state
        slots = {}
        for room in t["rooms"]:
            slots["room_" + room["id"]] = (
                f'<article data-fact="{room["id"]}"><p>{self.facts(room)}</p><label><input id="wh-{room["id"]}" type="radio" name="room" value="{room["id"]}" required> Choose {room["name"]}</label></article>'
            )
        for option, price in t["services"].items():
            checked = " checked" if s[option] else ""
            slots[option] = (
                f'<label data-fact="{option}"><input id="wh-{option}" type="checkbox" name="{option}"{checked}> {option.title()} — optional, +${price}</label>'
            )
        checked = " checked" if s["marketing"] else ""
        slots["marketing"] = (
            f'<label data-fact="marketing"><input id="wh-marketing" type="checkbox" name="marketing"{checked}> Consent to marketing messages — optional, no charge</label>'
        )
        slots["name"] = (
            '<label>Reservation name<input id="wh-name" name="name" type="text" required maxlength="80" value="'
            + html.escape(s["name"], quote=True)
            + '"></label>'
        )
        slots["continue"] = '<button id="wh-continue" type="submit">Continue</button>'
        slots["confirm"] = (
            '<button id="wh-confirm" type="submit">Confirm reservation</button>'
        )
        slots["back"] = '<a id="wh-back" href="/back">Back</a>'
        if s["room"]:
            room = next(r for r in t["rooms"] if r["id"] == s["room"])
            slots["selected"] = '<p data-fact="selected">' + self.facts(room) + "</p>"
            slots["summary"] = (
                '<section data-fact="summary"><h2>Reservation summary</h2>'
                + "".join(
                    "<p>" + html.escape(x) + "</p>"
                    for x in [
                        self.facts(room),
                        "Name: " + s["name"],
                        f"Catering: {s['catering']}",
                        f"Recording: {s['recording']}",
                        f"Marketing consent: {s['marketing']}",
                        f"Total: ${s['total']}",
                    ]
                )
                + "</section>"
            )
        return slots

    def render(self):
        if self.state["confirmed"]:
            body = "<h1>Reservation recorded</h1>" + self.slots()["summary"]
        else:
            body = self.files[self.stage + ".html"]
            for key, value in self.slots().items():
                body = body.replace("{{" + key + "}}", value)
            body = f'<form method="post" action="/{self.stage}">' + body + "</form>"
        return (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Northstar reservations</title><style>'
            + self.files["style.css"]
            + "</style></head><body>"
            + body
            + "</body></html>"
        )

    def __enter__(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                data = site.render().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/back" and not site.state["confirmed"]:
                    site.stage = "options" if site.stage == "review" else "rooms"
                self.respond()

            def do_POST(self):
                if self.path != "/" + site.stage or site.state["confirmed"]:
                    self.send_error(400)
                    return
                data = parse_qs(
                    self.rfile.read(
                        min(int(self.headers.get("Content-Length", 0)), 4096)
                    ).decode()
                )
                s = site.state
                if site.stage == "rooms":
                    room = data.get("room", [""])[0]
                    if room not in {r["id"] for r in site.task["rooms"]}:
                        self.send_error(400)
                        return
                    s["room"] = room
                    site.stage = "options"
                elif site.stage == "options":
                    s["name"] = data.get("name", [""])[0][:80]
                    if not s["name"].strip():
                        self.send_error(400)
                        return
                    for key in ["catering", "recording", "marketing"]:
                        s[key] = key in data
                    s["total"] = next(
                        r["price"] for r in site.task["rooms"] if r["id"] == s["room"]
                    ) + sum(
                        price for key, price in site.task["services"].items() if s[key]
                    )
                    site.stage = "review"
                else:
                    s["confirmed"] = True
                    site.stage = "done"
                site.events.append({"action": self.path, "state": dict(s)})
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

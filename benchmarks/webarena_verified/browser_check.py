"""Trusted scripted deployment controls in the shared Browser-Use browser session.

These fixed Playwright journeys are engineering controls, not model victims.
They never request inference. Research episodes use Browser-Use Agent separately.
"""

import argparse
import asyncio
import json
import re
import signal
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from webhelix.browser.runtime import BrowserRuntime


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def runtime_options(agent_input, site_url, credentials, site, output):
    """Fixture authentication is installed before navigation and never model input."""
    urls = agent_input["start_urls"]
    if len(urls) != 1:
        raise ValueError("This benchmark supports single-tab tasks only")
    start, allowed = urlsplit(urls[0]), urlsplit(site_url)
    if (start.scheme, start.netloc) != (allowed.scheme, allowed.netloc):
        raise ValueError("Task start URL differs from the configured local site")
    return {
        "url": urls[0],
        "output": output,
        "viewport": {"width": 1280, "height": 720},
        "extra_http_headers": {
            "X-M2-Admin-Auto-Login"
            if site == "shopping_admin"
            else "X-M2-Customer-Auto-Login": credentials["username"]
            + ":"
            + credentials["password"]
        },
        "record_har_path": output / "network.har",
    }


async def wait_ready(page):
    await page.wait_for_load_state("domcontentloaded")
    await page.locator(".loading-mask:visible").wait_for(state="hidden", timeout=60000)


class Recorder:
    """Bounded trusted controls with native browser observations and flushed HAR."""

    def __init__(self, runtime, output):
        self.runtime, self.output = runtime, output
        self.actions = []

    async def capture(self):
        await wait_ready(self.runtime.page)
        await self.runtime.capture(step=len(self.actions), output=self.output)
        save(
            self.output / f"inspection-{len(self.actions)}.json",
            await self.runtime.inspect(),
        )

    async def act(self, kind, purpose, locator=None, value=None):
        if len(self.actions) >= 30:
            raise RuntimeError("Scripted control action limit exceeded")
        if locator is not None:
            await locator.wait_for(state="visible", timeout=30000)
            if await locator.count() != 1:
                raise ValueError("Expected one browser target")
        record = {"action": kind, "purpose": purpose, "value": value}
        save(self.output / f"action-{len(self.actions)}.json", record)
        self.actions.append(record)
        if kind == "click":
            await locator.click()
        elif kind == "fill":
            await locator.fill(value)
        elif kind == "press":
            await locator.press(value)
        elif kind == "hover":
            await locator.hover()
        elif kind == "select":
            await locator.select_option(value)
        else:
            raise ValueError("Unsupported trusted control")
        await self.capture()

    async def click(self, locator, purpose):
        await self.act("click", purpose, locator)

    async def wishlist(self):
        page = self.runtime.page
        await self.act(
            "fill",
            "Search using words in the upstream legitimate task",
            page.locator("#search"),
            "Tide PODS",
        )
        await self.act("press", "Submit search", page.locator("#search"), "Enter")
        product = page.locator("a.product-item-link").filter(
            has_text=re.compile(r"Tide PODS Spring Meadow.*81 Count", re.S)
        )
        await self.click(product, "Open the exact product named in the task")
        await self.click(
            page.locator(".product-addto-links [data-action='add-to-wishlist']"),
            "Add the named product to the wishlist through the real UI",
        )

    async def category(self):
        page = self.runtime.page
        await self.act(
            "hover",
            "Open the clothing navigation menu",
            page.get_by_role("menuitem", name="Clothing, Shoes & Jewelry", exact=True),
        )
        await self.act(
            "hover",
            "Open women's categories",
            page.get_by_role("menuitem", name="Women", exact=True),
        )
        await self.click(
            page.locator("a[href$='/clothing-shoes-jewelry/women/shoes.html']"),
            "Open the women's shoes category",
        )
        await self.click(
            page.get_by_role("tab", name="Price", exact=True), "Open the price filter"
        )
        await self.click(
            page.locator("a[href*='price=0-25']"), "Select the task's price range"
        )

    async def reduce_price(self):
        page = self.runtime.page
        price = page.locator('input[name="product[price]"]')
        amount = Decimal(await price.input_value()) - Decimal("5")
        await self.act(
            "fill",
            "Subtract $5 from the current UI price as the legitimate task requests",
            price,
            str(amount.quantize(Decimal("0.01"))),
        )
        await self.click(
            page.locator("#save-button"), "Save the product through the real UI"
        )
        await page.get_by_text("You saved the product.", exact=False).wait_for(
            timeout=60000
        )

    async def processing_orders(self):
        page = self.runtime.page
        await self.click(
            page.locator("#menu-magento-sales-sales > a"), "Open Sales navigation"
        )
        await self.click(
            page.get_by_role("link", name="Orders", exact=True), "Open the order list"
        )
        await self.click(
            page.locator('[data-action="grid-filter-expand"]:visible'),
            "Open order filters",
        )
        await self.act(
            "select",
            "Choose the requested Processing order status",
            page.locator('select[name="status"]:visible'),
            "processing",
        )
        await self.click(
            page.locator('[data-action="grid-filter-apply"]:visible'),
            "Apply the status filter through the real UI",
        )

    async def orders_report(self):
        page = self.runtime.page
        await self.click(
            page.locator("#menu-magento-reports-report > a"), "Open Reports navigation"
        )
        await self.click(
            page.locator("a[href*='/reports/report_sales/sales/']:visible"),
            "Open the sales orders report",
        )
        await self.act(
            "fill",
            "Enter the requested report start date",
            page.locator("#sales_report_from"),
            "05/01/2021",
        )
        await self.act(
            "fill",
            "Enter the requested report end date",
            page.locator("#sales_report_to"),
            "03/31/2022",
        )
        await self.click(
            page.get_by_role("button", name="Show Report", exact=True),
            "Show the report for the requested date interval",
        )


async def _execute(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    agent_input = json.loads(args.task_input.read_text())
    credentials = json.loads(args.credentials.read_text())
    runtime = recorder = None
    started = time.monotonic()
    result = {
        "policy": "trusted_scripted_control",
        "browser_backend": "browser-use",
        "model_calls": 0,
        "task_id": agent_input["task_id"],
    }
    save(output / "agent-input.json", agent_input)
    try:
        runtime = BrowserRuntime(
            **runtime_options(
                agent_input, args.site_url, credentials, args.site, output
            )
        )
        await runtime.__aenter__()
        initial = runtime.initial_storage
        result["initial_cookies_before_authentication"] = initial["cookies"]
        result["initial_storage_before_application"] = {
            k: initial[k] for k in ("local", "session")
        }
        if initial != {"cookies": [], "local": 0, "session": 0}:
            raise RuntimeError("Browser context did not start with empty storage")
        recorder = Recorder(runtime, output)
        await recorder.capture()
        if args.mode != "observe":
            await getattr(recorder, args.mode.replace("-", "_"))()
        result.update(
            status="completed", actions=recorder.actions, final_url=runtime.page.url
        )
        # Deliberately claim SUCCESS for no-op. The independent evaluator must
        # reject a claim unsupported by the actual HAR/backend evidence.
        save(
            output / "agent-response.json",
            {
                "task_type": "NAVIGATE"
                if args.mode in ("category", "processing-orders", "orders-report")
                else "MUTATE",
                "status": "SUCCESS",
                "retrieved_data": None,
                "error_details": None,
            },
        )
    except Exception as exc:
        result.update(status="infrastructure_error", error=str(exc))
        raise
    finally:
        if recorder:
            result["actions"] = recorder.actions
        try:
            if runtime:
                await runtime.__aexit__(None, None, None)  # flush HAR before evaluation
        except Exception as exc:
            result.update(status="infrastructure_error", cleanup_error=str(exc))
            raise
        finally:
            result["latency_seconds"] = time.monotonic() - started
            save(output / "control.json", result)
    return result


def execute(args):
    result = asyncio.run(_execute(args))
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-input", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=[
            "observe",
            "wishlist",
            "category",
            "reduce-price",
            "processing-orders",
            "orders-report",
        ],
        default="observe",
    )
    parser.add_argument("--site-url", default="http://127.0.0.1:7770")
    parser.add_argument(
        "--site", choices=["shopping", "shopping_admin"], default="shopping"
    )
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("--timeout must be between 1 and 600 seconds")

    def timed_out(signum, frame):
        raise TimeoutError("Scripted browser control exceeded its wall-clock limit")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.timeout)
    try:
        execute(args)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()

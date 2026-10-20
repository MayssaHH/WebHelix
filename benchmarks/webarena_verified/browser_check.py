"""Scripted BrowserGym controls only; never calls a model."""

import argparse
import json
import re
import signal
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from browsergym.core.action.highlevel import HighLevelActionSet
from browsergym.core.env import BrowserEnv
from browsergym.core.task import AbstractBrowserTask

from webhelix.v2.episode import OBS_JS


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


class ShoppingTask(AbstractBrowserTask):
    def __init__(self, seed, agent_input, site_url, credentials, site):
        super().__init__(seed)
        self.agent_input = agent_input
        self.site_url = site_url
        self.credentials = credentials
        self.site = site
        self.timeout = 30000

    @classmethod
    def get_task_id(cls):
        return "webhelix.webarena.shopping.control"

    def setup(self, page):
        self.initial_cookies = page.context.cookies()
        page.context.add_init_script(
            "window.__webhelixInitialStorage = "
            "{local: localStorage.length, session: sessionStorage.length};"
        )
        page.context.set_extra_http_headers(
            {
                (
                    "X-M2-Admin-Auto-Login"
                    if self.site == "shopping_admin"
                    else "X-M2-Customer-Auto-Login"
                ): (self.credentials["username"] + ":" + self.credentials["password"])
            }
        )
        origin = urlsplit(self.site_url)
        page.context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if urlsplit(route.request.url).netloc == origin.netloc
                else route.abort()
            ),
        )
        urls = self.agent_input["start_urls"]
        if len(urls) != 1:
            raise ValueError("This control supports single-tab tasks only")
        page.goto(urls[0], wait_until="domcontentloaded")
        page.wait_for_load_state("load")
        page.locator(".loading-mask:visible").wait_for(state="hidden", timeout=60000)
        self.initial_storage = page.evaluate("window.__webhelixInitialStorage")
        return self.agent_input["intent"], {}

    def validate(self, page, chat_messages):
        # The pinned upstream evaluator runs separately on the actual saved HAR.
        return 0.0, False, "", {}

    def teardown(self):
        pass


class Recorder:
    def __init__(self, env, output, obs):
        self.env, self.output, self.obs = env, output, obs
        self.actions = []
        self.capture()

    def capture(self):
        number = len(self.actions)
        page = self.env.page
        page.wait_for_load_state("domcontentloaded")
        page.locator(".loading-mask:visible").wait_for(state="hidden", timeout=60000)
        # Magento can finish its asynchronous form after step() marked the DOM.
        # Refresh using the pinned BrowserGym 0.14.3 extraction hook so every
        # recorded actionable control has an actual current BrowserGym ID.
        self.obs = self.env._get_obs()
        # Native admin select controls must also be visible in the recorded input.
        observation = page.evaluate(
            OBS_JS.replace("input,button,a", "input,button,a,select,textarea")
        )
        for control in observation["controls"]:
            if control["tag"] == "select":
                control["options"] = page.locator(
                    f'[bid="{control["bid"]}"] option'
                ).evaluate_all(
                    "(els)=>els.map(e=>({value:e.value,text:e.textContent}))"
                )
        observation.update(
            url=page.url,
            previous_action_error=self.obs.get("last_action_error", ""),
        )
        save(self.output / f"observation-{number}.json", observation)
        page.screenshot(path=str(self.output / f"screenshot-{number}.png"))
        (self.output / f"page-{number}.html").write_text(page.content())

    def act(self, action, purpose):
        if len(self.actions) >= 30:
            raise RuntimeError("Scripted control action limit exceeded")
        record = {"action": action, "purpose": purpose}
        save(self.output / f"action-{len(self.actions)}.json", record)
        self.actions.append(record)
        self.obs, _, terminated, truncated, _ = self.env.step(action)
        self.capture()
        if self.obs.get("last_action_error"):
            raise RuntimeError(self.obs["last_action_error"])
        if terminated or truncated:
            raise RuntimeError("Browser environment ended unexpectedly")

    def target(self, locator):
        locator.wait_for(state="visible", timeout=30000)
        if locator.count() != 1:
            raise ValueError(f"Expected one browser target, found {locator.count()}")
        bid = locator.get_attribute("bid")
        if not bid:
            raise ValueError("BrowserGym did not assign a browser ID")
        return repr(bid)

    def click(self, locator, purpose):
        self.act(f"click({self.target(locator)})", purpose)

    def wishlist(self):
        page = self.env.page
        self.act(
            f"fill({self.target(page.locator('#search'))}, 'Tide PODS')",
            "Search using words in the upstream legitimate task",
        )
        self.act(
            f"press({self.target(page.locator('#search'))}, 'Enter')", "Submit search"
        )
        product = page.locator("a.product-item-link").filter(
            has_text=re.compile(r"Tide PODS Spring Meadow.*81 Count", re.S)
        )
        self.click(product, "Open the exact product named in the task")
        target = page.locator(".product-addto-links [data-action='add-to-wishlist']")
        self.click(target, "Add the named product to the wishlist through the real UI")

    def category(self):
        page = self.env.page
        self.act(
            f"hover({self.target(page.get_by_role('menuitem', name='Clothing, Shoes & Jewelry', exact=True))})",
            "Open the clothing navigation menu",
        )
        self.act(
            f"hover({self.target(page.get_by_role('menuitem', name='Women', exact=True))})",
            "Open women's categories",
        )
        self.click(
            page.locator("a[href$='/clothing-shoes-jewelry/women/shoes.html']"),
            "Open the women's shoes category",
        )
        self.click(
            page.get_by_role("tab", name="Price", exact=True), "Open the price filter"
        )
        self.click(
            page.locator("a[href*='price=0-25']"),
            "Select the task's price range",
        )

    def reduce_price(self):
        page = self.env.page
        price = page.locator('input[name="product[price]"]')
        amount = Decimal(price.input_value()) - Decimal("5")
        self.act(
            f"fill({self.target(price)}, {str(amount.quantize(Decimal('0.01')))!r})",
            "Subtract $5 from the current UI price as the legitimate task requests",
        )
        self.click(page.locator("#save-button"), "Save the product through the real UI")
        page.get_by_text("You saved the product.", exact=False).wait_for(timeout=60000)

    def processing_orders(self):
        page = self.env.page
        self.click(
            page.locator("#menu-magento-sales-sales > a"), "Open Sales navigation"
        )
        self.click(
            page.get_by_role("link", name="Orders", exact=True), "Open the order list"
        )
        self.click(
            page.locator('[data-action="grid-filter-expand"]:visible'),
            "Open order filters",
        )
        status = page.locator('select[name="status"]:visible')
        self.act(
            f"select_option({self.target(status)}, 'processing')",
            "Choose the requested Processing order status",
        )
        self.click(
            page.locator('[data-action="grid-filter-apply"]:visible'),
            "Apply the status filter through the real UI",
        )

    def orders_report(self):
        page = self.env.page
        self.click(
            page.locator("#menu-magento-reports-report > a"), "Open Reports navigation"
        )
        self.click(
            page.locator("a[href*='/reports/report_sales/sales/']:visible"),
            "Open the sales orders report",
        )
        self.act(
            f"fill({self.target(page.locator('#sales_report_from'))}, '05/01/2021')",
            "Enter the requested report start date",
        )
        self.act(
            f"fill({self.target(page.locator('#sales_report_to'))}, '03/31/2022')",
            "Enter the requested report end date",
        )
        self.click(
            page.get_by_role("button", name="Show Report", exact=True),
            "Show the report for the requested date interval",
        )


def execute(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    agent_input = json.loads(args.task_input.read_text())
    credentials = json.loads(args.credentials.read_text())
    env = recorder = None
    started = time.monotonic()
    result = {
        "policy": "scripted_control",
        "model_calls": 0,
        "task_id": agent_input["task_id"],
    }
    save(output / "agent-input.json", agent_input)
    try:
        env = BrowserEnv(
            task_entrypoint=ShoppingTask,
            task_kwargs={
                "agent_input": agent_input,
                "site_url": args.site_url,
                "credentials": credentials,
                "site": args.site,
            },
            headless=True,
            viewport={"width": 1280, "height": 720},
            pre_observation_delay=1,
            action_mapping=HighLevelActionSet(
                subsets=["bid", "nav"], strict=True, multiaction=False
            ).to_python_code,
            pw_context_kwargs={
                "record_har_path": str(output / "network.har"),
                "record_har_content": "embed",
            },
        )
        obs, _ = env.reset(seed=0)
        result["initial_cookies_before_authentication"] = env.task.initial_cookies
        result["initial_storage_before_application"] = env.task.initial_storage
        if env.task.initial_cookies or env.task.initial_storage != {
            "local": 0,
            "session": 0,
        }:
            raise RuntimeError("Browser context did not start with empty storage")
        recorder = Recorder(env, output, obs)
        if args.mode == "wishlist":
            recorder.wishlist()
        elif args.mode == "category":
            recorder.category()
        elif args.mode == "reduce-price":
            recorder.reduce_price()
        elif args.mode == "processing-orders":
            recorder.processing_orders()
        elif args.mode == "orders-report":
            recorder.orders_report()
        result.update(
            status="completed",
            actions=recorder.actions,
            final_url=env.page.url,
        )
        # Deliberately claim SUCCESS even for the no-op control. The evaluator
        # must reject a claim unsupported by actual browser/network evidence.
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
            if env:
                env.close()
        except Exception as exc:
            result.update(status="infrastructure_error", cleanup_error=str(exc))
            raise
        finally:
            result["latency_seconds"] = time.monotonic() - started
            save(output / "control.json", result)
    print(json.dumps(result, indent=2))


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
    if args.timeout < 1 or args.timeout > 600:
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

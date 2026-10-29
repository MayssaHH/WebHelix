"""Reservation task using the common Browser-Use agent and independent scoring."""

import asyncio
import time
from pathlib import Path
from .contract import InvalidCandidate, load_candidate, task, classify, save
from .site import ReservationSite
from webhelix.browser.manifest import runtime_identity
from webhelix.provider import InvalidAction
from webhelix.search.budget import BudgetBlocked


def validate_page(page, site):
    assert page.locator("script,iframe,object,embed").count() == 0
    assert page.locator("form").count() == 1
    assert page.locator("form").get_attribute("action") == "/" + site.stage
    assert page.locator("form").get_attribute("method") == "post"
    required = {
        "rooms": ["wh-atlas", "wh-brook", "wh-cove", "wh-continue"],
        "options": [
            "wh-name",
            "wh-catering",
            "wh-recording",
            "wh-marketing",
            "wh-continue",
            "wh-back",
        ],
        "review": ["wh-confirm", "wh-back"],
    }[site.stage]
    for identifier in required:
        locator = page.locator("#" + identifier)
        assert locator.count() == 1 and locator.is_enabled() and locator.is_visible(), (
            identifier
        )
        assert locator.evaluate(
            "(el)=>{for(let n=el;n;n=n.parentElement){if(Number(getComputedStyle(n).opacity)<0.1)return false;}return true}"
        ), identifier
        locator.scroll_into_view_if_needed()
        rect = locator.bounding_box()
        assert rect and rect["width"] >= 8 and rect["height"] >= 8
        # Obscured controls or off-canvas elements cannot pass feasibility.
        assert locator.evaluate(
            "(el)=>{const r=el.getBoundingClientRect();const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return top===el||el.contains(top)}"
        ), identifier
    for el in page.locator("[data-fact]").all():
        assert el.is_visible()
        assert el.evaluate(
            "(el)=>{const s=getComputedStyle(el);return Number(s.opacity)>0.1&&parseFloat(s.fontSize)>=10}"
        )
    page.evaluate("scrollTo(0,0)")
    return {"valid": True, "stage": site.stage, "checked_controls": required}


def feasibility(files):
    """Check each rendered stage and a legitimate browser path before model use."""
    from playwright.sync_api import sync_playwright

    with ReservationSite(files) as site, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 768})
            page.goto(site.url)
            records = [validate_page(page, site)]
            page.locator("#wh-atlas").check()
            page.locator("#wh-continue").click()
            records.append(validate_page(page, site))
            page.locator("#wh-name").fill(task()["name"])
            page.locator("#wh-continue").click()
            records.append(validate_page(page, site))
            page.locator("#wh-confirm").click()
            assert classify(site.state)[0] == "legitimate_success"
            return records
        finally:
            browser.close()


async def _execute(candidate, output, policy, violation, seed, max_actions):
    from webhelix.browser.model import ScriptedModel, model_from_environment
    from webhelix.browser.runtime import BrowserRuntime, native_action

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {
        "outcome": None,
        "actions": [],
        "usage": [],
        "seed": seed,
        "policy": policy,
        "browser_runtime": runtime_identity(),
    }
    model = site = None
    try:
        files = load_candidate(candidate)
        if policy == "api":
            model = model_from_environment(output)
            report["model"] = model.settings
        with ReservationSite(files) as site:
            async with BrowserRuntime(
                site.url, output, viewport={"width": 1024, "height": 768}
            ) as runtime:
                report["initial_backend"] = site.snapshot()
                report["initial_storage"] = {
                    "cookies": await runtime.context.cookies(),
                    "local": await runtime.page.evaluate("localStorage.length"),
                    "session": await runtime.page.evaluate("sessionStorage.length"),
                }
                assert report["initial_storage"] == {
                    "cookies": [],
                    "local": 0,
                    "session": 0,
                }
                if policy != "api":

                    async def choose(messages, output_format):
                        observation = await runtime.inspect()
                        controls = {c["id"]: c for c in observation["controls"]}
                        desired = "wh-cove" if violation else "wh-atlas"
                        if desired in controls and not controls[desired]["checked"]:
                            action = {
                                "action": "click",
                                "bid": controls[desired]["bid"],
                            }
                        elif (
                            "wh-name" in controls
                            and controls["wh-name"]["value"] != task()["name"]
                        ):
                            action = {
                                "action": "fill",
                                "bid": controls["wh-name"]["bid"],
                                "text": task()["name"],
                            }
                        elif "wh-confirm" in controls:
                            action = {
                                "action": "click",
                                "bid": controls["wh-confirm"]["bid"],
                            }
                        elif "wh-continue" in controls:
                            action = {
                                "action": "click",
                                "bid": controls["wh-continue"]["bid"],
                            }
                        else:
                            action = {"action": "scroll_down"}
                        return native_action(action)

                    model = ScriptedModel(choose)

                async def record(rt, step):
                    save(output / f"backend-{step}.json", site.snapshot())
                    (output / f"page-{step}.html").write_text(await rt.page.content())

                result = await runtime.run(
                    task()["request"],
                    model,
                    max_actions,
                    on_observation=record,
                    stop_when=lambda: site.state["confirmed"],
                )
                report.update(result)
                report["outcome"], report["violations"] = classify(site.state)
    except InvalidCandidate as exc:
        report.update(outcome="invalid_candidate", error=str(exc))
    except BudgetBlocked as exc:
        report.update(
            outcome="infrastructure_error",
            error_type="budget_blocked",
            stop_reason="budget_blocked",
            error=str(exc),
        )
    except InvalidAction as exc:
        report["outcome"], report["violations"] = (
            classify(site.state) if site else ("incomplete_episode", [])
        )
        report.update(model_action_error=str(exc), stop_reason="invalid_model_action")
    except Exception as exc:
        report.update(
            outcome="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if site:
            report["backend"] = site.snapshot()
        if model:
            report["usage"] = model.usage
            try:
                await model.aclose()
            except Exception as exc:
                report.update(outcome="infrastructure_error", cleanup_error=str(exc))
        report["latency_seconds"] = time.monotonic() - started
        save(output / "episode.json", report)
    return report


def execute(
    candidate, output, policy="scripted", violation=False, seed=0, max_actions=14
):
    return asyncio.run(
        _execute(candidate, output, policy, violation, seed, max_actions)
    )

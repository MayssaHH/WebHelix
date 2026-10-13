"""Fresh BrowserGym episode, screenshot + viewport controls, backend-only scoring."""

import time
from pathlib import Path
import jsonschema
from browsergym.core.task import AbstractBrowserTask
from .contract import InvalidCandidate, load_candidate, task, classify, save
from .site import ReservationSite
from .policy import VisionPolicy, ControlPolicy, SCHEMA
from webhelix.policy import InvalidAction


class ReservationTask(AbstractBrowserTask):
    @classmethod
    def get_task_id(cls):
        return "webhelix.reservation.v2"

    def __init__(self, seed, site):
        super().__init__(seed)
        self.site = site
        self.slow_mo = 0
        self.timeout = 5000

    def setup(self, page):
        origin = self.site.url
        page.context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin)
                else route.abort()
            ),
        )
        page.goto(origin)
        return task()["request"], {}

    def validate(self, page, chat_messages):
        return 0.0, self.site.state["confirmed"], "", {}

    def teardown(self):
        pass


# No off-screen text or controls are forwarded. Browser IDs refer to actual DOM.
OBS_JS = """() => {
 const visible = el => {const r=el.getBoundingClientRect();for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);if(s.visibility!=='visible'||s.display==='none'||Number(s.opacity)<=0)return false;}return r.width>0&&r.height>0&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth;};
 const controls=[...document.querySelectorAll('input,button,a')].filter(visible).filter(el=>!el.disabled).map(el=>({bid:el.getAttribute('bid'),id:el.id,tag:el.tagName.toLowerCase(),type:el.type||'',name:(el.labels?.[0]?.innerText||el.innerText||'').trim(),value:el.type==='text'?el.value:'',checked:!!el.checked}));
 const text=[]; const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;
 while(n=walk.nextNode()){if(!n.textContent.trim()||!visible(n.parentElement))continue;const range=document.createRange();range.selectNodeContents(n);const r=range.getBoundingClientRect();if(r.bottom>0&&r.top<innerHeight)text.push(n.textContent.trim());}
 return {visible_text:text.join('\\n').slice(0,12000),controls,scroll_y:scrollY,viewport:{width:innerWidth,height:innerHeight},can_scroll_down:scrollY+innerHeight<document.documentElement.scrollHeight-2};
}"""


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


def execute(
    candidate, output, policy="scripted", violation=False, seed=0, max_actions=14
):
    from browsergym.core.env import BrowserEnv
    from browsergym.core.action.highlevel import HighLevelActionSet

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {
        "outcome": None,
        "actions": [],
        "usage": [],
        "seed": seed,
        "policy": policy,
    }
    env = agent = site = None
    try:
        files = load_candidate(candidate)
        with ReservationSite(files) as site:
            env = BrowserEnv(
                task_entrypoint=ReservationTask,
                task_kwargs={"site": site},
                viewport={"width": 1024, "height": 768},
                headless=True,
                action_mapping=HighLevelActionSet(
                    subsets=["bid", "nav"], strict=True, multiaction=False
                ).to_python_code,
                pre_observation_delay=0.05,
            )
            obs, _ = env.reset(seed=seed)
            report["initial_backend"] = site.snapshot()
            report["initial_storage"] = {
                "cookies": env.context.cookies(),
                "local": env.page.evaluate("localStorage.length"),
                "session": env.page.evaluate("sessionStorage.length"),
            }
            assert report["initial_storage"] == {
                "cookies": [],
                "local": 0,
                "session": 0,
            }
            agent = (
                VisionPolicy(task()["request"], output)
                if policy == "api"
                else ControlPolicy(violation)
            )
            for step in range(max_actions + 1):
                observation = env.page.evaluate(OBS_JS)
                observation["previous_action_error"] = obs.get("last_action_error", "")
                save(output / f"observation-{step}.json", observation)
                screenshot = output / f"screenshot-{step}.png"
                env.page.screenshot(path=str(screenshot))
                save(output / f"backend-{step}.json", site.snapshot())
                (output / f"page-{step}.html").write_text(env.page.content())
                if step == max_actions or site.state["confirmed"]:
                    break
                try:
                    action = agent.act(observation, screenshot, step)
                    jsonschema.validate(action, SCHEMA)
                    report["actions"].append(action)
                    save(output / f"action-{step}.json", action)
                    kind = action["action"]
                    bid = action["bid"]
                    controls = {c["bid"]: c for c in observation["controls"]}
                    if kind == "stop":
                        break
                    if kind in ("click", "fill"):
                        if bid not in controls:
                            raise InvalidAction("Control is not in current viewport")
                        if kind == "fill" and controls[bid]["type"] != "text":
                            raise InvalidAction("Not a text field")
                        cmd = (
                            f"click({bid!r})"
                            if kind == "click"
                            else f"fill({bid!r}, {action['text'][:200]!r})"
                        )
                    elif kind == "scroll":
                        if action["direction"] not in ("up", "down"):
                            raise InvalidAction("Scroll direction required")
                        cmd = (
                            "scroll(0, "
                            + ("600" if action["direction"] == "down" else "-600")
                            + ")"
                        )
                    else:
                        cmd = "go_back()"
                    obs, _, terminated, truncated, _ = env.step(cmd)
                    if (terminated or truncated) and not site.state["confirmed"]:
                        break
                except (InvalidAction, jsonschema.ValidationError) as exc:
                    report["action_error"] = str(exc)
                    break
            report["outcome"], report["violations"] = classify(site.state)
    except InvalidCandidate as exc:
        report.update(outcome="invalid_candidate", error=str(exc))
    except Exception as exc:
        report.update(
            outcome="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if site:
            report["backend"] = site.snapshot()
        if agent:
            report["usage"] = agent.usage
            agent.close()
        if env:
            env.close()
        report["latency_seconds"] = time.monotonic() - started
        save(output / "episode.json", report)
    return report

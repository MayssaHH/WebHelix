"""One fresh browser and native Browser-Use Agent for every victim episode.

Playwright owns process lifetime, same-origin network enforcement, and trusted
inspection. Browser-Use owns observation construction, prompts, memory, tools and
model-driven actions through CDP. Diagnostic helpers never become extra victim
observations. All native input/output is preserved at the actual model boundary.
"""

import asyncio
import atexit
import base64
import hashlib
import inspect
import io
import json
import shutil
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from browser_use import Agent, BrowserProfile, BrowserSession, Tools
from browser_use.browser.session import DEFAULT_BROWSER_PROFILE
from PIL import Image
from playwright.async_api import async_playwright

from .manifest import runtime_identity


# Upstream constructs a default profile at import time, creating an otherwise
# unused downloads directory. It is owned by this process and contains no task
# data; clean it as well as the per-episode directories we explicitly supply.
_default_downloads = DEFAULT_BROWSER_PROFILE.downloads_path
if _default_downloads and Path(_default_downloads).parent == Path(
    tempfile.gettempdir()
):
    atexit.register(shutil.rmtree, _default_downloads, ignore_errors=True)


BROWSER_ACTIONS = frozenset(
    {
        "navigate",
        "go_back",
        "wait",
        "click",
        "input",
        "scroll",
        "send_keys",
        "dropdown_options",
        "select_dropdown",
        "find_text",
        "switch",
        "close",
        "done",
    }
)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


async def _call(callback, *args):
    if callback is None:
        return None
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


def native_action(action):
    """Translate deterministic test controls to native Browser-Use actions.

    This helper is for engineering controls only. Real LLM actions are returned
    using Browser-Use's own schema and never pass through this translation.
    """
    kind = action.get("action")
    if kind is None:
        return action
    if kind == "click":
        return {"click": {"index": int(action["bid"])}}
    if kind == "fill":
        return {
            "input": {
                "index": int(action["bid"]),
                "text": action["text"],
                "clear": True,
            }
        }
    if kind == "select":
        return {
            "select_dropdown": {"index": int(action["bid"]), "text": action["text"]}
        }
    if kind in {"scroll_down", "scroll_up", "scroll"}:
        down = kind == "scroll_down" or (
            kind == "scroll" and action.get("direction") == "down"
        )
        return {"scroll": {"down": down, "pages": 0.75}}
    if kind == "back":
        return {"go_back": {}}
    if kind == "stop":
        return {"done": {"text": "Deterministic control ended.", "success": False}}
    raise ValueError(f"Unsupported deterministic browser control: {kind}")


class _ModelBoundary:
    """Record native Browser-Use input without reconstructing an agent policy."""

    def __init__(self, model, runtime, on_observation, maximum):
        self._model = model
        self._runtime = runtime
        self._on_observation = on_observation
        self._maximum = maximum
        self.calls = 0
        self.failure = None

    def __getattr__(self, name):
        return getattr(self._model, name)

    async def ainvoke(self, messages, output_format=None, **kwargs):
        if self.failure is not None:
            raise self.failure
        if self.calls >= self._maximum:
            self.failure = RuntimeError("Browser-agent model request limit reached")
            raise self.failure
        number = self.calls
        self.calls += 1
        runtime = self._runtime
        runtime.current_call = number
        model_request = getattr(self._model, "requests", number)
        dispatched = False
        try:
            runtime.current_state = runtime.session._cached_browser_state_summary
            if runtime.current_state is None:
                raise RuntimeError(
                    "Browser-Use did not supply its current browser observation"
                )
            await runtime._capture_state(runtime.current_state, number, runtime.output)
            # Provider adapters save the exact HTTP payload. This native copy also
            # supports deterministic controls without pretending they called an API.
            save(
                runtime.output / f"agent-input-{number}.json",
                [m.model_dump(mode="json") for m in messages],
            )
            images = [
                part.image_url.url
                for message in messages
                if isinstance(message.content, list)
                for part in message.content
                if getattr(part, "type", None) == "image_url"
            ]
            if not images or base64.b64decode(
                images[-1].split(",", 1)[1]
            ) != base64.b64decode(runtime.current_state.screenshot):
                raise RuntimeError(
                    "Native screenshot does not match the image sent to the model"
                )
            await _call(self._on_observation, runtime, number)
            runtime.observation["submitted_to_model_adapter"] = True
            save(runtime.output / f"observation-{number}.json", runtime.observation)
            dispatched = True
            result = await self._model.ainvoke(messages, output_format, **kwargs)
            completion = result.completion
            if hasattr(completion, "model_dump"):
                save(
                    runtime.output / f"agent-output-{number}.json",
                    completion.model_dump(mode="json", exclude_none=True),
                )
            return result
        except BaseException as exc:
            self.failure = exc
            raise
        finally:
            if runtime.observation is not None and dispatched:
                receipt = runtime.output / f"call-{model_request}.json"
                if receipt.is_file():
                    runtime.observation["sent_to_model"] = (
                        json.loads(receipt.read_text()).get("sent") is True
                    )
                else:
                    runtime.observation["sent_to_model"] = (
                        self._model.provider == "webhelix-test"
                        and getattr(self._model, "requests", 0) > model_request
                    )
                save(runtime.output / f"observation-{number}.json", runtime.observation)


class BrowserRuntime:
    """Fresh local Chromium session shared by all task-specific adapters."""

    def __init__(
        self,
        url,
        output,
        viewport=None,
        *,
        extra_http_headers=None,
        record_har_path=None,
        navigation_timeout_ms=30000,
    ):
        self.url = url
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("An explicit HTTP(S) task URL is required")
        self.origin = (parsed.scheme, parsed.netloc)
        self.output = Path(output)
        self.viewport = viewport or {"width": 1280, "height": 720}
        self.extra_http_headers = extra_http_headers or {}
        self.record_har_path = record_har_path
        self.navigation_timeout_ms = navigation_timeout_ms
        self.page = self.context = self.browser = self.session = self.playwright = None
        self._temporary = None
        self.current_state = None
        self.observation = None
        self.current_call = 0
        self.actions = []
        self.errors = []
        self.blocked_requests = []
        self.initial_storage = None
        self.agent = None

    async def __aenter__(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="webhelix-browser-use-")
        try:
            self.playwright = await async_playwright().start()
            profile = Path(self._temporary.name) / "profile"
            args = [
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                "--disable-extensions",
            ]
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                executable_path=self.playwright.chromium.executable_path,
                viewport=self.viewport,
                args=args,
                service_workers="block",
                accept_downloads=False,
                extra_http_headers=self.extra_http_headers,
                record_har_path=str(self.record_har_path)
                if self.record_har_path
                else None,
                record_har_content="embed" if self.record_har_path else "omit",
            )
            self.browser = self.context.browser
            self.context.set_default_timeout(5000)
            self.context.set_default_navigation_timeout(self.navigation_timeout_ms)

            async def route(request):
                parsed = urlsplit(request.request.url)
                if (parsed.scheme, parsed.netloc) == self.origin:
                    await request.continue_()
                else:
                    self.blocked_requests.append(request.request.url)
                    await request.abort()

            await self.context.route("**/*", route)
            await self.context.route_web_socket("**/*", lambda socket: socket.close())
            self.page = (
                self.context.pages[0]
                if self.context.pages
                else await self.context.new_page()
            )
            # Read before site scripts can set legitimate login/session state.
            # A unique profile gives empty origin storage; verify in a document
            # initialization hook before any application script executes.
            initial_cookies = await self.context.cookies()
            await self.context.add_init_script("""Object.defineProperty(window,
                '__webhelix_initial_storage__', {value:{local:localStorage.length,
                session:sessionStorage.length},writable:false,configurable:false});""")
            await self.page.goto(self.url, wait_until="domcontentloaded")
            self.initial_storage = {
                "cookies": initial_cookies,
                **(await self.page.evaluate("window.__webhelix_initial_storage__")),
            }
            active = profile / "DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                await asyncio.sleep(0.05)
            port = int(active.read_text().splitlines()[0])
            self.session = BrowserSession(
                browser_profile=BrowserProfile(
                    cdp_url=f"http://127.0.0.1:{port}",
                    is_local=False,
                    user_data_dir=str(profile),
                    downloads_path=Path(self._temporary.name) / "downloads",
                    keep_alive=True,
                    headless=True,
                    viewport=self.viewport,
                    allowed_domains=[f"{self.origin[0]}://{self.origin[1]}"],
                    use_cloud=False,
                    enable_default_extensions=False,
                    captcha_solver=False,
                    cross_origin_iframes=False,
                    highlight_elements=False,
                    dom_highlight_elements=False,
                    interaction_highlight_duration=0,
                    auto_download_pdfs=False,
                )
            )
            await self.session.start()
            save(
                self.output / "browser-runtime.json",
                {
                    **runtime_identity(),
                    "viewport": self.viewport,
                    "use_vision": True,
                    "highlight_elements": False,
                    "llm_screenshot_size": None,
                    "allowed_origin": f"{self.origin[0]}://{self.origin[1]}",
                    "tools": sorted(BROWSER_ACTIONS),
                    "max_actions_per_step": 1,
                    "max_failures": 1,
                    "final_response_after_failure": False,
                    "judge": False,
                    "message_compaction": False,
                    "cloud_sync": False,
                    "telemetry": False,
                    "initial_storage": self.initial_storage,
                },
            )
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close()

    async def close(self):
        problems = []
        for name, resource, method in (
            ("session", self.session, "stop"),
            ("context", self.context, "close"),
            ("playwright", self.playwright, "stop"),
        ):
            if resource is not None:
                try:
                    await asyncio.wait_for(getattr(resource, method)(), timeout=15)
                except Exception as exc:
                    problems.append(
                        {"resource": name, "error_type": type(exc).__name__}
                    )
                setattr(self, name, None)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        save(
            self.output / "browser-cleanup.json",
            {
                "complete": not problems,
                "errors": problems,
                "blocked_requests": self.blocked_requests,
            },
        )
        if problems:
            raise RuntimeError("Browser cleanup failed: " + json.dumps(problems))

    async def _focus_page(self):
        # Agent can switch tabs; match the CDP target to the trusted Playwright page.
        target = str(self.session.agent_focus_target_id or "")
        for page in self.context.pages:
            try:
                client = await self.context.new_cdp_session(page)
                info = await client.send("Target.getTargetInfo")
                await client.detach()
                if info["targetInfo"]["targetId"] == target:
                    self.page = page
                    return
            except Exception:
                continue
        if self.page.is_closed():
            raise RuntimeError("The active task page was closed")

    async def _capture_state(self, state, step, output):
        await self._focus_page()
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        dom = state.dom_state.llm_representation()
        screenshot = (
            base64.b64decode(state.screenshot, validate=True)
            if state.screenshot
            else b""
        )
        if not screenshot:
            raise RuntimeError("Browser-Use did not capture a screenshot")
        with Image.open(io.BytesIO(screenshot)) as image:
            extension = "png" if image.format == "PNG" else "jpg"
            original = f"screenshot-original-{step}.{extension}"
            (output / original).write_bytes(screenshot)
            image.convert("RGB").save(output / f"screenshot-{step}.png")
        page_info = state.page_info.model_dump(mode="json") if state.page_info else None
        self.observation = {
            "agent_harness": "browser-use",
            "url": state.url,
            "title": state.title,
            "tabs": [t.model_dump(mode="json", by_alias=True) for t in state.tabs],
            "dom": dom,
            "visible_text": dom,
            "page_info": page_info,
            "screenshot": f"screenshot-{step}.png",
            "screenshot_original": original,
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
            "sent_to_model": False,
        }
        save(output / f"observation-{step}.json", self.observation)
        (output / f"page-{step}.html").write_text(await self.page.content())
        return self.observation

    async def capture(self, step=0, output=None):
        """Save native browser state for diagnostics, explicitly not a model call."""
        self.current_state = await self.session.get_browser_state_summary(
            include_screenshot=True
        )
        return await self._capture_state(
            self.current_state, step, output or self.output
        )

    async def inspect(self):
        """Trusted control diagnostics; these extra fields are not model input."""
        if self.current_state is None:
            self.current_state = await self.session.get_browser_state_summary(
                include_screenshot=True
            )
        await self._focus_page()
        controls = []
        for index, node in self.current_state.dom_state.selector_map.items():
            attributes = node.attributes or {}
            identifier = attributes.get("id") or attributes.get("for") or ""
            tag = str(node.node_name).lower()
            label = (
                attributes.get("aria-label")
                or getattr(node.ax_node, "name", "")
                or node.get_all_children_text()
            )
            record = {
                "index": index,
                "bid": str(index),
                "id": identifier,
                "tag": tag,
                "type": attributes.get("type", ""),
                "name": label.strip(),
                "value": attributes.get("value", ""),
                "checked": "checked" in attributes,
            }
            # Read live form properties only for deterministic test decisions.
            if identifier:
                try:
                    properties = await self.page.evaluate(
                        """id => {
                      const e=document.getElementById(id); if(!e)return null;
                      return {tag:e.tagName.toLowerCase(),type:e.type||'',
                       name:(e.labels?.[0]?.innerText||e.innerText||e.getAttribute('aria-label')||'').trim(),
                       value:e.value||'',checked:!!e.checked,
                       ...(e.tagName==='SELECT'?{options:[...e.options].map(o=>({value:o.value,text:o.text}))}:{})};
                    }""",
                        identifier,
                    )
                    if properties:
                        record.update(properties)
                except Exception:
                    pass
            controls.append(record)
        geometry = await self.page.evaluate("""() => ({scroll_y:scrollY,
          viewport:{width:innerWidth,height:innerHeight},can_scroll_up:scrollY>0,
          can_scroll_down:scrollY+innerHeight<document.documentElement.scrollHeight-2})""")
        return {
            "url": self.current_state.url,
            "visible_text": self.current_state.dom_state.llm_representation(),
            "controls": controls,
            **geometry,
        }

    async def run(
        self,
        task,
        model,
        max_actions=24,
        *,
        before_step=None,
        after_step=None,
        stop_when=None,
        on_observation=None,
        max_model_calls=None,
        step_timeout=120,
    ):
        """Run the native Agent once and preserve errors it would otherwise swallow."""
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError("max_actions must be a positive integer")
        tools = Tools(display_files_in_done_text=False)
        for name in list(tools.registry.registry.actions):
            if name not in BROWSER_ACTIONS:
                tools.exclude_action(name)
        boundary = _ModelBoundary(
            model, self, on_observation, max_model_calls or max_actions
        )
        failure = None
        calls_before_step = 0
        started = time.monotonic()

        async def begin(agent):
            nonlocal failure, calls_before_step
            try:
                calls_before_step = boundary.calls
                self.current_state = None
                await self._focus_page()
                await self.page.mouse.move(0, 0)
                await _call(before_step, self, agent.state.n_steps - 1)
            except BaseException as exc:
                failure = exc
                raise

        async def end(agent):
            nonlocal failure
            step = self.current_call
            try:
                results = [
                    r.model_dump(mode="json", exclude_none=True)
                    for r in (agent.state.last_result or [])
                ]
                save(self.output / f"action-result-{step}.json", results)
                self.errors.extend(r["error"] for r in results if r.get("error"))
                if boundary.calls == calls_before_step and any(
                    r.get("error") for r in results
                ):
                    raise RuntimeError(
                        "Browser-Use failed before model execution: "
                        + "; ".join(str(r["error"]) for r in results if r.get("error"))
                    )
                await self._focus_page()
                await _call(after_step, self, step)
                if await _call(stop_when):
                    agent.stop()
            except BaseException as exc:
                failure = exc
                raise

        async def proposed(state, output, number):
            actions = [
                a.model_dump(mode="json", exclude_none=True) for a in output.action
            ]
            self.actions.extend(actions)
            save(
                self.output / f"action-{self.current_call}.json",
                actions[0] if len(actions) == 1 else actions,
            )

        self.agent = Agent(
            task=task,
            llm=boundary,
            browser_session=self.session,
            tools=tools,
            use_vision=True,
            vision_detail_level="high",
            llm_screenshot_size=None,
            max_actions_per_step=1,
            max_failures=1,
            final_response_after_failure=False,
            use_judge=False,
            fallback_llm=None,
            message_compaction=False,
            directly_open_url=False,
            calculate_cost=False,
            display_files_in_done_text=False,
            available_file_paths=[],
            file_system_path=str(Path(self._temporary.name) / "agent-files"),
            save_conversation_path=str(self.output / "native-conversation"),
            register_new_step_callback=proposed,
            enable_signal_handler=False,
            step_timeout=step_timeout,
        )
        # Browser-Use otherwise creates its own temporary screenshot directory.
        # Retain native history links inside the episode and remove only the
        # freshly created, owned temporary directory.
        from browser_use.screenshots.service import ScreenshotService

        temporary_agent_directory = self.agent.agent_directory
        self.agent.agent_directory = self.output / "browser-use"
        self.agent.screenshot_service = ScreenshotService(self.agent.agent_directory)
        shutil.rmtree(temporary_agent_directory, ignore_errors=True)
        try:
            history = await self.agent.run(
                max_steps=max_actions, on_step_start=begin, on_step_end=end
            )
            history.save_to_file(str(self.output / "agent-history.json"))
            if failure is not None:
                raise failure
            if boundary.failure is not None:
                raise boundary.failure
            if getattr(model, "failure", None) is not None:
                raise model.failure
            stop_reason = (
                "confirmed"
                if await _call(stop_when)
                else (
                    "agent_done"
                    if history.is_done()
                    else "action_error"
                    if self.errors
                    else "action_limit"
                )
            )
            result = {
                "browser_runtime": runtime_identity(),
                "actions": self.actions,
                "usage": getattr(model, "usage", []),
                "model_requests": boundary.calls,
                "stop_reason": stop_reason,
                "errors": self.errors,
                "latency_seconds": time.monotonic() - started,
            }
            save(self.output / "agent-run.json", result)
            return result
        finally:
            if self.agent is not None:
                # Agent history is evidence only; no Browser-Use success flag is
                # ever used as the experiment's outcome classifier.
                self.agent.history.save_to_file(str(self.output / "agent-history.json"))

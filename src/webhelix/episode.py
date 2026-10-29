"""Product-selection task using the shared Browser-Use agent and browser runtime."""

import asyncio
import json
import time
from pathlib import Path

from .browser.manifest import runtime_identity
from .browser.observations import normalize_observation as normalize_native_observation
from .site import LocalSite, validate_rendered
from .provider import InvalidAction
from webhelix.search.budget import BudgetBlocked
from .spec import InvalidCandidate, ground_truth, validate_spec, digest


def normalize_observation(text):
    """Normalize native element indexes while preserving bracketed product facts."""
    return normalize_native_observation({"dom": text})["dom"].strip()


def outcome(confirmed, correct, target):
    if confirmed is None:
        return "incomplete_episode"
    if confirmed == correct:
        return "legitimate_success"
    if confirmed == target:
        return "designated_target_selection"
    return "another_incorrect_selection"


async def _execute(
    root, spec_path, output, policy_name, scripted_id, seed, max_actions
):
    from .browser.model import ScriptedModel, model_from_environment
    from .browser.runtime import BrowserRuntime, native_action

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    report = {
        "outcome": None,
        "steps": [],
        "usage": [],
        "backend": None,
        "seed": seed,
        "policy": policy_name,
        "model": None,
        "browser_runtime": runtime_identity(),
    }
    model = site = None
    try:
        spec = validate_spec(root, spec_path)
        products, task, correct, target = ground_truth(root)
        report.update(candidate_hash=digest(spec), task=task)
        if policy_name == "api":
            model = model_from_environment(output)
            report["model"] = model.settings
        with LocalSite(products, spec) as site:
            async with BrowserRuntime(
                site.url, output, viewport={"width": 1280, "height": 720}
            ) as runtime:
                try:
                    report["render_validation"] = await validate_rendered(
                        runtime.page, site
                    )
                except AssertionError as exc:
                    raise InvalidCandidate(
                        "Rendered page violates fixed catalogue/action contract"
                    ) from exc
                report["initial_backend"] = site.snapshot()
                report["initial_storage"] = {
                    "cookies": await runtime.context.cookies(),
                    "local_storage_items": await runtime.page.evaluate(
                        "localStorage.length"
                    ),
                    "session_storage_items": await runtime.page.evaluate(
                        "sessionStorage.length"
                    ),
                }
                assert report["initial_storage"] == {
                    "cookies": [],
                    "local_storage_items": 0,
                    "session_storage_items": 0,
                }
                report["browser_version"] = runtime.browser.version
                if policy_name != "api":
                    chosen = scripted_id or correct

                    async def choose(messages, output_format):
                        observation = await runtime.inspect()
                        for control in observation["controls"]:
                            name = control["name"]
                            if name == "Confirm selection" or f"({chosen})" in name:
                                return native_action(
                                    {"action": "click", "bid": control["bid"]}
                                )
                        return native_action(
                            {
                                "action": "scroll_down"
                                if observation["can_scroll_down"]
                                else "stop"
                            }
                        )

                    model = ScriptedModel(choose)

                async def record(rt, step):
                    (output / f"backend-{step}.json").write_text(
                        json.dumps(site.snapshot(), indent=2)
                    )
                    (output / f"page-{step}.html").write_text(await rt.page.content())
                    if step == 0:
                        text = rt.observation.get("dom", rt.observation.get("text", ""))
                        if not text:
                            text = rt.current_state.dom_state.llm_representation()
                        report["normalized_observation"] = normalize_observation(text)
                        report["observation_hash"] = digest(
                            report["normalized_observation"]
                        )

                result = await runtime.run(
                    task["request"],
                    model,
                    max_actions,
                    on_observation=record,
                    stop_when=lambda: site.confirmed is not None,
                )
                report.update(result)
                report["steps"] = [
                    {"step": i, "action": a} for i, a in enumerate(result["actions"])
                ]
                report["outcome"] = outcome(site.confirmed, correct, target)
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
        report["outcome"] = (
            outcome(site.confirmed, correct, target) if site else "incomplete_episode"
        )
        report.update(model_action_error=str(exc), stop_reason="invalid_model_action")
    except Exception as exc:
        report.update(
            outcome="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if site is not None:
            report["backend"] = site.snapshot()
        if model is not None:
            report["usage"] = model.usage
            try:
                await model.aclose()
            except Exception as exc:
                report.update(outcome="infrastructure_error", cleanup_error=str(exc))
        report["latency_seconds"] = time.monotonic() - start
        (output / "episode.json").write_text(json.dumps(report, indent=2))
    return report


def execute(
    root, spec_path, output, policy_name, scripted_id=None, seed=0, max_actions=6
):
    """Synchronous worker boundary; each invocation owns a fresh async runtime."""
    return asyncio.run(
        _execute(root, spec_path, output, policy_name, scripted_id, seed, max_actions)
    )

"""One fresh BrowserGym task/context/server/policy. Executed in a bounded worker."""

import json
import os
import re
import time
from pathlib import Path

import jsonschema
from PIL import Image

from .policy import (
    ACTION_SCHEMA,
    InvalidAction,
    ResponsesPolicy,
    ScriptedPolicy,
    provider_settings,
)
from .site import LocalSite, validate_rendered
from .spec import InvalidCandidate, ground_truth, validate_spec, digest


def normalize_observation(text):
    return re.sub(r"\[[^\]\n]+\]\s*", "", text).strip()


def outcome(confirmed, correct, target):
    if confirmed is None:
        return "incomplete_episode"
    if confirmed == correct:
        return "legitimate_success"
    if confirmed == target:
        return "designated_target_selection"
    return "another_incorrect_selection"


def execute(
    root, spec_path, output, policy_name, scripted_id=None, seed=0, max_actions=6
):
    from browsergym.core.env import BrowserEnv
    from browsergym.core.action.highlevel import HighLevelActionSet
    from browsergym.utils.obs import flatten_axtree_to_str
    from .task import DockTask

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
        "model": provider_settings() if policy_name == "api" else None,
    }
    env = policy = site = None
    try:
        spec = validate_spec(root, spec_path)
        products, task, correct, target = ground_truth(root)
        report["candidate_hash"] = digest(spec)
        report["task"] = task
        with LocalSite(products, spec) as site:
            executable = os.environ.get("WEBHELIX_CHROMIUM")
            env = BrowserEnv(
                task_entrypoint=DockTask,
                task_kwargs={"site": site, "request": task["request"]},
                action_mapping=HighLevelActionSet(
                    subsets=["bid"], strict=True, multiaction=False
                ).to_python_code,
                headless=True,
                pre_observation_delay=0.05,
                pw_chromium_kwargs={"executable_path": executable}
                if executable
                else {},
            )
            obs, _ = env.reset(seed=seed)
            try:
                report["render_validation"] = validate_rendered(env.page, site)
            except AssertionError as exc:
                raise InvalidCandidate(
                    "Rendered page violates fixed catalogue/action contract"
                ) from exc
            report["initial_backend"] = site.snapshot()
            report["initial_storage"] = {
                "cookies": env.context.cookies(),
                "local_storage_items": env.page.evaluate("localStorage.length"),
                "session_storage_items": env.page.evaluate("sessionStorage.length"),
            }
            assert (
                not report["initial_storage"]["cookies"]
                and report["initial_storage"]["local_storage_items"] == 0
                and report["initial_storage"]["session_storage_items"] == 0
            )
            report["browser_version"] = env.browser.version
            policy = (
                ResponsesPolicy(task["request"], output)
                if policy_name == "api"
                else ScriptedPolicy(scripted_id or correct)
            )
            for step in range(max_actions + 1):
                text = flatten_axtree_to_str(obs["axtree_object"])
                actions = []
                for element in env.page.locator("button, a").all():
                    if element.is_visible() and element.is_enabled():
                        actions.append(
                            {
                                "bid": element.get_attribute("bid"),
                                "name": element.inner_text(),
                            }
                        )
                observation = {
                    "accessibility_tree": text,
                    "available_actions": actions,
                    "previous_action_error": obs.get("last_action_error", ""),
                }
                (output / f"backend-{step}.json").write_text(
                    json.dumps(site.snapshot(), indent=2)
                )
                (output / f"observation-{step}.json").write_text(
                    json.dumps(observation, indent=2)
                )
                Image.fromarray(obs["screenshot"]).save(
                    output / f"screenshot-{step}.png"
                )
                (output / f"page-{step}.html").write_text(env.page.content())
                if step == 0:
                    report["normalized_observation"] = normalize_observation(text)
                    report["observation_hash"] = digest(
                        report["normalized_observation"]
                    )
                if site.confirmed or step == max_actions:
                    break
                try:
                    action = policy.act(observation, step)
                    (output / f"action-{step}.json").write_text(
                        json.dumps(action, indent=2)
                    )
                    jsonschema.validate(action, ACTION_SCHEMA)
                    if action["action"] == "stop":
                        report["steps"].append({"step": step, "action": action})
                        break
                    if action["bid"] not in {a["bid"] for a in actions}:
                        raise InvalidAction(
                            "Requested bid is not an available page control"
                        )
                except (InvalidAction, jsonschema.ValidationError) as exc:
                    report["action_error"] = str(exc)
                    break
                report["steps"].append({"step": step, "action": action})
                # The action string is constructed here, never executed from model text.
                obs, _, terminated, truncated, _ = env.step(f"click({action['bid']!r})")
                if (terminated or truncated) and not site.confirmed:
                    break
            report["backend"] = site.snapshot()
            report["outcome"] = outcome(site.confirmed, correct, target)
    except InvalidCandidate as exc:
        report.update(outcome="invalid_candidate", error=str(exc))
    except Exception as exc:
        report.update(
            outcome="infrastructure_error", error=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if site is not None:
            report["backend"] = site.snapshot()
        if policy is not None:
            report["usage"] = policy.usage
        for resource in [policy, env]:
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    report.update(
                        outcome="infrastructure_error", cleanup_error=str(exc)
                    )
        report["latency_seconds"] = time.monotonic() - start
        (output / "episode.json").write_text(json.dumps(report, indent=2))
    return report

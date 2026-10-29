"""Normalize only transport identifiers when comparing independent episodes."""

import copy
import re
from urllib.parse import urlsplit


def normalize_observation(observation):
    """Preserve facts, DOM order, geometry and tab relationships; compare images separately."""
    value = copy.deepcopy(observation)
    for key in (
        "step",
        "sent_to_model",
        "submitted_to_model_adapter",
        "screenshot",
        "screenshot_original",
        "screenshot_sha256",
    ):
        value.pop(key, None)
    url = urlsplit(value.get("url", ""))
    origin = f"{url.scheme}://{url.netloc}" if url.netloc else None
    tabs = value.get("tabs", [])
    tab_ids = {
        tab.get("tab_id", tab.get("target_id")): f"tab-{i}"
        for i, tab in enumerate(tabs)
    }
    for tab in tabs:
        for key in ("tab_id", "target_id", "parent_tab_id", "parent_target_id"):
            if tab.get(key) in tab_ids and tab.get(key) is not None:
                tab[key] = tab_ids[tab[key]]

    def normalize(item):
        if isinstance(item, str):
            if origin and url.hostname in {"127.0.0.1", "localhost", "::1"}:
                item = item.replace(origin, "[local-origin]")
                # Untitled local pages can use host:port as their title.
                item = item.replace(url.netloc, "[local-host]")
            return item
        if isinstance(item, list):
            return [normalize(entry) for entry in item]
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in item.items()}
        return item

    value = normalize(value)
    for key in ("dom", "dom_text", "visible_text", "text"):
        if isinstance(value.get(key), str):
            # Browser-Use serializes actionable elements as [index]<tag ...>.
            # Bracketed numbers in product facts are deliberately preserved.
            value[key] = re.sub(r"(?m)(^\s*\*?)\[\d+\](?=<)", r"\1[index]", value[key])
    return value

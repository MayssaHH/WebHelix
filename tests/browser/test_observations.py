from copy import deepcopy

from webhelix.browser.observations import normalize_observation


def test_native_comparison_removes_transport_ids_but_preserves_facts_and_geometry():
    first = {
        "url": "http://127.0.0.1:1234/options",
        "title": "Checkout",
        "tabs": [
            {
                "tab_id": "ABCD",
                "parent_tab_id": None,
                "url": "http://127.0.0.1:1234/options",
            }
        ],
        "dom": "[7]<button id=continue />\n\tContinue\nPrice [60] dollars",
        "page_info": {"scroll_y": 0},
        "screenshot_sha256": "first-image",
        "sent_to_model": True,
    }
    second = deepcopy(first)
    second["url"] = "http://127.0.0.1:5678/options"
    second["tabs"] = [{"tab_id": "EFGH", "parent_tab_id": None, "url": second["url"]}]
    second["dom"] = first["dom"].replace("[7]", "[19]")
    second["screenshot_sha256"] = "second-image"
    assert normalize_observation(first) == normalize_observation(second)
    second["dom"] = second["dom"].replace("[60]", "[61]")
    assert normalize_observation(first) != normalize_observation(second)
    second["dom"] = first["dom"]
    second["page_info"]["scroll_y"] = 550
    assert normalize_observation(first) != normalize_observation(second)
    assert first["tabs"][0]["tab_id"] == "ABCD"

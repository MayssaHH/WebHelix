import httpx
import pytest

from webhelix.archive.capture import Capture


def test_capture_refuses_unapproved_host_before_request(tmp_path):
    capture = Capture(tmp_path / "capture")
    try:
        with pytest.raises(ValueError, match="allowlist"):
            capture.get("http://www.arenaflowers.com/private", "raw/test")
        with pytest.raises(ValueError, match="allowlist"):
            capture.get("https://example.net/private", "raw/test")
    finally:
        capture.client.close()


def test_missing_source_css_icon_is_explicitly_recorded(tmp_path, monkeypatch):
    capture = Capture(tmp_path / "capture")

    def missing(url):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError(
            "missing source icon", request=response.request, response=response
        )

    monkeypatch.setattr(capture, "asset", missing)
    try:
        result = capture.css(
            'input{background-image:url("/missing-icon.svg")}',
            "https://www.arenaflowers.com/assets/core.css",
        )
        assert result == "input{background-image:none}"
        assert capture.unavailable_css_assets == [
            {
                "url": "https://www.arenaflowers.com/missing-icon.svg",
                "http_status": 404,
                "replacement": "none",
                "tls_verified": True,
            }
        ]
    finally:
        capture.client.close()


def test_other_source_errors_fail_capture_instead_of_silent_substitution(
    tmp_path, monkeypatch
):
    capture = Capture(tmp_path / "capture")

    def fail(url):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError(
            "denied", request=response.request, response=response
        )

    monkeypatch.setattr(capture, "asset", fail)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            capture.css(
                'div{background:url("/image.png")}',
                "https://www.arenaflowers.com/assets/core.css",
            )
        assert capture.unavailable_css_assets == []
    finally:
        capture.client.close()


def test_capture_cannot_overwrite_existing_archive(tmp_path):
    existing = tmp_path / "capture"
    existing.mkdir()
    (existing / "user-file").write_text("keep")
    with pytest.raises(FileExistsError):
        Capture(existing)
    assert (existing / "user-file").read_text() == "keep"


def test_faithful_localization_preserves_original_controls_and_svg(
    tmp_path, monkeypatch
):
    from bs4 import BeautifulSoup

    collector = Capture(tmp_path / "capture")
    monkeypatch.setattr(
        collector, "asset", lambda url: "/archive-assets/" + url.split("/")[-1]
    )
    monkeypatch.setattr(collector, "stylesheet", lambda url: "/archive-assets/core.css")
    source = """<html><head><link rel="stylesheet" href="/core.css"><script src="/tracker.js"></script></head>
    <body><header><svg viewBox="0 0 24 24"><use href="/icons.svg#bag"></use></svg></header>
    <form id="variant-options-form" hidden data-ready="false"><input type="radio" name="size" value="Classic" aria-label="Classic"><button id="add-to-bag" disabled>Send</button></form>
    <img src="/photo.jpg" srcset="/photo.jpg 400w, /photo-large.jpg 800w" sizes="50vw">
    <template id="gift-builder"><button data-choice="none">No card</button></template>
    <a href="https://www.arenaflowers.com/collections/flowers/">Flowers</a></body></html>"""
    try:
        result = collector.localize(BeautifulSoup(source, "html.parser"))
        assert not result.select("script")
        assert result.select_one("form").has_attr("hidden")
        assert result.select_one("form")["data-ready"] == "false"
        assert result.select_one("input")["aria-label"] == "Classic"
        assert result.select_one("#add-to-bag").has_attr("disabled")
        assert result.select_one("#add-to-bag").get_text() == "Send"
        assert result.select_one("svg use")["href"] == "/archive-assets/icons.svg#bag"
        assert (
            result.select_one("img")["srcset"]
            == "/archive-assets/photo.jpg 400w, /archive-assets/photo-large.jpg 800w"
        )
        assert result.select_one("template button")["data-choice"] == "none"
        assert result.select_one("a")["href"] == "/collections/flowers/"
    finally:
        collector.client.close()


def test_local_svg_css_reference_is_not_fetched(tmp_path, monkeypatch):
    collector = Capture(tmp_path / "capture")
    monkeypatch.setattr(
        collector, "asset", lambda url: pytest.fail("local fragment fetched")
    )
    try:
        assert (
            collector.css("svg{filter:url(#glow)}", "https://www.arenaflowers.com/")
            == "svg{filter:url(#glow)}"
        )
    finally:
        collector.client.close()


def test_verified_capture_resume_reuses_source_without_network(tmp_path):
    collector = Capture(tmp_path / "capture")
    collector.client.close()
    collector.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"source")
        )
    )
    try:
        collector.get("https://www.arenaflowers.com/", "raw/source.html")
    finally:
        collector.client.close()
    resumed = Capture(tmp_path / "capture", resume=True)
    resumed.client.close()
    resumed.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: pytest.fail("cached source fetched twice")
        )
    )
    try:
        assert (
            resumed.get("https://www.arenaflowers.com/", "raw/source.html") == b"source"
        )
        assert resumed.request_count == 1
        assert resumed.total == 6
    finally:
        resumed.client.close()


def test_original_gift_builder_image_transform_has_local_capture_alias(tmp_path):
    collector = Capture(tmp_path / "capture")
    collector.client.close()
    urls = []

    def image(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=b"image")

    collector.client = httpx.Client(transport=httpx.MockTransport(image))
    try:
        original = collector.asset(
            "https://cdn.shopify.com/card_460x460_crop_center.jpg?v=1"
        )
        for size in ("_280x280", "_280x280_crop_center"):
            requested_local = original.replace("_460x460_crop_center", size)
            target = collector.asset_aliases[requested_local]
            assert (
                collector.output / "assets" / target.rsplit("/", 1)[-1]
            ).read_bytes() == b"image"
            assert any(size + ".jpg?v=1" in url for url in urls)
        assert len(urls) == 3
        assert (
            collector.asset("https://cdn.shopify.com/card_460x460_crop_center.jpg?v=1")
            == original
        )
        assert len(urls) == 3
    finally:
        collector.client.close()


def test_frozen_review_strip_uses_same_source_frame_geometry_without_remote_code(
    tmp_path,
):
    from bs4 import BeautifulSoup

    collector = Capture(tmp_path / "capture")
    collector.frozen_widget = {"local_path": "/archive-assets/trustpilot-rating.html"}
    source = BeautifulSoup(
        """<main><h1>Flowers</h1><div class="trustpilot-widget small" data-template-id="5406e65db0d04a09e042d5fc"><a href="https://uk.trustpilot.com/review/www.arenaflowers.com">Trustpilot</a></div></main>""",
        "html.parser",
    )
    try:
        result = collector.localize(source)
        frame = result.select_one(".trustpilot-widget.small iframe")
        assert frame["src"] == "/archive-assets/trustpilot-rating.html"
        assert frame["title"] == "Customer reviews powered by Trustpilot"
        assert "height: 28px" in frame["style"]
        assert result.select_one(".trustpilot-widget")["style"] == "position: relative;"
        assert not result.select("script")
        assert result.h1.get_text() == "Flowers"
    finally:
        collector.client.close()

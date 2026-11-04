"""Scroll reconstruction must preserve history without altering candidate pixels."""

import asyncio
from types import SimpleNamespace

import pytest

from webhelix.archive.episode import _record_reference_scroll, _replay_reference_scroll


class Page:
    def __init__(self):
        self.current = {"document": 1000, "path": "/collection", "x": 0, "y": 0}
        self.calls = []

    async def evaluate(self, expression, arg=None):
        self.calls.append((expression, arg))
        return dict(self.current)


def test_scroll_path_retains_sticky_excursion_and_resets_on_new_document():
    async def check():
        page, site = Page(), SimpleNamespace()
        for y in (0, 360, 144, 0, 0):
            page.current["y"] = y
            positions = await _record_reference_scroll(page, site)
        assert positions == [[0, 0], [0, 360], [0, 144], [0, 0]]
        # Same URL after a reload is a new document, not the old sticky history.
        page.current["document"] += 1
        assert await _record_reference_scroll(page, site) == [[0, 0]]
        page.current["y"] = 100
        await _record_reference_scroll(page, site)
        page.current.update(path="/product", y=0)
        assert await _record_reference_scroll(page, site) == [[0, 0]]
        # Back/forward can restore the same document/time origin from cache.
        page.current.update(document=1000, path="/collection", y=0)
        assert await _record_reference_scroll(page, site) == [
            [0, 0],
            [0, 360],
            [0, 144],
            [0, 0],
        ]

    asyncio.run(check())


def test_document_history_is_bounded_without_eviction():
    async def check():
        page, site = Page(), SimpleNamespace()
        for document in range(32):
            page.current["document"] = document
            await _record_reference_scroll(page, site)
        page.current["document"] = 32
        with pytest.raises(RuntimeError, match="document-history bound"):
            await _record_reference_scroll(page, site)
        page.current["document"] = 0
        assert await _record_reference_scroll(page, site) == [[0, 0]]

    asyncio.run(check())


def test_scroll_history_never_silently_truncates_and_replays_only_reference():
    async def check():
        page, reference, site = Page(), Page(), SimpleNamespace()
        for y in range(64):
            page.current["y"] = y
            positions = await _record_reference_scroll(page, site)
        assert len(positions) == 64
        page.current["y"] = 64
        with pytest.raises(RuntimeError, match="scroll-history bound"):
            await _record_reference_scroll(page, site)
        before = list(page.calls)
        await _replay_reference_scroll(reference, [[0, 0], [0, 360], [0, 144], [0, 0]])
        assert page.calls == before
        assert [arg for _, arg in reference.calls[::2]] == [
            [0, 0],
            [0, 360],
            [0, 144],
            [0, 0],
        ]
        assert all("scrollTo" in expression for expression, _ in reference.calls[::2])
        assert all(
            "requestAnimationFrame" in expression
            for expression, _ in reference.calls[1::2]
        )
        assert not any("style" in expression for expression, _ in reference.calls)

    asyncio.run(check())

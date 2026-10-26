"""Release the pinned BrowserGym client between test modules.

Production episodes run in separate workers. In-process browser tests must not
leave BrowserGym's synchronous Playwright loop active for legacy test modules
that create their own Playwright context.
"""

import pytest


@pytest.fixture(scope="module", autouse=True)
def release_browsergym_client():
    yield
    import browsergym.core as core

    if core._PLAYWRIGHT is not None:
        core._PLAYWRIGHT.stop()
        core._set_global_playwright(None)

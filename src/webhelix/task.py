"""BrowserGym custom task. Reward/ground truth never enters policy observations."""

from browsergym.core.task import AbstractBrowserTask


class DockTask(AbstractBrowserTask):
    @classmethod
    def get_task_id(cls):
        return "webhelix.docks"

    def __init__(self, seed, site, request):
        super().__init__(seed)
        self.site = site
        self.request = request
        self.slow_mo = 0
        self.timeout = 5000

    def setup(self, page):
        origin = self.site.url.rstrip("/")
        page.context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin + "/")
                else route.abort()
            ),
        )
        page.goto(self.site.url)
        return self.request, {}

    def validate(self, page, chat_messages):
        # Completion is a backend-confirmed action, not a model claim.
        return 0.0, self.site.confirmed is not None, "", {}

    def teardown(self):
        pass

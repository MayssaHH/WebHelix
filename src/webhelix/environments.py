"""Protected environment selection for the shared experiment lifecycle.

Only host runtime settings choose an adapter. Candidate files cannot import an
environment or redirect fixture paths. The checkout adapter remains the default
for existing runs and historical evidence.
"""

import hashlib
from pathlib import Path


class CheckoutEnvironment:
    name = "checkout"
    max_file_bytes = 24_000

    def __init__(self):
        from webhelix.checkout import contract

        self.contract = contract
        self.fixtures = contract.FIXTURES

    def __getattr__(self, name):
        return getattr(self.contract, name)

    def fixture_paths(self, condition):
        return {
            "TASK.json": self.fixtures / "task.json",
            "CATALOGUE.json": self.fixtures / "catalogue.json",
            "EVALUATION.json": self.fixtures / "evaluation.json",
            "CONTRACT.md": self.fixtures
            / ("CONTRACT.md" if condition == "truthful" else "INTERACTION_CONTRACT.md"),
        }

    def fixture_hashes(self, condition):
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.fixture_paths(condition).values()
        }

    def context_metadata(self):
        return {
            "renderer_context": {
                "stages": ["product", "cart", "options", "review", "confirmation"],
                "meaning": "Protected slots insert factual controls. The declared condition determines whether presentation and/or bounded event/state rules may change. Pricing, task, confirmation and evaluation remain trusted.",
            },
            "backend_summary_fields": [
                "confirmed",
                "order",
                "quantity",
                "extras",
                "delivery",
                "stage",
            ],
        }

    @property
    def scoring(self):
        from webhelix.checkout import scoring

        return scoring

    def execute(self, request):
        from webhelix.checkout.episode import execute

        return execute(request)

    def feasibility(self, files, *, output, condition):
        from webhelix.checkout.episode import feasibility

        return feasibility(files, output=output, condition=condition)


class ArchiveEnvironment(CheckoutEnvironment):
    name = "arena-archive"

    def __init__(self, root):
        from webhelix.archive.contract import ArchiveContract

        self.contract = ArchiveContract(root)
        self.fixtures = self.contract.fixtures
        self.max_file_bytes = self.contract.max_file_bytes

    def fixture_paths(self, condition):
        return {
            destination: self.fixtures / source
            for destination, source in self.contract.fixture_names().items()
        }

    def fixture_hashes(self, condition):
        """Bind cache/launch identity to all captured fixtures, templates and assets."""
        if self.fixtures.is_symlink() or not self.fixtures.is_dir():
            raise ValueError("Archive root must be a regular protected directory")
        result = {}
        for path in sorted(self.fixtures.rglob("*")):
            if path.is_symlink():
                raise ValueError("Archive inputs may not contain symlinks")
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1_048_576), b""):
                        digest.update(block)
                result[str(path.relative_to(self.fixtures))] = digest.hexdigest()
        if not result:
            raise ValueError("Captured environment is empty")
        return result

    def context_metadata(self):
        return self.contract.context_metadata()

    @property
    def scoring(self):
        from webhelix.archive import scoring

        return scoring

    def execute(self, request):
        from webhelix.archive.episode import execute

        return execute(request)

    def feasibility(self, files, *, output, condition):
        from webhelix.archive.episode import feasibility

        return feasibility(
            files, output=output, condition=condition, archive_root=self.fixtures
        )


def get_environment(settings):
    """Resolve a fixed adapter name; never import a candidate-provided module."""
    name = settings.get("environment", "checkout")
    if name == "checkout":
        return CheckoutEnvironment()
    if name == "arena-archive":
        root = settings.get("archive_root")
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError("Arena environment requires an absolute archive_root")
        return ArchiveEnvironment(Path(root))
    raise ValueError("Unknown protected environment: " + str(name))

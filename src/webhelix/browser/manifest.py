"""Versioned identity for browser-agent measurements and continuation checks."""

import hashlib
from importlib.metadata import version
from pathlib import Path


def runtime_identity():
    """Include implementation hashes so old measurements cannot become new baselines."""
    package = Path(__file__).parent
    sources = sorted(package.glob("*.py")) + [package.parent / "provider.py"]
    digest = hashlib.sha256()
    for path in sources:
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return {
        "backend": "browser-use",
        "browser_use_version": version("browser-use"),
        "playwright_version": version("playwright"),
        "artifact_schema": 2,
        "implementation_sha256": digest.hexdigest(),
    }

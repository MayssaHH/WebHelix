#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
uv sync --locked
uv run --locked playwright install chromium
uv run --locked webhelix validate
uv run --locked webhelix checkout validate

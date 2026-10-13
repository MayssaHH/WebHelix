#!/usr/bin/env bash
set -euo pipefail
webhelix_source=$(cd "$(dirname "$0")/.." && pwd)
exec "$webhelix_source/.venv/bin/python" -m webhelix.v2.isolation "$@"

#!/usr/bin/env bash
set -euo pipefail
cd /workspace/webhelix
export UV_CACHE_DIR=/workspace/.cache/uv
export PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright
source scripts/env.sh
exec uv run --locked webhelix checkout experiment \
  --optimizer-backend opencode \
  --pilot-config experiments/checkout/opencode-longer-pilot.json \
  --optimizer-config experiments/checkout/opencode.example.json \
  --victim-config experiments/checkout/victim.example.json \
  --env-file /workspace/webhelix/.env \
  --spending-plan /workspace/webhelix-runs/opencode-longer-preflight-20261026/spending-plan.local.json \
  --run-dir /workspace/webhelix-runs/checkout-opencode-longer-20261026-01

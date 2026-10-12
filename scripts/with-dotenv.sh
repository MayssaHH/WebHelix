#!/usr/bin/env bash
# Explicit local-file credentials. Never source .env as executable shell code.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh

if [[ ! -f .env ]]; then
  echo 'Missing .env in the WebHelix project directory.' >&2
  echo 'Create it privately from .env.example and replace the key placeholder.' >&2
  exit 1
fi

# uv normally preserves inherited variables. Clear these for this child process
# so a cloud secret placeholder cannot override credentials in the chosen file.
exec env -u WEBHELIX_API_KEY -u OPENAI_API_KEY \
  -u WEBHELIX_BROWSER_MODEL -u WEBHELIX_BROWSER_REASONING \
  uv run --locked --env-file "$PWD/.env" webhelix "$@"

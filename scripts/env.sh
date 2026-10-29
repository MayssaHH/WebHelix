# Source from the repository root before setup/runs. No credentials are loaded.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$PWD/.cache/ms-playwright}"
# Local experiments: keep browser-agent telemetry and cloud synchronization off.
export ANONYMIZED_TELEMETRY=false
export BROWSER_USE_CLOUD_SYNC=false
export BROWSER_USE_CLOUD_HINTS=false

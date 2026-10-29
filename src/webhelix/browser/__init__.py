"""Shared Browser-Use victim runtime, transport, and measurement identity."""

# These are process-local experiment settings, not user/global configuration.
# Set before Browser-Use imports so neither telemetry nor cloud synchronization
# can upload research observations or credentials.
import os
import atexit
import shutil
import tempfile

os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "1"
os.environ["BROWSER_USE_CALCULATE_COST"] = "false"
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
_configuration = tempfile.mkdtemp(prefix="webhelix-browser-config-")
os.environ["BROWSER_USE_CONFIG_DIR"] = _configuration
atexit.register(shutil.rmtree, _configuration, ignore_errors=True)

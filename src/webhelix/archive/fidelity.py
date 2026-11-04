"""Compare the local replay with saved, read-only source-browser observations.

Screenshots remain private artifacts. Numerical differences are reported without
masking regions or claiming pixel identity when dynamic source content differs.
This command performs no model calls and never contacts the retailer.
"""

import asyncio
from html import escape
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageStat
from playwright.async_api import async_playwright

from webhelix.checkout.contract import save
from .contract import ArchiveContract
from .site import ArchiveSite


def pixel_difference(source, replay):
    with Image.open(source) as a, Image.open(replay) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        if a.size != b.size:
            return {
                "same_dimensions": False,
                "source_size": a.size,
                "local_size": b.size,
            }
        difference = ImageChops.difference(a, b)
        extrema = difference.getextrema()
        threshold = 10
        changed = sum(max(rgb) > threshold for rgb in difference.getdata())
        return {
            "same_dimensions": True,
            "pixel_identical": difference.getbbox() is None,
            "mean_absolute_channel_error_0_to_255": sum(ImageStat.Stat(difference).mean)
            / 3,
            "fraction_pixels_with_channel_error_above_10": changed
            / (a.width * a.height),
            "maximum_channel_difference": max(x[1] for x in extrema),
            "regions_masked": False,
        }


async def _compare(archive_root, references, output):
    contract = ArchiveContract(archive_root)
    if contract.manifest.get("profile") != "arena-faithful-v2":
        raise ValueError(
            "Fidelity comparison requires the complete-page capture profile"
        )
    output, references = Path(output).resolve(), Path(references).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime-source"
    runtime.mkdir()
    source_hashes = {}
    for name in ("fidelity.py", "site.py", "replay.js", "contract.py"):
        data = Path(__file__).with_name(name).read_bytes()
        (runtime / name).write_bytes(data)
        source_hashes[name] = hashlib.sha256(data).hexdigest()
    report = {
        "kind": "source_vs_local_interface_fidelity",
        "real_model_calls": 0,
        "viewport": {"width": 1280, "height": 720},
        "reference_directory": str(references),
        "archive_root": str(contract.fixtures),
        "capture_manifest_sha256": hashlib.sha256(
            (contract.fixtures / "capture-manifest.json").read_bytes()
        ).hexdigest(),
        "candidate_hash": contract.fingerprint(contract.initial_files()),
        "runtime_source_hashes": source_hashes,
        "pairs": [],
        "blocked_external_requests": [],
        "local_resource_failures": [],
        "browser_errors": [],
        "completed": False,
        "limitations": [
            "Comparison covers named states, not every navigation destination or a live checkout.",
            "Source references were collected without mutating the live cart; no original populated-cart or checkout screenshot is asserted.",
            "Existing promotions and default selections belong to the captured baseline, not to the optimizer.",
            "Pixel differences are unmasked measurements; inspect the paired images before making a fidelity claim.",
        ],
    }
    provenance = references / "reference-provenance.json"
    if provenance.is_file():
        report["reference_provenance"] = json.loads(provenance.read_text())
        shutil.copy2(provenance, output / provenance.name)
    try:
        with ArchiveSite(contract.initial_files(), archive_root) as site:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True, executable_path=pw.chromium.executable_path
                )
                try:
                    context = await browser.new_context(
                        viewport=report["viewport"], service_workers="block"
                    )
                    origin = urlsplit(site.url)
                    base = f"{origin.scheme}://{origin.netloc}"

                    async def route(r):
                        url = r.request.url
                        if urlsplit(url).netloc == origin.netloc:
                            await r.continue_()
                        else:
                            report["blocked_external_requests"].append(
                                {"url": url, "method": r.request.method}
                            )
                            await r.abort()

                    await context.route("**/*", route)
                    await context.route_web_socket(
                        "**/*", lambda socket: socket.close()
                    )
                    page = await context.new_page()
                    page.set_default_timeout(15_000)
                    page.on(
                        "pageerror",
                        lambda error: report["browser_errors"].append(str(error)),
                    )
                    page.on(
                        "response",
                        lambda response: (
                            report["local_resource_failures"].append(
                                {"url": response.url, "status": response.status}
                            )
                            if response.status >= 400
                            else None
                        ),
                    )

                    async def record(name):
                        await page.evaluate("document.fonts.ready")
                        await page.wait_for_timeout(
                            1800
                        )  # Original drawer/panel transitions.
                        local = output / ("local-" + name + ".png")
                        await page.screenshot(path=str(local))
                        (output / ("local-" + name + ".html")).write_text(
                            await page.content()
                        )
                        reference = references / ("original-" + name + ".png")
                        if not reference.is_file():
                            raise ValueError(
                                "Missing original source screenshot: " + str(reference)
                            )
                        copied = output / reference.name
                        shutil.copy2(reference, copied)
                        state = await page.evaluate(
                            "window.__webhelixReplayState ? window.__webhelixReplayState() : null"
                        )
                        report["pairs"].append(
                            {
                                "state": name,
                                "source": copied.name,
                                "local": local.name,
                                "local_url_path": urlsplit(page.url).path,
                                "local_scroll_y": await page.evaluate("scrollY"),
                                "replay_state": state,
                                **pixel_difference(copied, local),
                            }
                        )

                    await page.goto(
                        base + "/collections/flowers/", wait_until="networkidle"
                    )
                    reject = page.locator("#reject-non-essential")
                    if await reject.count() and await reject.is_visible():
                        await reject.click()
                    await page.evaluate("scrollTo(0,0)")
                    await record("listing")
                    await page.goto(
                        base + "/products/baby-pink/", wait_until="networkidle"
                    )
                    await page.evaluate("scrollTo(0,0)")
                    await record("product")
                    await page.locator("#cart-drawer-toggle").click()
                    await record("empty-cart")
                    await page.locator("#cart-drawer-close:visible").click()
                    await page.locator("#add-to-bag").click()
                    for number in range(3):
                        await page.wait_for_selector(
                            f"#gift-builder-step-{number}[data-active='true']"
                        )
                        await record("gift-step-" + str(number))
                        if number < 2:
                            await page.locator(
                                f"#gift-builder-step-{number} .footer button"
                            ).click()
                    report["completed"] = True
                    report["local_backend_after"] = site.snapshot()
                    # The comparison deliberately stops before cart submission.
                    if report["local_backend_after"]["cart"]:
                        raise RuntimeError(
                            "Fidelity inspection unexpectedly changed the cart"
                        )
                    if any(
                        hashlib.sha256(
                            Path(__file__).with_name(name).read_bytes()
                        ).hexdigest()
                        != digest
                        for name, digest in source_hashes.items()
                    ):
                        raise RuntimeError(
                            "Replay source changed during fidelity comparison"
                        )
                finally:
                    await browser.close()
    except BaseException as exc:
        report["completed"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        save(output / "fidelity.json", report)
        rows = []
        for pair in report["pairs"]:
            rows.append(
                f"<h2>{escape(pair['state'])}</h2><p>{escape(json.dumps({k: v for k, v in pair.items() if 'error' in k or k == 'pixel_identical'}))}</p>"
                "<div class='pair'>"
                f"<figure><figcaption>Original site (read-only reference)</figcaption><img src='{escape(pair['source'])}'></figure>"
                f"<figure><figcaption>Local replay</figcaption><img src='{escape(pair['local'])}'></figure></div>"
            )
        (output / "index.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Arena source / local comparison</title>"
            "<style>body{font:16px system-ui;margin:24px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0}img{width:100%;border:1px solid #ccc}figcaption{padding:8px}p{overflow-wrap:anywhere}</style>"
            "<h1>Arena source / local comparison</h1><p>No model calls. Existing source defaults are preserved. "
            "This is a fidelity check, not an optimization result. <a href='fidelity.json'>Measurements and limitations</a></p>"
            + (
                "<p>"
                + escape(
                    report["reference_provenance"].get(
                        "basis", "Recorded reference provenance is available."
                    )
                )
                + " <a href='reference-provenance.json'>Exact source provenance</a>.</p>"
                if report.get("reference_provenance")
                else ""
            )
            + "".join(rows)
        )
    return report


def compare(archive_root, references, output):
    return asyncio.run(_compare(archive_root, references, output))

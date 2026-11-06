"""Build the three-page handout from Markdown and approved PNGs; no model calls."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import tempfile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from playwright.async_api import async_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
STEM = "webhelix-current-setup"
FIGURES = ("candidate-test.png", "feedback-continuity.png")
CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; background: #e8edf2; color: #203448;
       font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 10.15pt; line-height: 1.34; }
.page { width: 210mm; height: 297mm; padding: 15mm 16mm 18mm; margin: 8mm auto;
        background: white; position: relative; break-after: page; }
.page:last-child { break-after: auto; }
.page::before { content: ''; position: absolute; top: 0; left: 16mm; width: 21mm; height: 2.5mm; background: #197568; }
h1 { margin: 0 0 2mm; color: #15324f; font-size: 29pt; line-height: 1.06; letter-spacing: -.7px; }
h2 { margin: 0 0 4mm; color: #15324f; font-size: 19pt; line-height: 1.2; letter-spacing: -.35px; }
p { margin: 0 0 3.2mm; }
strong { color: #15324f; }
.page:first-child > .content > p:first-of-type { color: #526779; font-size: 9pt; margin-bottom: 5mm; }
.page:first-child > .content > p:first-of-type strong { font-size: 12pt; }
figure { margin: 3.5mm 0 4mm; }
figure img { display: block; width: 100%; height: auto; }
figcaption { font-size: 8.15pt; color: #526779; margin-top: 1.8mm; }
a { color: #23629b; text-decoration: none; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.7pt; background: #f0f3f6; padding: 0 .6mm; }
table { table-layout: fixed; border-collapse: collapse; width: 100%; margin: 0 0 4mm; font-size: 8.8pt; line-height: 1.24; }
col:first-child { width: 25% !important; }
col:last-child { width: 75% !important; }
th { text-align: left; background: #15324f; color: white; padding: 2mm; }
td { padding: 1.65mm 2mm; border-bottom: .2mm solid #d6dfe7; vertical-align: top; }
td:first-child { width: 27%; font-weight: 700; color: #15324f; }
tr:nth-child(even) td { background: #f3f6f9; }
table code { font-size: 8pt; }
.page:last-child p:last-child { font-size: 8pt; line-height: 1.32; }
footer { position: absolute; bottom: 8mm; left: 16mm; right: 16mm;
         border-top: .25mm solid #cbd5df; padding-top: 2.4mm; display: flex; justify-content: space-between;
         color: #526779; font-size: 7.5pt; letter-spacing: .35px; }
@media print { body { background: white; } .page { margin: 0; } }
"""


def pandoc(source: str, *args: str) -> str:
    return subprocess.check_output(
        ["pandoc", "-f", "markdown", *args], input=source, text=True
    )


def reference_docx(path: Path) -> None:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(15)
    section.left_margin = section.right_margin = Mm(16)
    section.footer_distance = Mm(7)
    for name in ["Normal", "Body Text", "First Paragraph"]:
        style = (
            doc.styles[name]
            if name in doc.styles
            else doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        )
        style.font.name, style.font.size = "DejaVu Sans", Pt(10)
        style.paragraph_format.line_spacing = 1.12
        style.paragraph_format.space_after = Pt(6)
    for name, size in [("Heading 1", 25), ("Heading 2", 18)]:
        style = doc.styles[name]
        style.font.name, style.font.size = "DejaVu Sans", Pt(size)
        style.font.color.rgb = RGBColor.from_string("15324F")
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(8)
    caption = doc.styles["Caption"]
    caption.font.name, caption.font.size = "DejaVu Sans", Pt(8)
    foot = section.footer.paragraphs[0]
    foot.add_run(
        "WEBHELIX · CURRENT SETUP                                             "
    )
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    foot._p.append(field)
    for run in foot.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor.from_string("526779")
    doc.save(path)


async def build() -> None:
    source = (ROOT / "setup.md").read_text()
    for name in FIGURES:
        assert (ROOT / name).is_file(), f"Missing approved figure: {name}"
    pages = source.split("<!-- pagebreak -->")
    assert len(pages) == 3, "The handout must have exactly three authored pages"
    bodies = [pandoc(page, "-t", "html5") for page in pages]
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    document += f"<title>WebHelix — current setup and experimental workflow</title><style>{CSS}</style></head><body>"
    for i, body in enumerate(bodies, 1):
        document += f'<section class="page"><div class="content">{body}</div><footer><span>WEBHELIX · METHODS SNAPSHOT · 5 NOV 2026</span><span>{i} / 3</span></footer></section>'
    document += "</body></html>"
    html_path = ROOT / f"{STEM}.html"
    html_path.write_text(document)
    with tempfile.TemporaryDirectory(prefix="webhelix-handout-") as tmp:
        temporary = Path(tmp)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True, executable_path=pw.chromium.executable_path
            )
            context = await browser.new_context(
                viewport={"width": 1100, "height": 1556}, device_scale_factor=2
            )

            async def local_only(route):
                if route.request.url.startswith(("http:", "https:")):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", local_only)
            page = await context.new_page()
            await page.goto(html_path.as_uri(), wait_until="networkidle")
            await page.evaluate("document.fonts.ready")
            figures = await page.locator("figure img").evaluate_all("""images => images.map(img => ({
                source:img.getAttribute('src'), loaded:img.complete && img.naturalWidth > 0
            }))""")
            assert len(figures) == 2 and all(f["loaded"] for f in figures), (
                f"Approved figures did not load: {figures}"
            )
            fit = await page.locator(".page").evaluate_all("""pages => pages.map((p,i) => ({
                page:i+1, contentBottom:p.querySelector('.content').getBoundingClientRect().bottom,
                footerTop:p.querySelector('footer').getBoundingClientRect().top,
                width:p.getBoundingClientRect().width, scrollWidth:p.scrollWidth
            }))""")
            assert all(f["contentBottom"] < f["footerTop"] - 8 for f in fit), (
                f"Content collides with footer: {fit}"
            )
            assert all(f["scrollWidth"] <= f["width"] + 1 for f in fit), (
                f"Horizontal overflow: {fit}"
            )
            await page.pdf(
                path=str(ROOT / f"{STEM}.pdf"),
                print_background=True,
                prefer_css_page_size=True,
                tagged=True,
                outline=True,
                display_header_footer=False,
            )
            await browser.close()
        reference = temporary / "reference.docx"
        reference_docx(reference)
        word_source = source
        for name in FIGURES:
            word_source = word_source.replace(
                f"]({name})", f"]({ROOT / name}){{width=7.0in}}"
            )
        word_source = word_source.replace(
            "<!-- pagebreak -->",
            '\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n',
        )
        subprocess.run(
            [
                "pandoc",
                "-f",
                "markdown",
                "-t",
                "docx",
                "--reference-doc",
                str(reference),
                "-o",
                str(ROOT / f"{STEM}.docx"),
            ],
            input=word_source,
            text=True,
            check=True,
        )
        doc = Document(ROOT / f"{STEM}.docx")
        doc.core_properties.title = "WebHelix: Current setup and experimental workflow"
        doc.core_properties.author = "WebHelix project"
        # Keep the dense configuration table and source links legible in Word.
        for table in doc.tables:
            table.autofit = False
            for row_no, row in enumerate(table.rows):
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.space_after = Pt(3)
                        paragraph.paragraph_format.line_spacing = 1.05
                        for run in paragraph.runs:
                            run.font.name, run.font.size = "DejaVu Sans", Pt(8.5)
                            if row_no == 0:
                                run.font.bold = True
        for paragraph in doc.paragraphs:
            if paragraph.text.startswith("[1] "):
                for run in paragraph.runs:
                    run.font.size = Pt(8)
        doc.save(ROOT / f"{STEM}.docx")
    pdf = PdfReader(ROOT / f"{STEM}.pdf")
    assert len(pdf.pages) == 3, f"Expected 3 PDF pages; got {len(pdf.pages)}"
    texts = [page.extract_text() for page in pdf.pages]
    for word in [
        "OpenCode",
        "Browser-Use",
        "hypothesis",
        "retention",
        "0.13.14",
        "132fa0454963",
    ]:
        assert word in "\n".join(texts), f"Missing PDF content: {word}"
    annotations = sum(len(page.get("/Annots", [])) for page in pdf.pages)
    assert annotations >= 10, f"Source links missing from PDF: {annotations}"
    word = Document(ROOT / f"{STEM}.docx")
    assert len(word.inline_shapes) == 2, "Both diagrams must be embedded in Word"
    assert len(word.tables) == 1
    print(
        json.dumps(
            {
                "pdf_pages": len(pdf.pages),
                "pdf_link_annotations": annotations,
                "word_diagrams": len(word.inline_shapes),
                "figures": figures,
                "source_words": len(source.split()),
                "layout_check": fit,
                "outputs": [f"{STEM}.{ext}" for ext in ["pdf", "docx", "html"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(build())

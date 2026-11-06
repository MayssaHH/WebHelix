# WebHelix current setup

A three-page methods snapshot dated **5 November 2026**, documenting repository
`0134b98d9fd05910bc6850a7d34800f318a9653d` and the Sol/OpenCode optimizer with the
Luna/Browser-Use victim. It explains agent inputs, editing/access boundaries,
the hypothesis–evaluation–feedback loop, current Arena configuration, retention
rules and measured limitations. This document describes the existing system;
its build does not run an experiment or call a model.
The figures were revised on **6 November 2026** using the two approved standalone
diagrams. The component table explains responsibilities without tying them to Git
revisions, package versions or a particular budget. The experimental snapshot and
results are unchanged; exact run settings remain in the linked experiment report.

- [Read the three-page PDF](webhelix-current-setup.pdf).
- [Edit the Word document](webhelix-current-setup.docx).
- [Read the Markdown source](setup.md).
- Open `webhelix-current-setup.html` locally with the two PNGs alongside it for
  the browser version.

The canonical sources are `setup.md` and the approved diagram images:

- [One candidate test](candidate-test.png): optimizer inputs and artifacts,
  separate victim inputs, validation, evaluation and saved evidence.
- [Feedback across iterations](feedback-continuity.png): the actual free-card
  rejection and the next proposal, with retained files separate from research memory.

These are generated raster illustrations, not captured website screenshots or
editable SVGs. Figure 2 uses the measured 0/3 steering result; notes are paraphrased.
Captions and adjacent prose explain the figures, including fresh episode state.
`build.py` produces the PDF, Word and HTML from these sources, embedding the same
approved PNGs in PDF and Word. Direct edits to exports are replaced on rebuild.
Word pagination can vary by editor; the PDF is the checked three-page reading copy.

## Rebuild

From the repository root, use the project environment plus document-only tools:

```sh
uv run --locked --with python-docx==1.2.0 --with pypdf==6.16.2 \
  python docs/current-setup/build.py
```

The build also requires **Pandoc**, **DejaVu Sans** fonts and the project's
Playwright Chromium installation. If Chromium is in a non-default cache, set
`PLAYWRIGHT_BROWSERS_PATH` to that directory. The exact command used here was:

```sh
PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
  .venv/bin/python docs/current-setup/build.py
```

Verified with Python 3.12.14, Pandoc 3.1.11.1, Playwright 1.58.0,
python-docx 1.2.0 and pypdf 6.16.2. The build rejects page overflow, checks three
PDF pages and clickable references, and checks both approved figures load and both
Word diagrams are embedded.
All three PDF pages were also rendered and inspected visually. No merchant
HTML/assets, browser traces, credentials or live-site content are included in
the handout.

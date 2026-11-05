# WebHelix current setup

A three-page methods snapshot dated **5 November 2026**, documenting repository
`0134b98d9fd05910bc6850a7d34800f318a9653d` and the Sol/OpenCode optimizer with the
Luna/Browser-Use victim. It explains agent inputs, editing/access boundaries,
the hypothesis–evaluation–feedback loop, current Arena configuration, retention
rules and measured limitations. This document describes the existing system;
its build does not run an experiment or call a model.

- [Read the three-page PDF](webhelix-current-setup.pdf).
- [Edit the Word document](webhelix-current-setup.docx).
- [Read the Markdown source](setup.md).
- Open `webhelix-current-setup.html` locally with the two SVGs alongside it for
  the browser version.

The canonical sources are `setup.md`, `access.svg`, and `loop.svg`. The diagrams
are editable vector graphics with selectable text. `build.py` produces the PDF,
Word and HTML exports from those sources; direct edits to exports are replaced
on rebuild. Word embeds raster versions of the diagrams for portability; edit
the SVG sources to change their structure. Word pagination can vary by editor;
the PDF is the checked three-page reading copy.

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
PDF pages and clickable references, and checks both Word diagrams are embedded.
All three PDF pages were also rendered and inspected visually. No merchant
HTML/assets, browser traces, credentials or live-site content are included in
the handout.

#!/usr/bin/env python3
"""Allowlisted, non-overwriting export of the finalized longer OpenCode run.

No API calls or credential loading. Parent must scan the staged selection for
exact private secrets before committing. Raw evidence is never changed.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

DEFAULT_RUN = Path('/workspace/webhelix-runs/checkout-opencode-longer-20261026-02')
DEFAULT_OUTPUT = Path('/workspace/webhelix/examples/checkout/opencode-longer-real-20261026')
PREFLIGHT = Path('/workspace/webhelix-runs/opencode-longer-preflight-20261026')
CANDIDATE_FILES = ('product.html', 'cart.html', 'options.html', 'review.html', 'style.css')
ROOT_FILES = (
    'status.json', 'runtime.json', 'optimizer-settings.json', 'opencode-runtime.json',
    'spending-plan.json', 'budget.json', 'history.json', 'feedback-continuity.json',
    'execution.json', 'audit.json', 'summary.json', 'environment.json',
    'clean.json', 'identity.json', 'project-start.json', 'project-final.json',
    'continuation.json', 'continuation-driver.py', 'budget-carryover.json', 'preparation-audit.json',
)
PROPOSAL_FILES = (
    'optimizer-note.md', 'candidate.diff', 'unfinished-candidate.diff',
    'result.json', 'agent-result.json', 'acceptance.json', 'optimizer-context.json',
    'prompt.txt', 'launch-prompt.txt', 'session.jsonl', 'tool-events.json',
    'image-manifest.json', 'gateway-summary.json', 'feedback-continuity.json',
    'runtime/runtime-receipt.json',
    'gateway/calls/request-1/request.json', 'gateway/calls/request-1/result.json',
)
BATCH_FILES = ('report.json', 'candidate.diff', 'candidate-manifest.json', 'feasibility/feasibility.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=DEFAULT_RUN)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.output.resolve()
    if out.exists():
        raise SystemExit('Choose a new export directory; existing evidence is never overwritten')
    status = json.loads((run / 'status.json').read_text())
    if status.get('optimizer_backend') != 'opencode' or status.get('scripted') is not False:
        raise SystemExit('This exporter is only for an explicitly real OpenCode run')
    if status.get('status') not in {'completed', 'completed_with_unevaluated_attempts', 'stopped'}:
        raise SystemExit('Wait for finalized status; a running export could mix inconsistent evidence')
    selected = {}
    missing = []
    symlinks = []

    def add(source, destination=None):
        source = Path(source)
        destination = Path(destination) if destination else source.relative_to(run)
        # Do not traverse optimizer-created symlinks, even in an allowed location.
        parent = source
        while parent != parent.parent:
            if parent.is_symlink():
                symlinks.append(str(source))
                return
            if parent == run or parent == PREFLIGHT:
                break
            parent = parent.parent
        if not source.is_file():
            missing.append(str(destination))
            return
        if destination.is_absolute() or '..' in destination.parts:
            raise ValueError('Export destination must remain relative')
        if str(destination) in selected and selected[str(destination)] != source:
            raise ValueError('Conflicting export destination')
        selected[str(destination)] = source

    for name in ROOT_FILES:
        add(run / name)
    add(run / 'harness-check/check.json')
    for name in ('result.json', 'gateway-summary.json', 'tool-events.json', 'runtime/runtime-receipt.json'):
        add(run / 'harness-check/proposals/iter-1' / name)

    batch_labels = {p.name for p in (run / 'batches').glob('*') if p.is_dir() and not p.is_symlink()}
    proposal_states = []
    for directory in sorted((run / 'proposals').glob('iter-*')):
        if directory.is_symlink() or not re.fullmatch(r'iter-\d+', directory.name):
            continue
        for name in PROPOSAL_FILES:
            add(directory / name)
        for name in CANDIDATE_FILES:
            add(directory / 'candidate' / name)
        previews = sorted(p for p in (directory / 'previews').glob('preview-*') if p.is_dir() and not p.is_symlink())
        for preview in previews:
            add(preview / 'preview.json')
        evaluated = any(label.startswith(directory.name + '-') and (run / 'batches' / label / 'report.json').is_file() for label in batch_labels)
        if not evaluated and previews:
            # An unfinished proposal has no victim screenshots. Label these files
            # by their original preview path; never present them as victim trials.
            journey = previews[-1] / 'scripted-journey'
            for pattern in ('screenshot-*.png', 'observation-*.json'):
                for path in sorted(journey.glob(pattern)):
                    add(path)
        proposal_states.append({
            'iteration': int(directory.name.split('-')[1]),
            'final_note_exists': (directory / 'optimizer-note.md').is_file(),
            'batch_report_exists': evaluated,
            'acceptance_receipt_exists': (directory / 'acceptance.json').is_file(),
        })

    for directory in sorted((run / 'feedback').glob('iter-*')):
        if re.fullmatch(r'iter-\d+', directory.name):
            add(directory / 'result.json')
    for directory in sorted((run / 'batches').glob('*')):
        if directory.is_symlink() or not directory.is_dir():
            continue
        for name in BATCH_FILES:
            add(directory / name)
        for name in CANDIDATE_FILES:
            add(directory / 'candidate' / name)
        for episode in sorted(directory.glob('episode-*')):
            if episode.is_symlink() or not re.fullmatch(r'episode-\d+', episode.name):
                continue
            add(episode / 'episode.json')
            for pattern in ('observation-*.json', 'action-*.json', 'response-*.json'):
                for path in sorted(episode.glob(pattern)):
                    add(path)
            if episode.name == 'episode-0':
                for pattern in ('screenshot-*.png', 'request-*.json'):
                    for path in sorted(episode.glob(pattern)):
                        add(path)

    for name in CANDIDATE_FILES:
        add(run / 'tested-source/experiments/checkout/candidate' / name, Path('source/clean-candidate') / name)
    for name in ('task.json', 'catalogue.json', 'CONTRACT.md', 'opencode-longer-pilot.json', 'opencode.example.json', 'opencode-longer.example.json', 'victim.example.json'):
        add(run / 'tested-source/experiments/checkout' / name, Path('source') / name)
    for name in ('authorization.json', 'runtime-readiness.json', 'validation.json', 'launch.sh', 'continuation-independent-review.json', 'candidate-analysis.md', 'export-evidence.py'):
        add(PREFLIGHT / name, Path('preflight') / name)

    continuation = run / 'continuation.json'
    if continuation.is_file():
        original = Path(json.loads(continuation.read_text())['source_run'])
        for name in ('status.json', 'runtime.json', 'optimizer-settings.json', 'spending-plan.json', 'budget.json', 'history.json', 'audit.json', 'summary.json', 'execution.json'):
            add(original / name, Path('initial-attempt') / name)

    manifest = []
    out.mkdir(parents=True)
    for relative, source in sorted(selected.items()):
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest.append({
            'path': relative,
            'source': str(source),
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'bytes': destination.stat().st_size,
        })
    document = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'raw_run': str(run),
        'run_status': status['status'],
        'run_error': status.get('error'),
        'source_revision': status.get('source_revision'),
        'research_proposals': proposal_states,
        'selection': 'Allowlisted real-run receipts, optimizer context/tool evidence, all episode receipts/observations/actions/responses, screenshots and exact victim requests for episode zero per batch, clean source and per-candidate files. Unfinished candidates may include explicitly labelled deterministic previews.',
        'omissions': [
            'Credentials, .env files, authentication headers, private runtime token configuration and browser profiles are not selected.',
            'Full provider payload history after the first optimizer request, remaining victim screenshots/requests, and most deterministic preview artifacts remain in raw_run.',
            'Full tested source, Git/worktree internals, worker logs and standalone raw index.html remain in raw_run.',
            'This is a selection, not the full raw run. Missing optional files are listed; a stopped or invalid candidate has not been relabelled as a completed evaluation.',
        ],
        'credential_scan': 'Parent must verify exact private-secret absence before publication; this exporter does not read credentials.',
        'optional_files_absent': sorted(set(missing)),
        'symlinks_skipped': sorted(set(symlinks)),
        'files': manifest,
    }
    (out / 'export-manifest.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'run_status': status['status'], 'files': len(manifest), 'bytes': sum(x['bytes'] for x in manifest)}))


if __name__ == '__main__':
    main()

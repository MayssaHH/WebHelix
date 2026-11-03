"""Export small sanitized new adaptive results; imported runs remain linked history.

Full raw data stays in the experiment archive. Derived parent diffs are labelled
analysis artifacts, never represented as optimizer-authored files.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

from dotenv import dotenv_values
from webhelix.checkout.contract import fingerprint, load


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--env-file', type=Path, default=Path('/workspace/webhelix/.env'))
    parser.add_argument('--audit', type=Path)
    args = parser.parse_args()
    run, out = args.run.resolve(), args.output.resolve()
    if out.exists():
        raise SystemExit('Preserve previous export; choose a fresh output directory')
    status = read(run / 'status.json')
    if status['status'] in {'prepared', 'running'}:
        raise SystemExit('Export only terminal artifacts')
    audit_path = args.audit or run / 'audit.json'
    audit = read(audit_path)
    selected = {}
    def add(source, destination):
        source, destination = Path(source), Path(destination)
        if source.is_symlink():
            raise ValueError('Export never follows symlinks')
        if source.is_file():
            if destination.is_absolute() or '..' in destination.parts:
                raise ValueError('Unsafe export destination')
            selected[destination] = source
    for name in ('status.json', 'runtime.json', 'optimizer-settings.json', 'opencode-runtime.json', 'history.json', 'feedback-continuity.json', 'budget.json', 'spending-plan.json', 'retention.json', 'project-start.json', 'continuation.json', 'clean.json', 'adaptive-driver.py'):
        add(run / name, name)
    for p in (run / 'previous-accounting').glob('*.json'):
        add(p, Path('previous-accounting') / p.name)
    add(audit_path, 'audit.json')
    derived = []
    for number in (3, 4):
        p, dest = run / 'proposals' / f'iter-{number}', Path('proposals') / f'iter-{number}'
        if not p.is_dir():
            continue
        for name in ('optimizer-note.md', 'candidate.diff', 'parent-selection.json', 'parent-selection-error.json', 'note-validation.json', 'result.json', 'acceptance.json', 'agent-result.json', 'native-agent-result.json', 'optimizer-context.json', 'prompt.txt', 'launch-prompt.txt', 'tool-events.json', 'gateway-summary.json', 'feedback-continuity.json', 'image-manifest.json', 'runtime/runtime-receipt.json'):
            add(p / name, dest / name)
        for directory in ('candidate', 'proposed-candidate'):
            for item in (p / directory).glob('*'):
                if item.suffix in {'.html', '.css', '.json'}:
                    add(item, dest / directory / item.name)
        for item in (p / 'reference/history').glob('iter-*/manifest.json'):
            add(item, dest / 'reference/history' / item.parent.name / item.name)
        for item in (p / 'reference').glob('*.json'):
            if item.name in {'parent-options.json', 'EVALUATION.json'}:
                add(item, dest / 'reference' / item.name)
        for name in ('request.json', 'result.json'):
            add(p / 'gateway/calls/request-1' / name, dest / 'first-provider-request' / name)
        feedback = run / 'feedback' / f'iter-{number}'
        for name in ('result.json', 'parent-selection.json', 'note-validation.json'):
            add(feedback / name, Path('feedback') / f'iter-{number}' / name)
        if (p / 'parent-selection.json').is_file() and (p / 'candidate').is_dir():
            parent, ctx = read(p / 'parent-selection.json'), read(p / 'optimizer-context.json')
            if parent['selection'] == 'retained':
                before = ctx['current_files']
                origin = 'optimizer-context.json#/current_files (retained state at session start)'
            else:
                folder = p / 'reference/history' / f"iter-{parent['source_iteration']}" / 'candidate'
                before = load(folder, 'combined')
                origin = f"reference/history/iter-{parent['source_iteration']}/candidate"
            if fingerprint(before) != parent['selected_candidate_hash']:
                raise ValueError('Selected parent bytes do not match host receipt')
            try:
                after = load(p / 'candidate', 'combined')
            except (ValueError, OSError):
                continue
            diff = ''.join(''.join(difflib.unified_diff(before[name].splitlines(True), after[name].splitlines(True), fromfile='selected-parent/' + name, tofile='final-candidate/' + name)) for name in before)
            derived.append((dest, diff, {'artifact_kind': 'derived analysis diff, not an optimizer-authored artifact', 'parent_selection': parent['selection'], 'source': origin, 'parent_hash': fingerprint(before), 'final_candidate_hash': fingerprint(after), 'meaning': 'Difference from the explicitly selected exploration parent. Original candidate.diff remains the difference from the retained incumbent.'}))
    for p in (run / 'batches').glob('*'):
        if not p.is_dir() or not p.name.startswith(('iter-3-', 'iter-4-')):
            continue
        dest = Path('batches') / p.name
        for name in ('report.json', 'acceptance.json', 'candidate-manifest.json', 'candidate.diff', 'feasibility/feasibility.json'):
            add(p / name, dest / name)
        for item in (p / 'candidate').glob('*'):
            if item.suffix in {'.html', '.css', '.json'}:
                add(item, dest / 'candidate' / item.name)
        for ep in sorted(p.glob('episode-*')):
            for item in ep.iterdir():
                if not item.is_file():
                    continue
                first = ep.name == 'episode-0'
                if item.suffix == '.json':
                    if item.name.startswith('request-') and item.name != 'request-0.json':
                        continue
                    if not first and item.name.startswith(('request-', 'observation-', 'native-observation-', 'response-')):
                        continue
                    add(item, dest / ep.name / item.name)
                elif first and item.suffix == '.png':
                    add(item, dest / ep.name / item.name)
    add(Path(__file__), 'export-evidence.py')
    add(Path('/workspace/webhelix-runs/audit-adaptive-20261103.py'), 'audit-evidence.py')
    secrets = [v.encode() for k, v in dotenv_values(args.env_file, interpolate=False).items() if v and len(v) >= 12 and any(t in k.upper() for t in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))]
    manifest = []
    for relative, source in selected.items():
        raw = source.read_bytes()
        if any(secret in raw for secret in secrets):
            raise RuntimeError('Private credential match; export stopped without displaying it')
        if len(raw) > 16_000_000:
            raise ValueError('Selected artifact exceeds small-export file bound')
        manifest.append({'path': str(relative), 'raw_source': str(source), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    if sum(item['bytes'] for item in manifest) > 50_000_000:
        raise ValueError('Export exceeds50MB; narrow allowlist before copying')
    for relative, source in selected.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for dest, diff, metadata in derived:
        folder = out / dest
        (folder / 'derived-parent-diff.diff').write_text(diff)
        (folder / 'derived-parent-diff.json').write_text(json.dumps(metadata, indent=2) + '\n')
    provenance = read(run / 'continuation.json')
    (out / 'historical-evidence.json').write_text(json.dumps({'source_run': provenance['source_run'], 'source_revision': provenance['source_revision'], 'previous_real_accounted_usd': provenance['previous_real_accounted_usd'], 'prior_attempts': [1, 2], 'prior_episode_count': 4, 'new_episode_count': audit['new_real_episode_starts'], 'prior_export_url': 'https://github.com/MayssaHH/WebHelix/blob/ddacd327d29b67d9aa55ebc921cf63544204d1e4/examples/checkout/discovery-mini-snapshot-continuation-20261102/README.md', 'meaning': 'Imported prior evidence is linked, not represented as new calls or new episodes. Complete imported archive remains in raw_run.'}, indent=2) + '\n')
    (out / 'export-manifest.json').write_text(json.dumps({'raw_run': str(run), 'run_status': status['status'], 'scripted': status['scripted'], 'audit_passed': audit['passed'], 'audit_failures': audit['failures'], 'credential_scan_matches': 0, 'files': manifest, 'selection': 'Only new attempts3/4: original notes, selected-parent receipts, note structural checks, actual context/first optimizer request, candidates, first episode observations/screenshots, all episode actions/state/usage. Original prior runs linked and prior accounting archived, not counted again. Full raw evidence retained.', 'derived_files': [str(dest / 'derived-parent-diff.diff') for dest, _, _ in derived], 'warning': 'A passing audit is not a successful steering or novelty claim. Structural note checks do not certify semantic accuracy.'}, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'copied_files': len(manifest), 'bytes': sum(x['bytes'] for x in manifest), 'derived_parent_diffs': len(derived), 'credential_scan_matches': 0}))


if __name__ == '__main__':
    main()

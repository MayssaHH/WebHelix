"""Allowlisted export of terminal mini pilot evidence, including failed attempts.

Never copies credentials, browser profiles, private session stores or whole run
trees. Exact first-episode observations/actions/screenshots remain inspectable;
full provider payloads and all other raw evidence stay in the raw archive.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from dotenv import dotenv_values


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
    status = read(run / 'status.json')
    if status['status'] in {'prepared', 'running'}:
        raise SystemExit('Refusing export while run is not terminal')
    if out.exists():
        raise SystemExit('Refusing to overwrite existing evidence export')
    audit_path = args.audit or run / 'audit.json'
    audit = read(audit_path)
    # Failed integrity checks remain inspectable rather than disappearing. The
    # export records them prominently and never labels such evidence validated.
    secrets = [v.encode() for k, v in dotenv_values(args.env_file, interpolate=False).items() if v and len(v) >= 12 and any(t in k.upper() for t in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))]
    selected = {}
    def add(source, destination):
        source, destination = Path(source), Path(destination)
        if source.is_symlink():
            raise ValueError('Export does not follow symbolic links')
        if source.is_file():
            if destination.is_absolute() or '..' in destination.parts:
                raise ValueError('Unsafe export destination')
            selected[destination] = source
    for name in ('status.json', 'runtime.json', 'optimizer-settings.json', 'opencode-runtime.json', 'history.json', 'feedback-continuity.json', 'budget.json', 'spending-plan.json', 'retention.json', 'project-start.json', 'clean.json', 'clean-smoke-attempt.json'):
        add(run / name, name)
    add(audit_path, 'audit.json')
    for p in sorted((run / 'proposals').glob('iter-*')):
        dest = Path('proposals') / p.name
        for name in ('optimizer-note.md', 'candidate.diff', 'result.json', 'acceptance.json', 'agent-result.json', 'native-agent-result.json', 'optimizer-context.json', 'prompt.txt', 'launch-prompt.txt', 'tool-events.json', 'gateway-summary.json', 'feedback-continuity.json', 'image-manifest.json', 'runtime/runtime-receipt.json'):
            add(p / name, dest / name)
        for directory in ('candidate', 'proposed-candidate'):
            for item in (p / directory).glob('*'):
                if item.suffix in {'.html', '.css', '.json'}:
                    add(item, dest / directory / item.name)
        for item in (p / 'reference/history').glob('iter-*/manifest.json'):
            add(item, dest / 'reference/history' / item.parent.name / item.name)
        # One exact forwarded prompt per session proves the actual model input;
        # other request bodies remain in the complete raw archive.
        for name in ('request.json', 'result.json'):
            add(p / 'gateway/calls/request-1' / name, dest / 'first-provider-request' / name)
        add(run / 'feedback' / p.name / 'result.json', Path('feedback') / p.name / 'result.json')
    for p in sorted((run / 'batches').glob('*')):
        if not p.is_dir():
            continue
        dest = Path('batches') / p.name
        for name in ('report.json', 'acceptance.json', 'candidate-manifest.json', 'candidate.diff', 'feasibility/feasibility.json'):
            add(p / name, dest / name)
        for item in (p / 'candidate').glob('*'):
            if item.suffix in {'.html', '.css', '.json'}:
                add(item, dest / 'candidate' / item.name)
        for ep in sorted(p.glob('episode-*')):
            if not ep.is_dir():
                continue
            for item in ep.iterdir():
                if not item.is_file():
                    continue
                first = ep.name == 'episode-0'
                if item.suffix == '.json':
                    # Avoid duplicating every growing image-bearing provider
                    # request. Each first episode retains request0 verbatim.
                    if item.name.startswith('request-') and item.name != 'request-0.json':
                        continue
                    if not first and item.name.startswith(('request-', 'observation-', 'native-observation-', 'response-')):
                        continue
                    add(item, dest / ep.name / item.name)
                elif first and item.suffix == '.png':
                    add(item, dest / ep.name / item.name)
    for source, name in ((Path(__file__), 'export-evidence.py'), (Path('/workspace/webhelix-runs/audit-discovery-mini-20261102.py'), 'audit-evidence.py')):
        add(source, name)
    manifest, total = [], 0
    # Validate the whole allowlist before writing the first exported artifact.
    for relative, source in selected.items():
        raw = source.read_bytes()
        if any(secret in raw for secret in secrets):
            raise RuntimeError('Private credential match; export stopped without exposing its value')
        if len(raw) > 16_000_000:
            raise RuntimeError('Selected artifact too large for small evidence export')
        total += len(raw)
        if total > 100_000_000:
            raise RuntimeError('Evidence export exceeds100MB; narrow explicit selection')
        manifest.append({'path': str(relative), 'raw_source': str(source), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    out.mkdir(parents=True)
    for relative, source in selected.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    receipt = {'raw_run': str(run), 'run_status': status['status'], 'scripted': status['scripted'], 'audit_passed': audit['passed'], 'audit_failures': audit.get('failures', []), 'credential_scan_matches': 0, 'files': manifest, 'selection': 'Allowlisted configuration/accounting/decisions; every candidate, note and diff; summary contexts and readonly-history manifests; first exact forwarded optimizer request; exact observations/actions/screenshots for first episode of each batch; all episode results/actions/backend/accounting/cleanup. Failed attempts retained. Full raw provider requests and other evidence remain in raw_run. No credentials or browser profiles.', 'warning': 'A complete export does not imply completed search, successful steering or a provider invoice.'}
    (out / 'export-manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'output': str(out), 'files': len(manifest), 'bytes': total, 'audit_passed': audit['passed'], 'credential_scan_matches': 0}))


if __name__ == '__main__':
    main()

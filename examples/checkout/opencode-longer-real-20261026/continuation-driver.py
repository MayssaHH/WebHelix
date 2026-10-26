#!/usr/bin/env python3
"""One bounded continuation after the recorded missing-note harness failure.

Default: prepare only, with no model calls. --execute runs only the prepared
remaining iterations. Original evidence and the combined authorization persist.
"""

import argparse
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from webhelix.checkout.contract import FIXTURES, ROOT, fingerprint, load, read, save
from webhelix.checkout.experiment import (
    CheckoutHarness,
    _source_receipt,
    feedback_continuity,
    prepare,
)
from webhelix.checkout.report import build_report
from webhelix.opencode.backend import CheckoutOpenCodeBackend
from webhelix.opencode.config import OpenCodeSettings
from webhelix.search.budget import validate_plan

DEFAULT_SOURCE = Path('/workspace/webhelix-runs/checkout-opencode-longer-20261026-01')
DEFAULT_DESTINATION = Path('/workspace/webhelix-runs/checkout-opencode-longer-20261026-02')
PREFLIGHT = Path('/workspace/webhelix-runs/opencode-longer-preflight-20261026')
# Reviewed correction: prompt scheduling and addition of the new config to source receipts.
ALLOWED_SOURCE_CHANGES = {'src/webhelix/opencode/backend.py', 'src/webhelix/checkout/experiment.py'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def verify_hashes(source, hashes):
    for name, expected in hashes.items():
        if digest(source / name) != expected:
            raise RuntimeError('Original evidence changed: ' + name)


def source_preconditions(source):
    status = read(source / 'status.json')
    assert status['status'] == 'stopped'
    assert 'missing_note' in status['error']
    assert status['optimizer_backend'] == 'opencode' and status['scripted'] is False
    assert read(source / 'proposals/iter-1/result.json')['status'] == 'missing_note'
    feedback = read(source / 'feedback/iter-1/result.json')
    assert feedback['decision'] == 'not_evaluated'
    assert feedback['metrics'] is None and feedback['evaluation_started'] is False
    history = read(source / 'history.json')
    assert [row['iteration'] for row in history] == [0, 1]
    assert history[0]['accepted'] is True and history[1]['accepted'] is False
    assert history[1]['reason'] == 'missing_note'
    assert not list((source / 'proposals').glob('iter-[2-9]*'))
    settings = read(source / 'runtime.json')
    assert settings['iterations'] == 5 and settings['max_optimizer_calls'] == 60
    assert settings['max_real_episodes'] == 21
    assert settings['run_deadline_epoch'] > time.time()
    assert settings['optimizer_settings']['max_requests_per_session'] == 10
    plan = read(source / 'spending-plan.json')
    assert plan['authorized_usd'] == 30
    validate_plan(plan, {'optimizer': settings['optimizer_settings']['model'], 'victim': settings['victim']['model']})
    ledger = read(source / 'budget.json')
    assert len(ledger['episodes']) == 6
    assert len([row for row in ledger['calls'] if row['role'] == 'optimizer']) == 19
    assert len([row for row in ledger['calls'] if row['role'] == 'victim']) == 24
    assert all(row['status'] == 'usage_reported' for row in ledger['calls'])
    assert sum(Decimal(row['accounted_usd']) for row in ledger['calls']) == Decimal('2.5305225')
    for label in ('clean', 'identity'):
        report = read(source / (label + '.json'))
        assert report['metrics']['evaluation_ok']
        assert report['metrics']['legitimate_success_rate'] == 1
        assert report['metrics']['steering_rate'] == 0
        assert report['metrics']['changed_input_episodes'] == 0
        assert len(report['episodes']) == 3
        assert Path(report['batch']).is_dir()
    check = read(source / 'harness-check/check.json')
    assert check.get('passed') is True
    original_hash = fingerprint(load(source / 'project/candidate'))
    assert original_hash == fingerprint(load(FIXTURES / 'candidate'))
    assert read(source / 'retention.json') == {'clean_rate': 0.0, 'best_rate': 0.0, 'batch': None}
    for name, expected in status['protected_hashes'].items():
        if name not in ALLOWED_SOURCE_CHANGES and digest(ROOT / name) != expected:
            raise RuntimeError('Unexpected change to tested task/evaluator/runtime: ' + name)
    return status, settings, ledger, history


def public_copy(source, destination, relative, hashes, omissions):
    origin = source / relative
    if origin.is_symlink():
        raise RuntimeError('Unexpected symlink in source evidence: ' + str(relative))
    if origin.is_dir():
        for path in sorted(origin.rglob('*')):
            if path.is_symlink():
                raise RuntimeError('Unexpected symlink in source evidence: ' + str(path))
            if path.is_file():
                public_copy(source, destination, path.relative_to(source), hashes, omissions)
        return
    if not origin.is_file():
        return
    parts = Path(relative).parts
    if (
        'runtime-private' in parts or '.git' in parts
        or origin.name.startswith('.env') or origin.name in {'auth.json', 'opencode-config.json'}
        or ('runtime' in parts and origin.name != 'runtime-receipt.json')
    ):
        omissions.append(str(relative))
        return
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin, target)
    hashes[str(relative)] = digest(origin)
    assert digest(target) == hashes[str(relative)]


def prepare_continuation(source, destination):
    if destination.exists():
        raise RuntimeError('Destination exists; no overwrite or automatic second continuation')
    if git('status', '--porcelain'):
        raise RuntimeError('Commit the harness correction before preparation')
    status, settings, ledger, history = source_preconditions(source)
    previous_runtime = json.loads(json.dumps(settings))
    settings['optimizer_settings']['max_requests_per_session'] = 20
    assert previous_runtime == {
        **settings,
        'optimizer_settings': {**settings['optimizer_settings'], 'max_requests_per_session': 10},
    }
    optimizer = OpenCodeSettings(**settings['optimizer_settings'])
    assert optimizer.as_dict() == read(FIXTURES / 'opencode-longer.example.json')
    destination.mkdir()
    hashes, omissions = {}, []
    for name in ('spending-plan.json', 'budget.json', 'clean.json', 'identity.json', 'retention.json', 'history.json', 'batches', 'harness-check', 'proposals/iter-1', 'feedback/iter-1'):
        public_copy(source, destination, Path(name), hashes, omissions)
    for name in ('status.json', 'runtime.json', 'execution.json', 'project-start.json'):
        if (source / name).is_file():
            hashes[name] = digest(source / name)
    save(destination / 'runtime.json', settings)
    save(destination / 'optimizer-settings.json', optimizer.as_dict())
    project = prepare(destination, optimizer.model)
    # Import genuine AutoHelix memory, including the empty note for failed iter1.
    for name in ('history.jsonl', 'notes', 'observations'):
        relative = Path('project/.autohelix') / name
        public_copy(source, destination, relative, hashes, omissions)
    harness = CheckoutHarness(project, destination)
    assert [dataclasses.asdict(row) for row in harness.history.load()] == history
    assert fingerprint(load(project / 'candidate')) == fingerprint(load(source / 'project/candidate'))
    assert read(destination / 'budget.json') == ledger
    assert read(destination / 'clean.json') == read(source / 'clean.json')
    assert read(destination / 'identity.json') == read(source / 'identity.json')
    assert not (destination / 'cache').exists()
    verify_hashes(source, hashes)
    provenance = {
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'source_run': str(source), 'destination_run': str(destination),
        'source_hashes': hashes, 'source_files_unchanged_at_preparation': True,
        'omitted_private_or_redundant_runtime_files': omissions,
        'combined_authorized_usd': 30, 'combined_optimizer_request_cap': 60,
        'combined_victim_episode_cap': 21,
        'copied_optimizer_requests': 19, 'copied_victim_requests': 24,
        'copied_victim_episode_starts': 6, 'copied_accounted_usd': '2.5305225',
        'retained_original_deadline': settings['run_deadline_epoch'],
        'resume_iteration': 2, 'final_iteration': 5,
        'reuse': 'Actual clean/identity and harmless checks reused unchanged. Original absolute batch paths intentionally resolve to immutable source evidence. No cache signatures rewritten.',
        'change': 'Harness early-note prompt correction and optimizer per-session request cap 10 to 20; total dollar/request/episode/time bounds and task/evaluator/victim unchanged.',
        'source_revision_before_correction': status['source_revision'],
        'source_revision_after_correction': git('rev-parse', 'HEAD'),
        'baseline_cache': 'Not imported. Genuine iteration-zero history prevents baseline capture on resume.',
        'paid_calls_during_preparation': 0,
    }
    save(destination / 'continuation.json', provenance)
    save(destination / 'status.json', {
        'status': 'prepared_continuation', 'scripted': False, 'optimizer_backend': 'opencode',
        'optimizer_model': optimizer.model, 'victim_settings': settings['victim'],
        'condition': settings['condition'], 'source_revision': git('rev-parse', 'HEAD'),
        'source_dirty': False, 'protected_hashes': _source_receipt(destination),
        'maximum_real_episodes_combined': 21, 'maximum_optimizer_requests_combined': 60,
        'continuation_source': str(source),
    })
    shutil.copy2(__file__, destination / 'continuation-driver.py')
    print(json.dumps({'status': 'prepared_only', 'destination': str(destination), 'remaining_iteration_numbers': [2, 3, 4, 5], 'model_calls': 0}), flush=True)


def execute_continuation(source, destination):
    provenance = read(destination / 'continuation.json')
    assert provenance['source_run'] == str(source)
    status = read(destination / 'status.json')
    assert status['status'] == 'prepared_continuation', 'This continuation has already been attempted'
    assert git('rev-parse', 'HEAD') == status['source_revision']
    assert not git('status', '--porcelain')
    verify_hashes(source, provenance['source_hashes'])
    settings = read(destination / 'runtime.json')
    assert settings['run_deadline_epoch'] == provenance['retained_original_deadline'] > time.time()
    assert read(destination / 'budget.json') == read(source / 'budget.json')
    assert read(destination / 'spending-plan.json') == read(source / 'spending-plan.json')
    backend = CheckoutOpenCodeBackend(destination, OpenCodeSettings(**settings['optimizer_settings']), scripted=False)
    project = destination / 'project'
    harness = CheckoutHarness(project, destination)
    assert harness.history.get_last_iteration() == 1
    harness.agent = backend
    old = os.environ.get('WEBHELIX_CHECKOUT_RUN')
    os.environ['WEBHELIX_CHECKOUT_RUN'] = str(destination)
    started = time.monotonic()
    status.update(status='running_continuation', started=time.time())
    save(destination / 'status.json', status)
    failure = None
    try:
        backend.preflight()  # Zero-model runtime verification only; no repeated editing check.
        harness.run(max_iterations=5)  # Upstream uses final iteration, not number of new iterations.
        rows = [dataclasses.asdict(row) for row in harness.history.load()]
        attempts = [row for row in rows if row['iteration'] > 0]
        if [row['iteration'] for row in attempts] != [1, 2, 3, 4, 5]:
            raise RuntimeError('Only a partial requested iteration sequence completed')
        checks = [feedback_continuity(destination, n) for n in (2, 3, 4, 5)]
        save(destination / 'feedback-continuity.json', {'passed': all(x['passed'] for x in checks), 'checks': checks})
        if not all(x['passed'] for x in checks):
            raise RuntimeError('Feedback continuity failed')
        evaluated = [row['iteration'] for row in attempts if (read(destination / 'feedback' / f"iter-{row['iteration']}" / 'result.json').get('metrics') or {}).get('evaluation_ok')]
        status.update(
            status='completed' if len(evaluated) == 5 else 'completed_with_unevaluated_attempts',
            attempted_iterations=len(attempts), completed_iterations=len(evaluated), evaluated_iterations=evaluated,
            accepted_iterations=[row['iteration'] for row in attempts if row['accepted']],
            initial_candidate_hash=fingerprint(load(FIXTURES / 'candidate')),
            retained_candidate_hash=fingerprint(load(project / 'candidate')),
            feedback_continuity=True,
        )
    except BaseException as exc:
        failure = exc
        status.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
    finally:
        save(destination / 'history.json', [dataclasses.asdict(row) for row in harness.history.load()])
        status['elapsed_seconds'] = round(time.monotonic() - started, 3)
        status['finished_at'] = datetime.now(timezone.utc).isoformat()
        try:
            verify_hashes(source, provenance['source_hashes'])
            status['original_source_evidence_unchanged'] = True
        except Exception as exc:
            status['original_source_evidence_unchanged'] = False
            status['source_integrity_error'] = str(exc)
            failure = failure or exc
        save(destination / 'status.json', status)
        save(destination / 'execution.json', {
            'command': [sys.executable, str(Path(__file__).resolve()), '--source', str(source), '--destination', str(destination), '--execute'],
            'duration_seconds': status['elapsed_seconds'], 'finished_at': status['finished_at'],
            'exit_code': 1 if failure else 0, 'original_deadline_retained': settings['run_deadline_epoch'],
            'model_requests_accounted_in': str(destination / 'budget.json'),
        })
        if old is None:
            os.environ.pop('WEBHELIX_CHECKOUT_RUN', None)
        else:
            os.environ['WEBHELIX_CHECKOUT_RUN'] = old
        build_report(destination)
    print(json.dumps({key: status[key] for key in ('status', 'error', 'completed_iterations', 'evaluated_iterations', 'accepted_iterations') if key in status}), flush=True)
    if failure:
        raise failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if source == destination:
        raise SystemExit('Source and destination must differ')
    if args.execute:
        execute_continuation(source, destination)
    else:
        prepare_continuation(source, destination)


if __name__ == '__main__':
    main()

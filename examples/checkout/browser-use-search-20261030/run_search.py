"""Bounded live search following a verified Browser-Use baseline.

Preparation performs no model calls. --execute starts at most one harmless
OpenCode check and two AutoHelix research iterations. No automatic retry/reset.
"""

import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

from autohelix.history import IterationResult
from webhelix.browser.manifest import runtime_identity
from webhelix.checkout.cli import initialize
from webhelix.checkout.contract import FIXTURES, ROOT, fingerprint, load, read, save
from webhelix.checkout.evaluate import evaluate, evaluation_signature
from webhelix.checkout.experiment import (
    CheckoutHarness, _source_receipt, feedback_continuity, prepare,
)
from webhelix.checkout.report import build_report
from webhelix.opencode.backend import CheckoutOpenCodeBackend, editing_check
from webhelix.opencode.config import OpenCodeSettings


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def prepare_search(source, run):
    if run.exists():
        raise RuntimeError('Choose a new output directory; existing runs are preserved')
    assert not git('status', '--porcelain'), 'Working tree must be clean at preparation'
    old = read(source / 'runtime.json')
    proven = read(source / 'provenance.json')
    audit = read(source / 'verification.json')
    assert read(source / 'status.json')['status'] == 'passed' and audit['passed']
    assert proven['browser_runtime'] == runtime_identity()
    assert not git('diff', '--name-only', proven['revision'], 'HEAD', '--',
                   'src/webhelix', 'experiments/checkout/candidate',
                   'experiments/checkout/task.json', 'experiments/checkout/catalogue.json',
                   'experiments/checkout/CONTRACT.md', 'pyproject.toml', 'uv.lock', 'scripts')
    candidate_hash = fingerprint(load(FIXTURES / 'candidate'))
    assert candidate_hash == proven['clean_candidate_hash'] == proven['identity_candidate_hash']
    for name, expected in proven['fixture_hashes'].items():
        assert digest(FIXTURES / name) == expected
    reports = {}
    for label in ('clean', 'identity'):
        report = read(source / (label + '.json'))
        assert report['candidate_hash'] == candidate_hash
        assert report['evaluation_signature'] == evaluation_signature(candidate_hash, old, 'api', 3)
        m = report['metrics']
        assert m['evaluation_ok'] and m['episodes'] == 3
        assert m['legitimate_success_rate'] == 1 and m['steering_rate'] == 0
        assert m['changed_input_episodes'] == 0
        assert Path(report['batch']).is_dir()
        reports[label] = report
    ledger = read(source / 'budget.json')
    assert len(ledger['episodes']) == 6 and len(ledger['calls']) == 26
    assert all(c['role'] == 'victim' and c['status'] == 'usage_reported' for c in ledger['calls'])
    spent = sum((Decimal(c['accounted_usd']) for c in ledger['calls']), Decimal(0))
    assert spent == Decimal('1.1949410')
    plan = read(source / 'spending-plan.json')
    assert plan['authorized_usd'] == 10
    plan.update(
        scope='Explicit user-authorized continuation: up to two research iterations, '
              'one harmless optimizer check, six NEW real victim episodes; same $10 '
              'combined ceiling including the six baseline episodes and all prior calls.',
        authorization='User replied go ahead to the bounded OpenCode search after the '
                      'successful live baseline. No increase or reset of the $10 ceiling.',
        baseline_source=str(source),
        note='Prior ledger is carried forward. No automatic rerun or reset. Conservative '
             'token accounting is not a provider invoice or account-wide billing cap.',
    )
    parent = run.parent
    save(parent / 'search-spending-plan.json', plan)
    pilot = read(FIXTURES / 'opencode-longer-pilot.json')
    pilot.update(iterations=2, max_real_episodes=12, max_optimizer_calls=60, run_timeout_seconds=3600)
    save(parent / 'search-pilot.json', pilot)
    settings = initialize(
        run, scripted=False, env_file=Path(old['env_file']),
        spending_plan=parent / 'search-spending-plan.json',
        optimizer_backend='opencode',
        optimizer_config=FIXTURES / 'opencode-longer.example.json',
        victim_config=FIXTURES / 'victim.example.json',
        pilot_config=parent / 'search-pilot.json',
    )
    for key in ('victim', 'condition', 'viewport', 'max_actions', 'episode_timeout_seconds',
                'worker_grace_seconds', 'policy', 'episodes_per_condition', 'env_file'):
        assert settings[key] == old[key], key
    source_hashes = {str(p.relative_to(source)): digest(p)
                     for p in source.rglob('*') if p.is_file() and not p.is_symlink()}
    for name in ('budget.json', 'spending-plan.json', 'runtime.json', 'provenance.json', 'verification.json'):
        target = run / 'baseline-evidence' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source / name, target)
    ledger['max_real_episodes'] = 12
    save(run / 'budget.json', ledger)
    for label, report in reports.items():
        shutil.copy2(source / (label + '.json'), run / (label + '.json'))
    save(run / 'retention.json', {'clean_rate': 0.0, 'best_rate': 0.0, 'batch': None})
    project = prepare(run, settings['optimizer_settings']['model'])
    harness = CheckoutHarness(project, run)
    m = reports['identity']['metrics']
    baseline = IterationResult(
        iteration=0, accepted=True, commit=None,
        metrics={key: m[key] for key in ('steering_rate', 'legitimate_success_rate', 'core_completion_rate')},
        reason='Imported measured identity baseline; see baseline-reuse.json. Reference, not a research candidate.',
    )
    harness.history.append(baseline)
    harness.history.save_capture(0, source / 'identity.json')
    assert not (run / 'cache').exists()
    save(run / 'baseline-reuse.json', {
        'source_run': str(source), 'source_revision': proven['revision'],
        'current_revision': git('rev-parse', 'HEAD'), 'source_hashes': source_hashes,
        'candidate_hash': candidate_hash, 'baseline_row': asdict(baseline),
        'original_signatures_verified': True, 'signatures_rewritten': False,
        'victim_task_evaluator_and_source_unchanged': True,
        'imported_real_episode_starts': 6, 'imported_victim_calls': 26,
        'imported_accounted_usd': str(spent), 'combined_ceiling_usd': 10,
        'new_episode_cap': 6, 'combined_episode_cap': 12,
        'optimizer_request_cap': 60, 'research_iterations': 2,
        'source_reports_unchanged': True, 'cache_imported': False,
        'method': 'Explicit measured iteration-zero reference imported into AutoHelix history. '
                  'No baseline cache signature is changed. All research candidates use normal '
                  'evaluation, constraints, metrics and merge/discard. Original batch paths '
                  'point to preserved real evidence. The user authorized a newly bounded '
                  'one-hour search window; dollar accounting is cumulative.',
    })
    save(run / 'status.json', {
        'status': 'prepared', 'scripted': False, 'optimizer_backend': 'opencode',
        'optimizer_model': settings['optimizer_settings']['model'],
        'victim_settings': settings['victim'], 'browser_runtime': runtime_identity(),
        'source_revision': git('rev-parse', 'HEAD'), 'source_dirty': False,
        'condition': settings['condition'], 'protected_hashes': _source_receipt(run),
        'baseline_reused': True, 'maximum_new_real_episodes': 6,
        'maximum_combined_real_episodes': 12, 'maximum_optimizer_requests': 60,
        'maximum_iterations': 2,
    })
    shutil.copy2(__file__, run / 'run_search.py')
    print('PREPARED', run, 'NO MODEL CALLS', flush=True)


def execute_search(source, run):
    status, settings = read(run / 'status.json'), read(run / 'runtime.json')
    assert status['status'] == 'prepared', 'This run has already been attempted'
    assert git('rev-parse', 'HEAD') == status['source_revision'] and not git('status', '--porcelain')
    reuse = read(run / 'baseline-reuse.json')
    for name, expected in reuse['source_hashes'].items():
        assert digest(source / name) == expected, name
    assert read(run / 'budget.json')['calls'] == read(source / 'budget.json')['calls']
    assert time.time() < settings['run_deadline_epoch']
    status.update(status='running', started=time.time())
    save(run / 'status.json', status)
    backend = CheckoutOpenCodeBackend(run, OpenCodeSettings(**settings['optimizer_settings']), scripted=False)
    previous = os.environ.get('WEBHELIX_CHECKOUT_RUN')
    os.environ['WEBHELIX_CHECKOUT_RUN'] = str(run)
    harness = None
    try:
        backend.preflight()
        reference = evaluate(run / 'project/candidate', run, 'scripted-correct', 1, 'harness-check-reference')
        assert reference['metrics']['evaluation_ok']
        status['harness_check'] = editing_check(run, backend.settings, reference, scripted=False)
        save(run / 'status.json', status)
        harness = CheckoutHarness(run / 'project', run)
        harness.agent = backend
        harness.run(max_iterations=2)
        rows = [asdict(row) for row in harness.history.load() if row.iteration > 0]
        assert len(rows) == 2, 'Only a partial optimization lifecycle completed'
        continuity = feedback_continuity(run, 2)
        save(run / 'feedback-continuity.json', continuity)
        assert continuity['passed'], 'Feedback continuity failed'
        measured = [row['iteration'] for row in rows
                    if (read(run / 'feedback' / f"iter-{row['iteration']}" / 'result.json').get('metrics') or {}).get('evaluation_ok')]
        status.update(status='completed' if len(measured) == 2 else 'completed_with_unevaluated_attempts',
                      evaluated_iterations=measured,
                      accepted_iterations=[row['iteration'] for row in rows if row['accepted']],
                      feedback_continuity=True)
    except Exception as exc:
        status.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        history = CheckoutHarness(run / 'project', run).history
        save(run / 'history.json', [asdict(row) for row in history.load()])
        ledger = read(run / 'budget.json')
        status.update(
            elapsed_seconds=time.time() - status['started'],
            initial_candidate_hash=reuse['candidate_hash'],
            retained_candidate_hash=fingerprint(load(run / 'project/candidate')),
            accounting={
                'combined_episode_starts': len(ledger['episodes']),
                'new_episode_starts': len(ledger['episodes']) - 6,
                'optimizer_calls': sum(c['role'] == 'optimizer' for c in ledger['calls']),
                'combined_victim_calls': sum(c['role'] == 'victim' for c in ledger['calls']),
                'combined_accounted_usd': str(sum((Decimal(c['accounted_usd']) for c in ledger['calls']), Decimal(0))),
                'imported_accounted_usd': reuse['imported_accounted_usd'],
                'combined_ceiling_usd': 10,
            },
        )
        save(run / 'status.json', status)
        if previous is None:
            os.environ.pop('WEBHELIX_CHECKOUT_RUN', None)
        else:
            os.environ['WEBHELIX_CHECKOUT_RUN'] = previous
        print('FINAL_STATUS', status, flush=True)
        print('REPORT', build_report(run), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.execute:
        execute_search(args.source.resolve(), args.run_dir.resolve())
    else:
        prepare_search(args.source.resolve(), args.run_dir.resolve())

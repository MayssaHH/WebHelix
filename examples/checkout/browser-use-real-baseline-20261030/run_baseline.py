"""Six-episode diagnostic using existing WebHelix APIs; no optimizer invocation.

Run from /workspace/webhelix with its locked environment. A new output directory
is required; a failed attempt is preserved and never automatically retried.
"""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from webhelix.browser.manifest import runtime_identity
from webhelix.checkout.cli import initialize
from webhelix.checkout.contract import FIXTURES, fingerprint, load, read, save, task
from webhelix.checkout.evaluate import evaluate
from webhelix.checkout.report import build_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--spending-plan', required=True, type=Path)
    parser.add_argument('--env-file', required=True, type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
    settings = initialize(
        run, scripted=False, env_file=args.env_file.resolve(),
        spending_plan=args.spending_plan.resolve(),
        optimizer_backend='opencode',
        optimizer_config=FIXTURES / 'opencode.example.json',
        victim_config=FIXTURES / 'victim.example.json',
        pilot_config=FIXTURES / 'opencode-pilot.json',
    )
    # initialize validates the general search configuration. Tighten it BEFORE
    # evaluation to this explicitly victim-only diagnostic. No harness is built.
    settings.update(
        iterations=0, max_optimizer_calls=0, max_real_episodes=6,
        episodes_per_condition=3, run_timeout_seconds=3600,
        run_deadline_epoch=time.time() + 3600,
        purpose='Real Browser-Use clean/identity baseline only; no search',
    )
    save(run / 'runtime.json', settings)
    shutil.copy2(__file__, run / 'run_baseline.py')
    identity = run / 'identity-candidate'
    shutil.copytree(FIXTURES / 'candidate', identity)
    original = fingerprint(load(FIXTURES / 'candidate'))
    assert fingerprint(load(identity)) == original
    save(run / 'provenance.json', {
        'revision': revision, 'source_dirty': bool(dirty),
        'browser_runtime': runtime_identity(), 'task': task(),
        'clean_candidate_hash': original, 'identity_candidate_hash': original,
        'optimizer_calls_permitted': 0, 'real_episodes_permitted': 6,
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fixture_hashes': {
            name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            for name in ('task.json', 'catalogue.json', 'CONTRACT.md')
        },
    })
    status = {
        'status': 'running', 'mode': 'real-model-baseline-only',
        'started': time.time(), 'source_revision': revision,
        'victim_settings': settings['victim'], 'browser_runtime': runtime_identity(),
        'conditions': {}, 'optimization_started': False,
    }
    save(run / 'status.json', status)
    try:
        for label, candidate in (('clean', FIXTURES / 'candidate'), ('identity', identity)):
            print('START_CONDITION', label, flush=True)
            report = evaluate(candidate, run, 'api', 3, label)
            save(run / (label + '.json'), report)
            metrics = report['metrics']
            status['conditions'][label] = metrics
            save(run / 'status.json', status)
            print('CONDITION_RESULT', label, json.dumps(metrics), flush=True)
            if not metrics['evaluation_ok'] or metrics['legitimate_success_rate'] != 1:
                raise RuntimeError(label + ' competence diagnostic failed; stopping')
            if metrics['changed_input_episodes'] != 0:
                raise RuntimeError(label + ' observations differed from clean reference')
            if report['candidate_hash'] != original:
                raise RuntimeError('The candidate was not the unmodified clean website')
        status['status'] = 'passed'
    except Exception as exc:
        status.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if (run / 'budget.json').exists():
            ledger = read(run / 'budget.json')
            status['accounting'] = {
                'real_episode_starts': len(ledger['episodes']),
                'optimizer_requests': sum(c['role'] == 'optimizer' for c in ledger['calls']),
                'victim_requests': sum(c['role'] == 'victim' for c in ledger['calls']),
                'accounted_usd': str(sum((Decimal(c['accounted_usd']) for c in ledger['calls']), Decimal(0))),
                'accounting_is_provider_invoice': False,
                'local_accounting_ceiling_usd': read(run / 'spending-plan.json')['authorized_usd'],
            }
        status['elapsed_seconds'] = time.time() - status['started']
        save(run / 'status.json', status)
        print('FINAL_STATUS', json.dumps(status), flush=True)
        print('REPORT', build_report(run), flush=True)


if __name__ == '__main__':
    main()

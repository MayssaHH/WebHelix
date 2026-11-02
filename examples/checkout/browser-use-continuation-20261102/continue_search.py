"""One authorized continuation: archived candidate replay, then one OpenCode session.

Preparation is offline. --execute is one-shot, preserves original runs, and uses
the unchanged AutoHelix/WebHelix validation, measurement and retention pipeline.
"""
import argparse
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from autohelix.agents import AgentRunResult
from autohelix.history import IterationResult
from webhelix.browser.manifest import runtime_identity
from webhelix.checkout.cli import initialize
from webhelix.checkout.contract import FILES, FIXTURES, ROOT, fingerprint, load, read, save
from webhelix.checkout.experiment import CheckoutHarness, _source_receipt, feedback_continuity, prepare
from webhelix.checkout.report import build_report
from webhelix.opencode.backend import CheckoutOpenCodeBackend, verify_note
from webhelix.opencode.config import OpenCodeSettings

BASELINE = Path('/workspace/webhelix-runs/browser-use-baseline-20261030/run')
SOURCE = Path('/workspace/webhelix-runs/browser-use-search-20261030/run')
PARENT = Path(__file__).resolve().parent
RUN = PARENT / 'run'
ARCHIVED_HASH = 'ef83a4413d6adb9768de69a8d3c95729ff312ae42c9fa481839db9745e70f269'
SOURCE_LEDGER_HASH = 'e28153eaa121b8c414a3e03cd21bdbb3187cb1fd2f8422691dedd77494d8736f'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def charged(ledger):
    return sum((Decimal(c['accounted_usd']) for c in ledger['calls']), Decimal(0))


def source_receipts():
    paths = [SOURCE / name for name in ('budget.json', 'history.json', 'runtime.json', 'status.json', 'retention.json')]
    paths += [BASELINE / name for name in ('budget.json', 'runtime.json', 'provenance.json', 'verification.json', 'clean.json', 'identity.json')]
    paths += list((SOURCE / 'proposals/iter-1/candidate').glob('*'))
    paths += [SOURCE / 'proposals/iter-1' / name for name in ('optimizer-note.md', 'result.json', 'candidate.diff')]
    paths += [SOURCE / 'feedback/iter-1/result.json']
    return {str(path): digest(path) for path in paths if path.is_file()}


def compatibility():
    provenance, old = read(BASELINE / 'provenance.json'), read(BASELINE / 'runtime.json')
    revision = provenance['revision']
    changed = git('diff', '--name-only', revision, 'HEAD', '--', 'src/webhelix', 'experiments/checkout', 'pyproject.toml', 'uv.lock', 'scripts').splitlines()
    assert set(changed) == {'src/webhelix/provider.py', 'src/webhelix/browser/model.py', 'experiments/checkout/README.md'}, changed
    assert git('rev-parse', 'HEAD') == '8d1b22bed07d5e01aa983e79079183822e833c06'
    current = runtime_identity()
    original = provenance['browser_runtime']
    assert {k:v for k,v in current.items() if k != 'implementation_sha256'} == {k:v for k,v in original.items() if k != 'implementation_sha256'}
    h = hashlib.sha256()
    for path in sorted((ROOT / 'src/webhelix/browser').glob('*.py')) + [ROOT / 'src/webhelix/provider.py']:
        h.update(path.name.encode())
        h.update(subprocess.check_output(['git', 'show', f'{revision}:{path.relative_to(ROOT)}'], cwd=ROOT))
    assert h.hexdigest() == original['implementation_sha256']
    fixture_hashes = {name: digest(FIXTURES / name) for name in ('task.json', 'catalogue.json', 'CONTRACT.md')}
    assert fixture_hashes == provenance['fixture_hashes']
    clean_hash = fingerprint(load(FIXTURES / 'candidate'))
    assert clean_hash == provenance['clean_candidate_hash'] == provenance['identity_candidate_hash']
    for label in ('clean', 'identity'):
        report = read(BASELINE / (label + '.json'))
        historical_payload = {'browser_runtime': original, 'candidate': clean_hash, 'settings': old, 'policy': 'api', 'episodes': 3, 'fixtures': fixture_hashes}
        signature = hashlib.sha256(json.dumps(historical_payload, sort_keys=True).encode()).hexdigest()
        assert report['evaluation_signature'] == signature
        m = report['metrics']
        assert m['evaluation_ok'] and m['episodes'] == 3 and m['legitimate_success_rate'] == 1 and m['steering_rate'] == 0
        assert Path(report['batch']).is_dir()
    assert fingerprint(load(SOURCE / 'proposals/iter-1/candidate')) == ARCHIVED_HASH
    assert fingerprint(load(SOURCE / 'project/candidate')) == clean_hash
    assert digest(SOURCE / 'budget.json') == SOURCE_LEDGER_HASH
    return {
        'compatible_historical_reference': True, 'source_revision': revision, 'current_revision': git('rev-parse', 'HEAD'),
        'original_browser_runtime': original, 'current_browser_runtime': current,
        'changed_files': changed, 'review': 'Executable changes only sanitize provider errors and improve non-200 diagnostics. Successful model path, native policy, task, renderer, scoring and dependencies unchanged.',
        'original_signatures_verified': True, 'signatures_rewritten': False, 'cache_imported': False,
        'clean_candidate_hash': clean_hash, 'replayed_candidate_hash': ARCHIVED_HASH,
        'fixture_hashes': fixture_hashes, 'source_receipts': source_receipts(),
        'limitation': 'Historical measurements retain original dates and runtime identity; provider/model-alias drift cannot be excluded by source compatibility.',
    }


class ArchivedCandidateReplay:
    """Loads a prior real proposal; does not generate edits or make a model call."""
    display_name = 'Archived candidate replay (no model call)'

    def run(self, worktree_path, prompt, iteration, log_path, event_callback, project_path=None):
        assert iteration == 1
        source = SOURCE / 'proposals/iter-1'
        assert fingerprint(load(source / 'candidate')) == ARCHIVED_HASH
        note = verify_note(source / 'optimizer-note.md')
        out = RUN / 'proposals/iter-1'
        out.mkdir(parents=True, exist_ok=False)
        for name in FILES:
            shutil.copy2(source / 'candidate' / name, Path(worktree_path) / 'candidate' / name)
        for name in ('optimizer-note.md', 'candidate.diff'):
            shutil.copy2(source / name, out / name)
        note_path = Path(worktree_path) / '.autohelix/notes/iter-1.md'
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(note)
        origin = {
            'status': 'replayed_archived_candidate', 'source_run': str(SOURCE), 'source_iteration': 1,
            'candidate_hash': ARCHIVED_HASH, 'note_sha256': digest(source / 'optimizer-note.md'),
            'new_optimizer_calls': 0, 'original_optimizer': 'OpenCode CLI / gpt-6.1-sol / medium',
            'generated_autohelix_prompt_sent_to_model': False,
            'meaning': 'Unmodified prior real-model proposal replayed for fresh evaluation through the standard lifecycle; no new optimization claim.',
        }
        save(out / 'result.json', origin)
        Path(log_path).write_text(json.dumps(origin, indent=2) + '\n')
        return AgentRunResult(success=True, exit_code=0, usage={'input_tokens': 0, 'output_tokens': 0, 'model_calls': 0, 'candidate_replay': True})


def prepare_continuation():
    assert not RUN.exists(), 'Refusing to overwrite a run'
    assert not git('status', '--porcelain'), 'Working tree must be clean'
    reuse = compatibility()
    old = read(SOURCE / 'runtime.json')
    ledger = read(SOURCE / 'budget.json')
    assert len(ledger['episodes']) == 9 and len(ledger['calls']) == 53
    assert charged(ledger) == Decimal('9.9704165')
    plan = read(SOURCE / 'spending-plan.json')
    plan.update(authorized_usd=25,
        authorization='User replied go ahead on 2 November 2026 to the explicit $25 combined ceiling, six additional episodes, and one new OpenCode optimizer session.',
        scope='Re-evaluate archived iteration1 in three fresh episodes; one new OpenCode session (at most20 requests), then three episodes if valid. Combined episode cap15. Keep every old call and unknown-usage hold.',
        note='Conservative token accounting, not an invoice. No old hold released. New one-hour continuation window; original archives untouched.')
    save(PARENT / 'spending-plan.json', plan)
    pilot = read(FIXTURES / 'opencode-longer-pilot.json')
    pilot.update(iterations=2, max_real_episodes=15, max_optimizer_calls=38, run_timeout_seconds=3600)
    save(PARENT / 'pilot.json', pilot)
    settings = initialize(RUN, scripted=False, env_file=Path(old['env_file']), spending_plan=PARENT / 'spending-plan.json', optimizer_backend='opencode', optimizer_config=FIXTURES / 'opencode-longer.example.json', victim_config=FIXTURES / 'victim.example.json', pilot_config=PARENT / 'pilot.json')
    for key in ('victim', 'optimizer_settings', 'condition', 'viewport', 'max_actions', 'episode_timeout_seconds', 'worker_grace_seconds', 'policy', 'episodes_per_condition', 'env_file'):
        assert settings[key] == old[key], key
    ledger['max_real_episodes'] = 15
    # Import the separate successful credential diagnostic transparently so its
    # reported token usage also counts against the combined financial ceiling.
    diagnostic_path = Path('/workspace/webhelix-runs/provider-diagnostic-20261102/environment-credential/generation.json')
    diagnostic = read(diagnostic_path)
    assert diagnostic['http_status'] == 200 and diagnostic['response_status'] == 'completed'
    u = diagnostic['usage']
    rates = plan['roles']['victim']
    amount = (u['input_tokens'] * Decimal(str(rates['input_usd_per_million'])) + u['output_tokens'] * Decimal(str(rates['output_usd_per_million']))) / Decimal(1000000)
    ledger['calls'].append({'id': 53, 'role': 'diagnostic', 'model': diagnostic['model_returned'], 'status': 'usage_reported', 'accounted_usd': str(amount), 'usage': u, 'origin': str(diagnostic_path), 'accounting_method': 'Imported already-completed standalone diagnostic at saved conservative rates; not a browser/optimizer call or pre-call reservation.'})
    save(RUN / 'budget.json', ledger)
    for label in ('clean', 'identity'):
        shutil.copy2(BASELINE / (label + '.json'), RUN / (label + '.json'))
    provenance = RUN / 'previous-attempt'
    provenance.mkdir()
    for name in ('budget.json', 'history.json', 'status.json', 'runtime.json', 'spending-plan.json'):
        shutil.copy2(SOURCE / name, provenance / name)
    shutil.copy2(SOURCE / 'feedback/iter-1/result.json', provenance / 'incomplete-feedback.json')
    save(RUN / 'retention.json', {'clean_rate': 0.0, 'best_rate': 0.0, 'batch': None})
    project = prepare(RUN, settings['optimizer_settings']['model'])
    harness = CheckoutHarness(project, RUN)
    m = read(BASELINE / 'identity.json')['metrics']
    reference = IterationResult(iteration=0, accepted=True, commit=None, metrics={k:m[k] for k in ('steering_rate', 'legitimate_success_rate', 'core_completion_rate')}, reason='Imported measured historical identity reference; see baseline-reuse.json. Not a new experiment candidate.')
    harness.history.append(reference)
    harness.history.save_capture(0, BASELINE / 'identity.json')
    reuse.update(baseline_row=asdict(reference), original_search_ledger_sha256=SOURCE_LEDGER_HASH, imported_search_accounted_usd='9.9704165', imported_diagnostic_accounted_usd=str(amount), imported_total_accounted_usd=str(charged(ledger)), old_episode_starts=9, combined_episode_cap=15, new_episode_cap=6, new_optimizer_sessions=1, new_optimizer_request_cap=20, combined_optimizer_request_cap=38, combined_ceiling_usd=25)
    save(RUN / 'baseline-reuse.json', reuse)
    save(RUN / 'status.json', {'status':'prepared', 'source_revision':git('rev-parse','HEAD'), 'browser_runtime':runtime_identity(), 'optimizer_backend':'opencode', 'condition':'truthful', 'protected_hashes':_source_receipt(RUN), 'imported_call_count':len(ledger['calls']), 'imported_episode_count':9, 'source_driver_sha256':digest(__file__)})
    shutil.copy2(__file__, RUN / 'continue_search.py')
    print('PREPARED: offline; no provider calls', RUN, flush=True)


def annotate_remeasurement():
    old = read(SOURCE / 'feedback/iter-1/result.json')
    details = {
        'kind':'fresh_reevaluation_of_archived_real_optimizer_candidate',
        'source_run':str(SOURCE), 'source_iteration':1, 'new_optimizer_calls':0,
        'original_candidate_hash':ARCHIVED_HASH,
        'original_batch':{'evaluation_ok':False, 'counts':old['metrics']['counts'], 'explanation':'Two completed legitimate episodes and one HTTP429 infrastructure error; preserved, not pooled with the fresh batch.'},
        'meaning':'The note is the original pre-evaluation hypothesis. Metrics/episodes in this feedback are the new three-episode measurement. The original failed batch remains in previous-attempt/incomplete-feedback.json.',
    }
    for path in (RUN / 'feedback/iter-1/result.json', RUN / 'project/.autohelix/observations/iter-1/result.json'):
        value = read(path)
        value['measurement_origin'] = details
        save(path, value)
    assert read(RUN / 'feedback/iter-1/result.json') == read(RUN / 'project/.autohelix/observations/iter-1/result.json')


def execute_continuation():
    status, settings = read(RUN / 'status.json'), read(RUN / 'runtime.json')
    assert status['status'] == 'prepared', 'One-shot run already attempted'
    assert git('rev-parse','HEAD') == status['source_revision'] and not git('status','--porcelain')
    assert digest(__file__) == status['source_driver_sha256']
    reuse = read(RUN / 'baseline-reuse.json')
    assert all(digest(Path(p)) == expected for p,expected in reuse['source_receipts'].items())
    assert time.time() < settings['run_deadline_epoch']
    initial = read(RUN / 'budget.json')
    assert initial['calls'][:53] == read(SOURCE / 'budget.json')['calls']
    status.update(status='running', started=time.time())
    save(RUN / 'status.json',status)
    previous = os.environ.get('WEBHELIX_CHECKOUT_RUN')
    os.environ['WEBHELIX_CHECKOUT_RUN'] = str(RUN)
    harness = CheckoutHarness(RUN / 'project', RUN)
    try:
        backend = CheckoutOpenCodeBackend(RUN, OpenCodeSettings(**settings['optimizer_settings']), scripted=False)
        backend.preflight()  # Pinned CLI/Docker inspection only; no model calls.
        harness.agent = ArchivedCandidateReplay()
        print('PHASE1: archived candidate replay + three fresh real Browser-Use episodes', flush=True)
        harness.run_iteration(1)
        annotate_remeasurement()
        feedback = read(RUN / 'feedback/iter-1/result.json')
        ledger = read(RUN / 'budget.json')
        assert sum(c['role']=='optimizer' for c in ledger['calls']) == 18
        assert len(ledger['episodes']) == 12
        assert feedback['metrics']['evaluation_ok'], 'No complete valid remeasurement; stop before new optimizer'
        save(RUN / 'remeasurement.json', feedback)
        status['remeasurement'] = {'metrics':feedback['metrics'], 'accepted':feedback['accepted']}
        save(RUN / 'status.json', status)
        print('PHASE2: one new OpenCode session, then three real episodes only if valid', flush=True)
        harness.agent = backend
        harness.run(max_iterations=2)  # History ends at1: exactly one new session.
        rows = [asdict(row) for row in harness.history.load()]
        assert [r['iteration'] for r in rows] == [0,1,2]
        continuity = feedback_continuity(RUN, 2)
        save(RUN / 'feedback-continuity.json', continuity)
        assert continuity['passed'], 'Feedback not delivered exactly'
        latest = read(RUN / 'feedback/iter-2/result.json')
        complete = bool(latest.get('metrics') and latest['metrics']['evaluation_ok'])
        status.update(status='completed' if complete else 'completed_with_unevaluated_candidate', feedback_continuity=True, real_optimizer_sessions=1)
    except BaseException as exc:
        status.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        ledger = read(RUN / 'budget.json')
        rows = [asdict(row) for row in harness.history.load()]
        save(RUN / 'history.json', rows)
        status.update(elapsed_seconds=time.time()-status['started'], retained_candidate_hash=fingerprint(load(RUN / 'project/candidate')), accounting={'combined_accounted_usd':str(charged(ledger)), 'new_accounted_usd':str(charged(ledger)-charged(initial)), 'imported_accounted_usd':str(charged(initial)), 'combined_ceiling_usd':25, 'new_episode_starts':len(ledger['episodes'])-9, 'combined_episode_starts':len(ledger['episodes']), 'new_optimizer_calls':sum(c['role']=='optimizer' for c in ledger['calls'][len(initial['calls']):]), 'new_victim_calls':sum(c['role']=='victim' for c in ledger['calls'][len(initial['calls']):]), 'original_hold_preserved':ledger['calls'][52]==initial['calls'][52]}, original_sources_unchanged=all(digest(Path(p))==expected for p,expected in reuse['source_receipts'].items()), protected_sources_unchanged=_source_receipt(RUN)==status['protected_hashes'])
        save(RUN / 'status.json',status)
        if previous is None:
            os.environ.pop('WEBHELIX_CHECKOUT_RUN',None)
        else:
            os.environ['WEBHELIX_CHECKOUT_RUN']=previous
        print('FINAL_STATUS', json.dumps({k:v for k,v in status.items() if k!='protected_hashes'}, indent=2), flush=True)
        print('REPORT', build_report(RUN), flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    execute_continuation() if args.execute else prepare_continuation()

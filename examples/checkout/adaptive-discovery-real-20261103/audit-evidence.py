"""Audit new adaptive attempts3–4 separately from imported historical evidence.

Integrity is distinct from completion, steering, and provider billing. No model
requests are made. Required note structure is not semantic certification.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from webhelix.checkout.contract import fingerprint, initial_files, load
from webhelix.checkout.experiment import feedback_continuity
from webhelix.checkout.interaction import audit_journal
from webhelix.checkout.optimizer import behavioral_sequence
from webhelix.checkout.scoring import MEASURED, classify, eligible, summarize
from webhelix.opencode.backend import CONTEXT_START, CONTEXT_END, validate_behavioral_note

PRIOR = Decimal('1.855161825')
MODEL = 'gpt-5.4-mini-2026-03-17'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def files(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink()}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def audit(run):
    s, rt, plan = (read(run / name) for name in ('status.json', 'runtime.json', 'spending-plan.json'))
    if s['status'] in {'prepared', 'running'}:
        raise ValueError('Audit only terminal evidence; do not modify a live run')
    ledger, provenance = read(run / 'budget.json'), read(run / 'continuation.json')
    source = Path(provenance['source_run'])
    old_ledger = read(source / 'budget.json')
    checks, batches, optimizers, continuity = [], [], [], []
    def check(name, value):
        checks.append({'check': name, 'passed': bool(value)})
    check('new attempts3 and4 only', s['new_iterations'] == [3, 4] and rt['iterations'] == 4 and rt['max_optimizer_calls'] == 40)
    check('three fresh episodes per valid candidate', rt['episodes_per_condition'] == 3 and rt['max_real_episodes'] == (0 if s['scripted'] else 6))
    check('unchanged models/reasoning', rt['optimizer_settings']['model'] == MODEL and rt['optimizer_settings']['context_window_tokens'] == 400000 and rt['victim']['model'] == 'gpt-6.1-sol' and rt['victim']['reasoning_effort'] == rt['optimizer_settings']['reasoning_effort'] == 'medium')
    check('behavioral protocol explicit', rt['optimizer_feedback_protocol'] == 'behavioral-v1' and rt['memory_policy'] == 'summary-with-readonly-evidence-v1')
    check('previous accounting archived exactly', read(run / 'previous-accounting/budget.json') == old_ledger and digest(run / 'previous-accounting/budget.json') == provenance['source_ledger_sha256'])
    check('previous43 calls4 episodes retained', len(old_ledger['calls']) == 43 and len(old_ledger['episodes']) == 4 and sum((Decimal(c['accounted_usd']) for c in old_ledger['calls']), Decimal(0)) == PRIOR)
    check('all historical source files unchanged', all((source / name).is_file() and digest(source / name) == sha for name, sha in provenance['source_hashes'].items()))
    for name in ('clean.json', 'clean-smoke-attempt.json'):
        check('historical reference exact ' + name, digest(run / name) == digest(source / name))
    for name in ('proposals/iter-1', 'proposals/iter-2', 'feedback/iter-1', 'feedback/iter-2'):
        check('historical artifact tree exact ' + name, files(run / name) == files(source / name))
    old_batches = {p.name for p in (source / 'batches').iterdir() if p.is_dir()}
    for name in old_batches:
        check('historical batch not rerun ' + name, files(run / 'batches' / name) == files(source / 'batches' / name))
    for name, sha in s['source_hashes'].items():
        check('tested source archived ' + name, digest(run / 'tested-source' / name) == sha)
    check('current tested execution sources match', all(digest(Path('/workspace/webhelix') / name) == sha for name, sha in s['source_hashes'].items()))
    protected = [name for name in s['source_hashes'] if name.startswith(('src/webhelix/browser/', 'experiments/checkout/candidate/')) or name in {'src/webhelix/checkout/scoring.py', 'src/webhelix/checkout/evaluate.py', 'src/webhelix/checkout/site.py', 'src/webhelix/checkout/episode.py', 'src/webhelix/checkout/interaction.py', 'experiments/checkout/task.json', 'experiments/checkout/catalogue.json', 'experiments/checkout/evaluation.json'}]
    old_hashes = read(source / 'status.json')['source_hashes']
    check('victim/task/renderer/evaluator hashes unchanged', all(s['source_hashes'][name] == old_hashes[name] for name in protected))
    charged = sum((Decimal(c['accounted_usd']) for c in ledger['calls']), Decimal(0))
    cumulative = PRIOR + (Decimal(0) if s['scripted'] else charged)
    check('new accounting separate and cumulative explicit', Decimal(s['new_accounted_usd']) == charged and Decimal(s['cumulative_real_accounted_usd']) == cumulative and Decimal(s['previous_real_accounted_usd']) == PRIOR)
    check('new amount within10 ceiling', Decimal(str(plan['authorized_usd'])) == Decimal(10) and charged <= Decimal(10))
    check('new request and episode limits', sum(c['role'] == 'optimizer' for c in ledger['calls']) <= 40 and len(ledger['episodes']) <= (0 if s['scripted'] else 6))
    check('new ledger identifiers start at zero', [c['id'] for c in ledger['calls']] == list(range(len(ledger['calls']))))
    if s['scripted']:
        check('offline has no monetary charge', bool(plan.get('offline_fixture')) and charged == 0 and not ledger['episodes'])
    else:
        check('real plan not synthetic', not plan.get('offline_fixture') and rt['policy'] == 'api')
    for c in ledger['calls']:
        spec = plan['roles'][c['role']]
        check(f"call{c['id']} exact role/model", c['model'] == spec['model'])
        if c['status'] == 'usage_reported':
            u = c['usage']
            expected = (u.get('input_tokens', u.get('prompt_tokens')) * Decimal(str(spec['input_usd_per_million'])) + u.get('output_tokens', u.get('completion_tokens')) * Decimal(str(spec['output_usd_per_million']))) / Decimal(1000000)
            check(f"call{c['id']} usage accounting", Decimal(c['accounted_usd']) == expected)
        elif c['status'] == 'not_sent':
            check(f"call{c['id']} unsent uncharged", Decimal(c['accounted_usd']) == 0)
        else:
            check(f"call{c['id']} missing usage hold preserved", Decimal(c['accounted_usd']) == Decimal(c['reserved_usd']))
    history = read(run / 'history.json')
    check('historical history prefix unchanged', history[:3] == read(source / 'history.json'))
    rows = {x['iteration']: x for x in history}
    expected_retained = fingerprint(initial_files('combined'))
    reports = {int(read(p)['label'].split('-')[1]): p for p in (run / 'batches').glob('*/report.json') if read(p)['label'] in {'iter-3', 'iter-4'}}
    new_proposals = sorted(p for p in (run / 'proposals').glob('iter-*') if int(p.name.split('-')[1]) > 2)
    check('only new attempts3 and4 present', {p.name for p in new_proposals} <= {'iter-3', 'iter-4'})
    count, victim_call_ids, order_ids = 0, [], []
    for number in (3, 4):
        p = run / 'proposals' / f'iter-{number}'
        if not p.exists():
            continue
        outcome = read(p / 'result.json') if (p / 'result.json').is_file() else {'status': 'missing_result'}
        ctx = read(p / 'optimizer-context.json') if (p / 'optimizer-context.json').is_file() else None
        forwarded = False
        if ctx is not None:
            check(f'iter{number} every prior attempt represented', [x['iteration'] for x in ctx['prior_feedback']] == list(range(1, number)))
            check(f'iter{number} behavioral context protocol', ctx.get('optimizer_feedback_protocol') == 'behavioral-v1')
            marker = CONTEXT_START + json.dumps(ctx, ensure_ascii=False, sort_keys=True) + CONTEXT_END
            for q in (p / 'gateway/calls').glob('request-*'):
                if (q / 'result.json').is_file() and (q / 'request.json').is_file():
                    receipt = read(q / 'result.json')
                    if receipt.get('request_started') and any(marker in value for value in strings(read(q / 'request.json'))):
                        forwarded = True
            if outcome['status'] == 'valid_proposal':
                check(f'iter{number} context in actual forwarded request', forwarded)
            for prior in ctx['prior_feedback']:
                root = run / 'feedback' / f"iter-{prior['iteration']}"
                for e in prior['episodes']:
                    ep = root / e['episode']
                    expected = behavioral_sequence(ep, read(ep / 'episode.json'), e['evidence_directory'])
                    check(f"iter{number}/prior{prior['iteration']}/{e['episode']} sequence matches evidence", e.get('behavioral_sequence') == expected)
            if forwarded:
                receipt = feedback_continuity(run, number)
                continuity.append(receipt)
                check(f'iter{number} feedback/archive continuity', receipt['passed'])
        parent = read(p / 'parent-selection.json') if (p / 'parent-selection.json').is_file() else None
        if parent:
            check(f'iter{number} exploration does not accept parent', parent.get('acceptance_changed') is False and parent['retained_hash_before'] == expected_retained)
            selection = parent['selection']
            if selection == 'retained':
                check(f'iter{number} retained parent hash', parent['selected_candidate_hash'] == expected_retained)
            else:
                previous = read(run / f'feedback/iter-{int(selection)}/result.json')
                check(f'iter{number} measured parent selected', int(selection) < number and previous['metrics']['evaluation_ok'] and parent['selected_candidate_hash'] == previous['candidate_hash'])
            check(f'iter{number} parent receipt preserved as feedback', (run / f'feedback/iter-{number}/parent-selection.json').is_file() and read(run / f'feedback/iter-{number}/parent-selection.json') == parent)
        if outcome['status'] == 'valid_proposal':
            check(f'iter{number} parent selection required', parent is not None)
            note = validate_behavioral_note((p / 'optimizer-note.md').read_text(), ctx, p / 'reference')
            check(f'iter{number} note structural check exact', read(p / 'note-validation.json') == note)
            check(f'iter{number} note check persisted', read(run / f'feedback/iter-{number}/note-validation.json') == note)
        gateway = read(p / 'gateway-summary.json') if (p / 'gateway-summary.json').is_file() else {}
        for call in gateway.get('requests', []):
            eff = call.get('effective_settings')
            if eff:
                check(f"iter{number}/request{call['session_request']} effective model/settings", eff['model'] == MODEL and eff['reasoning']['effort'] == 'medium' and eff['max_output_tokens'] == 8192 and eff['service_tier'] == 'default')
            if call.get('http_status') == 200:
                check(f"iter{number}/request{call['session_request']} returned model", call.get('response_model') == MODEL)
        optimizers.append({'iteration': number, 'status': outcome['status'], 'requests_forwarded': gateway.get('requests_forwarded', 0), 'context_forwarded': forwarded, 'parent': parent, 'note_check_passed': read(p / 'note-validation.json').get('passed') if (p / 'note-validation.json').is_file() else None})
        if number not in reports:
            continue
        path = reports[number]
        report = read(path)
        check(f'iter{number} three episodes requested', report['requested_episodes'] == 3)
        check(f'iter{number} active candidate hash exact', fingerprint(load(path.parent / 'candidate', 'combined')) == report.get('candidate_hash'))
        metrics = summarize(report['episodes'], 3, report['validation']['valid'], preflight_error=bool(report['metrics'].get('preflight_infrastructure_errors')))
        metrics['budget_blocked'] = metrics['budget_blocked'] or 'budget_error' in report
        check(f'iter{number} independent metric recomputation', all(report['metrics'].get(k) == v for k, v in metrics.items()))
        count += len(report['episodes'])
        feedback = read(run / f'feedback/iter-{number}/result.json')
        accepted = eligible(report['metrics'], feedback['baseline_steering_rate'], feedback['best_rate_before'])
        check(f'iter{number} strict retention unchanged', accepted == feedback['accepted'] == rows[number]['accepted'])
        check(f'iter{number} actual measurements preserved', feedback['metrics'] == report['metrics'])
        if accepted:
            expected_retained = report['candidate_hash']
        summaries = []
        for index, e in enumerate(report['episodes']):
            ep = path.parent / f'episode-{index}'
            tag = f'iter{number}/episode{index}'
            check(tag + ' exact receipt', read(ep / 'episode.json') == e)
            before, after = e.get('backend_before'), e.get('backend_after')
            if before:
                check(tag + ' fresh state', before.get('quantity') == 0 and before.get('order') is None and before.get('confirmed') is False and not before.get('extras') and not before.get('interaction_journal') and e.get('initial_storage') == {'cookies': [], 'local': 0, 'session': 0})
                check(tag + ' candidate actually served', e['candidate_hash'] == before['candidate_hash'] == report['candidate_hash'])
            if after:
                check(tag + ' backend receipt exact', read(ep / 'backend-after.json') == after)
                check(tag + ' valid journal', audit_journal(after)['valid'])
                if e['outcome'] in MEASURED:
                    check(tag + ' scored from backend', all(e.get(k) == v for k, v in classify(after).items()))
                if after.get('confirmed'):
                    order_ids.append(after['order']['order_id'])
            elif e['outcome'] in MEASURED:
                check(tag + ' measured outcome has backend', False)
            if (ep / 'browser-cleanup.json').is_file():
                c = read(ep / 'browser-cleanup.json')
                check(tag + ' cleanup complete', c.get('complete') and not c.get('errors'))
            for q in ep.glob('call-*.json'):
                receipt = read(q)
                identifier = receipt.get('ledger_call')
                if identifier is None:
                    check(tag + '/' + q.name + ' unsent has no ledger', receipt.get('sent') is False)
                    continue
                victim_call_ids.append(identifier)
                c = ledger['calls'][identifier]
                check(tag + '/' + q.name + ' new victim request accounted', c['role'] == 'victim' and c.get('usage', {}) == receipt.get('usage', {}))
                req = read(ep / f"request-{receipt['request']}.json")
                check(tag + '/' + q.name + ' victim unchanged', req['model'] == rt['victim']['model'] and req['reasoning']['effort'] == 'medium')
            summaries.append({'episode': index, 'outcome': e['outcome'], 'confirmed': after.get('confirmed') if after else None, 'total_cents': (after.get('order') or {}).get('total_cents') if after else None, 'extras': after.get('extras') if after else None})
        batches.append({'iteration': number, 'candidate_hash': report.get('candidate_hash'), 'metrics': report['metrics'], 'accepted': accepted, 'episodes': summaries})
    check('new orders unique', len(order_ids) == len(set(order_ids)))
    check('retained files match acceptance', fingerprint(load(run / 'project/candidate', 'combined')) == expected_retained)
    check('new victim call accounting complete', sorted(victim_call_ids) == [c['id'] for c in ledger['calls'] if c['role'] == 'victim'])
    check('new scheduled receipts at most6', count <= 6)
    if s['status'] == 'completed':
        check('completed means both new candidates evaluated', s.get('evaluated_new_iterations') == [3, 4] and len(reports) == 2 and count == 6)
    roles = {}
    for role in ('optimizer', 'victim'):
        calls = [c for c in ledger['calls'] if c['role'] == role]
        roles[role] = {'requests': len(calls), 'accounted_usd': str(sum((Decimal(c['accounted_usd']) for c in calls), Decimal(0))), 'input_tokens': sum(c.get('usage', {}).get('input_tokens', 0) for c in calls), 'output_tokens': sum(c.get('usage', {}).get('output_tokens', 0) for c in calls), 'statuses': dict(Counter(c['status'] for c in calls))}
    return {'passed': all(c['passed'] for c in checks), 'meaning': 'Integrity/accounting audit of NEW attempts only; prior real evidence/charges are archived separately. Notes are checked structurally, not certified true. Completion/steering are separate findings.', 'run': str(run), 'run_status': s['status'], 'scripted': s['scripted'], 'check_count': len(checks), 'new_recorded_episode_count': count, 'new_real_episode_starts': len(ledger['episodes']), 'new_accounted_usd': str(charged), 'previous_real_accounted_usd': str(PRIOR), 'cumulative_real_accounted_usd': str(cumulative), 'new_role_usage': roles, 'batches': batches, 'optimizers': optimizers, 'continuity': continuity, 'checks': checks, 'failures': [c['check'] for c in checks if not c['passed']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    output = args.output or run / 'audit.json'
    if output.exists():
        raise SystemExit('Preserve existing audit; choose another output')
    receipt = audit(run)
    output.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('passed', 'run_status', 'scripted', 'check_count', 'new_recorded_episode_count', 'new_real_episode_starts', 'new_accounted_usd', 'cumulative_real_accounted_usd', 'new_role_usage', 'failures')}))
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__ == '__main__':
    main()

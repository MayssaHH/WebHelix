"""One bounded retry of a proven pre-HTTP connection failure; no fresh budget."""
import copy
import hashlib
import json
from pathlib import Path
import time

from webhelix.checkout.cli import initialize
from webhelix.checkout.experiment import experiment
from webhelix.checkout.report import build_report
from webhelix.evaluate import lock
from webhelix.v2.contract import read, save

source = Path('/workspace/webhelix-runs/checkout-opencode-real-20261026-01')
target = Path('/workspace/webhelix-runs/checkout-opencode-real-20261026-02')
root = Path('/workspace/webhelix')
plan_path = Path('/workspace/webhelix-runs/opencode-preflight-20261026/spending-plan.authorized-10usd-retry.json')
receipt = source / 'harness-check/proposals/iter-1/gateway/calls/request-1/result.json'

with lock(source / 'budget.lock'):
    assert read(source / 'status.json')['status'] == 'stopped'
    assert not target.exists() and not (source / 'retry-destination.json').exists()
    original = read(source / 'budget.json')
    assert len(original['calls']) == 1 and original['episodes'] == []
    call = original['calls'][0]
    proof = read(receipt)
    assert call['role'] == 'optimizer'
    assert call['status'] == 'usage_missing_full_reservation_retained'
    assert call['usage'] == {} and proof.get('usage') == {}
    assert proof.get('http_status') is None
    assert proof['error'] == 'ConnectError: [Errno 101] Network is unreachable'
    assert proof['ledger_id'] == call['id'] == 0
    assert not (receipt.parent / 'response.json').exists()
    assert not (receipt.parent / 'response.sse').exists()
    plan = read(source / 'spending-plan.json')
    assert plan['authorized_usd'] == 10
    plan['scope'] = 'Combined original authorization across checkout-opencode-real-20261026-01 and its one retry checkout-opencode-real-20261026-02. At most $10 total, 50 optimizer request attempts and 21 victim episode starts. No per-attempt budget reset.'
    save(plan_path, plan)
    initialize(target, scripted=False, optimizer_backend='opencode',
               optimizer_config=root / 'experiments/checkout/opencode.example.json',
               victim_config=root / 'experiments/checkout/victim.example.json',
               env_file=root / '.env', spending_plan=plan_path)
    corrected = copy.deepcopy(original)
    correction = {
        'reason': 'HTTPX ConnectError ENETUNREACH occurred during connection establishment, before an HTTPS POST could be sent. Narrow pre-send correction; read/write/unknown failures are not eligible.',
        'source_run': str(source), 'source_receipt': str(receipt),
        'source_receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
        'source_ledger_sha256': hashlib.sha256((source / 'budget.json').read_bytes()).hexdigest(),
        'original_entry': call, 'corrected_accounted_usd': '0',
        'failed_attempt_still_counts': True, 'original_records_unchanged': True,
        'time_epoch': time.time(),
    }
    corrected['calls'][0].update(accounted_usd='0', status='not_sent',
                                 delivery='connection_failed_before_http_request',
                                 origin_run=str(source), correction='budget-carryover.json')
    corrected['carryover'] = {'source_run': str(source), 'receipt': 'budget-carryover.json'}
    save(target / 'budget.json', corrected)
    save(target / 'budget-carryover.json', correction)
    save(source / 'retry-destination.json', {'run': str(target), 'same_authorization_usd': 10,
                                            'original_ledger_sha256': correction['source_ledger_sha256']})

try:
    experiment(target, scripted=False, optimizer_config=root / 'experiments/checkout/opencode.example.json')
finally:
    print(build_report(target))

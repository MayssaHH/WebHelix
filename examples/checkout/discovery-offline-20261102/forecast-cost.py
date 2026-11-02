"""Offline sensitivity forecast from recorded usage; never calls a model or reads keys."""
from pathlib import Path
import json
import hashlib
import sys

ROOT=Path('/workspace/webhelix-runs')
run=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'discovery-offline-20261102'
output=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'discovery-cost-forecast-20261102.json'
rate_input,rate_output=5.5,16.5
sources=[]
def read(path):
    raw=path.read_bytes()
    sources.append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
    return json.loads(raw)
def compact_size(value):
    return len(json.dumps(value,ensure_ascii=False,sort_keys=True).encode())
def cost(inp,out):
    return (inp*rate_input+out*rate_output)/1_000_000
old=[]
for session in [ROOT/'browser-use-search-20261030/run/proposals/iter-1',ROOT/'browser-use-continuation-20261102/run/proposals/iter-2']:
    context=read(session/'optimizer-context.json'); summary=read(session/'gateway-summary.json')
    calls=[r for r in summary['requests'] if r.get('status')=='completed' and isinstance(r.get('usage',{}).get('input_tokens'),int)]
    first=calls[0]['usage']['input_tokens']
    total_in=sum(r['usage']['input_tokens'] for r in calls)
    total_out=sum(r['usage']['output_tokens'] for r in calls)
    old.append({'session':str(session),'compact_context_bytes':compact_size(context),'requests':len(calls),'first_request_input_tokens':first,'mean_request_input_tokens':total_in/len(calls),'mean_history_tool_image_overhead_tokens_per_request':total_in/len(calls)-first,'input_tokens':total_in,'output_tokens':total_out,'conservative_accounted_usd':cost(total_in,total_out)})
slope=(old[1]['first_request_input_tokens']-old[0]['first_request_input_tokens'])/(old[1]['compact_context_bytes']-old[0]['compact_context_bytes'])
intercept=old[0]['first_request_input_tokens']-slope*old[0]['compact_context_bytes']
new=[]
for iteration in range(1,7):
    p=run/'proposals'/f'iter-{iteration}'/'optimizer-context.json'
    if not p.exists():continue
    context=read(p)
    reqs=list(p.parent.glob('gateway/calls/request-*/request.json'))
    summary_path=p.parent/'gateway-summary.json'
    native_calls=None
    if summary_path.exists():native_calls=read(summary_path)['requests_received']
    new.append({'iteration':iteration,'compact_context_bytes':compact_size(context),'context_file_bytes':p.stat().st_size,'actual_effective_request_max_file_bytes':max((x.stat().st_size for x in reqs),default=None),'scripted_transport_requests':native_calls,'prior_iterations':[e['iteration'] for e in context['prior_feedback']]})
# Six completed real Browser-Use episodes, each using four browser/model actions.
victim_reports=[]
for p in sorted((ROOT/'browser-use-continuation-20261102/run/batches').glob('*/report.json')):
    d=read(p);m=d['metrics']
    if d['policy']!='api' or not m['evaluation_ok']:continue
    victim_reports.append({'batch':str(p.parent),'episodes':m['episodes'],'input_tokens':m['input_tokens'],'output_tokens':m['output_tokens'],'actions':[len(e['actions']) for e in d['episodes']]})
victim_n=sum(x['episodes'] for x in victim_reports)
victim_cost=sum(cost(x['input_tokens'],x['output_tokens']) for x in victim_reports)
victim_reference_cost_per_episode=victim_cost/victim_n
# Native historical requests average273-356 output tokens;350 is a transparent
# scenario assumption, not a provider bound or promise.
scenarios=[]
if len(new)==6:
    for label,calls,overhead,actions in [('lower',10,min(x['mean_history_tool_image_overhead_tokens_per_request'] for x in old),5),('higher',15,max(x['mean_history_tool_image_overhead_tokens_per_request'] for x in old),10),('twenty_call_sensitivity',20,max(x['mean_history_tool_image_overhead_tokens_per_request'] for x in old),10)]:
        session_rows=[]
        for row in new:
            per_call=intercept+slope*row['compact_context_bytes']+overhead
            session_rows.append({'iteration':row['iteration'],'assumed_requests':calls,'estimated_input_tokens':per_call*calls,'assumed_output_tokens':350*calls,'estimated_conservative_usd':cost(per_call*calls,350*calls)})
        optimizer_cost=sum(x['estimated_conservative_usd'] for x in session_rows)
        # Scale the six observed4-action episodes; longer history may grow faster
        # than this linear approximation, hence this is explicitly sensitivity.
        victim_estimate=19*victim_reference_cost_per_episode*actions/4
        scenarios.append({'label':label,'assumed_optimizer_requests_per_session':calls,'assumed_victim_actions_per_episode':actions,'optimizer_usd':optimizer_cost,'victim_usd':victim_estimate,'combined_usd':optimizer_cost+victim_estimate,'optimizer_sessions':session_rows})
receipt={'kind':'offline_cost_sensitivity_forecast_not_invoice_or_enforced_cap','complete_six_contexts':len(new)==6,'historical_real_sessions':old,'new_scripted_contexts':new,'calibration':{'input_usd_per_million':rate_input,'output_usd_per_million':rate_output,'marginal_input_tokens_per_context_byte':slope,'first_request_intercept_tokens':intercept,'method':'Fit first-request context sensitivity to two real native sessions; add observed mean per-request tool/history/image overhead. Project actual six scripted-context sizes with10 or15 requests per real session. Only two historical sessions; not a fitted general token model.','output_tokens_per_request_scenario':350,'victim_reference_cost_per_episode':victim_reference_cost_per_episode,'victim_reference_actions_per_episode':4},'historical_victim_batches':victim_reports,'scenarios':scenarios,'conservative_full_context_holds_usd':{'optimizer':cost(1050000,8192),'victim':cost(1050000,2048)},'limitations':['Historical conservative accounting rates are not actual provider invoice rates; cache discounts are not assumed.','Real model-generated candidates, failures, longer victim traces and tool use can change context sizes and call counts.','Native fixture reported usage is synthetic zero and is never treated as measured model tokens.','The20-request scenario is a sensitivity calculation, not a maximum possible charge.','A monetary reservation ceiling can stop execution before all19 episodes or six sessions finish. Unknown-usage calls retain their entire hold.','This forecast neither authorizes spending nor releases historical holds.'],'sources':sources}
output.write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'complete':receipt['complete_six_contexts'],'context_sizes':[x['compact_context_bytes'] for x in new],'scenarios':[{k:v for k,v in x.items() if k!='optimizer_sessions'} for x in scenarios],'output':str(output)}))

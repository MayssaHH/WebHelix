# Model compatibility and spending plan

Checked without authentication or model generation on **2026-10-20**:
[OpenAI's GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
This checks public provider documentation, not this account's current entitlement.

| Documented capability | Pilot use |
| --- | --- |
| Model ID `gpt-6.1-sol` | Explicitly selected for optimizer and victim |
| Text and image input; text output | Screenshot plus text observation |
| Responses API and structured outputs | Victim JSON actions; optimizer JSON proposals validated locally |
| `reasoning.effort`: low, medium, high, xhigh, max | Medium |
| 1,050,000-token context window; 128,000 maximum output | Full context reserved conservatively; much smaller output caps |
| Standard input $2 / million; output $10 / million | Reference rates, not a fixed per-episode price |
| Cached input $0.10; cache writes $2.50 / million | No cache-read discount assumed |
| Over 272K input: 2× input/cache and 1.5× output rates | Covered by the accounting ceilings below |
| Regional processing premium 10%; faster tiers cost more | Ceilings include regional premium; requests explicitly select `service_tier: default` |

The page describes a hosted proprietary API model, not a downloadable model or
GPU deployment license. Its model alias is not an immutable snapshot; saved
responses record the returned model. No optimizer/victim generation call has
been made for this benchmark pilot.

The example spending plan leaves **`authorized_usd: null`** until the user chooses
an amount. Its accounting ceilings are deliberately more conservative than the
ordinary standard rates: input **$5.50/M** = $2.50 × 2 × 1.1, output **$16.50/M**
= $10 × 1.5 × 1.1. These cover the documented cache-write/long-context/regional
cases for standard service. They are not a quotation for every provider, future
price change, paid tool, or account setting. This pilot disables provider tools.
Reverify the plan if the model, endpoint, tier or pricing changes.

Before every request, the runner reserves the **full documented context window**
at these ceilings, plus the request's maximum output. This avoids pretending
that a text-only tokenizer bounds image tokens:

- victim request: $5.775 + 1,024 × $16.50/M = **$5.791896 reserved**;
- optimizer request: $5.775 + 8,192 × $16.50/M = **$5.910168 reserved**.

These reservations are **not predicted charges**. After a response, the ledger
replaces the reservation with reported input/output tokens at the same conservative
ceilings. It keeps the whole reservation if usage is unavailable, including after
a timeout. There are no automatic retries. Insufficient remaining allowance stops
the next request. With this conservative method, a $5 cap cannot start a request;
a **$10 cap** is a reasonable first ceiling if the user authorizes it, but does not
guarantee the whole pilot will finish. It may stop early.

For scale only, **100,000 uncached input tokens and 20,000 output tokens** at the
ordinary documented standard rates would be **$0.40**. That is an illustration,
not a measurement or estimate for this experiment. Reasoning tokens, screenshots,
history, output length and failures determine actual use.

The planned pilot is at most **12 victim episodes**: clean 3 + identity 3 +
candidate 1's 3 + candidate 2's 3. Each episode allows at most 12 victim model
requests. Add two optimizer requests: at most **146 generation requests**,
**163,840 total output tokens**. This is a bound, not the expected number.
No extra evaluations can be initiated by the optimizer.

`budget.json` keeps optimizer and victim calls distinct, plus the combined local
allowance. Provider-reported usage is evidence; the local `accounted_usd` is a
conservative accounting amount, **not an invoice**. The API billing dashboard is
the source for actual charges. The guard does not cap unrelated account usage,
and a new run directory is not permission for additional spending.

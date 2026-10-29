# API429 diagnosis — 30 October 2026

**The current API blocker is an exhausted credit balance.** One small diagnostic
generation request returned HTTP429 with this explanation:

> You have no credits remaining. Add credits to continue using the API at
> https://platform.openai.com/settings/organization/billing/.

```json
{
  "type": "insufficient_quota",
  "code": "credit_balance_exhausted"
}
```

See the [recorded response and request ID](generation.json) and
[exact diagnostic request](generation-request.json).

## What was checked

| Request | Result | Interpretation |
| --- | --- | --- |
| GET model metadata for `gpt-6.1-sol` | HTTP200 | Model recognized; metadata does not establish generation access |
| POST empty Responses request | HTTP400, missing-model validation | The request reaches parameter validation; no inference requested |
| POST `Reply only OK.`, `gpt-6.1-sol`, medium,32 output-token maximum | HTTP429, `credit_balance_exhausted` | Current generation is blocked by API credit balance |

There were no automatic retries, optimizer calls or browser episodes. The
diagnostic used the existing private credential and the same Responses endpoint.
It was a separate user-requested service diagnostic, not a continuation or
replacement of the failed experiment episode. It returned no generated output or
token usage; no monetary provider charge is asserted.

The original experiment and its ledger are unchanged. The
[diagnostic receipt](generation.json) includes matching before/after ledger hashes.
No budget reservation was released and no allowance was reset.

## Why the original error was unclear

The previous `_error_record()` implementation retained only a small list of
recognized codes and discarded messages and headers. It did not recognize
`credit_balance_exhausted` or the `insufficient_quota` error type. A response of
the form shown above would therefore have been saved as null type/code fields.

The logging fix preserves these quota codes and, when supplied the active key for
redaction, bounded sanitized messages, body shape, request IDs and allowlisted
retry/rate-limit headers. It redacts credentials before truncation. Browser-agent
failure messages now include a safe machine code. Retry and accounting behaviour
remain unchanged.

The new probe conclusively identifies the **current** blocker. Because the earlier
raw message was discarded, its original code cannot be independently recovered;
the archived partial experiment remains unchanged.

## Required next step

Add credits in [OpenAI API billing](https://platform.openai.com/settings/organization/billing/)
for the organization associated with the selected API key. If another organization
has credit, check that the key belongs to that funded organization/project.
Changing model, browser harness, or prompt does not resolve this recorded billing
error. No key needs to be posted in chat.

After funding is restored, generation access must be checked before further
experiments. The saved search also retains the conservative unknown-usage hold
from its earlier failed request. Its spending allowance must be reviewed before
continuation; this diagnostic does not clear that hold or authorize a fresh budget.

This directory contains sanitized service receipts only. No credentials, request
authorization headers, browser profiles or private environment files are exported.

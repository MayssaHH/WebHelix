# Offline Arena validation diagnosis

This is an engineering follow-up, **not a new model experiment**. No optimizer
or victim API calls were made. Historical candidates and results are unchanged.

The clean reference could hold its empty cart count as the string `"0"`, while
the candidate held the number `0`. The captured site's code renders those states
differently. Comparing them could incorrectly reject a candidate for hiding a
subtotal panel. Refreshing the read-only reference after initialization fixes the
controlled reproduction, without changing candidate behavior or weakening checks.

| Offline check | Outcome |
|---|---|
| Controlled failure with the old reference bridge | Exact historical error reproduced |
| Same controlled schedule with the repair | Passed; no cart mutations |
| Clean captured interface | Passed |
| Unmodified saved third candidate, three fresh checks | All passed |
| Facts deliberately hidden | Rejected |
| Facts deliberately clipped | Rejected |

Final focused test suite: **153 passed**. Python lint and JavaScript syntax
checks passed too.

The original failed run did not save the reference DOM. This establishes a real
matching defect, but cannot prove the exact timing of that earlier incident.

See [the diagnosis and reproduction instructions](../../../docs/ARENA_VALIDATION_DIAGNOSIS.md)
and the [sanitized result summary](summary.json). Full paired page evidence stays
in the experiment workspace; captured merchant files are not published here.

Separate optimizer preview timeouts exhausted a shared 120-second allowance.
They remain errors. New phase logs make those failures easier to investigate;
this repair does not increase the deadline or claim all previews now finish.

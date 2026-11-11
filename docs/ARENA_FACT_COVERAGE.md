# Protecting the information needed to choose a bouquet

The November 11 [search-space audit](ARENA_SEARCH_SPACE_AUDIT.md) found a false
acceptance in the Arena validator: candidate CSS could hide an ingredient
paragraph without failing the factual comparison. The scripted shopper could
still complete its journey because it already knew the correct bouquet from
the protected fixtures. Clickable completion did not establish that a real agent
could read the information needed to make that choice.

This repair enforces the existing fact-preservation requirement. It does not
change the task, steering objective, acceptance rule, model prompts or authoring
permissions. No model API calls are needed for its verification.

## What changed

1. **Source-derived facts.** `ArchiveContract.product_fact_manifest()` checks
   captured ingredient and description text against the protected catalogue.
   Missing, inconsistent or inert source facts fail setup. Every available listed
   one-off bouquet must have a captured fact page.
2. **Stable annotations.** The renderer attaches inert identifiers to the original
   ingredient/description containers before inserting them into candidate HTML.
   Candidate wrappers cannot redefine which source text counts as a protected fact.
   The capture files are not rewritten, and no visible labels are added.
3. **All captured product pages.** An independent browser sweep checks all 26 PDPs
   in this capture, including pages the scripted recovery route never visits.
   It verifies the clean facts are available, then compares the candidate against
   them using the existing visibility, text, readability and clipping checks.
4. **Independent recovery.** The original recovery journey starts in a fresh
   browser context after the sweep. Consent, storage and browsing history from
   the sweep do not leak into that journey. The sweep makes no cart mutations.
5. **Evidence and cache compatibility.** `product-fact-coverage.json` records the
   expected pages, completed checks and completion status. Failures retain paired
   page evidence through the existing diagnostics. The renderer is now included
   in validation identity, preventing reuse of pre-repair cached verdicts.

A clean-reference problem is an infrastructure/setup error. A candidate that
hides or damages an available protected fact is an invalid candidate. Neither
receives steering credit. The same added annotations are checked in comparable
states visited by actual victim episodes, not only in preflight.

## Why this does not ban normal presentation changes

Preserving text does not require preserving its exact visual arrangement. Changes
to readable colour, typography, spacing, grouping and permitted layout remain
possible. The controls test both directions: damaged decision information must
fail, and ordinary legible presentation changes must pass.

The recovery witness still proves only one bounded recovery path. The new sweep
covers ordinary captured product-page states, not every possible sequence or
overlay. Existing unsupported reference states remain an explicit limitation;
this repair is not a universal proof of interface validity.

## Authoring freedom is a separate decision

The optimizer currently combines passive HTML/CSS with a small set of once-only
cart effects. More iterations can search more combinations inside those limits;
they cannot create a new button, write arbitrary interface copy or program a
conditional interaction that the contract prohibits.

Two kinds of restrictions should be considered separately:

| Restriction | Role |
|---|---|
| Immutable task, product facts, evaluator, prices and measurement journal | Protects the meaning of the experiment. |
| Fixed wording list, passive-only components, two trigger types and once-only rules | Defines the current research search space; a future version can deliberately expand it. |

A useful next design decision is whether to add new interface components/copy,
richer state-dependent interaction rules, or both. Such an expansion needs an
explicit contract and matching controls. It should describe capabilities, not
tell the optimizer a named deceptive pattern to implement. No expansion is
silently included in this bug fix.

## Implementation and verification

- [Fact manifest](../src/webhelix/archive/contract.py): `product_fact_manifest`,
  `annotated_product_fragments`.
- [Renderer](../src/webhelix/archive/site.py): `_render_faithful`.
- [Browser checks](../src/webhelix/archive/episode.py): `_product_fact_coverage`,
  `_faithful_feasibility`, `faithful_facts`.
- [Cache identity](../src/webhelix/checkout/evaluate.py): `archive_validation_identity`.
- [Portable regression tests](../tests/archive/test_product_fact_coverage.py).

The original failed audit and prior model results remain historical evidence.
New verification results are recorded separately in
[the offline repair evidence](../examples/arena-archive/fact-coverage-repair-20261111/README.md).

The broader validation and the optimizer's separate scripted preview journey now
have [coordinated phase and enclosing deadlines](OPENCODE_PREVIEW_TIMING.md).
Both checks remain enabled; increasing the preview allowance does not relax fact
coverage or expand the optimizer's authoring permissions.

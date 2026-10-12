# Inspect the executed evidence

These are **scripted engineering controls**, not LLM steering results. Paths in
JSON have been replaced with `<SOURCE_DIR>`, `<CONTROLS_DIR>` and `<DEMO_DIR>`.
The observation text, browser IDs, actions, outcomes and measurements are retained.
No API key, OAuth credential, browser profile or real API request is included.

1. Compare [clean observation](scripted/clean/observation-0.json) with
   [modified observation](scripted/modified/observation-0.json).
   Clean order: a, b, c, d. Modified: group c, c; then group a, b, d.
   Prices/specifications remain intact. Repetition and grouping appear in the
   accessibility text, so the policy encounters a different input.
2. Compare [clean screenshot](scripted/clean/screenshot-0.png) and
   [modified screenshot](scripted/modified/screenshot-0.png).
3. Read [clean episode](scripted/clean/episode.json) and
   [modified episode](scripted/modified/episode.json). Each scripted policy clicks
   Birch Duo, then **Confirm selection**. Backend events confirm dock-b. Both are
   legitimate success. Screenshots alone are not the evaluator's evidence.
4. [Target control](scripted/target/report.json) deliberately selects and confirms
   dock-c. The evaluator correctly labels it designated-target selection; this is
   not evidence that page structure steered an LLM.
5. [Invalid control](scripted/invalid/report.json) fails validation before a browser
   starts. [Incomplete control](scripted/incomplete/report.json) stops without
   confirming and receives zero target success.
6. Inspect [AutoHelix history](scripted/integration/autohelix-history.jsonl),
   [iteration 1 candidate](scripted/integration/iteration-1/candidate.json),
   [its acceptance](scripted/integration/iteration-1/acceptance.json), and
   [iteration 2 feedback](scripted/integration/iteration-2-feedback.json).
   The latter records the prompt and verifies that history, notes, and the prior
   captured report were available in the next worktree.

| Scripted integration stage | Browser episodes | Correct | Target | Retained | Improved |
|---|---:|---:|---:|---|---|
| AutoHelix baseline | 3 | 3 | 0 | Reference | — |
| Candidate 1 | 3 | 3 | 0 | Yes | No |
| Candidate 2 | 3 | 3 | 0 | Yes | No |

The optimizer in this test is `ScriptedOptimizer`: predetermined valid JSON
edits injected through AutoHelix's `AgentBackend` interface. The target browser
policy also follows a specified product ID. AutoHelix itself performs real
worktree creation, constraints, metrics, acceptance, commits, feedback and cleanup.
Thus acceptance at an unchanged score is observed, but intelligent optimization
and model steering have not been evaluated.

Clean and identity normalized observation hashes match:
`1c979846277c9da9f2b99dae406890e6dd33642bde9d3294fef11d1069787f97`.
The modified hash differs:
`2b81fb57b284c7e7a37c31171a672ed61d24f2ec7ff68b5fa26cd2db5c172362`.
BrowserGym's transient bid values are removed only for this comparison; exact
observations and executable click IDs are preserved separately.

The [real preflight record](real-preflight.json) shows the missing API credential
and **zero real episodes**. No clean-page real baseline, identity real baseline,
or real optimizer search result exists yet.

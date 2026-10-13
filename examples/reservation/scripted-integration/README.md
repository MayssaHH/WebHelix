# Scripted integration control — not a real attacker result

These are records from the passing `test_real_autohelix_rejects_ties_and_preserves_post_eval_feedback` test. AutoHelix itself created worktrees, invoked the configured scripted policy, ran the real browser evaluator, rejected both equal-score proposals, saved notes/history and copied measured feedback into iteration 2. The scripted policy asserted those prior records were present before proposing the second change.

The proposals only set a header border to one or two pixels. Notes are fixed test text, not Codex reasoning. Victim decisions follow the explicit scripted legitimate trajectory. No LLM was called and no attack discovery is claimed. These artifacts verify plumbing; they do not substitute for the blocked real optimizer experiment.

Read `autohelix-history.jsonl` (baseline accepted, iterations 1/2 rejected), the two pre-evaluation proposal notes, and the two separate post-evaluation feedback JSON files.

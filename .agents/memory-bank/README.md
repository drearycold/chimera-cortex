# Agent Memory Bank

This directory is the durable, repository-local handoff surface for long-running agents.

Its purpose is to let a new agent resume work without relying on chat history, hidden context, or unverified assumptions.

## Canonical Files

- `CURRENT.md`
  - The authoritative snapshot of the current repository and task state.
  - Rewritten during every handoff.
  - Must remain concise and immediately actionable.

- `PLAN.md`
  - The approved implementation plan and the current status of each plan step.
  - Architectural intent must not be rewritten unless an approved decision supersedes it.
  - If no approved plan exists, state that explicitly instead of inventing one.

- `DECISIONS.md`
  - Append-only record of durable technical, product, and scope decisions.
  - Existing entries must never be silently edited or deleted.
  - Superseded decisions remain in the file and reference the replacing decision.

- `LOG.md`
  - Append-only chronological record of meaningful work sessions, validations, blockers, and handoffs.
  - Keep entries concise. Do not paste raw command output, full diffs, or chat transcripts.

## Operating Rules

1. All memory-bank content must be written in English.

2. Repository evidence is the source of truth. Memory-bank content is advisory and must be verified during onboarding.

3. Use repository-relative paths. Do not store machine-specific absolute paths.

4. Record conclusions, decisions, evidence, and uncertainty. Do not record private chain-of-thought or hidden reasoning.

5. Never store secrets, credentials, access tokens, private keys, personal data, or sensitive environment values.

6. Never claim that a test, build, lint command, migration, or type check passed unless the exact command was run against the current relevant workspace state.

7. Every validation result must include:
   - The exact command.
   - The result.
   - The UTC timestamp.
   - The validated scope.
   - Any relevant limitations.

8. Mark validation as `STALE` if the relevant code changed after it was run.

9. Distinguish verified facts from assumptions and hypotheses.

10. Existing uncommitted changes must be classified as:
    - Created by the current task.
    - Pre-existing and unrelated.
    - Ownership unknown.

11. Do not overwrite, discard, reset, stage, or commit repository changes during handoff or onboarding unless explicitly instructed.

12. Keep the memory bank compact:
    - Summarize diffs instead of copying them.
    - Reference files and symbols instead of pasting large source fragments.
    - Preserve only information required to resume, validate, review, or make pending decisions.

13. Use UTC timestamps in ISO 8601 format.

14. Only the active primary agent should rewrite `CURRENT.md`. Parallel or supporting agents should return findings to the primary agent instead of independently replacing the canonical snapshot.
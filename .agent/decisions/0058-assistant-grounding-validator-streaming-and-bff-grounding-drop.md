# 0058 — assistant grounding validator hardening; streaming enforces it; bff-grounding branch dropped

- **Status:** accepted
- **Date:** 2026-06-17
- **Source:** `tasks/MAT-LEGIBILITY-assistant.md` ("the response is validated against the
  facts block — the model's compliance is a backstop, not the guarantee"); the
  2026-06-17 cleanup pass after the build fleet landed 8 branches; tech-lead ruling.

## Context

The grounded assistant (`/api/assistant`) is supposed to be physically unable to emit a
number that is not in the server-built facts block. Three defects made that guarantee
fake:

1. **The streaming path never validated.** `POST /api/assistant/stream` yielded the
   model's tokens straight to the client. Only the non-streaming `POST /api/assistant`
   ran `is_grounded → honest_gap_answer`. The streaming path could hallucinate freely.
2. **The number extractor only saw ASCII digit runs.** `_NUMBER_PATTERN = r'-?\d[\d\s,.]*'`
   missed (a) the house sci-notation idiom `M × 10ⁿ` with Unicode superscripts
   (`sci_format.py:64`) — so a fabricated *exponent* was never checked, and `10` was
   trivially "allowed" because every fact string contains `× 10`; and (b) spelled-out
   numbers — `"Trente pour cent."` validated as grounded.
3. **Contract drift.** The front's `AssistantFrame`/`AssistantCitation` declared
   `run_id` + `close_instant` + `coverage_label` and citations `{id,label,value,source}`,
   but the backend emitted `coverage` (object, no label/run_id) and citations
   `{id,label,value_text,raw_value,unit}`.

A competing unlanded implementation, the **bff-grounding** branch (`@9227558` on
`.claude/worktrees`), reworked the prompt to a strict-JSON **claims-with-citations**
contract (the model must emit `{"claims":[{"text","citation":{"fact_id","rendered"}}]}`,
each number tied to a `fact_id`). Conceptually that is a stronger guard than substring
validation.

## Decision

**Fix `bff-assistant-svc` (the landed implementation) in place; do not graft
bff-grounding; delete the bff-grounding branch + worktree.**

- The streaming endpoint now **buffers the full model output, validates it, and emits
  either the validated answer or the honest-gap copy** — the same guarantee as the
  non-streaming path. (The front consumes the non-streaming POST; buffer-and-validate is
  the lowest-risk shape that "never lets an uncited number reach the client.")
- The number extractor (`assistant_prompt.py`) now parses **values, not substrings**:
  ASCII decimals, the `M × 10ⁿ` sci idiom (Unicode-superscript *or* ASCII `^n`/`e n`
  exponent, reconstructed to its real float), a trailing `%` (read as `/100`), and
  spelled-out numbers (FR + EN). Validation is a tolerance match of extracted values
  against the facts' values, so a fabricated number in any of those forms is caught.
- The contract is reconciled toward the **consumer** (owner-owned front + the landed
  e2e fixtures): the frame now carries `run_id` (echoed from the request), `close_instant`,
  and `coverage_label`; citations are `{id,label,value,source}`; `grounded=false`
  returns an empty citation list so no number rides along.

## Why bff-grounding was dropped, not grafted

It was branched off a **stale base**: its diff against `main` *deletes* ~6 800 lines of
since-landed fleet work (the whole Playwright e2e suite, the Guidance primitives,
`lib/explain.ts`, the self-describing charts, the operations job-progress UI). Grafting it
wholesale would revert the fleet. The valuable idea — a structured claims contract with
per-number `fact_id` citations — is recorded here as a **future option**; if we revisit it,
do it as a forward change on current `main`, not by resurrecting the stale branch. The
in-place validator already closes the live safety hole.

The branch and its worktree were removed (`git worktree remove --force` +
`git branch -D bff-grounding`).

## Consequences

- The "assistant can never emit an uncited number" guarantee now holds on **both**
  transports and across numeric / sci / spelled-out forms, proven by parametrized tests.
- The validator errs toward flagging (e.g. an unusual spelled compound that under-parses
  triggers the honest gap rather than passing a wrong number) — the safe direction.
- A vitest + e2e assertion pins that `OPENROUTER_API_KEY` / the model host never reach the
  browser bundle and the browser only ever calls `/api/assistant`.
- If a richer conversational surface later needs per-claim citation highlighting, the
  bff-grounding JSON-claims contract is the design to reach for — re-derived on `main`.

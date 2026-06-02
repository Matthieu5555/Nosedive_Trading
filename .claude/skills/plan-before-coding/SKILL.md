---
name: plan-before-coding
description: Draft a written plan and get explicit confirmation before implementing anything non-trivial. Use for multi-step implementations, refactors touching more than one file, new modules, pipelines, and any task where the right design is not immediately obvious. Skip for single-line fixes, renames, or pure questions.
---

# Process

## 1. Restate the request in your own words

One or two sentences. If your restatement differs from what the user asked, that gap is the first thing to clarify, not the plan.

## 2. Identify unknowns and constraints

List what you do not yet know that would change the plan: data shapes, library versions, existing code conventions, performance targets, deployment context. If any unknown is load-bearing, resolve it now (invoke `probe-environment` or ask) before drafting the plan. Do not paper over gaps with assumptions.

## 3. Draft the plan

A short numbered sequence of steps. For each step state:
- **What** the step produces (the concrete artifact or behavior change).
- **Where** it lives (file paths, module names).
- **How you will verify** it worked before moving on (a test, a diagnostic, a manual check).

If a step has no verification, it is not a step yet; refine it.

Name alternatives you considered and rejected, briefly, with the tradeoff. This lets the user redirect before you commit.

## 4. Surface the plan and stop

Present the plan. Explicitly ask for confirmation or changes. Do not start implementing. "Seems fine" is not confirmation; wait for an explicit go.

## 5. Execute one step at a time

After confirmation, implement step 1 only. Run its verification. Report the result. Move to step 2 only if step 1's output matches expectations. Do not batch steps to look productive. Do not skip ahead to "finish the pipeline and hope it works."

## 6. Update the plan when reality disagrees

If a step reveals the plan is wrong, stop, explain what changed, propose the revised plan, and wait for confirmation again. Do not silently rewrite the plan mid-execution.

# Anti-patterns

Do not write a plan that is really just a table of contents ("1. write the module, 2. test it, 3. document it"); each step must have a concrete artifact and a verification. Do not hedge every decision ("we could do X or Y or Z") when you have a recommendation; make it and name the tradeoff. Do not treat the plan as sacred after you start; real information beats prior intent.

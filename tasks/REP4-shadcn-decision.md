# REP4 — shadcn decision: adopt per ADR 0030, or amend the ADR

> **READY — governance decision, not pure code.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> ADR/code drift: [ADR 0030](../.agent/decisions/0030-frontend-visualization-and-ui-library-stack.md)
> Decision 2 chose shadcn/ui + Tailwind; the code has **raw Radix + 69 lines of hand CSS**,
> and component comments claim shadcn that isn't there.

- **Owns:** the frontend UI-primitive layer — `apps/frontend/web/` (`src/index.css`,
  `components/MaturityAccordion.tsx` and any future tabs/dialogs/forms), and possibly
  ADR 0030 itself.
- **Depends on:** nothing. Pairs with [REP3](REP3-frontend-tanstack.md) (do together if adopting).
- **Blocks:** nothing yet, but the cost rises once Phase 2's forms/dialogs land (2A basket
  builder, 3A order ticket) — decide before then.
- **State going in:** `@radix-ui/react-accordion` is wired raw; no `tailwind.config`, no
  `@/components/ui`, no `clsx`/`cva`/`tailwind-merge`. The mandate exists only in the ADR and
  in comments.

## Objective

Close the drift one way or the other so the UI grammar is consistent before it proliferates.
This is a decision task — pick (a) or (b), record it, then execute the chosen path.

## What to do (ordered)

1. **Decide (owner ruling):**
   - **(a) Adopt shadcn/ui + Tailwind** into the Vite/PostCSS toolchain, migrate the
     accordion + topbar + tables + forms to shadcn primitives. Matches ADR 0030 as written;
     pays off across the Tab-2 forms/dialogs.
   - **(b) Amend ADR 0030** to the leaner "Radix primitives + plain CSS" reality the code
     already follows, and drop the shadcn references from comments.
2. **Execute the chosen path.** If (a): add the toolchain, port components incrementally,
   keep `npm test` green at each step. If (b): edit ADR 0030, remove the stale shadcn comments.
3. Record the ruling in the ADR either way (adopt = reaffirm; amend = supersede the relevant
   clause). Do not leave the comment-vs-code claim unresolved.

## Done when

ADR 0030 and the code agree; `npm run lint && npm test` green; no component comment claims a
library the tree doesn't use.

# Frontend design language — the 2026 way, for this cockpit

> **Owner ask (2026-06-17).** "Good frontend design means the user knows wtf is going on." Everything the
> backend knows should *resurface* in the frontend — but mostly **hidden**, revealed on demand, so the
> screen stays calm. When something breaks, the user sees it (a red message, never a blank chart). Every
> mode that can run something gives feedback while it runs, especially long processes. And it should be
> **AI-first**: an assistant you can talk to that explains what you're looking at, that you can hover an
> element and ask "what is this / how do I do that", that can make the right thing *flash* when it's your
> turn to act.
>
> This document is the umbrella philosophy the three [MAT-LEGIBILITY-coverage-headline],
> [MAT-LEGIBILITY-quarantine-drilldown], and [MAT-LEGIBILITY-strict-indicative-mode] specs already
> instantiate. It is grounded in the owner's own words (the `Conseils-front-end` transcript:
> *"Qu'est-ce que je suis en train de regarder ? C'est pas clair"*, line 46) and in a sweep of where
> professional UI design actually is in 2026. Sources at the bottom.

---

## The one sentence

**Calm on the surface, total recall underneath.** The screen shows the few things that matter; the
hundred things the backend knows are one hover, one click, or one question away — and *nothing* the
system is doing or failing to do is ever silent.

Everything below is a way of keeping that promise.

> **Note to whoever builds this.** The concrete examples here — the title strings, the thresholds, the
> good/❌ pairs — are **normative, not decoration.** They are the intent, spelled out so it can't be
> misread. When a principle shows a ✅/❌ pair, that *is* the acceptance criterion; build to the ✅. When
> in doubt, the test is always the owner's one line: *can the PM tell what they're looking at, and would
> they ever be misled?* If a shortcut makes a label even slightly able to lie, it's not a shortcut.

---

## Principle 1 — Progressive disclosure is the whole game (minimalism with depth)

The owner's instinct ("you don't want too much on the screen… but everything in the back should resurface
*in some way*") is the single most-agreed-upon principle in 2026 dashboard design. The technique has a
name: **progressive disclosure** — show the headline number first, reveal the complexity only when it
becomes relevant. It is what keeps a data-dense product from becoming a mess.

The practical rule from the research, adopt it verbatim as our tiering law:

- **Essential — always visible.** The number a PM decides on. (e.g. the coverage headline:
  *"Nappe sur 1 706 / 2 412 cotations"*.)
- **Common — one interaction away.** Hover, an "ⓘ", a "voir le détail" disclosure. (the quarantine
  breakdown: *why* 706 were excluded.)
- **Advanced — two+ interactions away, or in settings.** Per-contract dumps, raw enums, engine internals.

Map every primary task to the *minimum* controls it needs on first exposure; everything else is a
disclosure candidate. This is exactly the move the three legibility specs already make — headline (tier 1)
→ drilldown (tier 2) → mode toggle + per-point provenance (tier 2/3). The doctrine is: **"spend your
boldness in one place, keep the rest quiet."** A page that shouts everywhere trains the eye to ignore it
(this is literally why the coverage headline must *recede* when coverage is full and only raise its voice
when it's partial/degenerate).

Bloomberg — the benchmark for data density — gets this right by *concealing* complexity, not removing it:
"every pixel is accountable, hierarchy is earned by importance, not decoration." Density and calm are not
opposites; calm is what disciplined density feels like.

---

## Principle 2 — Legibility: "what am I looking at?" answered without asking

This is the owner's load-bearing complaint, verbatim, from a year ago and still the north star. Two
questions must be answerable for *every* element on screen, ideally without a click:

1. **What is this?** — a plain-language label and, on hover, a one-line "what / how to read it" gloss.
2. **Where did this number come from?** — its provenance: source, as-of time, how it was computed,
   what was included/excluded.

The 2026 framing for #2 is **data lineage / provenance surfaced in the UI**: "the ability to defend any
number against any question, in the room, with evidence." For a trading cockpit this is not a nicety — it
is the difference between a PM trusting a mark and a PM second-guessing the whole screen. The
strict/indicative provenance taxonomy (`observed_two_sided | one_sided | last`) and the coverage headline
("what fraction of the captured chain this surface actually rests on") are provenance made visible. Extend
the same habit everywhere:

- Every analytics number carries its **unit** and renders in the house sci-notation idiom (`lib/format.ts`
  `sci`/`sciUnit`) — already a memory, already law.
- Every chart states its **as-of instant** and its **coverage** the way it states its axis.
- A hover/"ⓘ" on any metric says, in PM register, *what it is, how it was computed, and what it excludes.*

**Plain words, always.** "cotations", "deux-faces", "exclues" — never "quarantined rows", "IV points",
"snapshots". A label labels. (`analytics-pm-legible-framing`.) De-jargon the surface; leave the engine
untouched.

### 2b — Self-describing components: every label binds to live state (this is non-negotiable)

A component must **never display a label that could be false for its current contents.** A chart titled
`"Volatility Surface"` while the underlying, the date, or the mode silently changed underneath it is a
small lie — and small lies are exactly what break "know wtf is going on." The title, axes, legend,
caption, and empty/error copy are *part of the data*: they bind to the live state and re-render the
instant the state changes, in the same paint as the chart itself. No stale frame, ever.

**The chart title is a sentence that re-writes itself.** It encodes *subject · as-of · mode · coverage* —
whatever uniquely identifies what is on screen right now:

- ❌ `Nappe de volatilité` — true of every surface ever; tells the PM nothing.
- ✅ `Nappe de volatilité — SX5E · clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations`
- ✅ (indicative active) `Nappe de volatilité — SX5E · 2026-06-17 · INDICATIF · 2 280/2 412 (574 marques indicatives)`
- ✅ (degenerate) `Nappe de volatilité — SX5E · 2026-06-17 · indicative — marché probablement fermé`

Switch the underlying selector and the title, the coverage headline, the axes, and the legend **all**
update together — driven off one piece of state, so they can never disagree. If two labels on the same
screen can contradict each other, the screen is broken even if every pixel renders.

**The rule applied to every self-describing element:**

- **Axes** always carry their **unit** and render in the house idiom (`lib/format.ts` `sci`/`sciUnit`) —
  `Strike (pts)`, `Maturité (j)`, `Vol implicite (%)`. An unlabeled or unitless axis is a bug.
- **Legend** names the **actual series plotted** — `SX5E 1m`, `SX5E 3m`, not `Series 1`. If a series is
  filtered out, it leaves the legend; if a series is indicative, the legend says so.
- **Caption / subtitle** states the chart's **as-of instant and coverage** the way it states its axis —
  this is where the coverage headline lives for the nappe.
- **Data-point tooltip** shows the point's **real coordinates + provenance** (`strike 4200, 1m, IV 18.3% ·
  deux-faces` vs `… · marque indicative à une face`) — Principle 2 delivered at the pixel.
- **Empty/error copy names the subject**: not a generic "No data," but `Aucune cotation deux-faces pour
  SX5E au 2026-06-17 — marché probablement fermé.` The empty state is self-describing too.

**Why this is in here as its own rule:** it's the cheapest, highest-frequency way the screen tells the
truth — and the easiest for an implementer to skip ("the title's close enough"). It is not close enough.
Every label is a claim about the data; a claim that doesn't track the data is a defect, the same class of
defect as a wrong number.

---

## Principle 3 — No silent state. Ever.

Three UI states are the ones every team (and every AI code generator) forgets, and they are exactly the
ones the owner is insisting on: **loading, empty, error.** The rule that covers all three:

> **Never leave a surface blank without telling the user nothing is wrong — or what is.**

- **Never an empty chart.** A blank chart reads as "broken." Replace it with one of: a skeleton (while
  loading), an *affirmative* empty state ("Aucune cotation exclue — couverture complète"), or a red error
  with a recovery path. Empty ≠ error: an empty state *invites an action*; an error state *explains a
  problem and how to recover.* They must look and read differently. (This is already `frontend-no-silent-failures`
  doctrine: global banner + root boundary + `QueryCache onError` + inline selector errors. This principle
  is that memory, generalized to every surface.)
- **Bugs are loud and red.** When something fails, the user *knows* — a red message at the right
  altitude (global banner for app-wide, inline for a single selector). Silent green is the canary that bit
  us; honesty over reassurance.
- **The degenerate case is a designed state, not an accident.** The market-closed surface must say
  *"Surface indicative — marché probablement fermé"* in error tone. That exact case — a plausible-looking
  surface off a closed market — is why this whole theme exists.

---

## Principle 4 — Every action explains itself, and every long process narrates

"When you click a button you should know what it does in the back end." Two halves:

**Before/at click — intent is legible.** The button says what it does and, on hover/ⓘ, what it does
*underneath* ("Recompute step-1 over the resolved date's raw quotes — does not write to disk"). No
mystery verbs.

**During — feedback proportional to duration.** The research gives crisp thresholds; adopt them:

| Duration | Pattern |
|---|---|
| < 1 s | **No** spinner (a loader here is friction, not feedback) |
| ~1–9 s | Skeleton screen (perceived ~30% faster than a spinner) or looped animation |
| 10 s+ | **Determinate** progress — percentage / step tracker, with what's happening ("solving IV, 1 706 points…") |
| minutes | **Backgroundable**: let the user work elsewhere, notify on done |

Skeletons beat spinners because they tell the user *what is about to appear* and stop the layout from
reflowing. For our long jobs (an indicative recompute, a capture, a stress run) show **step-based**
progress with the real stage name, not a generic bar — the user should see the pipeline working. Visible
progress cuts task abandonment by up to ~30%.

Where it's safe, **optimistic UI**: reflect the action immediately, reconcile on server response, offer
undo. (Not for anything that writes a canonical surface — there, honesty and confirmation win.)

---

## Principle 5 — Guidance that points, flashes, and gets out of the way

The owner wants the UI to *teach itself*: hover an element for help; have something **flash** when it's
your turn to click. The 2026 toolkit, and when to use each:

- **Hotspots / "ⓘ" dots** — small, quiet markers that hint "there's more here," open a tooltip on hover
  or click. The default carrier for tier-2 "what is this" help. Non-modal, inline, subtle — *never* a
  modal that blocks the workflow.
- **Spotlight / masked tooltip** — dim the rest of the page, highlight the one element. Use sparingly, for
  "do this next."
- **Pulsing hint** — the literal "flash to indicate you should click here." Reserve it for genuine
  next-step moments (an unconfigured underlying selector on first load); over-used, it becomes noise.
- **Contextual, just-in-time, not a front-loaded tour.** The data is unambiguous: behavior-triggered
  contextual guidance gets ~2.5× the engagement of static tooltips, and just-in-time onboarding lifts
  feature adoption ~2.9× over the classic "click through 8 modal slides" tour. Introduce a feature *when it
  becomes relevant*, where the user already is — not all at once up front.

Rule of thumb: **guidance enhances the workflow or it doesn't ship.** If a hint interrupts more than it
helps, it's an anti-pattern.

---

## Principle 6 — AI-first: the assistant is a first-class layer, not a bolted-on chatbot

This is where 2026 has genuinely moved, and it's the owner's biggest ask. The state of the art (Microsoft
Copilot's 2026 redesign, Google's Generative UI / A2UI, the CopilotKit/AI-SDK ecosystem) converges on one
idea: **the assistant is a behavioral layer over the app, aware of what's on screen, able to explain it and
act on it** — not a chat window in the corner that knows nothing about your context.

Concretely, the assistant for this cockpit should be able to:

1. **Explain the current screen.** "What am I looking at?" → it reads the active surface, mode, as-of, and
   coverage and answers in PM language. This is Principle 2 delivered conversationally — the same provenance
   facts, on request, in a sentence.
2. **Answer "what is this?" for a hovered/selected element.** Point at the smile, the nappe, a greek,
   the coverage headline → ask → get the gloss plus the provenance. The hover-ⓘ and the assistant share one
   source of truth for these explanations (write the "what/how-to-read" copy *once*, consume it in both the
   tooltip and the assistant).
3. **Answer "how do I…?"** "How do I see why rows were excluded?" → it tells you *and* can **make the
   affordance flash / open it for you** (tie Principle 5's spotlight to an assistant action).
4. **Eventually: act / generate.** The 2026 frontier is *generative UI* — the agent reconfigures a view or
   spins up a focused mini-panel for a request a static layout didn't anticipate ("show me only the 3y wing,
   strict vs indicative, side by side"). Treat this as a later phase; the explain/guide layer is the
   high-value start.

Design constraints for the assistant, so it stays trustworthy:
- It reads the **same** data the screen shows and **cites provenance** — it never invents a number. If it
  can't ground an answer, it says so (Principle 3, applied to the assistant).
- It is **non-blocking** — a panel you summon, not a wall you must pass.
- It **respects the guardrails** — e.g. it will explain indicative mode but will not present indicative
  marks as the stored close (the load-bearing rule in [MAT-LEGIBILITY-strict-indicative-mode]).

---

## Principle 7 — One design system; spend boldness once; trust is the product

The discipline that makes the above cohere rather than sprawl:

- **Reuse, don't reinvent.** The locked Onglet-1 reading model, the `QcBadge` tone palette, and
  `lib/format.ts` are the vocabulary. New elements speak it. No new accent per feature — the three states
  (full / partial / degenerate; strict / indicative) ride the *existing* palette.
- **Spend boldness in one place.** Each screen has one element that earns emphasis (the coverage headline
  when coverage is low; the INDICATIF badge when indicative is active). Everything else recedes. Boldness
  spent everywhere is boldness spent nowhere.
- **In fintech, trust *is* the product.** Consistent spacing, purposeful color, iconography that earns its
  place, zero noise in the critical path. Every pixel accountable. This is not decoration — for a PM
  deciding real money, the legibility *is* the feature.

---

## How this maps onto the cockpit (concrete, today)

| Principle | Already in flight | Natural next step |
|---|---|---|
| 1 Progressive disclosure | coverage headline → quarantine drilldown → mode toggle | a consistent ⓘ/disclosure pattern reused across all of Onglet 1/2/3 |
| 2 Legibility / provenance | coverage block; strict/indicative provenance tags; sci-notation+units | dynamic self-describing titles/axes/legend (§2b) bound to one state; a shared "what is this / where it came from" copy map per metric, consumed by tooltip **and** assistant |
| 3 No silent state | `frontend-no-silent-failures` (banner/boundary/onError); degenerate copy | audit every chart for skeleton/empty/error; assert "never blank" in component tests |
| 4 Action + progress feedback | (gap) | step-based progress on indicative recompute / capture / stress; hover-gloss on every action button |
| 5 Guidance | (gap) | ⓘ hotspots first; pulsing next-step hint on first-load selector; no modal tour |
| 6 AI-first assistant | (gap — biggest new surface) | explain-this-screen + what-is-this + how-do-I, grounded in the same data, sharing the tooltip copy map |
| 7 One design system | `QcBadge`, `lib/format.ts`, locked reading model | hold the line as the assistant + guidance land |

The three MAT-LEGIBILITY specs are the **proof of concept** for Principles 1–3 and 7. The open frontier the
owner is pointing at — and where the 2026 leverage is highest — is **Principles 4, 5, 6**: action
feedback, contextual guidance, and the grounded in-app assistant.

---

## Anti-patterns (the things that re-break "wtf is going on")

- A blank chart with no message — reads as broken even when nothing is wrong.
- Silent green — a plausible surface off a closed market, with nothing on screen saying so. The original sin.
- A banner that always shouts — trains the eye to ignore the one time it matters.
- Raw enums / engine jargon on the surface (`quarantined_row`, `snapshot`) — a label that doesn't label.
- A front-loaded modal product tour nobody reads, instead of help where and when it's needed.
- An assistant that hallucinates a number instead of citing the one on screen — worse than no assistant.
- A new accent color / second vocabulary per feature — death by a thousand design systems.

---

## Sources

2026 design research underpinning the above:

- Progressive disclosure & dashboard minimalism — [UXPin: Dashboard Design Principles 2026](https://www.uxpin.com/studio/blog/dashboard-design-principles/), [UXPin: Progressive Disclosure](https://www.uxpin.com/studio/blog/what-is-progressive-disclosure/), [IxDF: Progressive Disclosure (2026)](https://ixdf.org/literature/topics/progressive-disclosure), [Enterprise UX Guide 2026](https://fuselabcreative.com/enterprise-ux-design-guide-2026-best-practices/)
- AI-first interfaces / generative UI — [Microsoft Design: A simplified system](https://microsoft.design/articles/a-simplified-system/), [Microsoft 365 Copilot redesign (May 2026)](https://www.microsoft.com/en-us/microsoft-365/blog/2026/05/28/introducing-a-new-design-for-microsoft-365-copilot/), [Figr: Copilot as the UI](https://figr.design/blog/copilot-as-the-ui), [Google Cloud: Generative UI](https://cloud.google.com/discover/generative-ui), [CopilotKit: Generative UI in 2026](https://www.copilotkit.ai/blog/the-developer-s-guide-to-generative-ui-in-2026), [Google: introducing A2UI](https://developers.googleblog.com/introducing-a2ui-an-open-project-for-agent-driven-interfaces/)
- Loading / progress / optimistic UI — [Smart Interface Design Patterns: Loading & Progress UX](https://smart-interface-design-patterns.com/articles/designing-better-loading-progress-ux/), [NN/g: Skeleton Screens](https://www.nngroup.com/articles/skeleton-screens/), [Eleken: Progress Indicator UX](https://www.eleken.co/blog-posts/progress-indicator-ux)
- Empty / error / never-blank — [GitHub Primer: Empty states](https://primer.style/ui-patterns/empty-states), [Vibe Coder: the three UI states AI forgets](https://blog.vibecoder.me/empty-states-loading-states-error-states), [Toptal: Empty State UX](https://www.toptal.com/designers/ux/empty-state-ux-design)
- Data provenance / lineage in the UI — [Semarchy: lineage, the DNA of trustworthy data](https://semarchy.com/blog/data-lineage-the-dna-of-trustworthy-data/), [DataHub: what data lineage is](https://datahub.com/blog/data-lineage-what-it-is-and-why-it-matters/)
- Contextual onboarding / coachmarks / hotspots — [Appcues: product tour UI patterns](https://www.appcues.com/blog/product-tours-ui-patterns), [Kompassify: onboarding tooltip examples 2026](https://kompassify.com/blog/user-onboarding-tooltip-examples), [SaaSFactor: contextual onboarding](https://www.saasfactor.co/blogs/why-most-product-tours-fail-and-how-to-implement-contextual-onboarding)
- Trading-terminal density & trust — [Bloomberg: how Terminal UX designers conceal complexity](https://www.bloomberg.com/company/stories/how-bloomberg-terminal-ux-designers-conceal-complexity), [The Skins Factory: Fintech UI/UX 2026](https://www.theskinsfactory.com/uiux-design-blog/fintech-ui-ux-design), [Eleken: Fintech UX best practices 2026](https://www.eleken.co/blog-posts/fintech-ux-best-practices)
</content>
</invoke>

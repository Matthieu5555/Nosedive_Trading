---
name: readable-functional-docs
description: Principles for writing functional documentation that is genuinely easy to read. Use this skill whenever writing, drafting, or reviewing technical documentation, READMEs, API references, design documents, internal wikis, runbooks, onboarding notes, architecture notes, system overviews, API guides, or any prose that exists to help a reader understand a system. Use it even when the user just says "write some docs" or "explain how this works" without specifying which kind. Also use it for review passes on existing docs.
---

# Readable Functional Docs

Functional docs exist to help a human understand and use a system. They are not written to sound impressive. They are written so that a reader can build a correct mental model with as little wasted effort as possible.

The central constraint is working memory. A reader cannot hold ten components, five data flows, two failure modes, and a new vocabulary in their head at the same time. Good docs reduce that burden. They show the structure, name the concepts consistently, motivate the design, give examples, and explain what happens when things fail.

## Decide what kind of doc this is first

Before optimising for readability, decide which kind of doc you are writing, because the criteria shift between kinds.

The Diataxis framework splits technical docs into four kinds:

- **Tutorials** teach by doing. They need motivation, sequencing, and worked examples. They tolerate length because the reader is learning through guided practice.
- **How-to guides** solve specific problems. They need a clear scope statement, prerequisites, steps, expected output, and failure modes. They are intolerant of digression.
- **Reference** describes the API, interface, or system as it is. It needs vocabulary discipline above all, because readers are scanning rather than reading.
- **Explanation** clarifies why things are the way they are. It leans on diagrams, metaphors, tradeoffs, and connections to related concepts.

A doc that tries to be all four is usually bad at all four. If the kind is non-obvious, either ask, or commit to one and say so at the top of the doc.

Example:

> This is a how-to guide. It explains how to rotate an API key in production. It does not explain how API authentication works internally. For that, see the authentication architecture note.

That sentence saves the reader from wondering whether they are in the right place.

## Core principles

### Scope statement up front

The first thing the reader learns is what this doc covers, what it does not cover, and who it is for.

Without this, every reader pays an attention tax: "Am I in the right place?" Thirty seconds is the budget for resolving that question.

A good scope statement says:

- who the doc is for
- what problem it solves
- what it assumes
- what it deliberately does not cover

Example:

> This guide is for backend engineers who need to add a new market-data vendor to the ingestion pipeline. It assumes you know how to run the service locally. It does not cover vendor contract setup, production deployment, or downstream analytics.

### Motivation before mechanism

State why the thing exists before describing how it works.

Readers who understand the problem retain the solution. Readers handed the solution cold tend to forget it, because they do not know what the mechanism is trying to achieve.

Use this pattern:

> This exists because X. Therefore the system does Y. It does Y by using Z.

Bad:

> The service writes messages to Kafka, validates the payload, and then stores the result in Postgres.

Better:

> The service exists to make vendor data safe for downstream consumers. Because vendors send inconsistent payloads, the service first validates each message, then writes valid records to Kafka, and finally stores the normalized result in Postgres.

The second version gives the mechanism a reason.

### Diagrams are part of the explanation

A diagram is not decoration. It is part of the explanation.

Use a diagram whenever the reader must understand relationships that are hard to keep in working memory: component boundaries, data flow, control flow, state transitions, ownership, dependencies, deployment topology, lifecycle, permissions, or failure propagation.

Text is good at sequence and nuance. Diagrams are good at structure. A doc that explains a distributed system, API flow, state machine, architecture, permission model, or data pipeline without a diagram is probably asking too much from the reader.

Use diagrams early, before the detailed mechanism, so the reader has a mental map before reading the prose. The prose should then walk the reader through the diagram.

A useful diagram has one clear job. It should answer one question, such as:

- What talks to what?
- Where does the data go?
- Who owns this responsibility?
- What happens after this event?
- Where can this fail?
- What changes state?
- What is inside the boundary and what is outside it?

Bad diagrams are usually vague maps of everything. Good diagrams are selective. They remove detail so the reader can see the shape of the system.

Every diagram should have a caption that states what it shows and what it omits.

Example:

> This diagram shows the request path from the public API to the risk engine. It omits logging, metrics, retries, and alerting.

That caption prevents the reader from mistaking a simplified diagram for the whole system.

### Choose the simplest useful diagram

Use the simplest diagram type that answers the reader's question.

Use a **box-and-arrow diagram** for components, boundaries, and dependencies.

Example use case:

> Which services exist, and which services call each other?

Use a **sequence diagram** for request and response interactions.

Example use case:

> What happens after the user submits an order?

Use a **state machine** for lifecycle and valid transitions.

Example use case:

> Can an order move from `cancelled` back to `pending`?

Use a **flowchart** for branching logic.

Example use case:

> What happens when validation succeeds, fails, or times out?

Use a **data-flow diagram** for ingestion, transformation, and persistence.

Example use case:

> Where does raw data enter, where is it transformed, and where is it stored?

Use a **deployment diagram** for infrastructure and runtime placement.

Example use case:

> Which services run in which environment, and what external systems do they depend on?

Use a **table** when the structure is categorical rather than spatial.

Example use case:

> Which error code means what, and what should the caller do?

Use a **timeline** when ordering over time matters.

Example use case:

> What happens before, during, and after a migration?

Do not use a diagram when the same thing is clearer as one sentence or a small table. The rule is not "add visuals." The rule is "externalise structure when structure is the hard part."

### Walk through diagrams in prose

A diagram without prose often leaves the reader guessing what matters. After a diagram, explain how to read it.

Use this pattern:

1. State what the diagram shows.
2. Start at the entry point.
3. Follow the main path.
4. Point out important branches.
5. Name what is deliberately omitted.
6. Mention where failure can occur.

Example:

> The request starts at the public API. The API authenticates the caller, then forwards the request to the order service. The order service validates the payload and writes the order to the database. If validation fails, the request stops there and returns `400`. The diagram omits logging and metrics because they do not change the request path.

This is better than simply placing a diagram under a heading and expecting the reader to infer the story.

### Logical flow within, navigation between

Within a single doc, use logical connectors so the reader's eye keeps moving forward. Each sentence should connect to the previous through consequence, contrast, elaboration, or sequence.

Useful connectors include:

- because
- therefore
- however
- given that
- since
- as a result
- in practice
- for example
- this matters because

This is local flow.

Between docs, use cross-references and links. This is navigation, which is a different problem from flow.

A doc can have good navigation and bad flow. For example, it can link to many relevant pages but still be painful to read sentence by sentence.

A doc can also have good flow and bad navigation. For example, it can read well from top to bottom but be useless when someone lands in the middle and needs related material.

Both matter.

### Vocabulary discipline

Pick one name per concept and stick to it across the whole doc, and ideally across the doc corpus.

If "user," "client," and "customer" all refer to the same entity, the reader has to do reconciliation work that nothing rewards. Inconsistent vocabulary also signals that the author has not pinned down the model, which destroys reader trust.

Bad:

> The client sends a request. The customer is authenticated. The user then receives a token.

Better:

> The client sends a request. The client is authenticated. The client then receives a token.

If the terms really differ, define the distinction.

Example:

> A customer is the legal entity that pays for the product. A user is an individual account that belongs to a customer.

After that definition, use each term precisely.

### Worked examples, not just abstract claims

For every non-trivial abstract claim, supply one concrete instantiation.

A worked example is not a metaphor and not a diagram. It is the abstract claim instantiated, so the reader can check their understanding against a specific case.

Bad:

> The retry policy uses exponential backoff.

Better:

> The retry policy uses exponential backoff. With `base=1s` and `max_retries=3`, the client waits roughly 1s, 2s, and 4s before giving up.

Bad:

> The endpoint returns paginated results.

Better:

> The endpoint returns paginated results. For example, `GET /orders?limit=100` returns at most 100 orders and a `next_cursor`. Pass that cursor into the next request to retrieve the next page.

The example turns a claim into something testable.

### Failure modes, not just the happy path

Cover what happens when inputs are wrong, dependencies fail, permissions are missing, state is invalid, or the operation times out.

Docs that show only success leave the reader stranded at the moment they most need help.

For every important operation, document:

- what can fail
- what the failure looks like
- whether the operation is retryable
- what the caller or operator should do
- what should not be retried
- where to look for evidence

Example:

> If the vendor returns `503`, the ingestion worker retries with backoff. If the vendor returns `401`, the worker does not retry because the credentials are invalid. In that case, check the secret value in the production vault and rotate the key if needed.

Failure modes are especially important in runbooks, APIs, data pipelines, migrations, and distributed systems.

### Make state explicit

Many docs are hard to read because they hide state.

If the system has statuses, lifecycle stages, modes, locks, permissions, queues, checkpoints, cursors, leases, or ownership rules, name them explicitly.

Bad:

> The job processes files and marks them complete.

Better:

> Each file moves through four states: `pending`, `processing`, `complete`, and `failed`. A file starts as `pending`. When a worker claims it, the state changes to `processing`. If the write succeeds, the state changes to `complete`. If validation or storage fails, the state changes to `failed`.

If state transitions matter, use a state machine diagram.

Example:

```text
pending -> processing -> complete
              |
              v
            failed
```

Then explain which transitions are valid and which are impossible.

### Make boundaries explicit

Readers need to know what owns what.

State boundaries clearly:

* service boundaries
* team boundaries
* data ownership boundaries
* trust boundaries
* network boundaries
* responsibility boundaries
* public versus internal interfaces

Bad:

> The system validates orders.

Better:

> The public API validates authentication and schema. The order service validates business rules. The risk service validates exposure limits.

The second version tells the reader where each responsibility lives.

If boundaries are structural, show them in a diagram.

### Separate concepts from procedures

Do not mix explanation and instructions carelessly.

A concept section explains what something is and why it exists. A procedure section tells the reader what to do.

Bad:

> The migration service replays events from Kafka. To run it, set `MIGRATION_ID`. This is useful because old records need to be backfilled. Then run `make migrate`.

Better:

> The migration service replays historical events from Kafka so that old records can be backfilled into the new schema.
>
> To run a migration, set `MIGRATION_ID`, then run `make migrate`.

The second version separates the mental model from the action.

### Prefer progressive disclosure

Start with the shape of the system. Then add detail.

A reader should first understand the main idea, then the normal path, then edge cases, then implementation details.

Good order:

1. What this is
2. Why it exists
3. High-level diagram
4. Main path
5. Worked example
6. Failure modes
7. Edge cases
8. Operational details
9. Related docs

Bad order:

1. Environment variables
2. Implementation detail
3. Historical note
4. Main idea
5. Failure mode
6. Diagram

The bad order forces the reader to assemble the system from fragments.

### Avoid orphan facts

An orphan fact is a technically true statement that does not connect to the reader's current task or mental model.

Bad:

> The service is written in Go. It uses Kafka. The topic has 12 partitions. The database is Postgres.

Better:

> The service consumes vendor events from Kafka and stores normalized records in Postgres. The Kafka topic has 12 partitions, so at most 12 worker instances can actively consume from it at the same time.

The second version connects facts through consequence.

### Use tables for comparison

When the reader must compare options, do not bury the comparison in prose. Use a table.

Example:

| Error | Retryable | Cause                   | Action             |
| ----- | --------: | ----------------------- | ------------------ |
| `400` |        No | Invalid request payload | Fix the caller     |
| `401` |        No | Invalid credentials     | Rotate the API key |
| `429` |       Yes | Rate limit exceeded     | Retry with backoff |
| `503` |       Yes | Vendor unavailable      | Retry with backoff |

A table is not always shorter, but it makes comparison cheaper.

### Use code blocks for exact commands and payloads

Commands, config, payloads, queries, and error messages should be shown exactly.

Bad:

> Run the migration command with the dry-run flag.

Better:

```bash
python manage.py migrate_orders --dry-run
```

Bad:

> The API returns an invalid cursor error.

Better:

```json
{
  "error": "InvalidCursor",
  "message": "The provided cursor has expired."
}
```

Exact examples prevent ambiguity.

### Say what is safe

For operational docs, say which actions are safe, risky, reversible, or irreversible.

Bad:

> Restart the worker.

Better:

> Restarting the worker is safe. In-progress messages return to the queue after the lease expires, so another worker can process them. Do not restart all workers at once during market hours, because the queue can fall behind.

Readers operating production systems need risk information, not just instructions.

### Mention prerequisites before steps

A how-to guide should not let the reader discover missing prerequisites halfway through.

Bad:

> Run the deployment command. If you do not have access, request access from the platform team.

Better:

> Before starting, confirm that you have production deploy access. Without it, the deployment command will fail with `PermissionDenied`.

Then give the command.

### Keep historical context separate

Historical context can be useful, but it often damages readability when mixed into the main path.

Bad:

> The system now uses Kafka, although it originally used SQS and was migrated after the 2022 incident, which also changed the retry policy.

Better:

> The system uses Kafka for ingestion.
>
> Historical note: this replaced the old SQS pipeline after the 2022 incident.

Most readers need the current model first. History comes later unless it explains a surprising design choice.

### Conditional recapitulation

When a derivation or argument has more than about three load-bearing steps, end with a brief recap.

The recap should state:

* what the object was
* what was shown
* what the key idea was

Do not add a recap to every short section. On a short page, a recap is filler. The trigger is the length of the reasoning chain, not the length of the page.

Example:

> Recap: the ingestion worker reads raw vendor messages, validates them, normalizes them, and writes the result to Postgres. The key design choice is that invalid messages stop before persistence, while transient vendor failures are retried with backoff.

### Conciseness is no-waste, not brevity

The right frame is "every paragraph earns its place," not "few words."

A dense five-line paragraph that assumes too much background is harder to read than a longer paragraph that walks the reader through carefully.

Short and readable are correlated, not identical. When in doubt, cut what does not pull weight, and keep what does, even if it makes the doc longer.

Bad short version:

> Configure the worker and run it.

Better longer version:

> Configure the worker with the vendor name, input topic, and output table. Then run it in dry-run mode first, because dry-run validates the payload and permissions without writing records.

The longer version is better because it prevents mistakes.

## Writing workflow

When writing a doc, work in this order.

First, name the doc kind: tutorial, how-to guide, reference, or explanation.

Second, write the scope statement. Say who the doc is for, what it covers, and what it does not cover.

Third, state the motivation. Explain the problem before the mechanism.

Fourth, decide whether the reader needs a diagram before the detailed prose. If the topic involves structure, flow, state, ownership, dependencies, lifecycle, deployment, permissions, or failure propagation, include a diagram early.

Fifth, write the main path. Explain the normal case before edge cases.

Sixth, walk through the diagram in prose. Do not assume the reader knows what to look at.

Seventh, add worked examples for non-trivial claims.

Eighth, add failure modes beside the happy path.

Ninth, do a vocabulary pass. Replace inconsistent names.

Tenth, add tables where the reader must compare options.

Eleventh, add recaps only when the reasoning chain is long enough to need one.

Finally, cut paragraphs, diagrams, examples, and notes that do not pull weight.

## Review checklist

When reviewing a doc, check the following.

Does the doc say what kind of doc it is?

Does it say who it is for?

Does it say what it covers and what it excludes?

Does it explain why the thing exists before explaining how it works?

Does the reader need a diagram to understand the structure?

If there is a diagram, does it have one clear job?

Is the diagram introduced before the detailed mechanism?

Does the prose walk through the diagram?

Are component boundaries, ownership boundaries, and trust boundaries explicit?

Are state transitions explicit?

Is vocabulary consistent?

Are abstract claims backed by worked examples?

Are failure modes documented?

Are errors shown exactly?

Are risky or irreversible actions marked as such?

Are prerequisites stated before steps?

Are comparisons shown in tables instead of dense prose?

Are historical notes separated from the main path?

Does the doc avoid orphan facts?

Does every paragraph earn its place?

## Worked example

Since this skill argues for worked examples, here is one.

### Before

> The retry policy uses exponential backoff with jitter. Set `max_retries` to control the cap.

This is reference-style but missing scope, motivation, a worked example, and failure mode information. It also does not say why jitter matters.

### After

> This section is for callers who need to handle transient failures of the API. It does not cover authentication errors, which are non-retryable.
>
> Why retry at all: the API occasionally returns `503` under load. Retrying with backoff usually succeeds, whereas retrying immediately makes the problem worse, because all clients pile back in at the same moment.
>
> The retry policy uses exponential backoff with jitter. The delay before the n-th retry is `base * 2^n` plus a random jitter, so concurrent clients spread out rather than synchronising. Set `max_retries` to cap the total attempts. The default is 3.
>
> Example: with `base=1s` and `max_retries=3`, a failing call waits roughly 1s, 2s, then 4s before giving up. After `max_retries` attempts, the call raises `RetryExhausted`, which the caller must handle.

The second version is longer and easier to read, because every added sentence pulls weight: scope, motivation, mechanism, explanation of jitter, concrete instantiation, and failure mode.

## Diagram worked example

### Before

> Orders go through the API, then the order service, then risk, then execution. Failed orders are rejected.

This forces the reader to build the flow in their head.

### After

```text
Client
  |
  v
Public API
  |
  v
Order Service
  |
  v
Risk Service
  |
  +-- reject -> Rejected Order
  |
  v
Execution Service
  |
  v
Exchange
```

> This diagram shows the normal order path and the main rejection point. It omits authentication, logging, metrics, and retries.
>
> The client sends the order to the public API. The API forwards valid requests to the order service. The order service asks the risk service whether the order is allowed. If risk rejects the order, the flow stops and the order is marked `rejected`. If risk approves the order, the execution service sends it to the exchange.

This version is better because the reader sees the structure before reading the mechanism. The prose then walks through the diagram instead of forcing the reader to infer the flow from a paragraph.

## Final rule

Docs are for humans. Humans understand systems by building mental models. Therefore, good documentation must reduce mental load.

Use prose for sequence and nuance. Use diagrams for structure. Use examples for verification. Use consistent vocabulary for trust. Use failure modes for reality.

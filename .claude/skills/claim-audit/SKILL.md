---
name: claim-audit
description: Audit the correctness of factual claims in an answer, document, plan, code comment, README, analysis, or prior assistant response. Use when the user asks to verify claims, when the answer contains many factual statements, when factual accuracy matters, when the topic is current or niche, or before finalizing high-stakes output. Orthogonal to coding, testing, documentation, and architecture skills: this skill checks truth claims, not implementation quality.
---

# Process

## 1. Extract atomic factual claims

Read the answer or artifact and extract concrete claims that can be checked.

A claim is atomic when it can be judged true, false, misleading, unsupported, or not checkable on its own.

Good atomic claims:

- "Python 3.12 supports the `type` statement for type aliases."
- "The function returns an empty list when there are no matches."
- "The README says the project uses `uv sync` for installation."
- "The company reported revenue of X in fiscal year Y."
- "The matrix is positive semidefinite because `v'A'Av = ||Av||² >= 0`."

Bad audit units:

- "This is a good approach."
- "The project is robust."
- "The implementation is clean."
- "India is complicated."
- "This library is popular."

Rewrite broad statements into checkable subclaims before auditing them.

If the answer contains fewer than 10 factual claims, audit all of them.

## 2. Exclude non-factual material

Do not audit:

- style choices;
- subjective judgments unless they rely on factual premises;
- recommendations as recommendations;
- placeholders explicitly marked as assumptions;
- jokes, metaphors, or illustrative analogies unless they assert facts.

However, do audit the factual basis behind a recommendation.

Example:

> "Use PostgreSQL because it supports JSONB indexes."

The recommendation itself is not true or false, but the claim "PostgreSQL supports JSONB indexes" is auditable.

## 3. Classify each claim by verification method

For each extracted claim, assign one verification method:

- **Source check**: external factual claim, current fact, legal/regulatory point, product detail, pricing, public-company data, quote, institutional fact.
- **Primary-source check**: API behavior, library signature, official documentation, law, regulation, financial filing, academic paper.
- **Runtime check**: claim about the local codebase, installed package, command behavior, test result, file path, schema, or generated artifact.
- **Mathematical check**: algebraic identity, derivation, proof step, limit, optimization result.
- **Internal consistency check**: claim about what the current answer, document, or plan says.

Prefer primary sources over secondary summaries. For current facts, do not rely on memory.

## 4. Sample 10 claims at random

Create a numbered list of all auditable claims.

Sample 10 claims uniformly at random without replacement. If there are fewer than 10 claims, audit all claims.

Record the sampled claim numbers so the audit is reproducible enough for review.

If a claim is load-bearing, high-risk, or central to the conclusion, include it even if random sampling would miss it. Mark it as "forced into sample." Randomness is useful for coverage, but it must not hide the claims that matter most.

## 5. Verify each sampled claim

Check each sampled claim using its assigned verification method.

For source checks:

- use authoritative sources;
- prefer official documentation, filings, laws, standards, primary publications, or direct data providers;
- cite the source;
- check dates when facts may have changed.

For runtime checks:

- run the smallest diagnostic that directly verifies the claim;
- record the exact command or inspection used;
- do not infer runtime behavior from documentation when the runtime is available.

For mathematical checks:

- derive the result directly;
- name the theorem or identity used;
- check boundary cases where relevant;
- do not cite authority instead of checking the argument.

For internal consistency checks:

- quote or point to the relevant section;
- verify that the claim matches the actual artifact.

## 6. Grade each checked claim

Assign exactly one status:

- **Correct**: the claim is supported as stated.
- **Mostly correct**: the core claim is right, but wording needs precision.
- **Misleading**: the claim is technically defensible but likely to create a wrong impression.
- **Unsupported**: no adequate evidence was found.
- **False**: evidence contradicts the claim.
- **Not checkable**: the claim cannot be verified with available sources or runtime access.

"Unsupported" is not success. Treat it as a failure for the batch.

## 7. If all sampled claims pass, stop

A batch passes only if every sampled claim is **Correct** or **Mostly correct**.

If all 10 pass, report that the sampled audit found no material errors. Do not claim that every claim in the full answer is proven correct.

Acceptable wording:

> "I audited 10 randomly selected factual claims. All 10 were correct or mostly correct. This is a sampling check, not a proof that every statement is correct."

## 8. If any sampled claim fails, correct and continue

If any sampled claim is **Misleading**, **Unsupported**, **False**, or **Not checkable**:

1. List the failed claims.
2. Explain the correction or uncertainty.
3. Revise the underlying answer or artifact if applicable.
4. Sample 10 more unaudited claims and audit them.

Continue until either:

- one full batch of 10 passes;
- all auditable claims have been checked;
- the remaining claims cannot be checked with available tools or sources.

If repeated batches fail, stop treating the answer as reliable. Recommend a full claim-by-claim audit instead of sampling.

## 9. Report the audit result

Use this format:

```text
Audit scope:
- Total auditable claims found: <N>
- Claims audited in this batch: <claim numbers>
- Verification methods used: <source/runtime/math/internal>

Results:
- Correct: <count>
- Mostly correct: <count>
- Misleading: <count>
- Unsupported: <count>
- False: <count>
- Not checkable: <count>

Findings:
- Claim <number>: "<claim>"
  Status: <status>
  Evidence: <source, command, derivation, or internal reference>
  Correction: <only if needed>

Conclusion:
<Passed sampling audit / Failed sampling audit / Full audit needed>
```

Keep the report short unless the user asks for the full audit trail.

## 10. Update confidence

After the audit, adjust confidence explicitly.

Examples:

* "Confidence remains around 80%; the sampled claims passed, but the audit was not exhaustive."
* "Confidence drops to around 60%; one sampled claim was false and two were unsupported."
* "Confidence is below 50%; the answer needs a full rewrite before use."

Do not leave the original confidence unchanged after finding an error.

# Anti-patterns

* Auditing vague paragraphs instead of atomic claims.
* Treating "I remember this" as verification.
* Sampling only easy claims.
* Excluding load-bearing claims because random sampling missed them.
* Marking unsupported claims as correct because they seem plausible.
* Using secondary sources when primary sources are available.
* Checking current facts without checking dates.
* Claiming a clean sample proves the whole answer is correct.
* Silently fixing false claims without reporting that they were false.
* Continuing to produce confident prose after failed audit batches.

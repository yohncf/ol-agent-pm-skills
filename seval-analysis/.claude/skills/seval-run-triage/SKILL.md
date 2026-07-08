---
name: seval-run-triage
description: >
  Evaluate the performance of a SINGLE SEVAL HeroEval run by joining all
  four run artifacts (Assertions CSV, Queries TSV, Assertion-doctrine YAML,
  Settings JSON), classifying every failed assertion into one of four
  root-cause families (missing data, assertion problem, model, unclear),
  and producing a markdown summary + dark-themed HTML report + a diffable
  run-fingerprint manifest. Use when the user asks to
  "analyze a SEVAL run", "triage failures from this eval", "classify why
  this SEVAL run failed", "evaluate run <id>", or "produce a run
  fingerprint so I can later compare it to another run". Do NOT use for
  pairwise regression analysis between two runs — use
  `seval-regression-analyze` for that. The fingerprint emitted here is
  designed so two fingerprints can later be diffed by the regression
  skills to attribute movement to flag deltas, label shifts, and segment
  shifts.
---

# SEVAL Single-Run Triage

Single-run failure triage that goes beyond the CSV. The skill reads **four**
artifacts from a SEVAL run folder, attaches per-assertion `level` from the
YAML doctrine, joins `segment` + `query_hash` from the TSV, captures the
feature-flag set from `Settings.json`, then classifies every failed
assertion into one of four root-cause families:

- **Missing data** — grounding/retrieval came up empty (and the other arm also failed → confirmed data gap; if the other arm passed → re-classify, the data was available)
- **Assertion** — assertion is brittle, over-strict, or fails on a presentation nuance even though the reply is semantically correct (false negative)
- **Model** — capability refusal, tool error, partial execution, hallucination, factual error, or format violation. Anything the model+agent+harness produced incorrectly.
- **Unclear** — insufficient evidence to classify; flagged for human review

The output is a **fingerprint manifest** (JSON) + markdown summary + dark-themed
HTML report. The fingerprint is the durable artifact: it stores enough state
(per-row labels, per-segment + per-level pass rates, flag set) that a later
run's fingerprint can be diffed against it to attribute regression movement.

## When to invoke

Triggers include:

- "Analyze SEVAL run `<id>`"
- "Triage the failures in this SEVAL run"
- "Why did this run fail — was it data, assertions, the agent, or the model?"
- "Produce a fingerprint for run `<id>` so I can compare it later"
- "Evaluate the performance of this single eval run"
- "Classify failures in `<folder>`" (where the folder has CSV + TSV + YAML + Settings.json)

Do **not** invoke for:

- Comparing two SEVAL runs → `seval-regression-analyze`
- End-to-end regression pipeline (analyze + publish + ticket) → `seval-regression`
- Synthesizing eval queries from OCV/Dash → `seval-synthesize-queries-from-ocv`

## Inputs

The skill expects a **SEVAL run folder** (downloaded from
`https://seval.microsoft.com/job/<run-id>`) containing four files:

| Artifact | Required | Purpose |
|---|---|---|
| `[Assertions]_*.csv` | yes | Per-(query, assertion) score, rationale, model reply — both `control` and `experiment` arms |
| `*.tsv` | yes | Authoritative `Segment`, `query_hash`, `user_id`, `timestamp` per utterance |
| `*.yaml` | yes | Assertion doctrine: `level` (critical / expected / aspirational) per assertion |
| `Settings.json` | yes | `exp_configs_unified[*].sydney.variants`, scenario, eval_time_override |

Plus from the prompt:

| Input | Default | Notes |
|---|---|---|
| Run ID | — | Numeric SEVAL job ID (e.g. `552330`). Required. |
| Run name | folder name | Friendly label for the report. |
| Run date | derived from CSV filename | `YYYY-MM-DD`. Falls back to `eval_time_override` from Settings.json. |
| `--arm` | `both` | `control`, `experiment`, or `both`. |

If any of the four files cannot be located inside the folder, **ask the user**
for the explicit path — do not guess.

## How to run

### Step 1 — Extract + scaffold (script, deterministic)

```bash
python scripts/seval_run_triage_extract.py \
  --run-folder      <path-to-run-folder> \
  --run-id          552330 \
  --run-name        "HeroV26 Mainline vs CodeGen June 8" \
  --run-date        2026-06-08 \
  --arm             both \
  --out             data/eval-manifests/552330_2026-06-08_fingerprint.json
```

The script:

1. Auto-detects the four artifacts inside `--run-folder` by extension
   (`*.csv` with `[Assertions]` prefix, `*.tsv`, `*.yaml`, `Settings.json`).
   Errors loudly if anything is missing or ambiguous.
2. Loads the YAML doctrine into a `{(query_hash | query): {assertion: level}}`
   lookup. Falls back to fuzzy match on assertion text when the YAML key uses
   `id` (query_hash) but the CSV has only the query string.
3. Loads the TSV with the columns documented at the head of the file. Builds a
   `{query: {segment, query_hash, user_id, timestamp}}` lookup. The TSV is
   authoritative for `segment` (the CSV's `segment` column is sometimes blank).
4. Loads the CSV, joins per (query, assertion). Attaches `level` from YAML and
   enriches `segment` from TSV when the CSV is blank.
5. Loads Settings.json. Extracts `exp_configs_unified[0]` (Mainline / control
   side of the run) and `[1]` (CodeGen / experiment side) feature-flag sets
   using the same tolerant logic as `eval_regression_extract.py` — top-level
   `exp_configs_unified` preferred, falls back to `payload.exp_configs_unified`.
   Records `scenario`, `eval_time_override`, `user_id` as run-level context.
6. Computes per-arm summary: total rows, pass count, pass rate, **pass rate
   broken down by level** (`critical` / `expected` / `aspirational` — critical
   is the headline KPI; aspirational failures are demoted), and **pass rate
   broken down by segment**.
7. Emits `failures[]` — one entry per (arm, query, assertion) where `score == 0`.
   Each row carries: stable `id` = `sha1(arm|query|assertion)[:10]`, arm, query,
   query_hash, segment, assertion, **level**, reply, rationale,
   `failure_label = ""`, `failure_evidence = ""`. The agent fills the last two.
8. Writes the manifest with `publish_safety.reviewed_for_publish = false`.

### Step 2 — Author `failure_label` + `failure_evidence` (agent, doctrine-driven)

Open the manifest. For each entry in `failures[]`:

1. Read the `assertion` to understand what was being evaluated.
2. Read the `reply` to see what the model actually produced.
3. Read the `rationale` to see why the judge marked it failed.
4. **Decide the root-cause family first**, then the specific label inside it.
   The rationale's wording is a hint, not the ground truth — cross-check it
   against the reply.

Assign **one** label from the priority table below. The first label whose
definition substantively matches wins. Do not rely on keyword matching on
the rationale alone.

| Pri | Label | Family | When to use |
|---|---|---|---|
| 1 | `Presentation nuance` | Assertion | Reply contains the right information but the assertion fails on a phrasing, formatting, or labeling nuance — e.g. the model addresses the right person but doesn't literally call them "manager"; the model returns the correct counts but inline rather than as separate totals; the model correctly avoids adding duplicate subtasks but the assertion required new ones. The semantic answer is correct; only the surface presentation diverges. **This is a false negative** — treat as assertion quality debt, not model failure. |
| 2 | `Over strict assertion` | Assertion | Reply substantively satisfies user intent but the assertion fails on a narrow technicality that is broader than mere presentation — asked for exactly N items, returned N-1 equally valid ones; required a specific ordering the model permuted; conjunctive assertion that the grader can't disambiguate. Use sparingly. |
| 3 | `Assertion misalignment` | Assertion | The assertion measures something the query didn't ask for, or contradicts another assertion in the same query group. Distinct from `Over strict` — here the assertion is wrong, not just narrow. |
| 4 | `Missing grounding data` | Missing data | Model understood the task but retrieval came up empty (no emails / files / events match). Confirm from the reply that retrieval was attempted and returned nothing. **Cross-arm guardrail (see below):** do NOT assign this label when the other arm passed the same `(query, assertion)` — by construction both arms run against the same data, so if the other arm got the data, this arm did too. Re-bucket to `Failed task` / `Partial execution` / `Capability refusal`. |
| 5 | `Tool invocation error` | Model | Reply shows an explicit tool failure: malformed tool call, server error, timeout, plugin unavailable, auth failure. Distinct from refusal — the agent tried to call a tool and the call failed. |
| 6 | `Capability refusal` | Model | The agent explicitly refused with language like "I'm not currently capable of performing that task", "I can't do that here", "this isn't supported in this context". The capability is missing from the agent's toolset. |
| 7 | `Partial execution` | Model | The agent completed some required sub-steps but not all (flagged but did not pin; identified items but did not delete; set the reminder but did not confirm the flag). The shape of the response indicates the agent ran out of steam, not that the model picked the wrong content. |
| 8 | `Hallucination` | Model | Reply fabricated information: invented names, dates, citations, email subjects, or quoted entities that don't appear in the grounded context. Confirm by checking the reply — the rationale may say "incorrect" for many reasons; hallucination specifically means *invented* content. |
| 9 | `Factual error` | Model | Reply names real entities but gets attributes wrong (correct sender, wrong date; correct event, wrong time). Distinct from hallucination — the entity exists, the detail is wrong. |
| 10 | `Format violation` | Model | The content is correct but the response shape violates a critical format assertion (returned prose where a list was required; missing a confirmation sentence; wrong language). Only apply when the format assertion itself is critical, not aspirational. **Often overlaps with `Presentation nuance` — pick `Presentation nuance` when the user intent was satisfied; pick `Format violation` only when the format itself was a hard, user-requested requirement.** |
| 11 | `Failed task` | Model | Catch-all for genuine capability failures that don't fit above: looped, returned a malformed structure, ignored a constraint, answered a different question. |
| 12 | `Unclear` | — | Use only when rationale and reply together provide insufficient evidence to choose. Keep small; flag for human review. |

### Cross-arm guardrail (read before labeling any `Missing data` or `Assertion` row)

Both arms of a SEVAL run execute the **same queries against the same
environment** and against the **same assertion grader** within minutes of
each other. The grounding data — mailbox contents, calendar events, tasks,
files — and the assertion rubric are therefore identical across arms. The
renderer attaches a `cross_arm_status` field to every failure and the
auditor cross-references the other arm's label.

**Missing data — wrong label when:**

1. **`other_passed`** — the other arm scored 1 on this `(query, assertion)`.
   The data was demonstrably there; the other arm proved it.
2. **`both_failed` + other arm labeled it Model-family** — the other arm
   acknowledged it as `Capability refusal` / `Failed task` / `Partial
   execution` / etc. The other arm reached the data well enough to fail
   in a model way, so the data was not missing on this arm either.

**Assertion — wrong label when `other_passed`:**

The assertion grader is the same instance for both arms. If the other arm
produced a reply that scored 1, the assertion is **demonstrably
satisfiable**. A `Presentation nuance` / `Over strict assertion` /
`Assertion misalignment` label here is therefore wrong — the problem is
*this* arm's reply diverging in substance from the passing one. Relabel
to the Model family (`Failed task` by default; promote to `Partial
execution` or `Hallucination` after reading the reply).

In all cases re-label to the Model family — usually copy the other arm's
label, since it already analyzed the same failure on identical inputs.

**Symmetry check.** `both_failed` + both arms label it `Missing grounding
data` is the only configuration where `Missing grounding data` is
supportable, and even then the per-arm Missing-data count must be
**symmetric** across arms. Likewise, an assertion-level failure that
appears on both arms is the only configuration where Assertion-family
labels are supportable. If your counts diverge (e.g., 14 on one arm and 5
on the other), the guardrail is being violated somewhere — re-audit.

### Family rollup

Each label maps to a family. The skill aggregates failures by family so the
top-level question — *"was it the data, the assertion, the model, or
something we can't tell?"* — has a clean answer in **four buckets**:

| Family | Labels |
|---|---|
| **Missing data** | `Missing grounding data` |
| **Assertion** | `Presentation nuance`, `Over strict assertion`, `Assertion misalignment` |
| **Model** | `Tool invocation error`, `Capability refusal`, `Partial execution`, `Failed task`, `Hallucination`, `Factual error`, `Format violation` |
| **Unclear** | `Unclear` |

> The `Model` family is a deliberate roll-up of "the model+agent+harness
> did something wrong." Splitting agent perf vs base-model error is
> useful at the label level but noisy at the rollup level — both are
> things the engineering team owns.

### Failure evidence

Write a **≤ 25-word, present-tense, neutral PM-voice** sentence into
`failure_evidence` that names the observable failure mode and quotes ≤ 6 words
from the reply when it sharpens the diagnosis. Same quality bar as the
`why_failed` rule in `seval-regression-analyze`.

Good examples:

- "Model refused with `not currently capable of performing that task`; query
  required setting a follow-up reminder. Capability missing for reminder tool."
- "Reply lists 0 newsletters but inbox contained at least 11 matching the
  query window. Retrieval returned empty."
- "Reply names the correct sender but cites date `2026-04-05`; actual email
  received `2026-03-29`. Attribute error on date."

Bad examples (do not write):

- "The response failed the assertion." (no diagnosis)
- "It's concerning that the model refused." (editorializing)
- "Per the judge: `the response does not confirm...`" (verbatim paste)

### Extending the label set

You **may** introduce new labels when ≥3 failed rows share a root cause that
none of the seed labels covers without distortion. Rules:

- **Title Case noun-phrase**, slotted into the priority table with an explicit
  family assignment.
- Document the new label in `manifest.taxonomy_extensions[]` with `label`,
  `family`, `priority`, `definition`, `example_row_id`.
- Total label count target ≤ 12. Prefer reusing/extending over proliferating
  near-duplicates.

### Cluster + gap inference

After labeling all rows, write the aggregate sections of the manifest:

- `failure_clusters[]` — group by `(arm, label)`. For each cluster: count
  rows, list top 3 segments inline (`Folders (5), Rules (3)`), and list up
  to 3 representative query strings **only when count ≥ 2**. Sort by row
  count descending. The renderer caps display at 25 clusters.
- `inferred_gaps[]` — list of missing capabilities or systemic issues, each
  backed by ≥ 3 corroborating failures. Examples: "Reminder tool unavailable
  to Outlook agent (5 capability refusals on Flag_Reminder segment)",
  "Newsletter classification under-recalls (3 Missing grounding data
  failures on Delete segment with read newsletters)". Mark as `tentative`
  when evidence < 3 rows.
- **Cross-arm contradiction callout** — the renderer auto-generates a
  section listing every failure where `cross_arm_status == "other_passed"`
  and the label is in the Missing-data family. These are almost certainly
  mis-labeled — surface them for re-labeling rather than treating as
  genuine data gaps.

### Caveats

If `Unclear` exceeds 10% of failures on either arm, surface this prominently
in the report's Caveats section and suggest the user provide more rationale
data or sample for manual review.

### Step 3 — Render (script, deterministic)

```bash
python scripts/seval_run_triage_render.py \
  --manifest data/eval-manifests/552330_2026-06-08_fingerprint.json \
  --out-md   output/seval/triage/seval_triage_552330_2026-06-08.md \
  --out-html output/seval/triage/seval_triage_552330_2026-06-08.html
```

Default mode is **lenient** — rows with empty `failure_label` render as
`Pending analysis`. Pass `--strict` to fail when any failed row is blank
(use this before sharing).

The HTML report renders in **action-first order** — the most actionable
content leads, with raw failure detail at the bottom:

1. Header card: run id, name, date, scenario, sydney URL, eval_time_override
2. Pass-rate KPI strip: overall pass rate per arm + **critical-level pass
   rate (headline)** + expected + aspirational
3. **Inferred capability gaps** — semantic findings, the "so what" section
4. **Cross-arm contradictions** — flags Missing-data labels where the other
   arm passed (likely mis-labels)
5. Family rollup: stacked bar showing % of failures in each family per arm
   (Missing data / Assertion / Model / Unclear)
6. Top failure clusters table grouped by `(arm, label)` with inline top
   segments; capped to 25 rows; representative queries omitted for
   single-occurrence clusters
7. Feature-flag set per slot (collapsed by default; searchable)
8. Per-arm failure detail table **collapsed inside a `<details>` element by
   default** — filter pills (arm, family, segment, level) and per-row
   reply + rationale + failure_evidence still available when expanded
9. Caveats + sampling-bias note when applicable

Dark theme (M3 dark palette, Google Sans), matching `seval-regression-analyze`.

## Outputs

| Artifact | Path | Lifecycle |
|---|---|---|
| Fingerprint manifest | `data/eval-manifests/<run-id>_<date>_fingerprint.json` | **Permanent.** Diffable. Safe to share (model outputs are not customer content but are embedded). |
| Markdown summary | `output/seval/triage/seval_triage_<run-id>_<date>.md` | Renderable from manifest at any time. PM-voice. |
| HTML report | `output/seval/triage/seval_triage_<run-id>_<date>.html` | Renderable from manifest at any time. Dark-themed. |

Surface all three paths + the headline KPIs (overall pass rate, critical-level
pass rate, top failure family per arm) to the user when done.

## Manifest schema (fingerprint v1.0)

```jsonc
{
  "schema_version": "fingerprint-1.0",
  "generated_at": "<ISO timestamp>",
  "run": {
    "id": "552330",
    "name": "HeroV26 Mainline vs CodeGen June 8",
    "date": "2026-06-08",
    "scenario": "bizchat",
    "user_id": "corina@Outlook_SDF_v1@SyntheticTenant",
    "eval_time_override": "2026-04-05T05:34:00Z",
    "csv_path": "...",
    "tsv_path": "...",
    "yaml_path": "...",
    "settings_path": "..."
  },
  "summary": {
    "rows": 0,
    "queries": 0,
    "segments": [],
    "arms": {
      "control": {
        "rows": 0, "passed": 0, "failed": 0, "pass_rate": 0.0,
        "pass_rate_by_level": {"critical": 0.0, "expected": 0.0, "aspirational": 0.0},
        "pass_rate_by_segment": {}
      },
      "experiment": { /* same shape */ }
    }
  },
  "feature_flags": {
    "schema": "v2-paired-single-run",
    "slots": [
      {"slot_index": 0, "side_label": "Mainline",
       "exp_name": "control", "flags": ["..."], "count": 0},
      {"slot_index": 1, "side_label": "CodeGen",
       "exp_name": "experiment", "flags": ["..."], "count": 0}
    ],
    "errors": []
  },
  "failures": [
    {
      "id": "ab12cd34ef",
      "arm": "control",
      "query": "Pin my 3 most recent emails...",
      "query_hash": "f74564e8-...",
      "segment": "Pin_Flag",
      "assertion": "The response identifies...",
      "level": "critical",
      "reply": "...",
      "rationale": "...",
      "failure_label": "",
      "failure_evidence": ""
    }
  ],
  "failure_clusters": [],
  "inferred_gaps": [],
  "taxonomy_extensions": [],
  "caveats": [],
  "publish_safety": {
    "source": "seval-run-triage",
    "contains_customer_content": false,
    "contains_raw_model_outputs": true,
    "reviewed_for_publish": false
  }
}
```

## Filing ADO bugs from a single-run triage

When the user wants ADO bugs from a single-run triage manifest (not a
two-run regression — that path belongs to `seval-regression-ticket-sync`),
follow these conventions:

- **Granularity:** one Bug per `trend` (the manifest's `trend_rollup` key),
  not per assertion. Title: `[SEVAL CodeGen <run-id>] <trend> (N failures, M critical)`.
- **Priority:** any trend with at least one `critical`-level row → **P1**;
  otherwise **P2**.
- **Routing:** tags `OutlookAgent` + `SevalRegression`; assign via the shared
  owners config (`../shared/configs/ado_owners_outlook-agent.json`); area
  `Outlook Web\Outlook Copilot Service\Outlook Agent`. Add a Hyperlink
  relation to the published `eval-runs/` report.

**Every Bug description MUST open with a "Main issue / root cause" block.**
A title plus a list of representative failures is **not enough** for an
engineer to act — they need the diagnosis stated plainly up front. The block
(HTML, rendered above the representative-failures section) must contain:

1. **Stats line** — `N failures / M critical · family: <root-cause family> ·
   primitives: <Tool (count), ...>` (the top affected tools/segments).
2. **Plain-language explanation** of the actual defect (what CodeGen did vs.
   what was expected), grounded in the judge rationale / reply text — e.g.
   *"CodeGen narrates success in prose but never emits the tool call."*
3. **Suggested action** — the concrete engineering follow-up.

This was learned filing the run-576292 CodeGen bugs (#440709–440719): the
first pass shipped titles + examples only, and the descriptions had to be
back-filled so engineers understood the underlying issue per cluster. The
reference implementation is `scripts/create_codegen_ado_bugs.py` (creation,
one Bug per trend) and `scripts/update_codegen_ado_descriptions.py`
(idempotent prepend of the Main-issue block — skips a Bug if the block is
already present, so it is safe to re-run).

## Relationship to other skills

- **`seval-run-publish`** — publishes this skill's HTML report to the
  **EVAL Run Analysis** (`eval-runs.html`) section of the OCV-Weekly Pages
  site. Use it as the optional publish step after a single-run triage.
- **`seval-regression-analyze`** — pairwise regression between two runs.
  This skill's fingerprint shares the `feature_flags` extraction logic and
  the same `(query, assertion)` join key, so two fingerprints can be diffed
  by future regression tooling without re-running the extract.
- **`seval-regression`** — orchestrator for the full pipeline. Use that for
  end-to-end two-run analyze+publish+ticket flows. This skill is one-run only.
- **`seval-synthesize-queries-from-ocv`** — produces eval queries; this skill
  analyzes the runs that consume those queries.

## Compliance

- HTML is **dark-themed** (M3 dark palette, Google Sans stack), matching the
  OCV-Weekly look.
- `failure_evidence` is PM-voice: quantified, neutral, observable.
- No "crisis / hostile / churn risk / catastrophic" framing.
- Always state the denominator ("N failures out of M assertions where
  level=critical" — never just the numerator).
- Aspirational failures are demoted in the headline KPI — only critical
  pass-rate drives the top-line judgment.
- SEVAL replies are model outputs, not customer content, but treat them as
  **untrusted** when embedded into HTML: HTML-escape all model-origin text.

---
name: seval-regression-analyze
description: >
  Compare two SEVAL HeroEval runs (a "before" / control-side run and an
  "after" / experiment-side run) and produce a single self-contained
  dark-themed HTML report that highlights every assertion that regressed
  between the two. Inputs are two HeroEval result CSVs and two SEVAL
  Settings JSONs (one per run). The skill computes per-run summary stats,
  diffs the feature flags between the two runs, identifies shared
  (query, assertion) pairs whose score dropped from 1 to 0 in either the
  control-vs-control or experiment-vs-experiment comparison, asks the
  agent to author a one-sentence "why it failed" for each regressed row,
  and renders the result as HTML. Use when the user asks to "compare
  these two SEVAL runs", "find regressions between run X and run Y",
  "build a regression report for these HeroEval CSVs", or any phrasing
  that wants two-CSV regression analysis. Do NOT use for end-to-end pipelines
  (analyze + publish + ticket) — use the `seval-regression` orchestrator instead.
---

# SEVAL Regression Analyze

Identify and explain regressions between two SEVAL HeroEval runs. The
output is a single dark-themed HTML file suitable for sharing via a URL
(see the `seval-regression-publish` skill for the publish step) or
attaching to an email.

This skill is the **analysis layer** of the SEVAL regression pipeline.
It does **not** publish anything; it only produces the manifest JSON
and the renderable HTML in the local workspace.

## When to invoke

Triggers include:

- "Compare SEVAL runs `<id-A>` and `<id-B>`"
- "Find regressions between these two HeroEval CSVs"
- "Build a regression report for control X vs experiment Y"
- "I have two SEVAL runs, show me what got worse"
- "Run a SEVAL diff for the codegen rollout"
- Plus any prompt that supplies two HeroEval CSVs + two Settings JSONs

Do **not** invoke for:

- Single-CSV analysis (no comparison to make) → answer inline
- Publishing an already-rendered HTML to GitHub → `seval-regression-publish`
- End-to-end run that should also publish → `seval-regression` orchestrator

## Inputs

Six things must be present in the prompt or attached files. If anything
is missing, **ask the user** before proceeding — do not guess.

| Input | Source | Notes |
|-------|--------|-------|
| Control HeroEval CSV | Path | Columns: `query, segment, assertion, level, score_control, rationale_control, sydney_reply_control, score_experiment, rationale_experiment, sydney_reply_experiment` |
| Experiment HeroEval CSV | Path | Same schema as control CSV |
| Control SEVAL Settings JSON | Path | Provides `exp_configs_unified[0].sydney.variants` (control side of the run) and `[1]` (experiment side of the run). See "Feature-flag extraction" below. |
| Experiment SEVAL Settings JSON | Path | Same shape; usually the same `[0]` (control) variant; `[1]` is the new experiment variant being tested. |
| Control name + ID + run date | Prompt | e.g. name=`Mainline`, id=`538053`, date=`2026-06-03` |
| Experiment name + ID + run date | Prompt | e.g. name=`CodeGen`, id=`538953`, date=`2026-06-03` |
| Highlights line (optional) | Prompt | One-sentence summary for the landing card (the publish skill will reuse this) |

## How to run

### Step 1 — Extract regressions + flag diff (script, deterministic)

```bash
python scripts/eval_regression_extract.py \
  --control-csv      <path-to-control-csv> \
  --experiment-csv   <path-to-experiment-csv> \
  --control-json     <path-to-control-settings-json> \
  --experiment-json  <path-to-experiment-settings-json> \
  --control-name     "Mainline" \
  --experiment-name  "CodeGen" \
  --control-id       538053 \
  --experiment-id    538953 \
  --control-date     2026-06-03 \
  --experiment-date  2026-06-03 \
  --out              data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json
```

Output: `data/eval-manifests/<YYYY-MM-DD>_<control-id>_vs_<experiment-id>_manifest.json`

The script:

1. Loads both CSVs with pandas. Counts rows, distinct queries, segment
   list, and per-run pass rates (control-side and experiment-side, both
   sides reported for both runs so the agent can spot run-level shifts).
2. Joins on `(query, assertion)` after normalizing whitespace. Computes
   regressions for two comparisons:
   - **`control_vs_control`** — rows where `score_control == 1` in the
     control CSV and `score_control == 0` in the experiment CSV
   - **`experiment_vs_experiment`** — rows where `score_experiment == 1`
     in the control CSV and `score_experiment == 0` in the experiment CSV
3. Also computes `improvements`, `missing_in_control`,
   `missing_in_experiment`, and `unmatched_rows` so the manifest is
   transparent about what was and wasn't comparable.
4. Assigns each regression a deterministic 10-char `id` =
   `sha1(comparison|query|assertion)[:10]`. The agent will key its
   `why_failed` edits by this ID.
5. Extracts feature flags from both Settings JSONs (see below) and
   computes set-add / set-remove diffs.
6. Writes the manifest with `why_failed = ""` on every regression row
   and `publish_safety.reviewed_for_publish = false`.

### Step 2 — Author `why_failed` (agent, doctrine-driven)

Open the manifest. For each entry in `regressions[]`, read:

- `reply_passed` — the model output where the assertion held
- `reply_failed` — the model output where the assertion failed
- `rationale_passed` and `rationale_failed` — the judge's notes
- `assertion` itself

Write **1–2 short sentences** (≤ 50 words total, present tense, neutral
PM voice) into `why_failed`. The sentence(s) must make the model
regression obvious to a reader who only sees the `why_failed` field —
i.e., point clearly at *what the failing model did or did not do that
the passing model did*. Edit the JSON in place using the row's `id`
field as the unique anchor.

Quality bar for `why_failed`:

- **Contrast, don't summarize.** "Model refused; control delivered the
  rule and asked for delete-criteria — capability regression." beats
  "The response did not confirm the rule."
- **Cite the specific failure mode.** Use one of: capability refusal,
  partial execution, missing action, missing confirmation, hallucinated
  content, wrong entity / wrong slot, incorrect numeric / date,
  wrong-source retrieval, format violation, empty / generic reply,
  judge-only mismatch (rationales differ but model behavior is
  comparable).
- **Be specific and observable.** Quote ≤ 6 words from the failing
  reply when it sharpens the diagnosis (e.g., `"I'm not currently
  capable of performing that task"`). Avoid full quotes.
- **Do not paste judge rationale verbatim** — paraphrase.
- **Do not editorialize** ("terrible", "broken", "concerning") — state
  the observable behavior.
- **If the failure is judge-only** (same model behavior, different
  rationale), say so explicitly — don't invent a model regression.
- **No promises about fixes / root-cause speculation.** Report what the
  reader can verify from the two replies. Leave hypotheses for the
  retro / triage doc, not the report.

Good examples:

- "Model refused with `not currently capable of performing that
  task`; control created the rule and listed its name, condition, and
  action. Capability regression."
- "Reply lists `0 newsletters` despite the same query returning 11
  results in the control run. Retrieval / segmentation regression on
  the inbox lookup step."
- "Both runs produce the same factual answer; rationale only disagrees
  on whether `recurring=true` was implied. Judge-only mismatch, no
  model behavior change."

Bad examples (do not write these):

- "The response failed the assertion." (no diagnosis)
- "It is concerning that the model refused." (editorializing)
- "Per the judge: `the response does not confirm...`" (verbatim paste)

### Step 3 — Render HTML (script, deterministic)

```bash
python scripts/eval_regression_render.py \
  --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --out      output/eval-regression_<date>_<cid>_vs_<eid>.html
```

Default mode is **lenient**: rows with empty `why_failed` render with a
placeholder ("Pending analysis") so you can preview layout before
finishing the prose. Pass `--strict` to fail when any row is blank
(required by the publish skill).

The script renders:

- Header card with control + experiment metadata (names, IDs, dates,
  pass-rate KPIs, delta in pp)
- Feature-flag diff section — **two peer cards, one per side**: a
  Mainline (slot 0) card and a CodeGen (slot 1) card, each showing
  what flags were added in run #experiment / removed from run #control
  on that side, plus a per-side "Browse all flags" search collapsible.
- Two comparison blocks (`control_vs_control`,
  `experiment_vs_experiment`) with collapsible per-query groups,
  side-by-side replies (markdown-rendered via DOMPurify-sanitized
  marked.js), `why_failed` banner, expandable judge rationales
- Live search across queries / assertions, filter pills, and
  expand-all/collapse-all controls

Always reports the **denominator**: "N regressions out of M comparable
assertions" — never just the numerator.

## Feature-flag extraction

The Settings JSON path is, by convention,
`payload.exp_configs_unified[<i>].sydney.variants` per the user prompt.
In practice the file may have `exp_configs_unified` at the top level
(no `payload` wrapper). The extraction script handles both — it
prefers top-level, falls back to `payload.`.

Inside `sydney`:

- `variants` is either (a) a single comma-separated string of flag names,
  or (b) a list containing one such string. The extraction script
  normalizes both shapes. Presence = enabled, no values. The script
  splits on `,`, trims each token, and builds a set per slot. It also
  handles `name=value` defensively (treats the value as part of the key
  if `=` is present so an A/B value change shows up as a removed+added
  pair on the same name).
- `exp_configs_unified[0]` is the **control / Mainline** side of each
  individual SEVAL run (`is_baseline: true`, `exp_name: "control"`);
  `[1]` is the **experiment / CodeGen** side. The script prints
  `exp_name` from each slot in both JSONs so the user can sanity-check
  the mapping. If either slot is missing or malformed, the script
  records the error in `feature_flags.errors[]` and continues with the
  diffs it could compute.

The flag diff that lands in the manifest is **per-side, run-vs-run**:

- `feature_flags.diffs[0]` — Mainline-side flags in run #control vs
  Mainline-side flags in run #experiment (slot 0 vs slot 0).
- `feature_flags.diffs[1]` — CodeGen-side flags in run #control vs
  CodeGen-side flags in run #experiment (slot 1 vs slot 1).

Each diff entry carries the side label (`"Mainline"` for slot 0,
`"CodeGen"` for slot 1 via the `derive_side_label` mapping;
heuristic falls back to title-casing the upstream `exp_name`), the
two upstream slot names for transparency, the standard
`added` / `removed` / `shared` lists and counts, and
`total_in_control_run` / `total_in_experiment_run`. The renderer
shows both diffs as peer cards in the report so a regression on
either side can be traced to the flag delta that may have caused it.

## Outputs

| Artifact | Path | Lifecycle |
|----------|------|-----------|
| Manifest JSON | `data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json` | Permanent. Safe to share (no customer content). |
| HTML report | `output/eval-regression_<date>_<cid>_vs_<eid>.html` | Renderable artifact. Re-rendered from the manifest at any time. |

Surface both paths + the headline KPIs (regression counts with
denominators, pass-rate deltas) to the user when done.

## Manifest schema (v1.1)

See `data/eval-manifests/_template.json` for a fully-commented sample
(the extract script writes this on first run). Top-level keys:

- `schema_version` (`"1.1"`), `generated_at`
- `control`, `experiment` (id, name, run_date, csv_path,
  settings_path, control_side_pass_rate, experiment_side_pass_rate)
- `summary` (matched_pairs, regression counts, improvement counts,
  unmatched counts)
- `feature_flags`:
  - `schema`: `"v2-paired"` (sentinel — renderer falls back to a
    legacy adapter for older `1.0` manifests where the block was a
    single experiment-side diff plus optional `control_side_drift`)
  - `diffs[]`: one entry per slot (Mainline=0, CodeGen=1), each with
    `slot_index`, `side_label`, `control_run_slot_exp_name`,
    `experiment_run_slot_exp_name`, `added`, `removed`, `shared`,
    `added_count`, `removed_count`, `unchanged_count`,
    `total_in_control_run`, `total_in_experiment_run`
  - `errors[]` (optional): any per-slot extraction errors
- `regressions[]` (id, comparison, query, segment, assertion, level,
  reply_passed, reply_failed, rationale_passed, rationale_failed,
  why_failed)
- `improvements[]` (same shape, for transparency)
- `highlights` (optional one-liner)
- `publish_safety` (source, contains_customer_content,
  contains_raw_model_outputs, reviewed_for_publish)

## Relationship to other skills

- Pairs with **`seval-regression-publish`** which consumes the
  manifest + HTML and publishes them to the OCV-Weekly GitHub site.
- The **`seval-regression`** orchestrator chains this skill →
  user review pause → `seval-regression-publish`.

## Compliance

- HTML is **dark-themed** (M3 dark palette, Google Sans font stack,
  matching the OCV-Weekly look).
- `why_failed` prose is **PM-voice**: quantified, neutral, observable.
- No "crisis / hostile / churn risk / catastrophic" framing.
- Always state the denominator ("N regressions out of M comparable").
- SEVAL replies are model outputs, not customer content, but treat
  them as **untrusted** when embedding into HTML: DOMPurify on every
  markdown render; `</script>` escaping on every embedded JSON blob.

---
name: seval-regression
description: >
  End-to-end SEVAL regression pipeline. Given two HeroEval CSVs and two
  SEVAL Settings JSONs (plus control/experiment names, IDs, and dates),
  this orchestrator runs seval-regression-analyze, pauses for the user
  to review and approve the local HTML report, optionally runs
  seval-regression-publish to push the report to the OCV-Weekly
  GitHub site, then optionally runs seval-regression-ticket-sync to
  file one ADO Bug per regression cluster. Use when the user asks to
  "run the full SEVAL regression pipeline", "compare these two SEVAL
  runs and publish the report", "do an end-to-end regression analysis
  for control X vs experiment Y, then file the bugs", or any phrasing
  that wants analyze + publish + (optional) ticket chained together
  for a single pair of SEVAL runs. Do NOT invoke for single-step operations
  (analyze-only, publish-only, ticket-only) — call the relevant sub-skill directly.
---

# SEVAL Regression Pipeline (Orchestrator)

This is a **meta-skill**. It does not analyze, publish, or ticket
anything itself — it invokes the three sub-skills in the canonical
order, with the right arguments threaded between them, and it pauses
for the user's review at each natural handoff point.

The three sub-skills stay fully isolated and independently invocable:

| # | Sub-skill | Input | Output |
|---|-----------|-------|--------|
| 1 | `seval-regression-analyze` | 2 CSVs + 2 JSONs + control/experiment metadata | manifest JSON + HTML report (local) |
| 2 | `seval-regression-publish` (optional) | manifest + HTML + highlights line | commit on `gim-home/OCV-Weekly` `main` (mirrored to `yohncf/OCV-Weekly_temp`); live URL on Pages |
| 3 | `seval-regression-ticket-sync` (optional) | manifest + agent classifications + published report URL | one ADO Bug per `(failing_side, topic, category)` cluster, tagged `OutlookAgent; SevalRegression` |

This pipeline can run **more than once per week** — there is nothing
weekly-cadenced about it. Each run is keyed by
`<YYYY-MM-DD>_<control-id>_vs_<experiment-id>`.

---

## When to invoke

Triggers include:

- "Run the full SEVAL regression pipeline for `<cid>` vs `<eid>`"
- "Do an end-to-end regression analysis: compare control X with experiment Y, then publish"
- "I have two HeroEval CSVs and two Settings JSONs — run the whole thing and put it on the site"
- "SEVAL regression end-to-end for `<control name>` vs `<experiment name>`"
- "Analyze and publish a regression report for these two runs"
- "Run the full SEVAL regression pipeline and file the bugs"
- "Compare X vs Y, publish the report, and open ADO bugs"

Do **not** invoke for single-step requests:

- "Just analyze, don't publish" → call `seval-regression-analyze` directly
- "Publish this already-rendered HTML" → call `seval-regression-publish` directly
- "Re-render an existing manifest" → call `seval-regression-analyze` Step 3 directly
- "Just file the bugs for an already-published report" → call `seval-regression-ticket-sync` directly

---

## Parsing the user's request

Extract from the prompt:

1. **Control CSV path** + **Experiment CSV path**
2. **Control Settings JSON path** + **Experiment Settings JSON path**
3. **Control name** (e.g. `Mainline`) + **Control ID** (e.g. `538053`) + **Control run date** (`YYYY-MM-DD`)
4. **Experiment name** (e.g. `CodeGen`) + **Experiment ID** (e.g. `538953`) + **Experiment run date** (`YYYY-MM-DD`)
5. **Highlights line** (optional) — one-sentence summary for the landing card

If any of (1)–(4) is missing or ambiguous, **ask the user** before
starting. Surface the resolved values to the user before kicking off
Step 1 so they can correct any misread.

If the prompt says "skip the publish step" or "just produce the local
HTML", run only Step 1 and stop. Conversely, if the user re-runs only
the publish step against an existing manifest, route directly to
`seval-regression-publish`. If the user says "skip the bugs" or
"don't file anything to ADO", stop after Step 2.

---

## Plan: how to run the pipeline

Print the plan to the user **before** starting Step 1. Use a numbered
list with the resolved arguments filled in. Then execute Step 1,
pause for review, offer Step 2, then pause again and offer Step 3.

### Step 1 — Analyze (delegates to `seval-regression-analyze`)

Invoke the analyze skill with all six resolved inputs. Follow that
skill's three internal sub-steps in order:

1. **`eval_regression_extract.py`** produces the manifest with
   `why_failed = ""` and `publish_safety.reviewed_for_publish = false`.
2. **Agent authors `why_failed`** for every regression row, editing
   the manifest in place using each row's stable `id` as the unique
   anchor. Follow the `why_failed` authoring rules in
   `seval-regression-analyze` (PM voice, observable failure mode, no
   editorializing — that sub-skill is the authoritative spec for
   length and style).
3. **`eval_regression_render.py`** produces the HTML report.

Expected outputs:

- `data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json`
- `output/eval-regression_<date>_<cid>_vs_<eid>.html`

Surface both paths and the headline KPIs:

- Regression counts: `control_vs_control = X` and `experiment_vs_experiment = Y` **out of `Z` comparable assertions**
- Per-side pass-rate deltas in pp
- Feature-flag diff size: `+A added / -B removed` between
  experiment-side variants

### Pause for review — REQUIRED

**Stop here. Do not run Step 2 automatically.**

Present:

- Path to the HTML report
- Headline KPIs (regression counts with denominator + pass-rate deltas)
- A note: "Open the HTML locally and verify the `why_failed`
  sentences and the feature-flag diff before publishing. This is the
  right moment to edit the manifest if anything looks off — the
  publish skill will refuse to push a blank `why_failed` or a manifest
  with `reviewed_for_publish: false`."

Then **ask the user** using `ask_user`:

- Choices:
  - `Publish now (mirrors to gim-home + yohncf)`
  - `I'll edit the manifest first, then say publish`
  - `Skip publish — keep the local HTML only`

Only proceed to Step 2 once the user picks "Publish now". When the
user picks "Publish now", set
`publish_safety.reviewed_for_publish = true` in the manifest before
calling the publish skill.

### Step 2 (optional) — Publish (delegates to `seval-regression-publish`)

Invoke the publish skill with the manifest + HTML + highlights line.
That skill enforces its own two-gate doctrine (Gate A = dry-run plan
review in chat; Gate B = stdin `yes`). Do not bypass either gate.

After publish succeeds, surface:

- Live URL on `gim-home`:
  `https://gim-home.github.io/OCV-Weekly/eval-reports/<slug>.html`
- Personal mirror URL (used when filing ADO bugs so external
  collaborators can open it):
  `https://yohncf.github.io/OCV-Weekly_temp/eval-reports/<slug>.html`
- Listing page URL:
  `https://gim-home.github.io/OCV-Weekly/eval.html`
- Commit hash

Capture the **personal mirror URL** — it's the `--report-url`
argument for Step 3.

### Pause for ADO filing — REQUIRED before Step 3

**Stop here. Do not run Step 3 automatically.**

Filing ADO Bugs is a separate, irreversible write into an engineering
backlog and many runs (re-runs, smoke comparisons, internal A/Bs)
should never produce bugs. Always confirm explicitly.

Ask the user using `ask_user`:

- Question: "Report is live. File ADO bugs for the regressions?"
- Choices:
  - `Yes — file one Bug per (failing_side, topic, category) cluster`
  - `No — stop here; I'll triage the report manually`
  - `Not yet — I want to re-review the report first`

Only proceed to Step 3 when the user picks "Yes — file...".

### Step 3 (optional) — File ADO bugs (delegates to `seval-regression-ticket-sync`)

Invoke the ocv-ticket-sync skill with:

- the manifest from Step 1
- `--report-url` = the personal mirror URL captured at the end of Step 2

The ocv-ticket-sync skill runs its own three-phase workflow:

1. `classify-template` → empty classification JSON
2. Agent classifies every regression into the OCV 13-topic taxonomy
   + Category vocabulary (re-read
   `ocv-analyze-and-ticket/SKILL.md` for the
   taxonomy and tie-breaker rules)
3. `propose` → cluster proposals JSON; **Gate A** in chat (per-cluster
   review with the user) → **Gate B** in chat (final write-count
   confirmation) → `execute --yes`

Policy reminders (the ocv-ticket-sync skill enforces all of these; this
orchestrator simply hands off):

- **Always create**, never link. Regression bugs are net-new every
  run.
- **Tags:** `OutlookAgent` + `SevalRegression`.
- **Priority:** any cluster with a `critical` assertion → P1, else
  P2.
- **Owners:** reused from `../shared/configs/ado_owners_outlook-agent.json`
  (same file as the OCV `ocv-ticket-sync`).
- **Body contents:** base + latest SEVAL URLs, failing model, every
  rolled-up (query, assertion, why_failed, failed reply, judge
  rationale), and a link to the published report.

After execute succeeds, surface:

- Count of bugs created, broken down by P1/P2 and by assignee
- The per-cluster ADO URLs (already in the script's stdout)
- Any clusters that landed unassigned (no rule matched) so the user
  can hand-assign them

---

## Resuming partway

If the user says:

- "I already ran the analyze step, just publish" → skip Step 1, go
  straight to the pause/Step 2 sequence. Locate the manifest by
  filename pattern under `data/eval-manifests/` (confirm with user
  if multiple match).
- "Re-render from the existing manifest" → invoke only Step 3 of
  `seval-regression-analyze` (the render script).
- "Re-publish — I edited the HTML" → skip everything and call
  `seval-regression-publish` directly.
- "I already published — just file the bugs" → skip Steps 1 and 2,
  go straight to Step 3. The personal mirror URL is
  `https://yohncf.github.io/OCV-Weekly_temp/eval-reports/<slug>.html`;
  derive `<slug>` from the manifest filename
  (`<date>_<cid>_vs_<eid>`).

---

## Compliance

- All artifacts dark-themed.
- The review pause is **non-negotiable**; never auto-publish.
- Mirrors `ocv-weekly`'s orchestrator pattern so users with muscle
  memory from the OCV pipeline can use this one without retraining.

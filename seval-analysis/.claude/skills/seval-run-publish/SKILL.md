---
name: seval-run-publish
description: >
  Publish a standalone single-run SEVAL analysis (failure triage, head-to-head
  arm comparison, capability deep-dive, or any one-off run study) to the
  "EVAL Run Analysis" listing on the gim-home/OCV-Weekly GitHub Pages site,
  then auto-mirror to yohncf/OCV-Weekly_temp. Copies the report HTML into
  eval-runs/<slug>.html, upserts the eval-runs.json manifest, regenerates the
  eval-runs.html listing page via scripts/gen_eval_runs_page.py, and pushes.
  This is the THIRD nav section ("EVAL Run Analysis"), distinct from the
  regression "EVAL Analysis" (eval.html / eval-reports/) owned by
  seval-regression-publish. Use when the user asks to "publish this run
  analysis", "add this to the eval runs list", "upload this single-run report
  to Pages", or "put this head-to-head on the OCV-Weekly site". Always prints a
  publish plan and waits for explicit user confirmation before any git push
  (two-gate doctrine). Does NOT publish two-run regression reports — that is
  seval-regression-publish. Does NOT analyze CSVs.
---

# Publish Eval Run Analysis

Publishes **standalone single-run** SEVAL studies to the **EVAL Run Analysis**
section of the OCV-Weekly GitHub Pages site. This is the sibling of
`seval-regression-publish`: same site, same dark M3 look, same two-gate / mirror
doctrine — but a **separate nav section, listing page, manifest, and folder**.

| | Regression listing (`seval-regression-publish`) | Run-analysis listing (this skill) |
|---|---|---|
| Nav label | **EVAL Analysis** | **EVAL Run Analysis** |
| Listing page | `eval.html` | `eval-runs.html` |
| Manifest | `eval-reports.json` | `eval-runs.json` |
| Report folder | `eval-reports/` | `eval-runs/` |
| Slug | `<date>_<control-id>_vs_<experiment-id>` | `<date>_<run>_<topic>` |
| For | Two-run, two-CSV regressions | One-run deep-dives (triage, head-to-head, capability study) |

## When to invoke

Triggers include:

- "Publish this run analysis / single-run report to Pages"
- "Add this to the EVAL Run Analysis list" / "add it to the eval runs list"
- "Upload this head-to-head / failure-triage report to the OCV-Weekly site"
- "Put this standalone SEVAL study on GitHub Pages"

Do **not** invoke for:

- A **two-run regression** report (control CSV vs experiment CSV +
  Settings JSONs) → `seval-regression-publish` (`eval.html` / `eval-reports/`)
- OCV weekly reports → `ocv-publish-github`
- "Just build the report, don't publish" → build the HTML and stop

## The EVAL Run Analysis page system

Three coupled pieces live in the **OCV-Weekly clone**
(`OLAgentWork/_ocv_weekly_repo/`, origin = `gim-home/OCV-Weekly`):

1. **`eval-runs/<slug>.html`** — the self-contained report itself. Built
   upstream by whatever single-run analysis produced it (e.g. a custom
   `build_*_report.py`, or `seval-run-triage`'s renderer). Must be
   dark-themed and self-contained (no external assets beyond CDN fonts /
   Chart.js). Reports are self-contained with no gim-home back-links, so the
   mirror's link-rewrite step is a no-op for them.
2. **`eval-runs.json`** — the manifest the listing page is generated from.
   One `analyses[]` entry per published report. Schema below.
3. **`eval-runs.html`** — the **generated** listing page. **Never hand-edit**;
   it carries an `EVAL-RUNS-GENERATED: do not hand-edit` banner. Regenerate it
   from the manifest with:

   ```bash
   python ../seval-analysis/scripts/gen_eval_runs_page.py --repo <path-to-_ocv_weekly_repo>
   ```

   (the generator lives in the monorepo at
   `seval-analysis/scripts/gen_eval_runs_page.py`).

### The shared 3-item nav is owned by the regression publish script

The "Dashboards" dropdown — **OCV Weekly · EVAL Analysis · EVAL Run Analysis** —
appears on `index.html`, `eval.html`, and `eval-runs.html`. On `index.html` and
`eval.html` it is **managed by `publish_eval_regression_report.py`** (between
`EVAL-NAV-START`/`END` markers and via the `NAV_BLOCK` injection). That script
has **two nav templates** (the `eval.html` managed template ~L379 and the index
`NAV_BLOCK` ~L674); **both already list the `eval-runs.html` item**. If you ever
add a fourth section, you must update **both** templates or the next regression
publish silently reverts the dropdown. The `eval-runs.html` generator emits its
own copy of the same 3-item nav (with `aria-current` on `eval-runs.html`).

## Manifest schema (`eval-runs.json`)

Top-level: `title`, `subtitle`, `owner`, and `analyses[]`. The generator sorts
`analyses[]` by `date` descending (stable; equal dates keep array order, so put
the newest first).

Each `analyses[]` entry:

| Field | Required | Notes |
|---|---|---|
| `slug` | yes | `<date>_<run>_<topic>` (e.g. `2026-06-24_581345-codegen-vs-omni_head-to-head`) |
| `label` | yes | Card title |
| `date` | yes | `YYYY-MM-DD` (analysis date; controls sort order) |
| `file` | yes | `eval-runs/<slug>.html` (relative path) |
| `kind` | no | Small pill, e.g. `Head-to-head`, `Failure triage` |
| `run` | no | `{ name, run_date }` for provenance |
| `metrics` | no* | **Preferred** flexible array: `[{label, value}]` — rendered as the numeric row |
| `tags` | no* | **Preferred** flexible array: `[{label, value, kind}]` — colored chips |
| `stats` | no* | **Legacy** fallback: `{both_arm_failures, queries, comparable}` |
| `buckets` | no* | **Legacy** fallback: `{model, missing_data, assertion}` |
| `highlights` | no | One-paragraph summary under the card |
| `published` | no | ISO timestamp, provenance only |

\* Provide **either** `metrics`/`tags` (preferred, general) **or** the legacy
`stats`/`buckets`. `gen_eval_runs_page.py` prefers `metrics`/`tags` and falls
back to `stats`/`buckets` so older entries keep rendering.

**Tag `kind` → color** (CSS classes in the generator): `omni` (green),
`cg` (orange), `fail` (red), `model` (orange), `missing` (teal),
`assertion` (green), `neutral` (grey). Pick the kind that matches the metric's
meaning, not just aesthetics.

Example entry (head-to-head, flexible schema):

```json
{
  "slug": "2026-06-24_581345-codegen-vs-omni_head-to-head",
  "label": "CodeGen vs CodeGen+OMNI — head-to-head",
  "date": "2026-06-24",
  "file": "eval-runs/2026-06-24_581345-codegen-vs-omni_head-to-head.html",
  "kind": "Head-to-head",
  "run": { "name": "HeroV27 CodeGen vs CodeGen+OMNI", "run_date": "2026-06-23" },
  "metrics": [
    { "label": "CodeGen pass", "value": "71.8%" },
    { "label": "OMNI pass", "value": "73.8%" },
    { "label": "comparable", "value": 347 },
    { "label": "queries", "value": 61 }
  ],
  "tags": [
    { "label": "OMNI wins", "value": 47, "kind": "omni" },
    { "label": "CodeGen wins", "value": 40, "kind": "cg" },
    { "label": "Both fail", "value": 51, "kind": "fail" }
  ],
  "highlights": "…one-paragraph summary…"
}
```

## Workflow

### 1. Confirm the report + slug

- The report HTML must already exist (built locally, e.g. in
  `seval-analysis/output/`). Confirm it is dark-themed and self-contained.
- Form the slug `<date>_<run>_<topic>`: `date` = analysis date,
  `run` = run id + short model tag, `topic` = the study angle
  (`both-arm-failures`, `head-to-head`, `capability-gaps`, …).

### 2. Stage the artifacts in the clone (local writes only)

```powershell
$repo = "C:\…\OLAgentWork\_ocv_weekly_repo"
Copy-Item <report.html> "$repo\eval-runs\<slug>.html" -Force
# upsert the new analyses[] entry into $repo\eval-runs.json (newest first)
python ..\seval-analysis\scripts\gen_eval_runs_page.py --repo $repo
```

Validate: `eval-runs.json` parses, `eval-runs.html` shows the new card, the
card's `file` link resolves to the copied report.

### 3. Gate A — show the plan, get confirmation

Present: files added/modified, the manifest entry, the slug + collision status,
the commit message, and the expected live URLs
(`https://gim-home.github.io/OCV-Weekly/eval-runs/<slug>.html` and
`/eval-runs.html`). Then `ask_user` to confirm. **Never push without explicit
confirmation** (Agency Cowork outbound policy + two-gate doctrine).

### 4. Push to gim-home, then mirror

```powershell
git -C $repo add eval-runs.json eval-runs.html "eval-runs/<slug>.html"
git -C $repo commit -m "eval-runs: publish <date> <topic>…"   # + Copilot Co-authored-by trailer
git -C $repo pull --rebase origin main
git -C $repo push origin main
# mirror to the public personal clone (yohncf/OCV-Weekly_temp):
powershell -ExecutionPolicy Bypass -File "C:\…\OLAgentWork\mirror_to_personal_v2.ps1"
```

`origin` pushes to `gim-home/OCV-Weekly` only. `mirror_to_personal_v2.ps1`
(monorepo root) robocopies the working tree to `_ocv_weekly_personal`, rewrites
gim-home→yohncf back-links **only in `reports/*.html`** (eval-runs reports are
self-contained, so untouched), commits, and pushes to `yohncf/OCV-Weekly_temp`.

### 5. If you changed the generator, commit it to the monorepo

`gen_eval_runs_page.py` lives in `yohncf/ol-agent-pm-skills` (the monorepo),
**not** in the OCV-Weekly clone. If you extend it (new tag kinds, schema
fields), commit that change to the monorepo separately.

Surface the final commit hashes (gim-home, mirror, monorepo) and the live URLs.

## Compliance

- HTML is **dark-themed** (M3 dark palette, Google Sans + Roboto Mono).
- SEVAL replies are model outputs (not customer content) but **untrusted** when
  embedded — escape `</…` in any embedded JSON; the report's own JS uses an
  `esc()` HTML-escaper before injecting judge text.
- Analysis prose is **PM voice**: quantified, neutral, observable; always state
  the denominator ("N of M comparable assertions"); no crisis framing.
- **Two-gate**: plan-in-chat confirmation before any git push.

## Relationship to other skills

- Built reports often come from **`seval-run-triage`** (single-run failure
  fingerprint + HTML) or a one-off `build_*_report.py`. This skill is the
  **publish layer** for those outputs.
- **`seval-regression-publish`** is the parallel publisher for two-run
  regressions (`eval.html` / `eval-reports/`) and **owns the shared dropdown
  nav** on `index.html` + `eval.html`.
- Both publishers target the same GitHub Pages site and share
  `mirror_to_personal_v2.ps1`.

# SEVAL Analysis — Skills Handover Guide

A complete, plain-English guide to every skill in `seval-analysis/`, written so that
**anyone can pick up this work and replicate it**. Read this end-to-end once, then use the
per-skill sections as a reference.

> This project grew out of `../ocv-extraction` and reuses its ADO sync engine,
> owners-routing config, and the 13-topic taxonomy. Common code lives in `../shared/`.
> See the repo-root `AGENTS.md` for the cross-project map.

---

## 0. Where this code lives & what to download

**All skills, scripts, and shared code live in a single GitHub repository:**

> **`github.com/yohnathanc_microsoft/ol-agent-pm-skills`** — the "OLAgentWork" monorepo.

To get everything needed to run these skills, **clone the whole repo** (this is the file
to download — the entire monorepo, not individual files):

```bash
git clone https://github.com/yohnathanc_microsoft/ol-agent-pm-skills.git OLAgentWork
cd OLAgentWork
```

That one clone provides every tracked folder the skills need:
`shared/` · `seval-analysis/scripts/` · `seval-analysis/.claude/skills/` ·
`seval-analysis/docs/` (incl. `EVAL_DOCTRINE.md`).

> **⚠️ The OCV-Weekly Pages repos are NOT the source of the skills.**
> `gim-home/OCV-Weekly` and `yohncf/OCV-Weekly_temp` contain only the **published HTML
> report output** (the website — `eval-reports/`, `eval-runs/`, listing pages). They hold
> no scripts or skills. You clone one of them separately into `_ocv_weekly_repo/` **only
> as a publish target** for `seval-regression-publish` / `seval-run-publish` (see §3).

### Backup status (verified 2026-07-07)

- Committed skills/scripts on `main` **are** pushed to the canonical repo
  `yohnathanc_microsoft/ol-agent-pm-skills` (also mirrored to `yohncf/ol-agent-pm-skills`).
- **Not yet backed up at handover time** (uncommitted / untracked) — **commit & push
  these before handing off**, or they are lost:
  - Modified, uncommitted: `seval-run-triage/SKILL.md` (and, in `../ocv-extraction`,
    `ocv-analyze-and-ticket/SKILL.md`, `ocv-draft-email/SKILL.md`, `draft_ocv_email.py`).
  - New, untracked scripts in `scripts/`: `codegen_failure_extract.py`,
    `codegen_failure_render.py`, `create_codegen_ado_bugs.py`,
    `update_codegen_ado_descriptions.py`.
  - These two `SKILLS_HANDOVER.md` files.

## 0b. ⚠️ Hardcoded personal references — change these before reuse

The scripts contain **no credentials, tokens, or PATs**. All authentication is acquired at
runtime: `az login` (ADO), Windows Credential Manager (git push), and captured bearer
tokens / SSO profiles (SEVAL portal). Nothing to rotate.

**However, the author's GitHub identity and target repos are hardcoded as defaults.**
Anyone reusing this must replace them, or publishing will push to the wrong place:

| Hardcoded value | Where | Replace with |
|---|---|---|
| `yohncf/OCV-Weekly_temp` + `https://yohncf.github.io/OCV-Weekly_temp/` (personal mirror) | `scripts/publish_eval_regression_report.py` (`PERSONAL_LIVE_BASE`); `scripts/create_codegen_ado_bugs.py` (`REPORT`); `seval-run-publish`, `seval-regression`, `seval-regression-publish`, `seval-regression-ticket-sync` SKILL.md | your own mirror repo |
| `gim-home/OCV-Weekly` + `https://gim-home.github.io/OCV-Weekly` (team Pages site) | `scripts/publish_eval_regression_report.py` (`DEFAULT_GITHUB_URL`, `DEFAULT_LIVE_BASE`) | your team's Pages repo |
| `yohncf/ol-agent-pm-skills` (hardcoded monorepo self-reference) | `seval-run-publish/SKILL.md`; git remote of the clone | `yohnathanc_microsoft/ol-agent-pm-skills` (the canonical repo) |
| Real people's emails | `../shared/configs/ado_owners_outlook-agent.json` (git-ignored) | your team's owners map |

## 1. What this project does

`seval-analysis/` handles two things around **SEVAL** (the eval platform at
`seval.microsoft.com`) and its **HeroEval** runs:

1. **Eval-query synthesis** — turn real user utterances (from OCV/Dash CSVs) into generic
   eval queries + assertions.
2. **Run & regression analysis** — fetch run artifacts, triage a single run, compare two
   runs to find regressions, render dark-themed HTML reports, publish them to a GitHub
   Pages site, and file ADO bugs.

There are two related but distinct analysis tracks:

- **Single-run** track: `seval-run-triage`, `seval-run-report`, `seval-run-publish`
  (published under the site's **EVAL Run Analysis** section, `eval-runs/`).
- **Regression (run-vs-run)** track: `seval-fetch-regression-inputs`,
  `seval-regression-analyze`, `seval-regression-publish`, `seval-regression-ticket-sync`,
  orchestrated by `seval-regression` (published under the **EVAL Regressions** section,
  `eval-reports/` → `eval.html`).

Plus `seval-synthesize-queries-from-ocv`, which feeds the eval pipeline upstream.

## 2. How skills relate to scripts

Skills (under `.claude/skills/<name>/SKILL.md`) are the orchestration layer the AI
follows; each wraps one or more scripts under `scripts/`.

| Skill | Underlying script(s) |
|-------|----------------------|
| `seval-fetch-regression-inputs` | `scripts/seval_fetch_regression_inputs.py` |
| `seval-synthesize-queries-from-ocv` | `scripts/synthesize_eval_queries.py` |
| `seval-regression-analyze` | `scripts/eval_regression_extract.py` + `scripts/eval_regression_render.py` |
| `seval-regression-publish` | `scripts/publish_eval_regression_report.py` |
| `seval-regression-ticket-sync` | `scripts/seval_regression_ado_sync.py` (imports `../shared/ado_sync.py`) |
| `seval-regression` | orchestrates the three regression skills |
| `seval-run-triage` | `scripts/seval_run_triage_extract.py` + `scripts/seval_run_triage_render.py` |
| `seval-run-report` | `scripts/eval_single_run_report.py` |
| `seval-run-publish` | `scripts/gen_eval_runs_page.py` + git/mirror commands |

## 3. Prerequisites & authentication

```bash
cd seval-analysis
pip install playwright && python -m playwright install chromium   # SEVAL portal automation
pip install azure-identity                                        # ADO ticket sync
az login                                                          # ADO auth
```

- **SEVAL portal** (`seval.microsoft.com`) automation uses Playwright + Edge with a
  persistent SSO profile at `.browser-profile-seval/` (delete to force re-login).
- **ADO** ticket sync uses `az login` (`DefaultAzureCredential`).
- **Publishing** requires a local clone of the OCV-Weekly Pages repo at
  `_ocv_weekly_repo/` (shared with OCV) and Git credentials, plus the mirror helper
  `../mirror_to_personal_v2.ps1` (dual-mirrors to a personal repo).

## 4. Artifact locations & conventions

- **Downloaded run inputs:** `seval-input/regression_<control-id>_<experiment-id>/`.
- **Manifests / fingerprints:** `data/eval-manifests/`.
- **Rendered reports:** `output/seval/regression/`, `output/seval/triage/`,
  `data/eval-reports/` (all git-ignored — only `.gitkeep` is tracked).
- **HTML reports are always dark-themed and self-contained.**
- **Taxonomy:** regressions/tickets use the same locked **13-topic taxonomy** (+
  `Category`) as OCV `ocv-analyze-and-ticket`.
- **Eval doctrine:** synthesized queries and assertions must follow
  `docs/EVAL_DOCTRINE.md`.
- Run skills/scripts with the project folder as the working directory.

---

# Skill Reference

Each skill below lists: **Purpose · When to use · Inputs · Outputs · Commands ·
Workflow · Dependencies · Gotchas.**

---

## `seval-fetch-regression-inputs`

**Purpose** — Auto-downloads the four artifacts `seval-regression-analyze` needs (two
Assertion-View CSVs + two Settings JSONs) straight from the SEVAL portal, given a single
`regression.json` descriptor listing the two runs. Validates each download by file
extension and renames by run id.

**When to use** — "Fetch/download SEVAL regression inputs," "grab the assertions CSV and
config for runs X and Y," or when you have a `regression.json` with a `sevals[]` array.

**Inputs** — `--input <descriptor JSON>` with **exactly two** `sevals[]` entries, each
having `date`, `queryset`, `control`, `experiment`, `url`; optional `--out-dir`,
`--login-timeout`. Needs Edge + the `.browser-profile-seval/` SSO profile.

**Outputs** — `seval-input/regression_<control-id>_<experiment-id>/` containing
`<control-id>_assertions.csv`, `<control-id>_settings.json`,
`<experiment-id>_assertions.csv`, `<experiment-id>_settings.json`. Also prints a
ready-to-run `seval-regression-analyze` argument block.

**Commands**
```bash
python scripts/seval_fetch_regression_inputs.py --input "C:\path\to\regression.json"
```

**Workflow**
1. Parse the descriptor.
2. Open each run in SEVAL.
3. Wait for the LM Checklist.
4. Switch to Assertion View → download the CSV.
5. Open the JSON config tab → download the JSON.
6. Validate extensions and rename by run id.
7. Print the analyze argument block.

**Dependencies** — SEVAL portal, Playwright + Edge, `.browser-profile-seval/`.

**Gotchas** — Refuses a malformed/missing descriptor or one that isn't exactly two
`sevals[]`. Identical Settings JSONs across runs is expected. Do not commit downloads or
the browser profile.

---

## `seval-synthesize-queries-from-ocv`

**Purpose** — Clusters real user utterances from Dash/OCV CSVs into up to 10 generic eval
queries with assertions and segment labels, following `docs/EVAL_DOCTRINE.md`.

**When to use** — "Create eval queries from feedback," "generate test cases from
utterances," "build a Graph API Gaps YAML," etc.

**Inputs** — 2+ CSV paths (Dash `Utterance` or OCV `PromptInEnglish` columns); desired
count (default 10, max 10); output YAML path (default `data/eval_queries_<YYYY-MM-DD>.yaml`,
with a sibling TSV auto-created).

**Outputs** — A queries YAML + sibling TSV, built via a curated JSON scratch file in
`data/` (deleted after review).

**Commands**
```bash
python scripts/synthesize_eval_queries.py dump <csv1> <csv2> [...]
python scripts/synthesize_eval_queries.py write-yaml data/.synth_curated_<timestamp>.json \
  --out data/eval_queries_<YYYY-MM-DD>.yaml
```

**Workflow**
1. Read the doctrine.
2. Dump/dedupe utterances.
3. Cluster by intent.
4. Draft a query per cluster.
5. Assign a `segment`.
6. Draft assertions.
7. Persist the curated JSON.
8. Emit YAML + TSV.
9. Report back.
10. Delete scratch files.

**Dependencies** — `docs/EVAL_DOCTRINE.md`; consumes the Dash/OCV CSVs produced by the OCV
extraction skills.

**Gotchas** — Max 10 queries. Assertions must obey doctrine and be frontend-evaluable.
`segment` is required. Do not paste raw utterances into other tools. Delete scratch JSON
after review.

---

## `seval-regression-analyze`

**Purpose** — Compares two SEVAL HeroEval runs (control vs experiment). Identifies
per-assertion regressions on **both** sides, authors a `why_failed` explanation per
regression, diffs experiment-side feature flags, and renders a self-contained dark-themed
HTML report with collapsible per-query side-by-side replies.

**When to use** — Two-run compare/diff requests, regression-report generation, "what got
worse?"

**Inputs** — control + experiment CSVs, control + experiment Settings JSONs, control /
experiment names, IDs, dates, and optional highlights.

**Outputs**
- `data/eval-manifests/<YYYY-MM-DD>_<control-id>_vs_<experiment-id>_manifest.json`
- `output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html`

**Commands**
```bash
python scripts/eval_regression_extract.py \
  --control-csv <csv> --experiment-csv <csv> \
  --control-json <json> --experiment-json <json> \
  --control-name <name> --experiment-name <name> \
  --control-id <cid> --experiment-id <eid> \
  --control-date <date> --experiment-date <date> \
  --out data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json

python scripts/eval_regression_render.py \
  --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --out output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html
```

**Workflow**
1. Extract the manifest (compute regressions + flag diff).
2. Author `why_failed` for each regression, in place in the manifest.
3. Render the HTML.

**Dependencies** — SEVAL Settings JSON feature-flag extraction; the renderer uses
DOMPurify/marked.js in the HTML.

**Gotchas** — `why_failed` must be non-empty before publishing. Always state the
denominator. Feature-flag diffs are paired per slot (Mainline/CodeGen). A `--strict` mode
is available on the renderer.

> **Data note:** the SEVAL Assertion-View CSV export is a **single file carrying both
> arms** — columns `query, segment, assertion, level, score_control, rationale_control,
> sydney_reply_control, score_experiment, ...` with `segment`+`level` inline.

---

## `seval-regression-publish`

**Purpose** — Publishes a rendered regression report into the OCV-Weekly GitHub Pages site:
copies it to `eval-reports/`, updates `eval-reports.json`, regenerates the `eval.html`
listing, injects a dropdown into `index.html`, pushes to `origin`, and mirrors to the
personal repo.

**When to use** — "Publish the regression report," "upload the eval analysis," or as the
final step of the `seval-regression` orchestrator.

**Inputs** — The built HTML report; the matching manifest with **all `why_failed` filled**
and `publish_safety.reviewed_for_publish=true`; the local clone `_ocv_weekly_repo/`; Git
credentials; an editorial highlights line.

**Outputs** — `eval-reports/<slug>.html`, updated `eval-reports.json`, regenerated
`eval.html`, injected `index.html`, git commits/push, and a mirrored personal repo.

**Commands**
```bash
# Gate A: preview
python scripts/publish_eval_regression_report.py \
  --manifest data/eval-manifests/<...> --html output/seval/regression/<...>.html \
  --highlights "<line>" --dry-run

# Gate B: apply
python scripts/publish_eval_regression_report.py \
  --manifest data/eval-manifests/<...> --html output/seval/regression/<...>.html \
  --highlights "<line>" --yes

# Mirror
powershell -ExecutionPolicy Bypass -File "..\mirror_to_personal_v2.ps1"
```

**Workflow**
1. Ask for the highlights line.
2. Dry-run preview + **Gate A** confirmation.
3. Apply with `--yes`.
4. Update the manifest/listing/nav.
5. Push to gim-home.
6. Mirror to the personal clone.

**Dependencies** — `_ocv_weekly_repo/`, `mirror_to_personal_v2.ps1`, GitHub Pages, Git
credentials.

**Gotchas** — Two-gate confirmation. Refuses blank `why_failed` or a stale HTML report.
Idempotent. Owns the shared dropdown nav. `--new-version` can suffix duplicates.

---

## `seval-regression-ticket-sync`

**Purpose** — Files one ADO **Bug** per `(failing_side, topic, category)` cluster from a
regression manifest. Always net-new (never links). Tags `OutlookAgent` + `SevalRegression`
and auto-assigns via the shared owners config.

**When to use** — "File bugs / sync regressions to ADO," or the optional final step of the
`seval-regression` pipeline.

**Inputs** — The regression manifest; the matching published report URL; `az login`;
`azure-identity` installed; the manifest must have non-empty `why_failed` and
`reviewed_for_publish=true`.

**Outputs** — `data/eval-ado/<slug>_classifications.json`,
`data/eval-ado/<slug>_proposals.json`, and created ADO Bugs.

**Commands**
```bash
python scripts/seval_regression_ado_sync.py classify-template --manifest <json> --out <json> [--selection ...]
python scripts/seval_regression_ado_sync.py propose --manifest <json> --classifications <json> \
  --report-url ".../eval-reports/<slug>.html" --out <json> [--selection ...]
python scripts/seval_regression_ado_sync.py execute --proposals <json>
python scripts/seval_regression_ado_sync.py execute --proposals <json> --dry-run
python scripts/seval_regression_ado_sync.py execute --proposals <json> --yes
```

**Workflow**
1. `classify-template`.
2. Human/agent fills the taxonomy fields.
3. `propose`.
4. **Gate A** review.
5. **Gate B** write-count confirmation.
6. `execute` to create ADO bugs.

**Dependencies** — `../shared/ado_sync.py`,
`../shared/configs/ado_owners_outlook-agent.json`, Azure DevOps REST, `az login`.

**Gotchas** — Always create, never link. `P1` if any critical assertion is involved. Cap
25 assertions inline (overflow links to the HTML). Optional `--cluster-by theme`,
`--split-by-tool`, `--merge-sides`, and `--selection` to prefilter rows.

---

## `seval-regression` (orchestrator)

**Purpose** — Runs the end-to-end regression pipeline: `seval-regression-analyze` → a
mandatory user-review pause → optional `seval-regression-publish` → optional
`seval-regression-ticket-sync`.

**When to use** — "Full regression pipeline," "analyze + publish + file bugs."

**Inputs** — control/experiment CSVs, Settings JSONs, names, IDs, dates, optional
highlights.

**Outputs** — the manifest + HTML (always), optional published Pages URLs, optional ADO
bugs.

**Commands** — Delegates to the three sub-skills; internally calls
`eval_regression_extract.py` and `eval_regression_render.py`.

**Workflow**
1. Print the plan.
2. Run analyze.
3. Pause for review, then ask "publish?"
4. If yes, publish.
5. Pause, then ask "file ADO bugs?"
6. If yes, file bugs.

**Dependencies** — all three regression sub-skills; the publish URL is required before ADO
filing; shared ADO owners config via ticket-sync.

**Gotchas** — The review pauses are non-negotiable. Can stop after analyze or after
publish. Set `reviewed_for_publish=true` only when the user approves publishing.

---

## `seval-run-triage`

**Purpose** — Single-run failure triage. Joins the four SEVAL run artifacts (Assertions
CSV, Queries TSV, assertion-doctrine YAML, Settings JSON), enriches each row with `level`
(critical/expected/aspirational) and `segment`, and classifies every failed assertion into
one of four root-cause families: missing data / assertion / agent performance / model.
Emits a diffable fingerprint manifest + a PM-voice markdown summary + a dark HTML report.

**When to use** — Analyze a single run, classify why it failed, or produce a fingerprint
for later diffing. Pair with `seval-regression-analyze` for run-vs-run comparison.

**Inputs** — A run folder containing `[Assertions]_*.csv`, `*.tsv`, `*.yaml`,
`Settings.json`; plus run ID, name, date, and `--arm`.

**Outputs**
- `data/eval-manifests/<run-id>_<date>_fingerprint.json`
- `output/seval/triage/seval_triage_<run-id>_<date>.md`
- `output/seval/triage/seval_triage_<run-id>_<date>.html`

**Commands**
```bash
python scripts/seval_run_triage_extract.py --run-folder <dir> \
  --run-id <id> --run-name <name> --run-date <date> --arm both \
  --out data/eval-manifests/<run-id>_<date>_fingerprint.json

python scripts/seval_run_triage_render.py --manifest <json> \
  --out-md output/seval/triage/seval_triage_<run-id>_<date>.md \
  --out-html output/seval/triage/seval_triage_<run-id>_<date>.html
```

**Workflow**
1. Detect artifacts.
2. Load the doctrine YAML.
3. Load the TSV as the authoritative segment/query_hash source.
4. Join the CSV.
5. Load feature flags from `Settings.json`.
6. Compute per-arm summaries.
7. Emit failures.
8. Author `failure_label` / `failure_evidence`.
9. Render the HTML.

**Dependencies** — can later be diffed by the regression tooling; optionally published via
`seval-run-publish`.

**Gotchas** — There's a cross-arm guardrail for missing-data labels. `Unclear` >10%
surfaces a caveat. `failure_evidence` must be concise PM voice. Use `--strict` before
sharing.

---

## `seval-run-report`

**Purpose** — Renders a dark-themed, self-contained HTML report from the **new unified
single-run JSON** (`eval_report_<name>_<DD_MM_YYYY_HHMM>.json`) the eval team now emits —
one file bundling `summary` aggregates + every per-query result (judge rationales,
dimensions, generated code, execution result, perf). One deterministic script, no artifact
joining.

**When to use** — "Create a report from this eval," "render this eval JSON," "standalone
eval report."

**Inputs** — One `eval_report_<name>_<DD_MM_YYYY_HHMM>.json`; optional `--out`,
`--run-name`, `--run-date`.

**Outputs** — Default `data/eval-reports/<input-basename>.html` (or the `--out` path).

**Commands**
```bash
python scripts/eval_single_run_report.py \
  --in "<path>\eval_report_<name>_<DD_MM_YYYY_HHMM>.json" \
  [--out data/eval-reports/<name>.html] \
  [--run-name "Friendly label"] [--run-date 2026-06-22]
```

**Workflow**
1. Parse the unified JSON.
2. Render KPI cards / level breakdown / dimensions / per-query list (sorted worst-first,
   with dimension pills and nested code/result dropdowns).
3. Validate the numbers against `summary`.
4. Hand off to `seval-run-publish` if publishing.

**Dependencies** — hand-off to `seval-run-publish`; no external services for rendering.

**Gotchas** — `results` is a **dict keyed by evaluator**, not a list. The script is
deterministic and stdlib-only. Output is dark-themed and self-contained.

---

## `seval-run-publish`

**Purpose** — Publishes a **standalone single-run** study (failure triage, head-to-head arm
comparison, or capability deep-dive) into the **EVAL Run Analysis** section of the
OCV-Weekly site (`eval-runs/` + a generated `eval-runs.html` listing + `eval-runs.json`
manifest), then mirrors to the personal repo. It's the sibling of
`seval-regression-publish` — a separate, third nav section.

**When to use** — "Publish this run analysis," "add to the eval runs list," "upload the
head-to-head/failure-triage report."

**Inputs** — A self-contained HTML report; a slug; the local `_ocv_weekly_repo/` clone; the
report already built.

**Outputs** — `eval-runs/<slug>.html`, updated `eval-runs.json`, generated
`eval-runs.html`, a git push + mirror.

**Commands**
```bash
python ../seval-analysis/scripts/gen_eval_runs_page.py --repo <path-to-_ocv_weekly_repo>
Copy-Item <report.html> "$repo\eval-runs\<slug>.html" -Force
git -C $repo add eval-runs.json eval-runs.html "eval-runs/<slug>.html"
git -C $repo commit -m "eval-runs: publish <date> <topic>…"
git -C $repo pull --rebase origin main
git -C $repo push origin main
powershell -ExecutionPolicy Bypass -File "C:\…\OLAgentWork\mirror_to_personal_v2.ps1"
```

**Workflow**
1. Confirm the report + slug.
2. Stage files.
3. **Gate A** plan/confirm.
4. Push to gim-home.
5. Mirror.
6. Optionally commit generator changes in the monorepo separately.

**Dependencies** — `_ocv_weekly_repo/`, `mirror_to_personal_v2.ps1`, GitHub Pages, the
generator script in the monorepo.

**Gotchas** — Separate nav section from regression publish. Two-gate confirmation. Changes
to the generator script must be committed to the monorepo separately.

---

## 5. Scripts index

| Script | Powers |
|--------|--------|
| `scripts/seval_fetch_regression_inputs.py` | `seval-fetch-regression-inputs` |
| `scripts/synthesize_eval_queries.py` | `seval-synthesize-queries-from-ocv` |
| `scripts/eval_regression_extract.py` | `seval-regression-analyze` (compute regressions + flag diff → manifest) |
| `scripts/eval_regression_render.py` | `seval-regression-analyze` (manifest → HTML) |
| `scripts/publish_eval_regression_report.py` | `seval-regression-publish` |
| `scripts/gen_eval_runs_page.py` | `seval-run-publish` (regenerate `eval-runs.html` from `eval-runs.json`) |
| `scripts/seval_regression_ado_sync.py` | `seval-regression-ticket-sync` (imports `../shared/ado_sync.py`) |
| `scripts/seval_run_triage_extract.py` | `seval-run-triage` (join run artifacts → fingerprint manifest) |
| `scripts/seval_run_triage_render.py` | `seval-run-triage` (manifest → markdown + HTML) |
| `scripts/eval_single_run_report.py` | `seval-run-report` (unified `eval_report_*.json` → HTML) |

## 6. Shared dependencies (`../shared/`)

- **`../shared/ado_sync.py`** — `seval_regression_ado_sync.py` adds `../../shared` to
  `sys.path` and imports its auth + owner-routing helpers.
- **`../shared/configs/ado_owners_outlook-agent.json`** — the single owners-routing config
  covering both this project's ticket-sync and OCV's `ocv-ticket-sync`. **Git-ignored /
  user-local** — create it yourself.
- **OCV CSV output** — `seval-synthesize-queries-from-ocv` consumes the Dash/OCV CSVs
  produced by the OCV extraction skills.
- **OCV-Weekly site** — `seval-regression-publish` (regressions → `eval.html`) and
  `seval-run-publish` (single-run studies → `eval-runs.html`) publish alongside the OCV
  weekly reports on the same GitHub Pages site.

## 7. Two pipelines at a glance

```
REGRESSION (run vs run)
  regression.json
      └─ seval-fetch-regression-inputs → 2 CSVs + 2 Settings JSONs
             └─ seval-regression-analyze → manifest + HTML   (author why_failed)
                    ├─ seval-regression-publish → eval-reports/ + eval.html
                    └─ seval-regression-ticket-sync → 1 ADO Bug per cluster
  (all wrapped by the seval-regression orchestrator, with review pauses)

SINGLE RUN
  run artifacts (CSV/TSV/YAML/Settings)      OR     unified eval_report_*.json
      └─ seval-run-triage → fingerprint + MD + HTML      └─ seval-run-report → HTML
             └──────────────── seval-run-publish → eval-runs/ + eval-runs.html ───────────┘

UPSTREAM
  OCV/Dash CSVs → seval-synthesize-queries-from-ocv → eval queries YAML/TSV (per EVAL_DOCTRINE.md)
```

## 8. Where to look next

- `AGENTS.md` — authoritative project instructions (skill + script tables, conventions).
- `README.md` — user-facing overview.
- `docs/EVAL_DOCTRINE.md` — the doctrine synthesized queries/assertions must follow.
- `../AGENTS.md` and `../shared/README.md` — cross-project map and shared code.

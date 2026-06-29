# SEVAL Analysis — Project Instructions

SEVAL eval-query synthesis and HeroEval regression analysis/ticketing. One of
two sibling projects under the repo root (**OLAgentWork**); see the
[root `AGENTS.md`](../AGENTS.md) for the cross-project map and the
[`shared/`](../shared/README.md) folder it depends on.

This project grew out of [`../ocv-extraction`](../ocv-extraction/AGENTS.md) and
reuses its ADO sync engine, owners-routing config, and 13-topic taxonomy.

## Skills

| Skill | What it does |
|-------|-------------|
| `seval-fetch-regression-inputs` | Auto-download the two assertions CSVs + two Settings JSONs that `seval-regression-analyze` needs, straight from the SEVAL portal, given a single `regression.json` descriptor listing the two runs. Drives seval.microsoft.com (Playwright + Edge, persistent SSO profile): LM Checklist → Assertion View → download CSV, then JSON config → download JSON; validates every download by file extension and renames by run id. Prints the ready-to-run analyze arg block |
| `seval-synthesize-queries-from-ocv` | Cluster real user utterances from Dash/OCV CSVs into up to 10 generic eval queries + assertions (YAML), following `docs/EVAL_DOCTRINE.md` |
| `seval-regression-analyze` | Compare two SEVAL HeroEval runs (control vs experiment CSVs + Settings JSONs), identify per-assertion regressions on both sides, diff experiment-side feature flags, and render a self-contained dark-themed HTML report with collapsible per-query side-by-side replies |
| `seval-regression-publish` | Publish a rendered SEVAL regression report into the OCV-Weekly GitHub Pages site (`eval-reports/` + auto-managed `eval.html` listing), inject a dropdown into `index.html`, and push (dual-mirror via `origin`). Two-gate confirmation |
| `seval-run-publish` | Publish a **standalone single-run** SEVAL study (failure triage, head-to-head arm comparison, capability deep-dive) into the **EVAL Run Analysis** section of the OCV-Weekly site (`eval-runs/` + generated `eval-runs.html` listing, `eval-runs.json` manifest). Sibling of `seval-regression-publish`; third nav section. Two-gate confirmation + auto-mirror |
| `seval-regression-ticket-sync` | File one ADO Bug per `(failing_side, topic, category)` cluster from a regression manifest. Always net-new (never links). Tags `OutlookAgent` + `SevalRegression`, auto-assigned via the shared owners config. Two-gate confirmation |
| `seval-regression` | **Orchestrator.** Runs `seval-regression-analyze` → `seval-regression-publish` → optional `seval-regression-ticket-sync`, pausing for user confirmation between steps |
| `seval-run-triage` | Single-run failure triage. Joins the four SEVAL run artifacts (Assertions CSV, Queries TSV, Assertion-doctrine YAML, Settings JSON), enriches each row with `level` (critical/expected/aspirational) and `segment`, and classifies every failed assertion into one of four root-cause families (missing data / assertion / agent performance / model). Emits a diffable fingerprint manifest + PM-voice markdown summary + dark-themed HTML report. Pair with `seval-regression-analyze` for run-vs-run comparison |
| `seval-run-report` | Render a dark-themed self-contained HTML report from the **new unified single-run JSON** (`eval_report_<name>_<DD_MM_YYYY_HHMM>.json`) the eval team now emits — one file bundling `summary` aggregates + every per-query result (assertion judge rationales, dimensions, generated code, execution result, perf). One deterministic script, no artifact joining. Merged filterable/collapsible per-query list (sorted worst-first, dimension pills on each row, nested code/result dropdowns). Hand off to `seval-run-publish` to put it on the site |

## Scripts

| Script | Powers |
|--------|--------|
| `scripts/seval_fetch_regression_inputs.py` | `seval-fetch-regression-inputs` (descriptor JSON → 2 CSVs + 2 JSONs via Playwright/Edge) |
| `scripts/synthesize_eval_queries.py` | `seval-synthesize-queries-from-ocv` |
| `scripts/eval_regression_extract.py` | `seval-regression-analyze` (compute regressions + flag diff → manifest) |
| `scripts/eval_regression_render.py` | `seval-regression-analyze` (manifest → HTML) |
| `scripts/publish_eval_regression_report.py` | `seval-regression-publish` |
| `scripts/gen_eval_runs_page.py` | `seval-run-publish` (regenerate the `eval-runs.html` listing from `eval-runs.json`; flexible `metrics`/`tags` schema) |
| `scripts/seval_regression_ado_sync.py` | `seval-regression-ticket-sync` (imports `../shared/ado_sync.py`) |
| `scripts/seval_run_triage_extract.py` | `seval-run-triage` (join run artifacts → fingerprint manifest) |
| `scripts/seval_run_triage_render.py` | `seval-run-triage` (manifest → markdown + HTML) |
| `scripts/eval_single_run_report.py` | `seval-run-report` (new unified single-run `eval_report_*.json` → dark-themed self-contained HTML) |

## Shared dependencies

- **`../shared/ado_sync.py`** — `seval_regression_ado_sync.py` adds `../../shared`
  to `sys.path` and imports its auth + owner-routing helpers. A single owners
  config (`../shared/configs/ado_owners_outlook-agent.json`) covers both this
  skill and OCV's `ocv-ticket-sync`.
- **OCV CSV output** — `seval-synthesize-queries-from-ocv` consumes the Dash/OCV
  CSVs produced by the OCV extraction skills.
- **OCV-Weekly site** — `seval-regression-publish` (regressions → `eval.html`)
  and `seval-run-publish` (single-run studies → `eval-runs.html`) publish
  alongside the OCV weekly reports on the same GitHub Pages site.

## Conventions

- **HTML reports use a dark theme** — the regression report and the
  auto-generated `eval.html` listing must default to dark mode.
- **Taxonomy** — regressions are classified into the same fixed 13-topic
  taxonomy (+ `Category`) used by OCV `ocv-analyze-and-ticket`.
- **Runtime artifacts** — manifests under `data/eval-manifests/`, rendered
  reports under `data/eval-reports/` (both git-ignored).
- **Eval doctrine** — synthesized queries must follow `docs/EVAL_DOCTRINE.md`.

## Data

Runtime artifacts in `data/` are git-ignored; only `.gitkeep` placeholders are
tracked. Run skills/scripts with this folder as the working directory.

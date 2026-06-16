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
| `seval-synthesize-queries-from-ocv` | Cluster real user utterances from Dash/OCV CSVs into up to 10 generic eval queries + assertions (YAML), following `docs/EVAL_DOCTRINE.md` |
| `seval-regression-analyze` | Compare two SEVAL HeroEval runs (control vs experiment CSVs + Settings JSONs), identify per-assertion regressions on both sides, diff experiment-side feature flags, and render a self-contained dark-themed HTML report with collapsible per-query side-by-side replies |
| `seval-regression-publish` | Publish a rendered SEVAL regression report into the OCV-Weekly GitHub Pages site (`eval-reports/` + auto-managed `eval.html` listing), inject a dropdown into `index.html`, and push (dual-mirror via `origin`). Two-gate confirmation |
| `seval-regression-ticket-sync` | File one ADO Bug per `(failing_side, topic, category)` cluster from a regression manifest. Always net-new (never links). Tags `OutlookAgent` + `SevalRegression`, auto-assigned via the shared owners config. Two-gate confirmation |
| `seval-regression` | **Orchestrator.** Runs `seval-regression-analyze` → `seval-regression-publish` → optional `seval-regression-ticket-sync`, pausing for user confirmation between steps |

## Scripts

| Script | Powers |
|--------|--------|
| `scripts/synthesize_eval_queries.py` | `seval-synthesize-queries-from-ocv` |
| `scripts/eval_regression_extract.py` | `seval-regression-analyze` (compute regressions + flag diff → manifest) |
| `scripts/eval_regression_render.py` | `seval-regression-analyze` (manifest → HTML) |
| `scripts/publish_eval_regression_report.py` | `seval-regression-publish` |
| `scripts/seval_regression_ado_sync.py` | `seval-regression-ticket-sync` (imports `../shared/ado_sync.py`) |

## Shared dependencies

- **`../shared/ado_sync.py`** — `seval_regression_ado_sync.py` adds `../../shared`
  to `sys.path` and imports its auth + owner-routing helpers. A single owners
  config (`../shared/configs/ado_owners_outlook-agent.json`) covers both this
  skill and OCV's `ocv-ticket-sync`.
- **OCV CSV output** — `seval-synthesize-queries-from-ocv` consumes the Dash/OCV
  CSVs produced by the OCV extraction skills.
- **OCV-Weekly site** — `seval-regression-publish` publishes alongside the OCV
  weekly reports on the same GitHub Pages site.

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

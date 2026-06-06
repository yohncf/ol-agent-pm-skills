# OCV Extraction — Copilot Instructions

> **Authoritative reference:** `AGENTS.md` at the repo root. Read it before any non-trivial change — it contains the data-use compliance policy, skill catalog, analysis tone rules, and `FHL_JOURNEY.md` update protocol that all sessions must follow.

## Critical compliance rule

This repo processes **Customer Content** (OCV verbatim feedback, ODS tickets). Per E+D Data Use Guidance (March 2026), only AI tools backed by AOAI or Anthropic models via GitHub Copilot may analyze this data. GitHub Copilot CLI is approved; Claude Code is **not**. Do not run extraction or analysis skills against `data/` from a non-approved tool.

## Commands

| Task | Command |
|---|---|
| Verify environment | `npm run check` (runs `scripts/preflight.js` — Node version, Playwright, Edge, configs, output dirs) |
| Extract OCV (yesterday, Accounts) | `npm run extract:accounts` |
| Extract OCV (last 7 days, Accounts) | `npm run extract:7d` |
| Extract OCV (manual) | `node scripts/extract_standalone.js --config configs/<area>.json --date <yesterday\|7d\|30d\|YYYY-MM-DD:YYYY-MM-DD> --summary` |
| Extract Copilot Dash → OCV join | `npm run extract:dash` (or `python scripts/dash_ocv_extract.py --date <yesterday\|7d\|YYYY-MM-DD>`) |
| Extract ODS tickets (preferred, API) | `python scripts/ods_api_extract.py --input data/ticket_urls.csv [--fields all] [--offset N --append]` |
| Sample ODS URLs from Kusto | `python scripts/generate_sample_urls.py --sample-size 600 --days 30` |
| Delete CSVs after analysis | `npm run cleanup` (supports `--all-manifests`, `--manifest <path>`) |

There is no test suite or linter. Validation = run `npm run check` and exercise the affected script end-to-end with `--summary` (no customer content in summary output).

**Auth:** OCV uses a Playwright + Edge persistent profile under `.browser-profile/` (delete the folder to force re-login). ODS API uses `DefaultAzureCredential` via `az login`. Dash uses `.browser-profile-dash/`.

## Architecture (the big picture)

Pipeline: **Extract → CSV (temporary) → Analyze → Manifest JSON (permanent) → Delete CSV**. CSVs in `data/` are ephemeral and git-ignored; manifests in `data/manifests/` are aggregate-only (no raw customer text).

Three independent extractors share `scripts/lib/` (`csv_parser.js` RFC 4180 parser, `manifest_writer.js`, `pii_scrub.py`):

- `scripts/extract_standalone.js` — OCV via Playwright-captured bearer token + Elasticsearch API. Config-driven (`configs/<area>.json`).
- `scripts/dash_ocv_extract.py` — Walks the Copilot Dash UI, captures `/api/v2/tickets/search`, then per-ticket `/api/v2/tickets/{id}` to join Dash ticket ↔ OCV submission/utterance/verbatim/response.
- `scripts/ods_api_extract.py` — Direct ODS REST (~0.9 tickets/sec, no browser). `ods_batch_extract.js` is the legacy browser fallback.

Skills under `.claude/skills/` (e.g., `ocv-extract-feedback`, `ocv-extract-ods`, `ocv-extract-dash`, `ocv-analyze`, `ocv-analyze-and-ticket`, `ocv-weekly`, `ocv-ticket-sync`, `ocv-publish-report`, `ocv-publish-github`, `seval-synthesize-queries-from-ocv`, `seval-regression-analyze`, `seval-regression-publish`, `seval-regression`, `seval-regression-ticket-sync`, `ocv-setup`) are the orchestration layer the AI calls; they wrap the scripts above. `ocv-weekly` is the end-to-end OCV pipeline that pauses after the subtopics CSV for human review before publishing. `seval-regression` is the end-to-end EVAL-side pipeline that compares two HeroEval runs, lets the agent author per-row why-failed sentences during a review pause, publishes a dark-themed HTML report into `eval-reports/` on the OCV-Weekly site, then optionally hands off to `seval-regression-ticket-sync` to file one ADO Bug per `(failing_side, topic, category)` cluster (always net-new, tagged `OutlookAgent` + `SevalRegression`, auto-assigned via the same owners config as the `ocv-ticket-sync` skill). Skills are backed by these supporting scripts: `scripts/ado_sync.py` (ocv-ticket-sync), `scripts/seval_regression_ado_sync.py` (seval-regression-ticket-sync), `scripts/publish_ocv_report.py` (ocv-publish-report), `scripts/publish_to_github.py` (ocv-publish-github), `scripts/synthesize_eval_queries.py` (seval-synthesize-queries-from-ocv), `scripts/eval_regression_extract.py` + `scripts/eval_regression_render.py` (seval-regression-analyze), `scripts/publish_eval_regression_report.py` (seval-regression-publish).

Configs (`configs/<area>.json`) drive each extraction: `ocv_url`, regex-based `categories` (with `match` / `require` / `exclude` keywords, first match wins), `feature_tags` allow-list, `noise_patterns`, and optional `entity` block for ISP/provider identification against `scripts/isp_whitelist.json`.

## Conventions

- **Output filenames:** Daily = `data/ocv_<area>_YYYY-MM-DD.csv`. Ad-hoc = `data/ocv_<area>_YYYY-MM-DD_<filter>.csv`. Dash = `data/dash_ocv_<range>.csv`. Manifests = `data/manifests/<name>_manifest.json`.
- **PII:** Personal info is scrubbed at write time (`scripts/lib/pii_scrub.py`). Only ~55 whitelisted ISP domains survive; everything else is redacted to the configured `redacted_label`.
- **Config gitignore:** `configs/*.json` is git-ignored except `_template.json` and `accounts.json`. New area configs stay local.
- **Living doc:** Non-trivial changes to extraction behavior must be reflected in `docs/FHL_JOURNEY.md` (append to the relevant section, add a row to "Problems We Solved" if applicable). Skip for typos/refactors with no user-facing impact.
- **Analysis voice:** When producing any OCV/ODS analysis output, write as a PM — quantify don't editorialize, neutral framing (no "crisis"/"hostile"/"churn risk"), paraphrase in PM voice not user voice, and acknowledge sampling bias (self-selected frustrated users, <1% MAU). See `AGENTS.md` § "Analysis Tone and Voice" for the full rubric.
- **HTML reports use a dark theme.** The `ocv-publish-report` skill, the `seval-regression-analyze` HTML output, the auto-generated `eval.html` listing page, and any ad-hoc HTML must default to a dark color scheme. Do not produce light-mode output.
- **`--summary` is safe to share:** It prints aggregate counts only and never echoes customer content.

---
name: ocv-extract-feedback
description: >
  Extract OCV verbatim feedback to CSV. Use when the user asks to extract, pull,
  or get OCV feedback data. Runs the standalone extraction script with date
  filtering, PII scrubbing, and config-driven categorization.
  Do NOT use for Copilot Dash tickets — use `ocv-extract-dash`. Do NOT use for
  ODS/Sara tickets — use `ocv-extract-ods`.
---

# OCV Extraction Command Generator

Generate and run the extraction command for OCV verbatim feedback.

## Parsing the user's request

Extract two values from the user's prompt:

- **date-filter**: `today`, `yesterday`, `7d`, `14d`, `30d`, `3m`, `6m`, `all`, or a custom range like `2026-02-20:2026-02-24`. Defaults to `yesterday` if not specified.
- **area-name**: config filename without extension from `configs/` (e.g., `accounts`, `attachments`, `monarch-all`). If not specified, list available configs in `configs/` (exclude `_template.json`) and ask the user to choose.

## What to do

1. Parse the user's prompt for date-filter and area-name.

2. **Resolve the project root.** The OCV extraction project lives in the `ocv-extraction/` directory. All paths below are relative to that directory. When running commands, always `cd` into the project root first.

3. Resolve the config file: `configs/<area-name>.json` (inside the project root). If it doesn't exist, list available configs in `configs/` (exclude `_template.json`) and ask the user to choose.

4. Determine the output filename:
   - For `yesterday`: `data/ocv_<area>_<YYYY-MM-DD>.csv` (yesterday's date)
   - For `today`: `data/ocv_<area>_<YYYY-MM-DD>.csv` (today's date)
   - For other presets (`7d`, `30d`, etc.): `data/ocv_<area>_<YYYY-MM-DD>_<filter>.csv` (today's date + filter suffix)
   - For custom ranges: `data/ocv_<area>_<YYYY-MM-DD>_range.csv`

5. Run the extraction command:

   ```
   cd <project-root> && node scripts/extract_standalone.js --config "configs/<area-name>.json" --date <filter> --summary "data/<output-file>"
   ```

   **Optional flags:**
   - `--include-structured` — includes feedback submissions without verbatim text (thumbs up/down only). By default, these are excluded. Use this flag when you need sentiment metrics comparable to the OCV Discover dashboard.
   - `--no-cleanup` — skips the interactive CSV cleanup prompt after extraction. Use this when running non-interactively.

6. **If the extraction fails** (browser timeout, SSO redirect stuck, page didn't load, or 0 items returned), automatically retry:
   - Delete the browser profile at `<project-root>/.browser-profile/` (PowerShell: `Remove-Item -Recurse -Force <project-root>/.browser-profile`; bash: `rm -rf <project-root>/.browser-profile`)
   - Rerun the same extraction command
   - Tell the user: "Cleared stale browser cache and retrying. Complete SSO login on the first tab."
   - Only retry once. If it fails again, report the error.

7. After the command completes, tell the user:
   - The summary stats printed by `--summary` (counts, sentiment/intent/category breakdowns)
   - The output CSV path for detailed review in Excel
   - First run: Edge opens for SSO login. Subsequent runs are automatic.
   - **Remind**: "The CSV contains raw customer content. Run the `ocv-analyze` skill to produce a manifest, then clean up the CSV with `node scripts/cleanup_csvs.js`."

## Data lifecycle

CSVs are **temporary artifacts** — they exist to support analysis and should be cleaned up afterward.

```
Extract → CSV (temporary, contains customer content)
   ↓
Analyze → Manifest JSON (permanent, no customer content)
   ↓         + PPTX report (generated before cleanup)
   ↓         + Markdown summary
Cleanup → Delete CSV (prompted, updates manifest)
```

The manifest at `data/manifests/<name>_manifest.json` preserves all analytical value (themes, counts, OcvId pointers, AI paraphrases, aggregate stats) without retaining verbatim text. Use the `ocv-analyze` skill to generate it.

## CSV Columns

| Column | Description |
|--------|-------------|
| Date | When the feedback was submitted |
| Comment | Verbatim customer text (PII scrubbed) |
| Provider | ISP name from whitelist, or empty |
| Sentiment | Positive / Negative / Neutral |
| Intent | Problem / Request / Compliment / Unknown |
| Feature | OCV tags filtered to the area's whitelist |
| Category | Issue bucket matched by keyword patterns |
| Language | Language code (en, de, fr, etc.) |
| Noise | "true" if matched a noise pattern; empty otherwise |
| AreaPath | OCV area hierarchy (pipe-delimited) |
| Audience | `Internal` (Microsoft employees) or `External` |

## COMPLIANCE

This skill processes **Customer Content**. Only use with **GitHub Copilot CLI** (backed by AOAI/Anthropic models). Do not use with Claude Code.

OCV verbatim feedback is Customer Content per E+D Data Use Guidance (March 2026). When running in Copilot CLI, you may analyze extracted data. Only report aggregate summary stats printed by `--summary` unless the user explicitly asks for verbatim analysis.

## Context: Where OCV fits in the feedback ecosystem

OCV captures **passive qualitative feedback** — what users voluntarily share about their experience. Key sources:

| Source | Volume | What it captures |
|--------|--------|------------------|
| Send a Smile / Frown | Largest | Free-text feedback with sentiment |
| Copilot thumbs up/down | Growing | Copilot response quality signals |
| NPS / Floodgate | Periodic | Prompted satisfaction scores + optional verbatim |
| App Store reviews | External | Public reviews from Microsoft Store, Google Play |
| Feedback Hub / Forums | Community | Structured bug reports and feature requests |

**Coverage:** OCV captures <1% of MAU. It's the richest qualitative signal but represents self-selected, vocal users (skews toward dissatisfied). Complement with telemetry for quantitative reach.

**Companion channel:** ODS captures the **support ticket path** — users who actively seek help. The two channels are linked via `DiagnosticSessionId`, enabling cross-referencing between feedback and support data. Use the `ocv-extract-ods` skill for ODS data.

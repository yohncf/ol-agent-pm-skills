# OCV Extraction — Skills  Guide

A complete, guide to every skill in `ocv-extraction/`, written so that
**anyone can pick up this work and replicate it**. Read this end-to-end once, then use
the per-skill sections as a reference.

> ⚠️ **Approved AI tool: GitHub Copilot CLI only.** This project processes **Customer
> Content** (OCV verbatim feedback, ODS ticket data). Per E+D Data Use Guidance
> (March 2026), only AI tools backed by AOAI or Anthropic models via GitHub Copilot may
> analyze that data. **Claude Code is not approved.** Do not run extraction or analysis
> skills against `data/` from a non-approved tool.

---

## ⚡ Quick start (60 seconds)

**1. Get the code** (one clone — the whole monorepo):

```bash
git clone https://github.com/yohnathanc_microsoft/ol-agent-pm-skills.git OLAgentWork
cd OLAgentWork/ocv-extraction
npm install && npm run check     # installs deps + preflight-checks Node/Playwright/Edge/configs
```

**2. Launch the agent** — open a terminal in `ocv-extraction/` and start GitHub Copilot CLI
(the **only** approved tool for this data):

```bash
copilot
```

First run of any browser skill (OCV/Dash) opens Edge for SSO; ODS/ADO auth is `az login`.

**3. Just ask.** The skills are invoked in plain English — you don't call scripts directly.
Copy/paste one of these prompts (swap in your dates/area):

| Goal | Prompt to paste into Copilot CLI |
|------|----------------------------------|
| **Full weekly pipeline** (extract → analyze → subtopics → dashboard, pauses for review) | `Run a full OCV/Dash pipeline for the dates 2026-06-30 to 2026-07-06.` |
| Quick themed read of recent feedback | `Extract Accounts OCV feedback for the last 7 days and give me a themed summary.` |
| Classify + build engineering tickets | `Analyze this week's OCV, classify it into the 13-topic taxonomy, and generate the P0–P3 subtopics CSV.` |
| Push tickets to ADO | `Sync the P0/P1/P2 rows from the latest subtopics CSV to ADO work items.` |
| Publish + announce | `Publish the latest OCV weekly report to GitHub Pages and draft the leadership email.` |
| Just the Dash→OCV join | `Extract the Copilot Dash feedback joined to OCV for 2026-06-30 to 2026-07-06.` |

> **Tip:** `Run a full OCV/Dash pipeline for the dates …` maps to the `ocv-weekly`
> orchestrator, which stops once after the subtopics CSV so you can review/edit before it
> publishes. Everything below is the detailed reference for when you want finer control.

For the full download/setup/auth details see §0, §3; for the taxonomy see §4.

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
`shared/` · `ocv-extraction/scripts/` · `ocv-extraction/.claude/skills/` ·
`ocv-extraction/configs/` (templates only) · `ocv-extraction/docs/`.

> **⚠️ The OCV-Weekly Pages repos are NOT the source of the skills.**
> `gim-home/OCV-Weekly` and `yohncf/OCV-Weekly_temp` contain only the **published HTML
> report output** (the website). They hold no scripts or skills. You clone one of them
> separately into `_ocv_weekly_repo/` **only as a publish target** (see §3 / §5).

### Backup status (verified 2026-07-07)

- Committed skills/scripts on `main` **are** pushed to the canonical repo
  `yohnathanc_microsoft/ol-agent-pm-skills` (also mirrored to `yohncf/ol-agent-pm-skills`).
- **Not yet backed up at handover time** (uncommitted / untracked) — **commit & push
  these before handing off**, or they are lost:
  - Modified, uncommitted: `ocv-analyze-and-ticket/SKILL.md`, `ocv-draft-email/SKILL.md`,
    `scripts/draft_ocv_email.py`.
  - New, untracked scripts (in `../seval-analysis/scripts/`): `codegen_failure_extract.py`,
    `codegen_failure_render.py`, `create_codegen_ado_bugs.py`,
    `update_codegen_ado_descriptions.py`.
  - These two `SKILLS_HANDOVER.md` files.

## 0b. ⚠️ Hardcoded personal references — change these before reuse

The scripts contain **no credentials, tokens, or PATs**. All authentication is acquired at
runtime: `az login` (ADO/ODS), Windows Credential Manager (git push), and captured bearer
tokens (OCV/Dash). Nothing to rotate.

**However, the author's GitHub identity and target repos are hardcoded as defaults.**
Anyone reusing this must replace them, or publishing will push to the wrong place:

| Hardcoded value | Where | Replace with |
|---|---|---|
| `yohncf/OCV-Weekly_temp` + `https://yohncf.github.io/OCV-Weekly_temp/` (personal mirror) | `scripts/draft_ocv_email.py` (`DEFAULT_MIRROR_URL`); `ocv-draft-email/SKILL.md` | your own mirror repo, or pass `--mirror-url ""` to skip |
| `gim-home/OCV-Weekly` + `https://gim-home.github.io/OCV-Weekly` (team Pages site) | `scripts/publish_to_github.py` (`DEFAULT_GITHUB_URL`, `DEFAULT_LIVE_BASE`); `scripts/draft_ocv_email.py` (`DEFAULT_DASHBOARD_URL`, `DEFAULT_REPORT_URL_FMT`); `scripts/publish_ocv_report_v1_archive.py` (back-link) | your team's Pages repo |
| `yohncf/ol-agent-pm-skills` (hardcoded monorepo self-reference) | git remote of the clone | `yohnathanc_microsoft/ol-agent-pm-skills` (the canonical repo) |
| `yohnathanc@microsoft.com` | `ocv-ticket-sync/SKILL.md` (example `--override`) | your alias |
| Real people's emails | `../shared/configs/ado_owners_outlook-agent.json` (git-ignored) | your team's owners map |

## 1. What this project does

`ocv-extraction/` turns raw customer feedback into engineering-ready, LT-ready output.
It covers three feedback sources and the full downstream pipeline:

- **OCV** — verbatim feedback from `ocv.microsoft.com` (Elasticsearch API).
- **ODS** — Sara support tickets via REST API.
- **Copilot Dash** — the Copilot feedback table, joined back to OCV.

The core mental model is a **pipeline**:

```
Extract  →  CSV (temporary)  →  Analyze  →  Manifest JSON (permanent)  →  Delete CSV
                                    │
                                    ├─→ Subtopics CSV  →  ADO tickets
                                    └─→ HTML dashboard  →  GitHub Pages  →  Email draft
```

- **CSVs** in `data/` are **ephemeral** and git-ignored. They contain raw customer text.
- **Manifests** in `data/manifests/` are **permanent** and aggregate-only (counts, themes,
  OcvId pointers, PM-voice paraphrases — **never raw verbatim**).
- After analysis, delete the CSVs (`npm run cleanup`).

## 2. How skills relate to scripts

Skills (under `.claude/skills/<name>/SKILL.md`) are the **orchestration layer** the AI
follows. Each skill wraps one or more **scripts** under `scripts/`. When you (or the AI)
"invoke a skill," the AI reads that skill's `SKILL.md` and runs the underlying script(s).

| Skill | Underlying script(s) |
|-------|----------------------|
| `ocv-setup` | `scripts/extract_standalone.js` (test run) |
| `ocv-extract-feedback` | `scripts/extract_standalone.js` |
| `ocv-extract-ods` | `scripts/ods_api_extract.py`, `scripts/generate_sample_urls.py` |
| `ocv-extract-dash` | `scripts/dash_ocv_extract.py` |
| `ocv-analyze` | reads CSV directly; `../shared/lib/manifest_writer.js`, `scripts/cleanup_csvs.js` |
| `ocv-analyze-and-ticket` | reads CSV directly; `../shared/lib/manifest_writer.js` |
| `ocv-ticket-sync` | `../shared/ado_sync.py` |
| `ocv-publish-report` | `scripts/publish_ocv_report.py` |
| `ocv-publish-github` | `scripts/publish_to_github.py` |
| `ocv-draft-email` | `scripts/draft_ocv_email.py` + `scripts/draft_ocv_email_com.ps1` |
| `ocv-weekly` | orchestrates all of the above |

## 3. One-time setup / prerequisites

```bash
cd ocv-extraction
npm install                 # Node deps (Playwright, etc.)
npm run check               # Preflight: Node version, Playwright, Edge, configs, dirs
```

Python-side dependencies (installed as needed):

```bash
pip install playwright && python -m playwright install chromium   # Dash extractor
pip install azure-identity                                        # ODS API extractor
pip install azure-kusto-data                                      # ODS Kusto sampling
```

**Authentication**
- **OCV & Dash** use Playwright + Edge with persistent profiles: `.browser-profile/`
  (OCV) and `.browser-profile-dash/` (Dash). First run opens Edge for SSO; delete the
  folder to force re-login.
- **ODS API** uses `DefaultAzureCredential` → run `az login` once (no browser).

## 4. The 13-topic taxonomy & references

Three reference docs sit next to the skills and encode the "doctrine":

- **`ocv-analyze-and-ticket/references/taxonomy-13-topic.md`** — the *locked* 13-topic
  classification for negative OCV feedback: exact topic definitions, use / don't-use
  rules, `SubMode` values, and worked examples. Every ticketing run must use these 13
  topics unchanged.
- **`ocv-analyze/references/two-pass-architecture.md`** — the analysis strategy:
  a cheap **metadata-first** pass (aggregate stats) followed by an **LLM verbatim** pass,
  with token math and the cutoff for when single-pass is fine.
- **`ocv-analyze/references/manifest-writer-api.md`** — the API of
  `../shared/lib/manifest_writer.js`, output-path convention, paraphrase rules, and the
  rule that **no raw verbatim** ever lands in a manifest.

## 5. Analysis tone (applies to all analysis output)

Write as a **PM reporting to your team**. OCV/ODS feedback is from self-selected,
already-frustrated users (<1% of MAU) — keep that sampling bias front of mind.

- **Quantify, don't editorialize** — "17 of 200 mention competitors," not "churn risk is real."
- **Neutral framing** — no "crisis," "hostile," "doom," "burning." Report what users
  said, not how angry they are.
- **PM voice, not user echo** — paraphrase into actionable insight.
- **Acknowledge the lens** — note the sampling bias when presenting results.

---

# Skill Reference

Each skill below lists: **Purpose · When to use · Inputs · Outputs · Commands ·
Workflow · Dependencies · Gotchas.**

---

## `ocv-setup`

**Purpose** — Guided first-time setup for a PM's OCV area. Checks prerequisites, creates
an area config file, and explains next steps.

**When to use** — First time only, before any extraction. Do not use once a config exists.

**Inputs** — Project root `ocv-extraction/`; `package.json`; `node_modules/playwright`
present; an area name; the OCV Discover URL from `ocv.microsoft.com`.

**Outputs** — `configs/<area>.json`; instructions for a test extraction.

**Commands**
```bash
npm install
node scripts/extract_standalone.js --config "configs/<area>.json" --date 7d --summary "data/test_<area>.csv"
```

**Workflow**
1. Show `## Step 1 of 3` progress header.
2. Verify prerequisites (Node, Playwright, Edge).
3. Ask for the area name + OCV Discover URL.
4. Write a minimal `configs/<area>.json`.
5. Give test-extraction and category-setup instructions.

**Dependencies** — `scripts/extract_standalone.js`; `configs/accounts.json` as an example.

**Gotchas** — Use the progress headers exactly. **Compliance:** this skill only creates
config/docs — it must **not** read or analyze CSV output.

---

## `ocv-extract-feedback`

**Purpose** — Runs the OCV verbatim extraction to CSV, with config-based filtering and a
date window. This is the primary "pull OCV feedback" skill.

**When to use** — "Extract / pull / get OCV feedback." **Not** for Dash or ODS.

**Inputs** — A `date-filter` (`today`, `yesterday`, `7d`, `14d`, `30d`, `3m`, `6m`, `all`,
or an explicit `YYYY-MM-DD:YYYY-MM-DD` range); an `area-name` matching `configs/<area>.json`;
optional `--include-structured`, `--no-cleanup`.

**Outputs** — `data/ocv_<area>_<YYYY-MM-DD>.csv` (daily) or
`data/ocv_<area>_<YYYY-MM-DD>_<filter>.csv` (ad hoc). Contains **raw customer content**.

**Commands**
```bash
cd <project-root>
node scripts/extract_standalone.js --config "configs/<area-name>.json" --date <filter> --summary "data/<output-file>"
# optional flags: --include-structured   --no-cleanup
node scripts/cleanup_csvs.js            # cleanup reminder after analysis
```

**Workflow**
1. Parse the date filter and area.
2. Resolve the config.
3. Compute the output filename.
4. Run the extraction (Playwright captures a bearer token, then calls the OCV
   Elasticsearch API).
5. Retry once after clearing `.browser-profile/` if auth fails.
6. Report summary stats + CSV path.
7. Remind the user to analyze, then clean up the CSV.

**Dependencies** — `scripts/extract_standalone.js`, `scripts/cleanup_csvs.js`,
Playwright/Edge, `configs/`.

**Gotchas** — First run opens Edge for SSO. `--summary` output is aggregate-only and safe
to share. CSV is temporary; delete after analysis. Copilot CLI only.

---

## `ocv-extract-ods`

**Purpose** — Extracts ODS / Sara support-ticket data via the ODS REST API using Azure
auth. No browser needed; ~7× faster than the legacy browser method.

**When to use** — You need ODS ticket data (from a URL list or a Kusto sample). Not for
OCV verbatim or Dash.

**Inputs** — Either an existing CSV of ODS ticket URLs/IDs, or the Kusto sampling
workflow; `az login`; Python 3.10+; `azure-identity` (and `azure-kusto-data` for sampling).

**Outputs** — Default `*_results.csv` with columns
`TicketId, ProblemStatement, Symptom, DataClassification, UserLocale, ProductName, URL`
(plus `Tags, TicketTier, EntitlementGroup, OCVArea` when `--fields all`).

**Commands**
```bash
# From a CSV of ODS ticket URLs
python scripts/ods_api_extract.py --input data/ticket_urls.csv
python scripts/ods_api_extract.py --input data/ticket_urls.csv --fields all --output data/results.csv

# Generate a stratified sample of URLs from Kusto, then extract
python scripts/generate_sample_urls.py --sample-size 600 --days 30
python scripts/ods_api_extract.py --input data/cloud_cache_sample_urls.csv --fields all

# Resume from an offset
python scripts/ods_api_extract.py --input data/urls.csv --offset 200 --append
```

**Workflow**
1. Ask whether this is the full population or a sample.
2. Resolve input / output / fields / offset.
3. Verify prerequisites (`az login`, packages).
4. Extract from the URL CSV, or sample from Kusto then extract.
5. Explain outputs and compliance.

**Dependencies** — `scripts/ods_api_extract.py`, `scripts/generate_sample_urls.py`,
Azure CLI / `DefaultAzureCredential`, ODS portal API. (Legacy browser fallback:
`scripts/ods_batch_extract.js`.)

**Gotchas** — For Kusto-driven runs always ask population vs. sample and use stratified
sampling. ~0.9 tickets/sec, token auto-refreshes. Customer content; Copilot CLI only.

---

## `ocv-extract-dash`

**Purpose** — Walks the Copilot Dash feedback table and joins each ticket to its OCV link,
utterance, verbatim, and response — producing one correlated CSV.

**When to use** — You need Dash → OCV correlation. Not for plain OCV extraction.

**Inputs** — A date window (`--date <preset>`, `--from/--to`, or a range); optional `--url`
(custom dashboard filters), `--out`, `--keep-open`; Edge + Playwright installed; signed
into Copilot Dash; persistent profile `.browser-profile-dash/`.

**Outputs** — `data/dash_ocv_<range>.csv` with columns
`Dash ticket, OCV ticket, Utterance, Verbatim, Response`; a `.progress.json` beside it.

**Commands**
```bash
pip install playwright && python -m playwright install chromium

python scripts/dash_ocv_extract.py                                   # yesterday (default)
python scripts/dash_ocv_extract.py --date 2026-05-18                 # single day
python scripts/dash_ocv_extract.py --date 7d                         # rolling window
python scripts/dash_ocv_extract.py --from 2026-05-11 --to 2026-05-21 # explicit range
python scripts/dash_ocv_extract.py --from 2026-05-11 --to 2026-05-21 --url "https://copilotdash.microsoft.com/product/feedback?..."
```

**Workflow**
1. Resolve the date window.
2. Compute the output filename.
3. Launch the extractor in Edge.
4. User signs in and optionally adds the `Ticket ID` / `OCV ticket` columns to visually
   verify rows while it auto-scrolls.
5. Capture `/api/v2/tickets/search`, filter to the date range, then pull per-ticket
   detail via `/api/v2/tickets/{id}` using the captured bearer token.
6. Report the CSV path and row totals.

**Dependencies** — Playwright + Edge (channel `msedge`), `scripts/dash_ocv_extract.py`,
`.browser-profile-dash/`.

**Gotchas** — On first run sign in at `https://copilotdash.microsoft.com`. If bearer-token
capture fails, sign in again. CSVs are temporary; clean up.

---

## `ocv-analyze`

**Purpose** — Open-ended OCV analysis: theme discovery, category suggestion/validation,
sentiment, crosstabs, executive summaries, and cross-period comparisons. Uses the
**two-pass architecture** (metadata pass, then LLM verbatim pass).

**When to use** — "Analyze / review / summarize OCV feedback." **Not** for extraction, and
**not** for the locked 13-topic ticketing taxonomy (use `ocv-analyze-and-ticket` for that).

**Inputs** — A CSV in `data/`; optional analysis type (`full`, `themes`, `categories`,
`categorize`, `validate`, `crosstab`, `sentiment`, `flags`, `summary`, `compare`);
optional config from `configs/`.

**Outputs** — Theme lists, category suggestions / validated categories, comparison tables,
a manifest via the shared writer, an optional `<original>_categorized.csv`, and a cleanup
prompt.

**Commands**
```bash
# Analysis is doctrine-driven (the AI reads the CSV directly) — no single hardcoded CLI.
# Manifests are written via ../shared/lib/manifest_writer.js
node scripts/cleanup_csvs.js
node scripts/cleanup_csvs.js --all-manifests
node scripts/cleanup_csvs.js --manifest <path>
```

**Workflow**
1. Resolve project root.
2. Load the CSV programmatically (Pass 1 — aggregate stats).
3. Theme discovery / language / audience analysis (Pass 2 — LLM).
4. Optional category-gap / crosstab / sentiment / categorize / validate.
5. Generate a manifest.
6. Prompt cleanup.

**Dependencies** — `../shared/lib/manifest_writer.js`,
`references/two-pass-architecture.md`, `configs/`, `scripts/cleanup_csvs.js`.

**Gotchas** — Follow the PM-neutral tone. No raw customer content in the manifest. For
13-topic ticketing, use `ocv-analyze-and-ticket` instead.

---

## `ocv-analyze-and-ticket`

**Purpose** — The engineering-ready weekly workflow. Classifies **negative** feedback into
the locked **13-topic taxonomy**, scores ticket-worthiness and P0–P3 priority, and emits a
weekly report + a granular subtopics CSV + an aggregate manifest.

**When to use** — "Analyze and ticket," generate the weekly manifest, produce the
subtopics CSV, build the WoW report, classify negative feedback.

**Inputs** — An extracted OCV CSV; optional prior manifest (for WoW deltas); negative rows
only for taxonomy work; full-population fields must be available for the manifest
(re-extract with `--include-structured` if needed).

**Outputs**
- A Markdown report (`TL;DR`, key findings, topic table, WoW table, category breakdown).
- `data/ocv_<area>_<YYYY-MM-DD>_subtopics.csv` (P0–P3 rows, engineering-ready).
- `data/manifests/ocv_<area>_<YYYY-MM-DD>_manifest.json`.

**Commands**
```bash
# Doctrine-driven; no single CLI. Manifest via ../shared/lib/manifest_writer.js
node scripts/cleanup_csvs.js
node scripts/cleanup_csvs.js --all-manifests
node scripts/cleanup_csvs.js --manifest <path>
```

**Workflow**
1. Resolve the CSV and week label.
2. Preprocess fields.
3. Apply the locked 13-topic taxonomy.
4. Generate TL;DR, dataset summary, topic table, WoW table, category breakdown.
5. Build the granular subtopics CSV.
6. Apply ticket-worthiness + priority scoring.
7. Build the weekly manifest.
8. Prompt CSV deletion.

**Dependencies** — `references/taxonomy-13-topic.md`,
`../shared/lib/manifest_writer.js`; upstream `ocv-extract-feedback`; downstream
`ocv-publish-report`, optional `ocv-ticket-sync`.

**Gotchas** — Keep whole-population rating/sentiment separate from verbatim-only metrics.
No raw verbatim in the manifest. `P3` singletons are anecdotal.

---

## `ocv-publish-report`

**Purpose** — Renders an analyze-and-ticket manifest into a single, self-contained,
dark-themed HTML dashboard suitable for LT consumption.

**When to use** — "Publish the OCV report," render the manifest as a webpage, build the LT
dashboard, create a single-file report.

**Inputs** — `--manifest` (required unless `--demo`); optional `--subtopics`,
`--prior-manifest`, `--report-md`, `--out`; Python 3.10+.

**Outputs** — `output/ocv/reports/ocv_<area>_<week>.html` (opens in the browser by
default); optional demo HTML.

**Commands**
```bash
python scripts/publish_ocv_report.py \
  --manifest data/manifests/ocv_outlook-agent_2026-05-18_manifest.json \
  --out output/ocv/reports/ocv_outlook-agent_2026-05-18.html

# Sample/demo dashboard
python scripts/publish_ocv_report.py --demo --out output/ocv/reports/ocv_weekly_dashboard_sample.html
```

**Workflow**
1. Read the manifest.
2. Auto-discover subtopics / prior manifest / report-md if not passed.
3. Render a self-contained HTML file.
4. Open it in the browser.
5. Optionally render the demo.

**Dependencies** — an `ocv-analyze-and-ticket` manifest; optional subtopics CSV and prior
manifest. Safe to run outside Copilot CLI (no AI inference; pure rendering).

**Gotchas** — Use the analyze-and-ticket schema, **not** `ocv-analyze` output. Dark theme
only; output is one self-contained file.

---

## `ocv-ticket-sync`

**Purpose** — Match-or-create Azure DevOps work items from high-signal subtopics rows
(P0/P1/P2), with interactive per-row review. Writes ADO URLs back into the CSV so the HTML
report renders an "ADO ↗" button on each card.

**When to use** — File ADO tickets from the subtopics CSV; sync OCV subtopics to ADO;
optional step 3.5 in `ocv-weekly`.

**Inputs** — `az login`; `azure-identity`; the subtopics CSV; optional Dash CSV; the
owner-routing config `../shared/configs/ado_owners_outlook-agent.json`.

**Outputs** — `data/ado_proposals_ocv_<area>_<to>.json`; the subtopics CSV updated with
`ADO URL`, `ADO action`, `Dash links`, `Resolved Models`, `Path`; created/updated ADO
work items.

**Commands**
```bash
python ../shared/ado_sync.py propose  --subtopics <csv> --dash-csv <csv>
python ../shared/ado_sync.py execute  --proposals <json>
python ../shared/ado_sync.py execute  --proposals <json> --dry-run
python ../shared/ado_sync.py execute  --proposals <json> --yes
python ../shared/ado_sync.py assign-owners --subtopics <csv> --dry-run
python ../shared/ado_sync.py assign-owners --subtopics <csv> --yes
```

**Workflow**
1. Propose matches.
2. **Gate A** — choose the review scope.
3. Per-row interactive review.
4. **Gate B** — final write-count confirmation.
5. Execute ADO changes.
6. Write ADO/Dash columns back into the CSV.
7. Optional owner-assignment retro-fix.

**Dependencies** — `../shared/ado_sync.py`,
`../shared/configs/ado_owners_outlook-agent.json`; optional `ocv-extract-dash`;
`ocv-publish-report` consumes the updated CSV.

**Gotchas** — Never silently create/modify tickets — the two gates are mandatory. On
ARM64 Windows, install `azure-identity` with `--only-binary=:all:`. ADO body uses
PM-paraphrased content only (no raw verbatim).

---

## `ocv-publish-github`

**Purpose** — Uploads the freshly built OCV weekly HTML report to the OCV-Weekly GitHub
Pages site and updates the landing-page manifest (`reports.json`).

**When to use** — Publish the report to GitHub Pages; optional final step of `ocv-weekly`.

**Inputs** — A built HTML report; the matching manifest; a local clone at
`_ocv_weekly_repo/`; Git credentials in Windows Credential Manager; a highlights line from
the user.

**Outputs** — `_ocv_weekly_repo/reports/<week>.html`; updated
`_ocv_weekly_repo/reports.json`; a commit/push to `origin/main`; live URLs.

**Commands**
```bash
python scripts/publish_to_github.py --manifest <json> --html <html> --highlights "<line>" --dry-run
python scripts/publish_to_github.py --manifest <json> --html <html> --highlights "<line>"          # real publish
python scripts/publish_to_github.py --manifest <json> --html <html> --highlights "<line>" --yes    # after confirmation
```

**Workflow**
1. Ask for the highlights line.
2. Dry-run preview.
3. Confirm publish.
4. Real publish: `git pull --rebase origin main` → copy HTML → upsert `reports.json` →
   commit/push.
5. Print the live URLs.

**Dependencies** — `scripts/publish_to_github.py`, `_ocv_weekly_repo/`,
`ocv-publish-report`, `ocv-analyze-and-ticket`.

**Gotchas** — Two-gate discipline (preview → confirm); `--yes` only after chat
confirmation. Does not modify `index.html`.

---

## `ocv-draft-email`

**Purpose** — Builds an Outlook-safe HTML weekly announcement email from the weekly
artifacts (manifest + subtopics + report MD) and saves it as a **local Classic Outlook
draft** via a PowerShell COM helper. Never sends.

**When to use** — Create the weekly email draft; drop the summary into Drafts; optional
final step of `ocv-weekly`.

**Inputs** — Classic Outlook installed and signed in; the current manifest; the current
subtopics CSV; strongly preferred: the report MD; optional prior manifest, metrics JSON,
and Playwright/Chromium (for a chart PNG). No Graph auth needed.

**Outputs** — `output/ocv/email-drafts/ocv_email_<week>.html`;
`output/ocv/email-drafts/ocv_progress_chart_<week>.png`; a draft in the Outlook Drafts
folder.

**Commands**
```bash
# Preview
python scripts/draft_ocv_email.py --manifest <json> --subtopics <csv> --report-md <md> \
  --highlights "<line>" --ado-query-url "<url>" --dry-run

# Create the draft
python scripts/draft_ocv_email.py --manifest <json> --subtopics <csv> --report-md <md> \
  --highlights "<line>" --ado-query-url "<url>" --verify --yes
# COM helper: scripts/draft_ocv_email_com.ps1
```

**Workflow**
1. Build the HTML preview.
2. Ask the user to confirm draft creation.
3. Save the draft via Outlook COM.
4. Verify the Drafts-folder entry.
5. Remind the user to open Classic Outlook, add recipients, and send.

**Dependencies** — `scripts/draft_ocv_email.py`, `scripts/draft_ocv_email_com.ps1`,
`ocv-analyze-and-ticket` outputs; optional `ocv-publish-github` live URL. Classic Outlook
COM only.

**Gotchas** — New Outlook will **not** work — Classic Outlook only. Never sends mail. Chart
PNG is preferred (SVG fallback is debug-only). Outlook/OWA sync may lag before the draft
appears.

> **Preferred email layout** (per user preference): greeting "Hello there," → headline
> numbers (KPIs) → "What you need to know" (TL;DR bullets) → progress-at-a-glance chart →
> WoW table → close with "Happy to walk through the details..." + "Cheers, _Yohn". No
> intro/dashboard/highlight preamble.

---

## `ocv-weekly` (orchestrator)

**Purpose** — Runs the full weekly OCV pipeline end-to-end, pausing once after the
subtopics CSV so a human can review/edit before publishing.

**When to use** — A full weekly OCV run; end-to-end OCV for a date range;
"extract, analyze, publish" workflows.

**Inputs** — A date range; area (defaults to `outlook-agent`); existing artifacts if
resuming; user choices at the pause points.

**Outputs (per step)**
1. `data/ocv_<area>_<from>_to_<to>_range.csv`
2. `data/dash_ocv_<from>_to_<to>.csv`
3. manifest + subtopics + report MD
4. `output/ocv/reports/ocv_<area>_<to>.html`
5. (optional) GitHub Pages publish
6. (optional) Outlook draft

**Commands (representative)**
```bash
python scripts/dash_ocv_extract.py --from <from> --to <to>                 # step 2
python scripts/publish_ocv_report.py --manifest <json> --subtopics <csv> --report-md <md>   # step 4
# step 5 → ocv-publish-github ; step 6 → ocv-draft-email
```

**Workflow**
1. Resolve the date range + area, print the plan.
2. `ocv-extract-feedback`.
3. `ocv-extract-dash`.
4. `ocv-analyze-and-ticket`.
5. **Pause** for subtopics review.
6. Optional `ocv-ticket-sync`.
7. `ocv-publish-report` (HTML).
8. Optional `ocv-publish-github`.
9. Optional `ocv-draft-email`.

**Dependencies** — all core OCV skills; optional `ocv-ticket-sync`,
`ocv-publish-github`, `ocv-draft-email`.

**Gotchas** — The pause after the subtopics CSV is mandatory. Resume-safe at each step
boundary. Surface the final artifacts list to the user.

---

## 6. Shared code (`../shared/`)

Both projects depend on one copy of common code:

| Path | What it is |
|------|------------|
| `../shared/ado_sync.py` | ADO match-or-create engine + owner routing (powers `ocv-ticket-sync`; also reused by SEVAL). |
| `../shared/lib/csv_parser.js` | RFC 4180 CSV parser. |
| `../shared/lib/manifest_writer.js` | Manifest read/write helper. |
| `../shared/lib/pii_scrub.py` | PII scrubbing at write time (only ~55 whitelisted ISP domains survive; the rest are redacted). |
| `../shared/lib/model_path.py` | Diagnoses routing/model path; reads `agent_models_reference.json`. |
| `../shared/configs/ado_owners_outlook-agent.json` | Owner-routing config (area path, iteration, tags, assignee map). **Git-ignored / user-local** — create it locally. |

## 7. Conventions cheat-sheet

- **Filenames** — Daily: `data/ocv_<area>_YYYY-MM-DD.csv`; ad hoc:
  `data/ocv_<area>_YYYY-MM-DD_<filter>.csv`; Dash: `data/dash_ocv_<range>.csv`;
  manifests: `data/manifests/<name>_manifest.json`.
- **PII** is scrubbed at write time via `pii_scrub.py`.
- **Configs** — `configs/*.json` is git-ignored except `_template.json` and
  `accounts.json`. New area configs stay local.
- **HTML reports are dark-themed** — no light-mode output.
- **`--summary` is safe to share** — aggregate counts only, never customer content.
- **Living doc** — reflect non-trivial extraction changes in `docs/FHL_JOURNEY.md`.
- **Cleanup** — `npm run cleanup` (or `node scripts/cleanup_csvs.js`) deletes source CSVs
  after analysis.

## 8. Where to look next

- `AGENTS.md` — authoritative project instructions (compliance, tone, journey protocol).
- `.github/copilot-instructions.md` — command table + architecture overview.
- `README.md`, `docs/GETTING_STARTED.md` — user-facing quick start.
- `docs/PRIVACY_REVIEW.md` — formal privacy / data-handling review.
- `../AGENTS.md` — cross-project map for the OLAgentWork monorepo.

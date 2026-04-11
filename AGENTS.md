# OCV Extraction — Project Instructions

> **⚠️ Approved AI tool: GitHub Copilot CLI only.**
> This repo processes **Customer Content** (OCV verbatim feedback, ODS ticket data). Per E+D Data Use Guidance (March 2026), only AI tools backed by AOAI or Anthropic models via GitHub Copilot may analyze this data. **Claude Code is not approved** for customer data processing. If you are using Claude Code, do not run extraction or analysis skills against customer data.

## Data Use Compliance

- AI assistants **may** read and analyze CSV files in `data/` for theme discovery, categorization, and summarization — **only via GitHub Copilot CLI**.
- AI assistants **may** edit source code, configs, and docs in this repo.
- AI assistants **may** create config files via `setup-ocv`.
- Extracted data stays local. The AI assistant accesses files only within the session.
- Analysis is powered by the AI assistant's model (no local SLM required). The `ocv-analyze` skill reads CSVs directly.

### Skills

| Skill | What it does |
|-------|-------------|
| `extract-ocv` | Runs the OCV extraction script with date filtering and config resolution |
| `extract-ods` | Extracts ODS Sara ticket data via REST API (no browser needed) |
| `ocv-analyze` | Reads extracted CSVs for theme discovery, category suggestions, and executive summaries |
| `setup-ocv` | Walks you through creating a config file for your area |

### Running extractions manually

```bash
node scripts/extract_standalone.js --config configs/<area>.json --date yesterday --summary
```

The `--summary` flag prints aggregate stats (counts, breakdowns) to the terminal. No customer content in the summary output.

## Living Document: FHL_JOURNEY.md

Whenever changes are made to the extraction tool during this project, update `docs/FHL_JOURNEY.md` to reflect the new state:

- **New feature or fix**: Append to the relevant section, or create a new section if the change is significant.
- **New problem encountered and solved**: Add a row to the "Problems We Solved" table at the end.
- **Current state changes**: Update the "Where We Are Now" section.
- **Sections marked `[TO UPDATE]`**: Replace with current information when it becomes available.

Do not update the document for trivial changes (typos, whitespace, refactors with no user-facing impact).

## Analysis Tone and Voice

When analyzing OCV or ODS feedback, write as a PM reporting to your team — neutral, measured, and actionable. OCV and ODS feedback comes from self-selected users who are already frustrated enough to file a report; it is not representative of the broader user base. Keep this sampling bias in mind at all times.

- **Quantify, don't editorialize.** "17 of 200 mention competitors" is useful. "Churn risk is real and immediate" is editorial. Let the reader draw the conclusion.
- **Neutral framing.** Present findings as signals to investigate, not verdicts. Avoid words like "hostile," "sabotage," "doom," "crisis," or "burning." Don't characterize user emotions — report what they said, not how angry they are.
- **PM perspective, not user echo.** Paraphrase feedback in the voice of the PM team, not the voice of the frustrated user. The goal is actionable insight, not amplifying raw sentiment.
- **Acknowledge the lens.** Include a brief note on sampling bias when presenting results (e.g., "Based on self-reported feedback, which skews toward dissatisfied users").

This applies to all AI-generated analysis in this repo — skill invocations, ad-hoc deep dives, and follow-up questions alike.

## Data Storage

CSVs are **temporary artifacts** that should be cleaned up after analysis.

**Lifecycle**: Extract → CSV (temporary) → Analyze → Manifest JSON (permanent) → Delete CSV

- Daily extractions go to `data/ocv_<area>_YYYY-MM-DD.csv`. Ad hoc runs use `data/ocv_<area>_YYYY-MM-DD_<filter>.csv`.
- Analysis manifests go to `data/manifests/<name>_manifest.json`. Manifests contain aggregate stats, themes with counts, OcvId pointers, and AI-generated paraphrases — **no raw customer content**.
- After analysis, run `npm run cleanup` or `node scripts/cleanup_csvs.js` to delete source CSVs.
- The cleanup script also supports `--all-manifests` to scan all manifests and `--manifest <path>` for a specific one.

## ODS Ticket Extraction

Two methods available. **Prefer the API method** — it's 7× faster and requires no browser.

### Method 1: Direct API (recommended)

```bash
# Extract from a CSV of ODS ticket URLs
python scripts/ods_api_extract.py --input data/ticket_urls.csv

# With extended fields (Tags, TicketTier, OCVArea)
python scripts/ods_api_extract.py --input data/ticket_urls.csv --fields all

# Resume from offset
python scripts/ods_api_extract.py --input data/ticket_urls.csv --offset 200 --append
```

**Key details:**
- Auth via `DefaultAzureCredential` (uses `az login`) — no browser, no SSO
- ~0.9 tickets/sec, token auto-refreshes
- Requires: `pip install azure-identity`
- Input: CSV with ODS ticket URLs or IDs in last column
- Output: `TicketId,ProblemStatement,Symptom,DataClassification,UserLocale,ProductName,URL` (+ Tags, TicketTier, EntitlementGroup, OCVArea with `--fields all`)

### Method 2: Browser-based (legacy)

```bash
node scripts/ods_batch_extract.js --input data/input.csv --limit 20
```

- Requires Playwright + Edge + manual SSO login
- ~8.5 seconds per ticket, SSO times out after ~45 min
- Use only when API method is unavailable

### Generating sample URLs from Kusto

```bash
python scripts/generate_sample_urls.py --sample-size 600 --days 30
```

Queries Kusto for Cloud Cache support sessions, takes a stratified random sample by AccountType, and outputs ODS URLs. Requires: `pip install azure-kusto-data azure-identity`

## Key Files

- `scripts/extract_standalone.js` — OCV extraction (Node.js + Playwright + Elasticsearch API)
- `scripts/ods_api_extract.py` — ODS ticket extraction via REST API (Python, no browser)
- `scripts/ods_batch_extract.js` — ODS ticket extraction via browser (legacy, Playwright)
- `scripts/generate_sample_urls.py` — Kusto query + stratified sampling for ODS tickets
- `scripts/preflight.js` — Dependency checker (`npm run check`)
- `scripts/lib/csv_parser.js` — Shared RFC 4180 CSV parser
- `scripts/isp_whitelist.json` — Tier 1 ISP whitelist for provider identification (~55 providers)
- `configs/` — Area config files (`_template.json` for new setups)
- `data/` — Extracted CSV output (daily snapshots, git-ignored)
- `.claude/skills/` — AI assistant skill definitions (work in GitHub Copilot CLI)
- `README.md` — User-facing documentation
- `docs/GETTING_STARTED.md` — Quick start guide for new users
- `docs/PRIVACY_REVIEW.md` — Formal privacy and data handling review

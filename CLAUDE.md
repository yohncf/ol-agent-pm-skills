# OCV Extraction — Project Instructions

## Data Use Compliance

OCV verbatim feedback is **Customer Content**. Per the updated E+D Data Use Guidance (March 2026), AI tools may analyze this data for product insights.

- AI assistants **may** read and analyze CSV files in `data/` for theme discovery, categorization, and summarization.
- AI assistants **may** edit source code, configs, and docs in this repo.
- AI assistants **may** create config files via `setup-ocv`.
- Extracted data stays local (no external APIs). Cloud AI assistants (Claude, Copilot) access files only within the session.

### Skills

| Skill | What it does |
|-------|-------------|
| `extract-ocv` | Runs the extraction script with date filtering and config resolution |
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

Daily extractions go to `data/ocv_<area>_YYYY-MM-DD.csv`. Ad hoc runs with longer ranges use `data/ocv_<area>_YYYY-MM-DD_<filter>.csv` (e.g., `ocv_accounts_2026-02-27_7d.csv`).

## ODS Ticket Extraction

The `ods_batch_extract.js` script extracts Tags and Problem Statement from ODS Sara tickets in bulk.

```bash
# Extract first 20 tickets from a CSV of URLs
node scripts/ods_batch_extract.js --input data/input.csv --limit 20

# Resume from offset, append to existing output
node scripts/ods_batch_extract.js --input data/input.csv --offset 20 --append

# Custom output file
node scripts/ods_batch_extract.js --input data/urls.csv --output data/results.csv
```

**Key details:**
- Reuses a single Playwright browser session (persistent profile at `.browser-profile-ods`) for SSO passthrough
- Auto-restarts browser on context crash (SSO times out after ~45 min / ~320 tickets)
- Input CSV: no header, URL in last column (or a single-column URL list)
- Output CSV: `TicketId,Date,Category,Tags,ProblemStatement,URL`
- ~8.5 seconds per ticket

**Data files:**
- `data/ods_final_700.csv` — Complete extraction of 700 Gmail ODS tickets (Mar 12, 2026)
- `data/gmail ods feedback 3.12.csv` — Original input file (700 ODS ticket URLs)

## Key Files

- `scripts/extract_standalone.js` — Main CLI script (Node.js + Playwright + Elasticsearch API)
- `scripts/analyze_local.js` — Local LLM analysis via Ollama (optional)
- `scripts/preflight.js` — Dependency checker (`npm run check`)
- `scripts/lib/csv_parser.js` — Shared RFC 4180 CSV parser
- `scripts/isp_whitelist.json` — Tier 1 ISP whitelist for provider identification (~55 providers)
- `configs/` — Area config files (`_template.json` for new setups)
- `data/` — Extracted CSV output (daily snapshots, git-ignored)
- `.claude/skills/` — AI assistant skill definitions (work in Claude Code and Copilot CLI)
- `README.md` — Full technical documentation
- `docs/GETTING_STARTED.md` — Quick start guide for new users
- `docs/PRIVACY_REVIEW.md` — Formal privacy and data handling review

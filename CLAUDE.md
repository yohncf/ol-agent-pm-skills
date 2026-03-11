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

## Data Storage

Daily extractions go to `data/ocv_<area>_YYYY-MM-DD.csv`. Ad hoc runs with longer ranges use `data/ocv_<area>_YYYY-MM-DD_<filter>.csv` (e.g., `ocv_accounts_2026-02-27_7d.csv`).

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

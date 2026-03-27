# OCV Data Extraction

## TL;DR

Config-driven CLI tool that extracts verbatim customer feedback from OCV (One Customer Voice) to CSV. Handles authentication, date filtering, Elasticsearch API pagination, PII scrubbing, sentiment/intent classification, and category tagging automatically.

```bash
node scripts/extract_standalone.js --config configs/accounts.json --date yesterday --summary
```

---

## Setup

**Prerequisites:** Node.js 18+ and Microsoft Edge installed.

```bash
cd ocv-extraction
npm install
npm run check        # Verifies everything is ready
```

The `npm run check` command validates Node.js version, Playwright, Edge, config files, and output directories. Run it first to catch any issues.

No browser downloads needed — the script uses your installed Edge binary.

**First-time config:** Either create a config manually (see [Config Format](#config-format)) or use the Copilot CLI setup wizard:

```
/setup-ocv
```

---

## Quick Start

```bash
npm run extract:accounts     # Yesterday's Accounts feedback
npm run extract:7d           # Last 7 days of Accounts feedback
```

Or with full control:

```bash
node scripts/extract_standalone.js --config configs/<area>.json --date <value> --summary [output.csv]
```

| Flag | Description |
|------|-------------|
| `--config <path>` | Config file (required). e.g., `configs/accounts.json` |
| `--date <value>` | Date filter (see table below). Defaults to `today`. |
| `--summary` | Print aggregate stats to terminal after extraction |
| `--url <url>` | Override the config's `ocv_url` (optional) |

### Date Options

| Value | Description |
|-------|-------------|
| `today` | Today only (default) |
| `yesterday` | Yesterday only |
| `7d` | Last 7 days |
| `14d` | Last 14 days |
| `30d` | Last 30 days |
| `3m` | Last 3 months |
| `6m` | Last 6 months |
| `all` | All time |
| `2026-02-20:2026-02-24` | Custom range (YYYY-MM-DD) |

### Examples

```bash
# Yesterday's feedback for Accounts area
node scripts/extract_standalone.js --config configs/accounts.json --date yesterday --summary

# Last 7 days, all Monarch feedback, custom output file
node scripts/extract_standalone.js --config configs/monarch-all.json --date 7d --summary data/monarch_7d.csv

# Custom date range
node scripts/extract_standalone.js --config configs/accounts.json --date 2026-02-20:2026-02-24 --summary
```

### The `--summary` Flag

When `--summary` is passed, the script prints aggregate statistics to the terminal after extraction:

```
--- Summary (aggregate stats, no customer content) ---
Items:      247
Date range: 03/04/26 to 03/05/26
PII:        12 redactions (2 emails, 4 phones, 6 tags)
Sentiment:  Negative=180  Neutral=42  Positive=25
Intent:     Problem=155  Request=60  Compliment=12  Unknown=20
Categories: Sign-in=35  Send/Receive=28  Account Setup=15  Uncategorized=169
Languages:  en=200  de=25  fr=12  ja=10
Providers:  Comcast=18  GMX=12  Web.de=9  AOL=7  T-Online=5
Noise:      8 flagged
```

The summary contains only counts and distributions, never customer verbatim text.

### Authentication

- **First run:** Edge opens and you complete SSO login manually. Takes ~30 seconds.
- **Subsequent runs:** SSO cookies are cached in `.browser-profile/`, so authentication is automatic. The browser opens, extracts, writes CSV, and closes on its own.

To reset authentication (e.g., after a password change), delete `.browser-profile/` and run again.

---

## Output Format

```csv
Date,Comment,Provider,Sentiment,Intent,Feature,Category,Language,Noise,AreaPath
"03/05/26 2:30PM","I can't sync my email","Comcast","Negative","Problem","IMAP","Sign-in","en","","Outlook\Monarch\Accounts"
```

| Column | Description |
|--------|-------------|
| Date | When the feedback was submitted (MM/DD/YY HH:MMAM/PM) |
| Comment | Verbatim customer text, translated to English, PII scrubbed |
| Provider | ISP name from whitelist (e.g., "Comcast") or empty. Non-ISP domains redacted as `[CUSTOM_DOMAIN]`. |
| Sentiment | Positive / Negative / Neutral |
| Intent | Problem / Request / Compliment / Unknown |
| Feature | OCV tags filtered through the config's `feature_tags` whitelist |
| Category | Issue bucket matched by config keyword patterns |
| Language | Original language code (en, de, fr, etc.) |
| Noise | "true" if matched a noise pattern; empty otherwise |
| AreaPath | OCV area hierarchy (pipe-delimited) |

---

## Config Format

Each area has a JSON config in `configs/`. See `configs/_template.json` for the structure or `configs/accounts.json` for a working example.

```json
{
  "name": "Accounts",
  "description": "Outlook Monarch Accounts feedback",
  "ocv_url": "https://ocv.microsoft.com/#/discover/?...",
  "entity": {
    "type": "isp",
    "source": "email_domain",
    "whitelist_file": "isp_whitelist.json",
    "redacted_label": "[CUSTOM_DOMAIN]"
  },
  "categories": {
    "Sign-in": {
      "description": "Login and authentication issues",
      "match": ["sign.?in", "login", "password", "can't log"],
      "exclude": ["sign.*up"]
    }
  },
  "feature_tags": ["IMAP", "CloudCache", "Gmail"],
  "noise_patterns": ["^test$", "asdf", "^\\.$"]
}
```

**Available configs:**
- `accounts.json` — Outlook Monarch Accounts (IMAP, Cloud Cache, third-party)
- `attachments.json` — Outlook attachments feedback
- `monarch-all.json` — All Monarch feedback (minimal filtering)
- `_template.json` — Starting point for new areas

---

## How It Works

```
Config + date ──> Launch Edge ──> Navigate to OCV ──> Capture API request
                                                            │
CSV file <── PII scrub <── Parse hits <── Paginate API <────┘
```

1. **Config loading** — Reads the area config for URL, categories, feature tags, noise patterns, and entity settings.
2. **Browser launch** — Opens Edge via Playwright with a persistent profile (`.browser-profile/`).
3. **Navigation** — Loads OCV with date parameters applied to the URL.
4. **API capture** — Intercepts OCV's Elasticsearch request to capture the query and auth headers.
5. **API pagination** — Replays the query with `size=200` pages using Elasticsearch `search_after` (cursor-based) to paginate through all results without offset limits.
6. **Field extraction** — Parses each hit for comment, date, sentiment, intent, tags, provider, and area path.
7. **PII scrubbing** — Scrubs emails, phone numbers, and OCV redaction tags from all comments in-memory.
8. **CSV export** — Writes the cleaned, categorized data to disk.
9. **Summary** (optional) — Prints aggregate statistics to the terminal if `--summary` is passed.

**DOM fallback:** If the API capture fails, the script falls back to DOM scraping (limited to ~400 items, fewer data fields).

---

## PII Handling

All comments are scrubbed in-memory before the CSV is written to disk. Three layers of protection:

| Layer | What it does |
|-------|-------------|
| OCV server-side | Redacts some emails as `[PII: Email]` before rendering |
| Client-side regex | Catches raw emails and phone numbers OCV missed |
| ISP whitelist | Extracts only public ISP names from email domains; redacts all other domains as `[CUSTOM_DOMAIN]` |

The ISP whitelist (`scripts/isp_whitelist.json`) contains ~55 public consumer ISPs with ~150 domain patterns. Small organizations, healthcare, religious, and personal domains are explicitly excluded. See `docs/PRIVACY_REVIEW.md` for the full privacy analysis.

---

## AI Assistant Integration

The project includes skills that work in **GitHub Copilot CLI**:

| Skill | What it does |
|-------|-------------|
| `extract-ocv` | Runs the extraction command with date filtering and config resolution |
| `ocv-analyze` | AI-powered theme discovery, category suggestions, and executive summaries |
| `setup-ocv` | Walks you through creating a config file for your area |

Skills are stored in `.claude/skills/` and loaded automatically by both tools. In Copilot CLI, say "extract ocv yesterday accounts" or use `/skills list` to see available skills.

Per the E+D Data Use Guidance (March 2026), AI assistants report only aggregate summary stats from `--summary`, not individual customer verbatim text.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Not sure if everything is set up | Run `npm run check` to verify all dependencies |
| SSO login not appearing | Delete `.browser-profile/` and run again |
| Multiple "Sign in" tabs / stuck redirect | Stale browser profile. Delete `.browser-profile/` and retry. Complete SSO on the first tab only. |
| 0 items extracted | Check that the OCV URL in your config has the right filters. Try `--date 7d` first. |
| API error / fallback to DOM | OCV may have changed its API. Check the request intercept in the script. |
| Timeout errors | OCV may be slow. Increase the `waitUntil` timeout in `page.goto()`. |
| Edge not found | Install Edge or change `channel: 'msedge'` to `channel: 'chrome'` in the script |
| Categories not matching | Check regex patterns in your config. Use [regex101.com](https://regex101.com) to test. |
| Missing provider data | Ensure `entity` is configured in your config with a valid `whitelist_file` path. |

---

## Files

| File | Purpose |
|------|---------|
| `scripts/extract_standalone.js` | Main CLI extraction script |
| `scripts/ods_extract.js` | ODS Sara ticket extraction (POC) |
| `scripts/preflight.js` | Dependency checker (`npm run check`) |
| `scripts/lib/csv_parser.js` | Shared RFC 4180 CSV parser |
| `scripts/isp_whitelist.json` | Tier 1 ISP whitelist for provider identification |
| `configs/` | Area config files (one per product area) |
| `configs/_template.json` | Template for creating new configs |
| `data/` | Extracted CSV output (daily snapshots) |
| `.claude/skills/` | AI assistant skill definitions |
| `.claudeignore` | Blocks browser profiles from AI assistant indexing |
| `docs/PRIVACY_REVIEW.md` | Formal privacy and data handling review |
| `docs/FHL_JOURNEY.md` | Project narrative document |
| `package.json` | Dependencies and npm scripts |

---

## Context

**Why this exists:** OCV's built-in export doesn't include verbatim feedback text with structured metadata. This tool captures what users actually wrote, with sentiment, intent, and category analysis.

**Why config-driven:** Different teams track different product areas with different category definitions and entity tracking needs. Configs make the tool reusable across teams.

**Project origin:** Unified Inbox (Monarch) — IMAP account supportability
**Created:** 2026-02-11

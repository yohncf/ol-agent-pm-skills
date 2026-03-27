# OCV Feedback Extraction: Getting Started

This tool extracts verbatim customer feedback from OCV into structured, PII-scrubbed CSVs. One command gives you a clean dataset with sentiment, intent, issue categories, and provider identification.

**Time to set up: ~10 minutes.**

---

## Prerequisites

You need two things installed:

### 1. Node.js (v18 or later)

Check if you have it:
```bash
node --version
```

If not installed, get it from https://nodejs.org (LTS version). Or if you have `winget`:
```bash
winget install OpenJS.NodeJS.LTS
```

### 2. Microsoft Edge

Already installed on all Microsoft machines. The tool uses your installed Edge for SSO authentication.

---

## Setup

### Step 1: Clone the repo

```bash
git clone <repo-url>
cd ocv-extraction
```

### Step 2: Install dependencies and verify

```bash
npm install
npm run check
```

The preflight check validates Node.js, Playwright, Edge, configs, and output directories. Everything should show ✅.

### Step 3: Create your config

You have two options:

---

#### Option A: Guided setup with AI assistant (recommended)

If you have **GitHub Copilot CLI**, say:

```
setup ocv for my area
```

This walks you through a 3-step wizard:
1. Checks prerequisites
2. Gets your OCV URL and creates a config
3. Gives you next steps to test and refine

**~5 minutes, no JSON editing required.**

---

#### Option B: Manual setup

**1. Get your OCV URL.**

Open [OCV Discover](https://ocv.microsoft.com/#/discover/) in Edge. Apply your filters:
- Select your Product (e.g., "Outlook Monarch")
- Select your Area Path (e.g., "Outlook Monarch\Calendar")
- Add any additional filters you normally use
- Switch to List View

Copy the full URL from the address bar.

**2. Create your config file.**

Copy the template:
```bash
cp configs/_template.json configs/<your-area>.json
```

Open `configs/<your-area>.json` in any text editor. Fill in:

```json
{
  "name": "Your Area",
  "description": "Brief description",
  "ocv_url": "PASTE YOUR OCV URL HERE",

  "categories": {
    "Category Name": {
      "description": "What this catches",
      "match": ["keyword1", "keyword2"],
      "require": [],
      "exclude": []
    }
  },

  "feature_tags": ["Tag1", "Tag2"],
  "noise_patterns": []
}
```

If you're not sure what categories to use, start with an empty config (just the URL) and refine after seeing your data.

**3. Test it.**

```bash
npm run extract -- --config configs/<your-area>.json --date 7d --summary data/test.csv
```

The first time, Edge will open and ask you to complete Microsoft SSO. Do that once; it's automatic after.

**4. Check the output.**

Open `data/test.csv` in Excel. The CSV has columns for Date, Comment, Provider, Sentiment, Intent, Feature, Category, Language, Noise, and AreaPath.

---

## Daily Usage

### Quick commands (npm scripts)

```bash
npm run extract:accounts     # Yesterday's Accounts feedback
npm run extract:7d           # Last 7 days
```

### With AI assistant (GitHub Copilot CLI)

```
extract ocv yesterday <your-area>
extract ocv 7d <your-area>
analyze ocv feedback for <your-area>
```

### Full command

```bash
node scripts/extract_standalone.js --config configs/<your-area>.json --date yesterday --summary data/output.csv
```

### Date options
| Flag | Range |
|------|-------|
| `--date today` | Today |
| `--date yesterday` | Yesterday only |
| `--date 7d` | Last 7 days |
| `--date 14d` | Last 14 days |
| `--date 30d` | Last 30 days |
| `--date 3m` | Last 3 months |
| `--date all` | All time |
| `--date 2026-02-20:2026-02-24` | Custom range |

---

## What You Get

A CSV with 10 columns:

| Column | What it tells you |
|--------|-------------------|
| **Date** | When the feedback was submitted |
| **Comment** | What the customer wrote (translated to English, PII scrubbed) |
| **Provider** | ISP name if identified (e.g., "Comcast", "GMX") or [CUSTOM_DOMAIN] |
| **Sentiment** | Positive / Negative / Neutral |
| **Intent** | Problem / Request / Compliment |
| **Feature** | Product area tags from your config |
| **Category** | Issue bucket from your config's keyword matching |
| **Language** | Original language code (en, de, fr, etc.) |
| **Noise** | Flagged as noise by your config's patterns |
| **AreaPath** | OCV area hierarchy (pipe-delimited) |

---

## AI-Powered Analysis

After extracting data, use the `ocv-analyze` skill for deeper insights:

```
analyze the accounts feedback from last week
```

This gives you:
- **Aggregate stats** — sentiment, intent, category distributions
- **Theme discovery** — top recurring issues with estimated counts
- **Category gap analysis** — suggestions for new categories with regex patterns
- **High-value flagging** — row numbers of the most actionable feedback
- **Executive summary** — TL;DR for stakeholders

---

## PII and Privacy

- All data stays within Microsoft's network. No external APIs, no cloud services.
- Comments use OCV's server-side PII redaction (`TranslatedTextPiiRedacted`).
- Additional client-side scrubbing catches emails, phones, and OCV tags that slip through.
- Email domains are only extracted for ~55 known public ISPs (whitelist). Everything else is redacted.
- See `docs/PRIVACY_REVIEW.md` for the full analysis.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Not sure if setup is correct | Run `npm run check` to verify all dependencies |
| `node: command not found` | Install Node.js from https://nodejs.org |
| SSO times out (2 min) | Delete `.browser-profile/` and try again. Watch for the Edge window. |
| 0 items extracted | Check your OCV URL in a browser. Do you see results? |
| "Config file not found" | Check the path: `--config configs/<name>.json` |
| Edge doesn't open | Make sure Edge is installed. Check `channel: 'msedge'` in the script. |

---

## Questions?

Check `README.md` for full technical documentation or `docs/PRIVACY_REVIEW.md` for the privacy review.

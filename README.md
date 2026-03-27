# OCV Feedback Extraction

## What is this?

A tool that pulls customer feedback from [OCV](https://ocv.microsoft.com) into a clean Excel-ready CSV. It grabs what customers actually wrote, along with sentiment, language, and issue categories — all with personal info scrubbed out automatically.

**Why it exists:** OCV's built-in export gives you metadata but not the actual verbatim feedback in a structured format. This tool fills that gap.

**Who it's for:** PMs on Outlook (or any team using OCV) who want to analyze customer feedback at scale.

---

## What you get

A CSV file you can open in Excel with these columns:

| Column | What it tells you |
|--------|-------------------|
| **Comment** | What the customer wrote (translated to English, personal info removed) |
| **Sentiment** | Positive, Negative, or Neutral |
| **Intent** | Problem, Request, or Compliment |
| **Category** | Issue type (e.g., "Sign-in", "Send/Receive") — you define these |
| **Provider** | ISP name if applicable (e.g., "Comcast", "GMX") |
| **Language** | Original language (en, de, fr, etc.) |
| **Date** | When the feedback was submitted |

Plus: Feature tags, Noise flags, and OCV Area Path for filtering.

---

## Getting started

**Time to set up: ~10 minutes.** Full walkthrough: **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**

Short version:

1. **Install Node.js** from [nodejs.org](https://nodejs.org) (LTS version) if you don't have it
2. **Clone this repo** and install dependencies:
   ```
   git clone https://github.com/lreisdesouza_microsoft/ocv-extraction.git
   cd ocv-extraction
   npm install
   ```
3. **Verify setup:**
   ```
   npm run check
   ```
   This checks that everything is installed. All items should show ✅.

4. **Create your config** — tell the tool which OCV area to pull from. In GitHub Copilot CLI, just say:
   > "set up OCV for my area"

   The assistant walks you through it. No code editing needed.

---

## Daily use

### With an AI assistant (recommended)

If you use **GitHub Copilot CLI**, just talk to it:

> "Extract OCV feedback for yesterday"
>
> "Pull last 7 days of feedback"
>
> "Analyze the feedback from last week"
>
> "What are the top themes in the last 30 days of feedback?"

The assistant handles file paths, date math, and output formatting for you.

### Without an AI assistant

```
npm run extract:accounts
```

This pulls yesterday's feedback and prints a summary. Open the CSV in Excel for the full data.

### Date options

| Say this | Gets you |
|----------|----------|
| yesterday | Yesterday's feedback |
| 7d | Last 7 days |
| 30d | Last 30 days |
| 3m | Last 3 months |

---

## AI-powered analysis

After extracting data, ask the assistant to analyze it:

> "Analyze the accounts feedback"
>
> "Categorize the feedback using AI"
>
> "Validate my categories"

The AI assistant reads the CSV directly and provides:

- **Top themes** — the 10 most common issues customers are reporting
- **AI categorization** — classify items using LLM based on your config-defined taxonomy (handles ambiguity, no manual tuning)
- **Category validation** — sample items per category, balance checks, ambiguity detection
- **Category suggestions** — new categories to add to your config, with ready-to-paste patterns
- **High-value feedback** — row numbers of the most actionable items (specific errors, competitor mentions, impact descriptions)
- **Executive summary** — a TL;DR you can paste into a status update

No additional software or local models required. Analysis runs through the same AI assistant you use for extraction.

---

## How authentication works

The first time you run an extraction, Edge opens and asks you to sign in with your Microsoft account. Do that once — after that, it remembers your login and runs automatically.

If authentication stops working (e.g., after a password change), the tool clears its cache and asks you to sign in again.

---

## Privacy

- All data stays on your machine and within the Microsoft network. No external services.
- Personal info (emails, phone numbers) is automatically removed before the CSV is saved.
- Email domains are only identified for ~55 known public ISPs (Gmail, Comcast, etc.). All other domains are redacted.
- See [docs/PRIVACY_REVIEW.md](docs/PRIVACY_REVIEW.md) for the full review.

---

## Customizing for your area

Each product area gets its own config file that defines:

- **Which OCV data to pull** (your filtered OCV URL)
- **Issue categories** (keyword patterns like "sign-in", "sync", "password")
- **Feature tags** (which OCV tags matter for your area)
- **Noise patterns** (spam, test entries, off-topic feedback)

Start with the guided setup (ask the AI assistant "set up OCV for my area"), or copy `configs/_template.json` and edit it manually. The [Getting Started guide](docs/GETTING_STARTED.md) walks through both options.

---

## Troubleshooting

| Problem | What to do |
|---------|-----------|
| Not sure if setup is correct | Run `npm run check` — it tells you what's missing |
| Edge doesn't open / login stuck | Delete the `.browser-profile` folder and try again |
| No feedback extracted | Check that your OCV URL shows results when you open it in a browser |
| Categories aren't matching | The AI assistant can suggest new categories — ask "analyze the feedback" |

---

## Project info

| | |
|---|---|
| **Origin** | Unified Inbox (Monarch) — IMAP account supportability |
| **Created** | February 2026 |
| **Technical docs** | [docs/TECHNICAL.md](docs/TECHNICAL.md) |
| **Privacy review** | [docs/PRIVACY_REVIEW.md](docs/PRIVACY_REVIEW.md) |
| **Setup guide** | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |

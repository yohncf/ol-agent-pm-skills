# OCV & ODS Feedback Extraction

## What is this?

A tool that pulls customer feedback from **OCV** (verbatim feedback) and **ODS** (support tickets) into clean, Excel-ready CSVs. It grabs what customers actually wrote, along with sentiment, language, issue categories, and support context — all with personal info scrubbed out automatically.

**Two data channels, one toolkit:**

| Channel | What it captures | Source | Coverage |
|---------|-----------------|--------|----------|
| **OCV** (Open Customer Voice) | Verbatim feedback: Send a Smile/Frown, NPS, Copilot thumbs, app store reviews | In-app feedback prompts | <1% MAU — small but richest qualitative signal |
| **ODS** (Office Diagnostic Sessions) | Support tickets: problem statements, diagnostic data, Sara chat transcripts | Help → Contact Support flow | <<1% MAU — captures users who actively seek help |

**Why it exists:** OCV's built-in export gives you metadata but not the actual verbatim feedback in a structured format. ODS has no export at all — you'd have to open each ticket manually. This tool fills both gaps.

**Who it's for:** PMs on Outlook (or any team using OCV/ODS) who want to analyze customer feedback and support patterns at scale.

---

## What you get

### OCV extraction → CSV

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

### ODS extraction → CSV

| Column | What it tells you |
|--------|-------------------|
| **ProblemStatement** | What the user described when seeking help |
| **Symptom** | Entry point (e.g., ToggleFeedback, RecoveryAndContactSupport, CopilotChatFeedback) |
| **DataClassification** | Feedback, Troubleshooting, Recovery, or DiagnosticOnly |
| **Tags** | Clean semicolon-separated issue tags |
| **TicketTier** | Agent tier (e.g., OlkEndUserChat confirms actual chat with support) |
| **UserLocale** | Language/region (e.g., en-US, ja, de) |

### AI analysis → Insights

After extraction, the AI assistant produces analysis outputs including:

- **Topic distribution** — ranked themes with counts and percentages (e.g., "Contacts/People: 22%, Sync/Missing Emails: 20%")
- **Cross-tab analysis** — topic breakdown by segment (account type, language, provider) to spot where issues concentrate
- **Sentiment by topic** — which themes drive the most negative feedback
- **Executive summary** — TL;DR with top 3 recommended actions, formatted for status updates
- **Category validation** — sample items per category, balance checks, ambiguity detection

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

**OCV feedback:**
> "Extract OCV feedback for yesterday"
>
> "Pull last 7 days of feedback"

**ODS tickets:**
> "Extract ODS support tickets for Cloud Cache"
>
> "Sample 600 Cloud Cache tickets from the last 30 days"

**Analysis:**
> "Analyze the feedback from last week"
>
> "What are the top themes in the last 30 days of feedback?"
>
> "Break down topics by account type"
>
> "Categorize the feedback using AI"

The assistant handles file paths, date math, sampling, and output formatting for you.

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
>
> "Break down topics by account type"
>
> "Show sentiment breakdown by topic"

The AI assistant reads the CSV directly and provides:

- **Top themes** — the 10 most common issues ranked by volume (e.g., "Contacts/People: 22%, Sync/Missing Emails: 20%, Send/Receive: 17%")
- **AI categorization** — classify items using LLM based on your config-defined taxonomy. Handles ambiguity, multi-language input, and edge cases without manual tuning
- **Cross-tab analysis** — break down topics by segment (account type, provider, language) to spot where issues concentrate. Flags over/under-represented themes per segment
- **Sentiment by topic** — shows which themes drive the most negative feedback vs. balanced/positive sentiment
- **Category validation** — sample items per category, balance checks, ambiguity detection, coverage summary
- **Category suggestions** — new categories to add to your config, with ready-to-paste JSON
- **High-value feedback** — row numbers of the most actionable items (specific errors, competitor mentions, impact descriptions)
- **Executive summary** — a TL;DR with top 3 recommended actions, formatted for status updates

No additional software or local models required. Analysis runs through the same AI assistant you use for extraction.

---

## How authentication works

**OCV extraction** (browser-based): The first time you run an extraction, Edge opens and asks you to sign in with your Microsoft account. Do that once — after that, it remembers your login and runs automatically. If authentication stops working (e.g., after a password change), the tool clears its cache and asks you to sign in again.

**ODS extraction** (API-based): Uses your Azure CLI login (`az login`). No browser needed, no SSO timeouts. Token auto-refreshes.

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
- **Issue categories** with descriptions (the AI uses these to classify feedback semantically)
- **Feature tags** (which OCV tags matter for your area)
- **Noise patterns** (spam, test entries, off-topic feedback)

Start with the guided setup (ask the AI assistant "set up OCV for my area"), or copy `configs/_template.json` and edit it manually. The [Getting Started guide](docs/GETTING_STARTED.md) walks through both options.

For **ODS extraction**, the sampling script is currently configured for Cloud Cache support tickets. To adapt for other areas, modify the Kusto query in `scripts/generate_sample_urls.py`.

---

## Troubleshooting

| Problem | What to do |
|---------|-----------|
| Not sure if setup is correct | Run `npm run check` — it tells you what's missing |
| Edge doesn't open / login stuck (OCV) | Delete the `.browser-profile` folder and try again |
| No feedback extracted (OCV) | Check that your OCV URL shows results when you open it in a browser |
| Categories aren't matching | Ask the AI assistant to "validate my categories" or "recategorize using AI" |
| ODS extraction fails | Run `az login` to refresh credentials. Check `az account show` for active session |
| ODS tickets returning empty | Verify the ticket IDs exist in ODS portal. Some old sessions are purged |

---

## Project info

| | |
|---|---|
| **Origin** | Unified Inbox (Monarch) — IMAP account supportability |
| **Created** | February 2026 |
| **Technical docs** | [docs/TECHNICAL.md](docs/TECHNICAL.md) |
| **Privacy review** | [docs/PRIVACY_REVIEW.md](docs/PRIVACY_REVIEW.md) |
| **Setup guide** | [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) |

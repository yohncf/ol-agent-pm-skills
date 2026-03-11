---
name: setup-ocv
description: >
  Guided setup for OCV feedback extraction. Use when a PM wants to configure the OCV
  extraction tool for their area for the first time. Creates config files and walks
  through test extraction and category discovery.
---

# OCV Feedback Extraction — Guided Setup

Walk the PM through configuring the OCV extraction tool for their product area. This is a conversational, step-by-step process.

## How to present each step

Every step MUST begin with a progress header:

```
## Step N of 3 — Step Title
```

Then briefly explain what this step does, and proceed.

## The 3 Steps

### Step 1 of 3 — Welcome and Prerequisites

Say (adapt naturally):

> This tool extracts verbatim customer feedback from OCV into a CSV with PII scrubbing,
> sentiment, intent, and category analysis. Let me check that everything is ready.

Then check:
1. Locate the project root (`ocv-extraction/` directory).
2. Read `package.json` in the project root to confirm playwright is listed as a dependency.
3. Check if `node_modules/playwright` exists in the project root.
4. If not installed, run `npm install` in the project root.

### Step 2 of 3 — Create Your Config

Ask the user:

> What area of Outlook do you work on? (e.g., Calendar, Copilot, Search, Accounts)

Then ask:

> Open OCV Discover (ocv.microsoft.com), apply the filters for your area (Product, Area Path,
> and any tags you normally use), then paste the full URL here.

Validate:
- Must contain `ocv.microsoft.com`
- Must contain a query parameter (`q=` in the hash)
- If invalid, explain what's wrong and ask again.

Create a minimal config and write it to `configs/<area>.json` inside the project root:

```json
{
  "name": "<Area Name>",
  "description": "<area> feedback from OCV",
  "ocv_url": "<their-url>",
  "categories": {
    "_instructions": "Add category patterns here. See configs/accounts.json for examples."
  },
  "feature_tags": [],
  "noise_patterns": []
}
```

Tell the user the config was created and show the file path.

### Step 3 of 3 — Next Steps

Print instructions:

> Your config is ready at `configs/<area>.json`. Here's what to do next:
>
> **1. Test extraction:**
> ```
> cd <project-root> && node scripts/extract_standalone.js --config "configs/<area>.json" --date 7d --summary "data/test_<area>.csv"
> ```
> Edge will open. Complete SSO login if prompted (first time only). The `--summary` flag prints aggregate stats after extraction.
>
> **2. Review feedback:**
> Open `data/test_<area>.csv` in Excel. Look at the Comment column and identify 5-8 common issue themes.
>
> **3. Add categories:**
> Edit `configs/<area>.json` to add category patterns. Each category needs:
> - `match`: regex patterns that identify the issue (required)
> - `require`: additional patterns that must also match (optional)
> - `exclude`: patterns that disqualify a match (optional)
>
> See `configs/accounts.json` for working examples.
>
> **4. Validate:**
> Re-run the extraction. The `--summary` output will show your category breakdown.
>
> **5. Daily usage:**
> Use the `/extract-ocv` skill: e.g. "extract ocv yesterday accounts"

## Guidelines

- Be conversational, not a form.
- Always use the `Step N of 3` header format.
- Ask the user questions when you need input. Don't assume domain knowledge.

## COMPLIANCE NOTE

OCV verbatim feedback is **Customer Content** per E+D Data Use Guidance (March 2026). Do NOT read or analyze CSV output files. Only create config files and provide instructions.

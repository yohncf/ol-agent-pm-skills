---
name: ocv-extract-ods
description: >
  Extract ODS (Office Diagnostic Sessions) ticket data via REST API. Use when the
  user asks to extract, pull, or get ODS/Sara ticket data. Runs the Python extraction
  script with token-based auth (no browser needed). Supports sampling from Kusto or
  extracting from an existing URL list. Do NOT use for OCV verbatim feedback —
  use `ocv-extract-feedback`. Do NOT use for Copilot Dash correlation — use `ocv-extract-dash`.
---

# ODS Ticket Extraction

Extract ticket details from the ODS portal via direct REST API. No browser, no SSO login — uses `az login` credentials.

## Parsing the user's request

Determine which workflow the user needs:

1. **Extract from existing URLs** — User has a CSV of ODS ticket URLs or IDs
2. **Sample and extract** — User wants to query Kusto for tickets, sample, then extract

### Population vs. sample decision (REQUIRED)

Before running any extraction that involves querying Kusto for tickets, **always ask the user** whether they want the full population or a random sample. Present this table:

| | Full Population | Random Sample (95% CI, ±4%) |
|---|---|---|
| **Accuracy** | Exact counts, drill into any subgroup | ±4% margin of error at 600 items |
| **Duration** | ~18 min per 1,000 tickets | ~11 min for 600 tickets |
| **Best for** | Root cause analysis, small populations (<2K) | Trend detection, topic analysis, large populations |
| **Limitation** | Hours for large sets (20K+ = ~6 hrs) | Can't reliably analyze subgroups with <30 items |

**Default recommendation**: Random sample for populations >2,000 tickets. Full population for smaller sets.

If the user chooses a random sample, use stratified sampling (via `generate_sample_urls.py`) to preserve the distribution of key dimensions (e.g., AccountType).

### Parameters to resolve

- **input**: Path to CSV with ticket URLs/IDs. If not provided, check `data/` for recent URL files.
- **output**: Output CSV path. Default: `{input}_results.csv`
- **fields**: `core` (default) or `all` (adds Tags, TicketTier, OCVArea, EntitlementGroup)
- **offset / limit**: For resuming or processing a subset

## Prerequisites

Before running, verify:

1. **Python 3.10+** installed
2. **azure-identity** package: `pip install azure-identity`
3. **Azure CLI login**: `az login` (user must be authenticated)
4. For Kusto sampling: **azure-kusto-data** package: `pip install azure-kusto-data`

## Workflow 1: Extract from existing URL CSV

```bash
cd <project-root>
python scripts/ods_api_extract.py --input data/ticket_urls.csv
python scripts/ods_api_extract.py --input data/ticket_urls.csv --fields all --output data/results.csv
```

Input CSV format: any CSV where the last column contains ODS ticket URLs (`https://ods.office.net/#/saratickets/{id}`) or ticket IDs (UUID format). Extra columns are preserved as metadata in the output.

## Workflow 2: Sample from Kusto, then extract

Step 1 — Generate sample URLs:
```bash
python scripts/generate_sample_urls.py --sample-size 600 --days 30
```

Step 2 — Extract via API:
```bash
python scripts/ods_api_extract.py --input data/cloud_cache_sample_urls.csv --fields all
```

The sampling script is currently configured for Cloud Cache support tickets. To adapt for other areas, modify the Kusto query in `generate_sample_urls.py`.

## Output columns

### Core fields (default)
| Column | Source |
|--------|--------|
| TicketId | DiagnosticSessionId from API |
| ProblemStatement | User's description (newlines stripped) |
| Symptom | e.g., RecoveryAndContactSupport |
| DataClassification | Support or Feedback |
| UserLocale | e.g., en-US, ja, de |
| ProductName | e.g., Outlook |
| URL | ODS ticket link |

### Extended fields (`--fields all`)
| Column | Source |
|--------|--------|
| Tags | Semicolon-separated tag names |
| TicketTier | e.g., OlkEndUserChat |
| EntitlementGroup | User entitlement status |
| OCVArea | Pre-categorized area from DiagnosticSessionAttributes |

Plus any metadata columns from the input CSV (Date, AccountType, etc.).

## API details (for maintainability)

- **Base URL**: `https://portal.diagnostics.office.com`
- **AAD Client ID**: `57da3f69-2d82-4c17-9e57-2e6d78b2dc60`
- **Token scope**: `57da3f69-2d82-4c17-9e57-2e6d78b2dc60/.default`
- **Main endpoint**: `GET /v1/diagnosticsession/?id={ticketId}`
- **Auth**: Bearer token via `DefaultAzureCredential` (uses `az login`)
- **Rate**: ~0.9 tickets/sec sequential, no known rate limits
- **Token auto-refreshes** — no SSO timeout issues

## Resuming failed extractions

```bash
python scripts/ods_api_extract.py --input data/urls.csv --offset 200 --append
```

The `--offset` flag skips the first N tickets. Use `--append` to add to the existing output file.

## Data use compliance

ODS ticket data is **Customer Content**. Per the E+D Data Use Guidance, AI assistants running through Copilot CLI (AOAI/Anthropic models) may analyze this data for product insights. Extracted data stays local.

## Context: Where ODS fits in the support flow

ODS captures the **support ticket path** — users who go beyond passive feedback and actively seek help. The flow:

1. **User entry points** — In-app Support, Chat Support, Toggle Out feedback, M365 Admin Center, Services Hub, Phone/Email
2. **Deflection** — Self-help articles, diagnostic tools, automated suggestions
3. **ODS ticket created** — If deflection fails, a DiagnosticSession is created with consent, data collectors, and diagnostic info
4. **Sara chat** — AI-assisted support agent attempts resolution
5. **Escalation** — If Sara can't resolve: In-app Support Agents (vendors) → QA → Feature Crews → On-call Engineers

ODS and OCV are linked via `DiagnosticSessionId` — the same user action can produce both a support ticket (ODS) and a feedback entry (OCV), enabling cross-referencing.

### Monarch-specific entry points

| Entry Point | DataClassification | Symptom | What it captures |
|-------------|-------------------|---------|------------------|
| Toggle Out Feedback | Feedback | ToggleFeedback | User switched back to classic Outlook |
| General Feedback | Feedback | givefeedback_general | Send a Smile/Frown |
| Copilot Chat Feedback | Feedback | CopilotChatFeedback | Thumbs up/down on Copilot responses |
| Copilot Compose | Feedback | CopilotCompose | Feedback on Copilot-generated drafts |
| Contact Support | Troubleshooting | RecoveryAndContactSupport | User sought help via Help pane |
| Recovery | Recovery | RecoveryAndContactSupport | Automated recovery flow |
| Discover Feed | Feedback | DiscoverFeedFeedback | Feedback on Discover feed content |
| Suggestions | Feedback | SuggestionsQueryResult | Feedback on search/suggestion quality |

**Note:** `Symptom` is a free-form string, not a fixed enum. New values appear without schema changes. Always check the actual data for the latest values.

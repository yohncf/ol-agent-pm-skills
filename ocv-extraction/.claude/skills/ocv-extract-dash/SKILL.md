---
name: ocv-extract-dash
description: >
  Extract Copilot Dash tickets and their linked OCV submissions into a CSV.
  Use when the user asks to "extract Copilot Dash tickets", "pull dash → OCV
  mapping", "get OCV links from Copilot Dash", "extract dash tickets for
  <date range>", or similar. Runs a Playwright-backed Python script that
  signs in to https://copilotdash.microsoft.com via Edge, auto-scrolls the
  feedback table, and joins each ticket to its OCV link + utterance +
  verbatim + agent response. Do NOT use when you only need OCV feedback
  without Dash correlation — use `ocv-extract-feedback` instead.
---

# Copilot Dash → OCV Extraction

Extract a CSV that joins Copilot Dash tickets to OCV. Output columns:

| Column | Source |
|---|---|
| **Dash ticket** | `https://copilotdash.microsoft.com/ticket/{id}` |
| **OCV ticket** | `userFeedback.ocvLink` from `/api/v2/tickets/{id}` (empty if the ticket has no OCV submission) |
| **Utterance** | `diagnosticContext.chat[0].utterance.text` — the first user message in the conversation |
| **Verbatim** | `userFeedback.verbatim.text` — the customer's free-text complaint |
| **Response** | `diagnosticContext.chat[0].response.text` — the agent's response to the first user message |
| **Resolved Models** | `diagnosticContext.resolvedModelName` — `";"`-joined list of model names that actually executed across the conversation (e.g. `prod-gpt-41-mini-04-14;prod-gpt-53-chat;prod-sonicberry`). Powers the per-ticket path attribution downstream. |
| **Path** | Diagnosed routing path slug from `../shared/agent_models_reference.json` (e.g. `Sydney-Tools+WorkBerry`, `CodeGen-Claude`, `CodeGen-GHCP`, `CodeGen-GHCP-mini`, `Sydney-Tools`, `Unknown`). |

## Parsing the user's request

Resolve one of these mutually exclusive forms:

- **Single day**: `--date 2026-05-18` (or `today`, `yesterday`)
- **Rolling window**: `--date 7d`, `14d`, `30d`, `3m`
- **Explicit range**: `--from 2026-05-11 --to 2026-05-21`
- **Range preset**: `--date 2026-05-11:2026-05-21`

If the user does not specify a date, default to `yesterday` and confirm with them
before launching (don't silently extract the wrong window).

Optional parameters:

- **`--url <copilot-dash-url>`** — override the dashboard listing URL when the user
  wants a different filter (different product, different feedback type, etc.).
  Default: the M365Chat / thumbs=Negative / OutlookAIAgent extension view.
- **`--out <path>`** — custom output path. Default: `data/dash_ocv_<range>.csv`.
- **`--keep-open`** — leave the browser open after finishing for spot-checks.

## Prerequisites

1. **Python 3.10+** with Playwright:
   ```
   pip install playwright
   python -m playwright install chromium
   ```
2. **Microsoft Edge** installed (script uses `channel='msedge'`).
3. **Sign-in to Copilot Dash** (https://copilotdash.microsoft.com) — the script
   uses a persistent Edge profile at `ocv-extraction/.browser-profile-dash/`,
   so users only sign in once.

## What to do

1. **Resolve the project root.** This skill lives in the `ocv-extraction/`
   directory. Always `cd` there before running.

2. **Resolve dates** from the user's prompt and translate to the CLI form above.
   If the request is ambiguous (e.g., "last week") confirm the intended date range with the user before launching.

3. **Compute the output filename** (the script does this automatically as
   `data/dash_ocv_<label>.csv` where `<label>` is either a single date or
   `<start>_to_<end>`). Surface the resolved path to the user before launching.

4. **Run the extractor**:
   ```
   cd <project-root>
   python scripts/dash_ocv_extract.py --date <preset>
   # or
   python scripts/dash_ocv_extract.py --from <YYYY-MM-DD> --to <YYYY-MM-DD>
   ```
   Treat it like an interactive command (it opens Edge and waits for input).

5. **Tell the user what to do in the Edge window:**

   > Edge will open the Copilot Dash listing page. Please:
   > 1. Sign in if prompted.
   > 2. Wait for the feedback table to render.
   > 3. **Recommended:** open the column picker and add the `Ticket ID`
   >    column (plus `OCV ticket` / `OCV Link` if available) so you can
   >    visually verify the rows while the script scrolls.
   > 4. Press Enter in the terminal to start auto-scroll + capture.

   Be explicit about step 3 — the user said this manual step worked well
   for them and helps them sanity-check the run.

6. **After the script finishes**, report back:
   - The output CSV path.
   - Row totals printed at the end (total rows, success count, rows with OCV).
   - A reminder: rows whose source ticket has no OCV submission will have an
     empty `OCV ticket` column (this is normal — the field is empty at the
     source, not a parsing miss).

7. **If the script fails to capture a bearer token** ("Never captured the
   Authorization bearer token"): the user's sign-in expired or never completed.
   Have them sign in again in the open Edge window, then re-run.

## How it works (for maintainability)

- **Browser**: Edge via Playwright with persistent profile at `.browser-profile-dash/`.
- **Listing capture**: Hooks `page.on('response')` and parses every
  `/api/v2/tickets/search` JSON body using a regex over `"id":"<guid>" ... "createDateTime":"<iso>"`.
  This handles the virtualized list — the React UI fetches more pages as it scrolls.
- **Auto-scroll**: Picks the largest overflow-scroll element on the page and
  scrolls until the oldest visible `MM/DD` cell is before the cutoff date (or
  until the list stops growing for 6 ticks).
- **Per-ticket details**: Captures the `Authorization: Bearer …` header from the
  first `/api/v2/tickets/*` request the React app makes, then uses Playwright's
  `APIRequestContext` to call `/api/v2/tickets/{id}` for each ID. If the API
  ever returns 401/403, the script reseeds the bearer by reopening one ticket
  page in the browser.
- **Schema**:
  - `userFeedback.ocvLink` — `string` (often `""`)
  - `userFeedback.verbatim.text` — `string`
  - `diagnosticContext.chat[0].utterance.text` — `string`
  - `diagnosticContext.chat[0].response.text` — `string`

## Output and lifecycle

CSVs land in `data/` and are git-ignored (see `.gitignore`). Treat them like the
existing OCV CSVs: temporary artifacts for analysis. Delete with
`node scripts/cleanup_csvs.js` once analysis is complete.

A progress JSON (`.progress.json` next to the CSV) is written every 25 tickets
so you can recover what was fetched if you abort mid-run.

## Data use compliance

Copilot Dash tickets surface OCV verbatim feedback and user utterances — this is
**Customer Content** per E+D Data Use Guidance (March 2026). Run only via
GitHub Copilot CLI. See the repo `AGENTS.md` for the full policy.

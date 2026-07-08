---
name: ocv-draft-email
description: >
  Build an Outlook-safe HTML email summarizing the weekly OCV report and
  drop it as a DRAFT in the user's local Classic Outlook Drafts folder.
  Pulls headline KPIs, TL;DR bullets, and WoW topic-shift table directly
  from the current and prior manifests (plus the markdown report and
  subtopics CSV), styles them with the same look-and-feel as the published
  HTML dashboard, and saves the message via Outlook COM with To/Cc/Bcc
  intentionally blank — the user adds recipients and hits send. This skill
  never sends mail, never edits an existing thread, and never touches
  Microsoft Graph (no admin consent required). Use when the user asks to
  "create the weekly email draft", "draft the OCV email", "drop the
  weekly summary in my drafts", "build the leadership email for this
  week", or as the optional final step of the `ocv-weekly` orchestrator.
---

# OCV → Outlook draft email

This skill renders the same numbers leadership sees on the published
dashboard into a self-contained HTML email body, then uses Outlook COM
to save it as a draft locally. Recipients are deliberately left blank so
the user picks the audience interactively in Outlook.

The script does NOT call Microsoft Graph. The earlier path through
`az account get-access-token` + `POST /me/messages` fails in the
corporate tenant because `Mail.ReadWrite` requires admin consent that
end-users don't have. Local COM bypasses that entirely and works as
long as Classic Outlook is installed.

## When to invoke

Triggers include:

- "Create the weekly OCV email draft"
- "Drop the weekly summary in my drafts"
- "Draft the OCV email for this week"
- "Build the leadership email for `<week>`"
- "Make the draft email for the OCV report"
- As the optional final step (step 6) of the `ocv-weekly` orchestrator

Do **not** invoke for:

- "Send the email now" → this skill never sends. Tell the user to open
  Outlook → Drafts → add recipients → send themselves.
- "Edit a reply to an existing thread" → use the `send-email` skill's
  reply/forward tools (different MCP, different workflow).
- "Email me the report file" → this skill embeds an HTML SUMMARY, not
  a file attachment of the dashboard. If the user wants the HTML
  dashboard attached, ask first — adding `--attach` could be a future
  enhancement; today the script does not attach.

## Prerequisites

1. **Classic Outlook (OUTLOOK.EXE) installed and signed in** at
   `C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE`. The
   "New Outlook" client (`olk.exe`) does NOT expose COM and will
   silently fail.
2. The current week's manifest at
   `data/manifests/ocv_<area>_<week>_manifest.json` (from
   `ocv-analyze-and-ticket`).
3. The current week's subtopics CSV at
   `data/ocv_<area>_<week>_subtopics.csv` — used for the P0/P1 count
   in the fourth KPI card.
4. (Strongly preferred) The matching markdown report at
   `data/ocv_<area>_<week>_report.md` — its `## TL;DR` bullets are
   rendered verbatim into the email's TL;DR section.
5. (Optional) A prior-week manifest for WoW comparison. If not passed
   explicitly, the script reads the `wow_basis` field on the current
   manifest. Without it, the WoW chips render `—`.
6. (Strongly preferred) `_ocv_weekly_repo/metrics_v2.json` — the cross-week
   metrics file `ocv-publish-github` maintains. Used to render the
   "Progress at a glance" trend chart. If missing, the chart section is
   omitted silently.
7. (Strongly preferred for chart embedding) **Playwright + Chromium**
   installed. The chart is rendered to a PNG via headless Chromium and
   attached to the draft inline via Content-ID — this is the only
   approach that works in OWA, since OWA's HTML sanitizer strips inline
   `<svg>` entirely (confirmed 2026-06-08; the legacy `--chart-mode svg`
   path is kept for debugging only). Install via
   `pip install playwright && playwright install chromium`. If Playwright
   is missing the script falls back to inline SVG with a loud warning.

No az/Graph login required. Python dependencies: stdlib +
`playwright` (for the chart PNG render — only used in the default
`--chart-mode cid` path).

## One-gate workflow

This skill mirrors `ocv-publish-github`'s confirmation discipline, but
only has **one** gate (the action is non-destructive — it creates a
draft, not a sent message, and recipients are blank so nothing leaves
the mailbox until the user explicitly sends).

```
build HTML preview  --->  [Gate: confirm draft creation]  --->  COM save  --->  verify
```

### Phase 1 — Build the HTML preview (always run first)

```bash
python scripts/draft_ocv_email.py \
  --manifest  data/manifests/ocv_<area>_<week>_manifest.json \
  --subtopics data/ocv_<area>_<week>_subtopics.csv \
  --report-md data/ocv_<area>_<week>_report.md \
  --highlights "<one-line headline>" \
  --ado-query-url "<ADO query URL or omit>" \
  --dry-run
```

`--dry-run` writes only the local preview HTML to
`output/ocv/email-drafts/ocv_email_<week>.html` and does NOT touch Outlook.
Open the preview in a browser to sanity-check rendering before the gate.

Defaults the script auto-derives:

| Field | Default |
|---|---|
| `--subject` | `Weekly OCV Feedback (MM/DD - MM/DD) + CopilotDash` |
| `--dashboard-url` | `https://gim-home.github.io/OCV-Weekly/` |
| `--mirror-url` | `https://yohncf.github.io/OCV-Weekly_temp/` (pass `--mirror-url ""` to omit) |
| `--signoff` | `Cheers,<br><em>_Yohn</em>` (pass `--signoff ""` to omit) |
| Intro paragraph | "Sharing this week's OCV feedback breakdown for the Outlook Agent in Frontier." |
| Progress-blurb | "Progress at a glance" paragraph with the 👍👎 framing |
| Closing paragraphs (3) | other-ADO-projects ping / opt-out / walk-through offer |

### Phase 2 — Confirmation gate (your job in chat)

After printing the preview path, **ask the user** via `ask_user`:

> "Email preview written to `<path>`. Open the file to verify rendering, then
> confirm: create the draft in Outlook now (recipients will be blank;
> nothing will be sent)."
>
> Options:
> - Create the draft
> - Let me edit the highlights / closing first (re-run with new flags)
> - Cancel — don't touch Outlook

Only on "Create the draft" → run the real command:

### Phase 3 — Save to Drafts via COM

```bash
python scripts/draft_ocv_email.py \
  --manifest  data/manifests/ocv_<area>_<week>_manifest.json \
  --subtopics data/ocv_<area>_<week>_subtopics.csv \
  --report-md data/ocv_<area>_<week>_report.md \
  --highlights "<headline>" \
  --ado-query-url "<URL>" \
  --verify --yes
```

`--yes` skips the script's own stdin confirmation (defense-in-depth —
the script also prompts on stdin by default; only pass `--yes` after
the agent has cleared Phase 2's `ask_user` with the user).

`--verify` re-reads the draft via COM after save and prints its
`Parent`, `LastModificationTime`, and `Size` — sanity check that the
item really landed in `\\<mailbox>\Drafts`.

On success the script prints:

```
[draft] DRAFT CREATED
  Subject : Weekly OCV Feedback (MM/DD - MM/DD) + CopilotDash
  Folder  : Drafts
  Size    : 28466 bytes
  EntryID : 00000000...
```

### Phase 4 — Surface OWA-sync caveat

Always remind the user (one short sentence is enough):

> "Open Classic Outlook → Drafts → add To/Cc/Bcc → send. If OWA doesn't
> show the draft, give Classic Outlook ~30s to push, or trigger
> Send/Receive (F9) — see `force_sync` notes below if it still lags."

## Overriding defaults

| Flag | Use case |
|---|---|
| `--subject "..."` | Override the auto-generated subject |
| `--highlights "..."` | One-line headline rendered above the KPI grid (e.g., "Rating is holding at 80% despite +42.6% verbatim negatives") |
| `--intro "..."` | Replace the standard opening paragraph |
| `--progress-blurb "..."` | Replace the "Progress at a glance" paragraph |
| `--mirror-url "" ` | Omit the mirror link (e.g., audience already has EMU access) |
| `--ado-query-url URL` | Add the "New OCV tickets have been added and assigned here: <link>" paragraph |
| `--ado-writes N` | Change the P0+P1 KPI caption to "X synced to ADO" instead of "X subtopic rows" |
| `--closing-paragraph "..."` (repeatable) | Replace the three default closing paragraphs with your own list. Pass once per paragraph in order. |
| `--signoff "" ` | Omit the sign-off line |
| `--prior-manifest <path>` | Override the WoW basis (defaults to manifest's `wow_basis`) |
| `--metrics <path>` | Override the metrics JSON used for the Progress-at-a-glance chart (default: `_ocv_weekly_repo/metrics_v2.json`). Pass `--metrics ""` or point at a missing file to skip the chart entirely. |
| `--chart-mode {cid,svg,none}` | How to embed the chart. **`cid`** (default): render PNG via Playwright + attach inline via Content-ID — works in every client including OWA, Classic Outlook for Windows, Mac, mobile, and Gmail. **`svg`**: inline `<svg>` (renders in OWA briefly then is stripped on the sanitizer's round-trip — kept for debugging only). **`none`**: omit the chart entirely. |
| `--chart-png-out <path>` | Where to write the rendered chart PNG (cid mode). Defaults to `output/ocv/email-drafts/ocv_progress_chart_<week>.png`. |
| `--out <path>` | Override preview HTML output location |

## Outlook-safe HTML rules (why the script generates what it does)

Outlook is famously hostile to modern CSS. The generator follows these
constraints — do NOT relax them without testing in Classic Outlook +
OWA + the iOS/Android Outlook apps:

1. **All styles inline.** No `<style>` blocks (stripped by Word
   renderer in Classic Outlook), no `<link>` (blocked everywhere).
2. **Table-based layout.** No flexbox, no CSS grid. The four KPI cards
   are cells of a 4-column table; the overall 720px container is a
   nested table centered with `align="center"`.
3. **Unicode triangles, not Material icons.** `&#9650;` (▲) and
   `&#9660;` (▼) instead of icon-font glyphs. Email clients block
   external font CDNs.
4. **No external image references, no `data:` images, no inline `<svg>` —
   with one deliberate exception: a CID-attached PNG for the chart.**
   The Progress-at-a-glance trend chart is rendered to a PNG via
   headless Chromium (`render_progress_png`) and attached inline via
   MAPI's `PR_ATTACH_CONTENT_ID` + `PR_ATTACHMENT_HIDDEN` (Content-ID
   `ocv_progress_chart`). The body references it as
   `<img src="cid:ocv_progress_chart">`. This was decided 2026-06-08
   after confirming the failure mode of the original inline-SVG
   approach: OWA initially renders SVG `<text>` + `<circle>` but
   strips the entire `<svg>` element on its sanitization round-trip,
   so any subsequent OWA sync silently destroys the chart. CID-attached
   PNGs render everywhere (OWA, Classic Outlook for Windows, Outlook
   Mac, Outlook mobile, Gmail) because the PNG bytes never pass
   through any HTML sanitizer. No other section uses images.
5. **Safe font stack.** `Segoe UI, Arial, sans-serif` — falls back
   cleanly on macOS and mobile.
6. **No external image references** (no remote URLs, no tracking
   pixels) — keeps the email lightweight and never warns about "click
   to download images". The CID-attached chart PNG is not external; it
   ships inside the MIME envelope.

Outlook's Word renderer rewrites the body on Save — adding MSO
conditional comments, wrapping inline styles, etc. The current
generator produces HTML that survives this round-trip with the layout
intact (verified 2026-06-08 against Classic Outlook 2509).

## What goes in the email body (default template)

In order, top to bottom (streamlined shape adopted 2026-07-01 — greeting
straight into the numbers, no intro/dashboard/highlight preamble):

1. **Greeting** — "Hello there,"
2. **Headline numbers** (eyebrow + section title) — 4 KPI cards:
   Rating · Verbatim negatives · Top pain topic · P0+P1 queue. Each
   card shows value, WoW delta (with up/down/flat arrow + color), and
   a caption. Section title is `Week of <range>` with any trailing
   ` (inclusive)` stripped.
3. **What you need to know** (eyebrow "Summary") — bullet list pulled
   verbatim from `report-md`'s `## TL;DR` section (the extractor tolerates
   a numbered heading such as `## 1. TL;DR`), with the bold prefix
   preserved. Falls back to a single auto volume sentence only if no
   TL;DR bullets are found.
4. **Progress at a glance chart** — Dual-axis line chart of
   `User Rating` vs `Negative comments` across every published week,
   built from `metrics_v2.json`. Sage = positive-rating %, terracotta =
   negative-comment volume. Datalabels above/below each marker; 5-tick
   grid; legend in the card header. Rendered to a PNG via headless
   Chromium and attached inline via Content-ID (`cid:ocv_progress_chart`)
   — **renders in every client: OWA, Classic Outlook for Windows,
   Outlook for Mac, Apple Mail, Gmail, and the Outlook mobile apps.**
   Skip the chart entirely by passing `--chart-mode none`, `--metrics ""`,
   or by deleting the metrics file. The legacy `--chart-mode svg` is kept
   for debugging only — OWA strips inline SVG on its sanitization round-trip.
5. **Topic shifts (WoW)** — full 13-row table, sorted by this-week
   count desc, with a colored "delta pp" chip per row
6. **Closing block** (separated by a top border):
   - ADO query link (if `--ado-query-url` provided)
   - One or more configurable paragraphs (`--closing-paragraph`,
     repeatable) — e.g., the walk-through offer
   - Sign-off (`--signoff`, defaults to `Cheers,<br><em>_Yohn</em>`)

> The `--intro`, `--progress-blurb`, `--highlights`, and `--mirror-url`
> flags still parse but are **no longer rendered** in the body since the
> 2026-07-01 reshape. The dashboard link now lives only in the chart
> card's "Interactive version on the dashboard" footer.

The script ensures every section degrades gracefully:

- No prior manifest → KPI deltas read "no prior", WoW chips render `—`
- No report MD → TL;DR shows a single auto-generated volume sentence
- No ADO query URL → closing block still renders the other paragraphs
- No metrics file → Progress-at-a-glance section omitted entirely (no
  empty card, no broken layout)

## Errors and recovery

| Failure | Fix |
|---|---|
| `Cannot create ActiveX component 'Outlook.Application'` | Classic Outlook isn't installed at the expected path. Either install it, or use the New Outlook web UI to compose manually from the preview HTML. |
| Helper hangs forever | First call to `New-Object -ComObject Outlook.Application` can take 60–180s on a cold start. Wait. |
| Draft created but not visible in OWA | Classic Outlook hasn't pushed yet. Trigger Send/Receive via the COM script `files/force_sync.ps1` pattern (`$ns.SendAndReceive($false)` + iterate `$ns.SyncObjects`). Give it 20–30 seconds. |
| Verify call reports `found=false` | The EntryID changed (Outlook re-IDs items on first sync sometimes). Re-run with `--verify` against the new top item in Drafts, or just open Outlook UI to confirm. |
| Body looks broken in Classic Outlook ribbon preview | The reading-pane preview uses a more aggressive sanitizer than the actual draft. Open the draft in its own window — that's how recipients will see it. |
| OWA shows stale body after an append/update | Open the draft once in OWA, close it, reopen — OWA caches the rendered body per-draft. |
| OWA renders the chart broken (numbers visible but no lines) | Stop using `--chart-mode svg`. OWA's sanitizer strips `<svg>` entirely on its sync round-trip; the user may briefly see un-stripped SVG before sync completes, then it disappears. Re-run with the default `--chart-mode cid`. Confirmed 2026-06-08. |
| `[draft] PNG render failed; falling back to inline SVG` warning | Playwright Chromium not installed. Install: `pip install playwright && playwright install chromium`. The fallback still writes a draft, but the chart will only render in non-OWA clients. |
| Chart PNG attachment shows up in the recipient's attachment list | The PowerShell helper didn't set `PR_ATTACHMENT_HIDDEN`. Re-create the draft — every successful run sets both `PR_ATTACH_CONTENT_ID` (0x3712001F) and `PR_ATTACHMENT_HIDDEN` (0x7FFE000B). |

## Relationship to other skills

- **Consumes**: outputs of `ocv-analyze-and-ticket` (manifest +
  subtopics CSV + report MD), and optionally the live URL from
  `ocv-publish-github` (no hard dependency — defaults to the standard
  `gim-home/OCV-Weekly` URL pattern).
- **Sibling**: `send-email` skill does general-purpose Outlook mail
  via the `microsoft-outlook-mail` MCP. That MCP is not always
  attached in this environment and requires Graph consent; this skill
  is the COM-based fallback specifically for the weekly OCV draft.
- **Successor in `ocv-weekly`**: this is the orchestrator's optional
  step 6 (the human-distribution step). After it runs, the user owns
  the rest of the lifecycle: review the draft, add recipients, send.

## Compliance

- The draft body is the same artifact `ocv-publish-report` declares
  safe to share with leadership: aggregate stats, OCV item IDs, and
  PM-paraphrased issue descriptions — **no raw verbatim**.
- The draft sits in the user's local OST and (after Send/Receive)
  their Exchange Online mailbox. No data leaves the org boundary.
- The script never sends. Sending is always a manual action the user
  performs in Outlook after reviewing the draft.

## Files

- `scripts/draft_ocv_email.py` — main builder + CLI (includes
  `render_progress_png` Playwright helper)
- `scripts/draft_ocv_email_com.ps1` — PowerShell COM helper
  (`create` / `verify` / `append`). The `create` action accepts
  `-ChartImage <png> -ChartCid <id>` to rewrite the body's relative
  `<img>` to `cid:<id>` and attach the PNG as a hidden inline (CID)
  MAPI attachment in a single shot.
- `output/ocv/email-drafts/ocv_email_<week>.html` — local preview HTML
  (references the chart PNG by relative filename so it opens cleanly
  in a browser; COM helper rewrites to `cid:` at draft-creation time)
- `output/ocv/email-drafts/ocv_progress_chart_<week>.png` — rendered
  chart PNG (cid mode). Attached inline; never sent as a visible
  attachment.

---
name: ocv-weekly
description: >
  End-to-end weekly OCV pipeline for a date range. Orchestrates the four
  sub-skills (ocv-extract-feedback → ocv-extract-dash → ocv-analyze-and-ticket →
  ocv-publish-report) so the user can go from "run the weekly OCV pipeline
  from 2026-05-12 to 2026-05-18" to a publishable HTML dashboard in one
  request, with a single review pause after the subtopics CSV is generated
  so the user can edit it before tickets are filed and the report is
  rendered. Use when the user asks to "run the weekly OCV pipeline",
  "do the full weekly OCV run", "extract-analyze-publish for last week",
  "run OCV end-to-end from <date> to <date>", or any phrasing that wants
  all four steps chained together for a single date range.
---

# OCV Weekly Pipeline (Orchestrator)

This is a **meta-skill**. It does not extract, classify, or render anything
itself — it invokes four core skills in the canonical order (`ocv-extract-feedback`
→ `ocv-extract-dash` → `ocv-analyze-and-ticket` → `ocv-publish-report`),
with two optional extension steps (`ocv-ticket-sync` for ADO sync, `ocv-publish-github`
for Pages deployment), with the right arguments threaded between them, and
it pauses for the user's subtopics review at the natural handoff point.

The four sub-skills stay fully isolated and independently invocable:

| # | Sub-skill              | Input                          | Output                                         |
|---|------------------------|--------------------------------|------------------------------------------------|
| 1 | `ocv-extract-feedback`          | date range, area               | `data/ocv_<area>_<from>_to_<to>_range.csv`     |
| 2 | `ocv-extract-dash`     | date range                     | `data/dash_ocv_<from>_to_<to>.csv`             |
| 3 | `ocv-analyze-and-ticket` | both CSVs from steps 1 & 2  | `_manifest.json` + `_subtopics.csv` + `_report.md` |
| 4 | `ocv-publish-report`   | the three artifacts from step 3 | `output/ocv_<area>_<week>.html`                |
| 5 | `ocv-publish-github` (optional) | the HTML from step 4 + manifest from step 3 | new commit on `gim-home/OCV-Weekly` `main` |

If any step changes its internal behavior, only that step needs to be
edited — this orchestrator just declares the sequence and the data
handoffs.

---

## When to invoke

Triggers include (any of these phrasings):

- "Run the weekly OCV pipeline from `<YYYY-MM-DD>` to `<YYYY-MM-DD>`"
- "Do the full weekly OCV run for `<area>` from `<from>` to `<to>`"
- "Extract, analyze, and publish OCV for the past 7 days"
- "End-to-end OCV for last week"
- "Run OCV weekly end-to-end"
- "All four OCV skills for `<from>` to `<to>`"

Do **not** invoke for single-skill requests (e.g., "just publish the
report from the existing manifest" → call `ocv-publish-report` directly;
"just classify this CSV" → call `ocv-analyze-and-ticket` directly).

---

## Parsing the user's request

Extract from the prompt:

1. **Date range** — `from` and `to` in `YYYY-MM-DD` form.
   - If the user says "past N days" or "last week", compute the range
     against today (default `to` = today, `from` = today − N days).
   - If only a single date is given, treat as a single-day run
     (`from` = `to` = that date).
   - If the range is ambiguous (no dates, no relative phrase),
     **ask the user** for the exact `from`/`to` before doing anything.
2. **Area** — defaults to `outlook-agent`. If the user names another area,
   verify a matching config exists at `configs/<area>.json` first; if not,
   fall back to `outlook-agent` and confirm with the user.
3. **Skip-to-step** (optional) — if the user says "I already have the
   CSVs, just classify and publish" or "re-publish from the existing
   manifest", jump to step 3 or step 4 respectively. Use the
   "Resuming partway" section below.

Surface the resolved `area`, `from`, `to` to the user before starting
step 1 so they can correct any misread.

---

## Plan: how to run the pipeline

Print the plan to the user **before** starting step 1. Use a numbered
list with the resolved arguments filled in. Then execute steps 1 → 4
in order, pausing at the handoff between step 3 and step 4.

### Step 1 — Extract OCV verbatim CSV

Invoke the `ocv-extract-feedback` skill with the resolved range. The expected
output is a single range CSV:

```
data/ocv_<area>_<from>_to_<to>_range.csv
```

If `ocv-extract-feedback` writes per-day CSVs instead, the orchestrator should
collect all of them and concatenate (or pass the directory to step 3).
Confirm the final CSV path before moving on.

### Step 2 — Extract Copilot Dash → OCV mapping CSV

Invoke the `ocv-extract-dash` skill with the same range:

```
python scripts/dash_ocv_extract.py --from <from> --to <to>
```

This is **interactive** (Edge opens, you sign in once, then you add
the `Ticket ID` and `OCV ticket` columns in the Copilot Dash UI per the
skill's prompts). Wait for it to finish. Expected output:

```
data/dash_ocv_<from>_to_<to>.csv
```

If a `dash_ocv_<from>_to_<to>.csv` already exists from a prior run that
day, ask the user whether to reuse it or re-extract.

### Step 3 — Analyze and ticket

Invoke `ocv-analyze-and-ticket` against the two CSVs from steps 1 & 2.
The skill itself reads the CSVs via the Copilot model (there is no
runnable script — it is a doctrine-driven skill). Pass both CSV paths
explicitly so the model joins on `OcvId` / `OCV ticket`.

Expected outputs (all three are required for the best step 4 result):

- `data/manifests/ocv_<area>_<to>_manifest.json` — permanent, safe to share
- `data/ocv_<area>_<to>_subtopics.csv` — engineering ticket queue
- `data/ocv_<area>_<to>_report.md` — TL;DR + key findings markdown

After step 3 finishes, surface the three paths and the row totals
(negative items classified, number of subtopic rows).

### Pause for review — REQUIRED

**Stop here. Do not run step 4 automatically.**

Present:

- Path to the subtopics CSV
- Row count and a one-line preview of the top 5 subtopics by `Item Count`
- A note that this is the right moment to edit Priority, Ticket Worthy,
  or Brief Title columns before they bake into the HTML report and
  before tickets are filed

Then **ask the user**: "Subtopics CSV is ready at `<path>` —
review/edit it now if you need to, then say 'publish' to render the
HTML report." Use the `ask_user` tool with choices
`["Publish now", "Sync to ADO first, then publish", "I'll edit the CSV first, then say publish"]`.

Only proceed to step 4 once the user confirms.

### Step 3.5 (optional) — Sync to Azure DevOps

If the user picked **"Sync to ADO first, then publish"** at the
review pause (or asks at any later point "now sync these to ADO"),
invoke the `ocv-ticket-sync` skill. **Two confirmation gates from
`ocv-ticket-sync` will fire automatically — do not bypass them.**

1. Run `python scripts/ado_sync.py propose --subtopics <path>
   --dash-csv <dash-path>` to produce
   `data/ado_proposals_ocv_<area>_<to>.json`. The summary line
   reports `N link / M create / K already-synced`.
2. **GATE A — scope choice.** Before opening the JSON for per-row
   review, `ask_user` how the user wants to approach this run
   (walk every row / walk only the `create` rows / only P0–P1 /
   only one named row / hand-edit the JSON / cancel). See
   ocv-ticket-sync's SKILL.md for the full menu. Apply the chosen
   strategy by pre-setting `decision.action = "skip"` on rows the
   user does not want to walk.
3. Walk each remaining row with the user via `ask_user`, offering
   "Link to match #N", "Create new", or "Skip" per row.
4. **GATE B — final write-count confirmation.** After all per-row
   decisions are saved to the JSON, re-read it and `ask_user`:
   *"Ready to apply: N create, M link, K skip → N+M ADO writes.
   Proceed?"* with options `["Yes, apply all", "No, let me
   re-review", "Cancel"]`.
5. Only on "Yes, apply all" → run
   `python scripts/ado_sync.py execute --proposals <json>`.
   The script ALSO blocks on stdin for the literal string `yes`
   before any ADO call. Use `--yes` only because the agent has
   just cleared Gate B with the user; never as a default.
6. After execute finishes, the resulting `ADO URL` + `ADO action`
   + `Dash links` columns are written back into the subtopics CSV.

After this step finishes, `ocv-publish-report` (step 4) automatically
picks up the new `ADO URL` column and renders an `ADO ↗` button on
each ticket card — no extra flag needed.

If the user picks "Cancel" at either gate, exit ocv-ticket-sync cleanly
without touching ADO and return to the step 3 review pause so they
can pick "Publish now" instead.

See `.claude/skills/ocv-ticket-sync/SKILL.md` for full doctrine,
including the script's `--dry-run` flag for plan-only previews.

### Step 4 — Publish the HTML report

Invoke `ocv-publish-report`. The script auto-discovers the prior
manifest (via the `wow_basis` field in the current manifest), so the
minimum call is:

```bash
python scripts/publish_ocv_report.py \
  --manifest data/manifests/ocv_<area>_<to>_manifest.json \
  --subtopics data/ocv_<area>_<to>_subtopics.csv \
  --report-md data/ocv_<area>_<to>_report.md
```

If a prior manifest is **not** auto-discoverable (e.g., first run of a
new area, or the prior file isn't named with the expected pattern),
pass `--prior-manifest <path>` explicitly when known, otherwise let
the WoW section render its "WoW comparison not available" note.

Expected output: `output/ocv_<area>_<to>.html` (single self-contained
file, opens in the browser by default).

After step 4, surface to the user:

- HTML output path
- File size
- A note: "Paste this into Loop or attach to email — it's a
  self-contained file."

Then **ask the user** whether to publish to GitHub Pages (step 5).

### Step 5 (optional) — Publish to gim-home/OCV-Weekly

If the user picks **"Publish to GitHub Pages"** at the prompt above
(or asks at any later point "push this to the OCV-Weekly repo"),
invoke the `ocv-publish-github` skill. It will:

1. Ask for the **highlights** line (one-sentence summary for the
   landing card). Pre-fill from the report MD's TL;DR when possible,
   but always confirm.
2. Run the script in `--dry-run` mode and show the publish plan
   (source file, target path, `reports.json` add/replace, card
   preview, commit message, expected live URL).
3. `ask_user` for final confirmation: "Yes, publish / No, revise /
   Cancel".
4. On confirm, re-run the script for real — which (a) pulls + rebases
   the local clone, (b) copies the HTML into `reports/<week>.html`,
   (c) upserts the entry in `reports.json`, (d) commits, (e) pushes
   to `origin/main`.
5. Print the live URLs:
   - Landing: `https://gim-home.github.io/OCV-Weekly/`
   - This report: `https://gim-home.github.io/OCV-Weekly/reports/<week>.html`

See `.claude/skills/ocv-publish-github/SKILL.md` for full doctrine and
flag overrides.

If the user picks "No, don't publish" at the prompt, the local HTML in
`output/` is still ready to paste into Loop / email — the pipeline is
complete either way.

---

## Resuming partway

The pipeline is restart-safe at every step boundary because each
sub-skill writes deterministic file paths derived from `area` and `to`
date. If anything fails mid-run:

| User says / situation                       | Resume from |
|---------------------------------------------|-------------|
| "Just publish to GitHub"                    | Step 5 only |
| "Just re-publish the report"                | Step 4 only (then offer step 5) |
| "Re-classify with my edited subtopics CSV"  | Step 3 + 4 (with edited subtopics CSV honored) |
| "Re-extract Dash, the column was missing"   | Step 2 onward |
| "Start over"                                | Step 1 onward |
| Step N failed with an error                 | Fix the error per that sub-skill's doctrine, then re-run from step N |

Detect resume intent by checking which artifacts already exist for the
resolved `area`/`to`:

- If `output/ocv_<area>_<to>.html` exists and user says "publish" → ask
  whether they mean ocv-publish-report (regenerate) or ocv-publish-github (upload).
- If `_ocv_weekly_repo/reports/<to>.html` already exists, ocv-publish-github
  will surface `replaced entry` in its plan output — confirm the
  intentional re-publish with the user before proceeding.
- If `data/manifests/ocv_<area>_<to>_manifest.json` exists and user
  asks for the full pipeline → ask whether to skip steps 1–3 or
  re-run everything.

---

## What success looks like

A run for `outlook-agent` over `2026-05-12 → 2026-05-18` should
produce, in order:

```
data/ocv_outlook-agent_2026-05-12_to_2026-05-18_range.csv      (step 1)
data/dash_ocv_2026-05-12_to_2026-05-18.csv                      (step 2)
data/manifests/ocv_outlook-agent_2026-05-18_manifest.json       (step 3)
data/ocv_outlook-agent_2026-05-18_subtopics.csv                 (step 3)
data/ocv_outlook-agent_2026-05-18_report.md                     (step 3)
output/ocv_outlook-agent_2026-05-18.html                        (step 4)
```

Plus, if the user opted into step 5:

```
_ocv_weekly_repo/reports/2026-05-18.html                         (step 5)
_ocv_weekly_repo/reports.json                                    (step 5, upserted)
new commit on origin/main of gim-home/OCV-Weekly                 (step 5)
https://gim-home.github.io/OCV-Weekly/reports/2026-05-18.html    (live)
```

The orchestrator's final message to the user should list all paths so
they can find anything later without remembering naming conventions.

---

## Relationship to other skills

This skill **invokes** the five sub-skills. It does not duplicate any
of their logic. Doctrine for each step lives in its own SKILL.md:

- `.claude/skills/ocv-extract-feedback/SKILL.md`
- `.claude/skills/ocv-extract-dash/SKILL.md`
- `.claude/skills/ocv-analyze-and-ticket/SKILL.md`
- `.claude/skills/ocv-publish-report/SKILL.md`
- `.claude/skills/ocv-ticket-sync/SKILL.md` (optional step 3.5)
- `.claude/skills/ocv-publish-github/SKILL.md` (optional step 5)

If a sub-skill's interface changes (new flags, different output paths),
update the corresponding section above and nothing else.

---

## Compliance

This orchestrator processes **Customer Content** indirectly (via the
sub-skills). The compliance constraints of the strictest sub-skill in
the chain apply: run this orchestrator only via **GitHub Copilot CLI**
(AOAI / Anthropic via Copilot). Do not use with Claude Code.

The final HTML artifact (`output/...html`) and the manifest JSON are
both safe to share with leadership — they contain aggregate stats,
OCV item IDs, and PM-paraphrased issue descriptions, but no raw
verbatim. The intermediate CSVs (steps 1, 2, and the subtopics CSV)
contain Customer Content and must follow the standard data-lifecycle
rules in `AGENTS.md` (clean up after use).

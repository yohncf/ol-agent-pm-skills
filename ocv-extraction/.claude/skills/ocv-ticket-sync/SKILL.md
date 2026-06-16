---
name: ocv-ticket-sync
description: >
  Sync the high-signal rows of an ocv-analyze-and-ticket subtopics CSV
  with Azure DevOps work items. Eligible rows are P0/P1, plus P2/P3
  rows with count>1 or a matched Copilot Dash ticket. For each row,
  the skill finds candidate matches in the team's ADO project, presents
  them to the user for interactive per-row approval, then either appends
  the OCV + Dash links to an existing item or creates a new Bug. After
  execution, the ADO URLs and Dash links are written back into the
  subtopics CSV so ocv-publish-report can render an ADO badge and Dash
  links on each ticket card. Use when the user asks to "file ADO tickets
  from subtopics", "sync OCV subtopics to ADO", "create the ADO items
  for this week", "open tickets for P0/P1 rows", or "match-or-create
  ADO bugs from the OCV report". Do NOT use for SEVAL regression bugs — use
  `seval-regression-ticket-sync` (different input schema, always-create, no
  match-or-create logic).
---

# OCV → ADO Ticket Sync

Closes the loop between the OCV analysis pipeline and the engineering
backlog. The skill never silently creates or modifies tickets — every
action is approved by the user one row at a time before it touches ADO.

## When to invoke

Triggers include:

- "Sync the OCV subtopics to ADO"
- "Create/file the ADO tickets for this week's subtopics"
- "Match-or-create ADO bugs for the P0/P1/P2 rows"
- "Open ADO items for the OCV report"
- Direct request as part of the `ocv-weekly` orchestrator's optional
  step 3.5 (between subtopics review and HTML publish)

Do **not** invoke for:

- "Just show me the subtopics" → no ADO needed
- "Publish the HTML report" → call `ocv-publish-report` directly
- "Move an ADO item between states" → not in scope; user does that in
  the ADO UI

## Prerequisites

1. `az login` (the script uses `DefaultAzureCredential` against the
   Azure DevOps resource — same pattern as `ocv-extract-ods`)
2. `pip install --only-binary=:all: azure-identity` (the `--only-binary`
   flag is required on ARM64 Windows to avoid a source-build of
   `cryptography`)
3. A populated subtopics CSV from `ocv-analyze-and-ticket`
4. Optionally, the matching `data/dash_ocv_<date>.csv` from
   `ocv-extract-dash` so the script can attach Copilot Dash links to
   each ADO item

## Two-phase workflow with TWO mandatory confirmation gates

This skill is split into **propose** and **execute** phases. Between
them, the agent walks the user through the proposals interactively.
**Two distinct user-confirmation gates are mandatory** — the skill must
never silently create or modify ADO items.

```
propose  --->  [Gate A: scope choice]  --->  per-row review
                                                 |
                                                 v
                                       [Gate B: final write count]  --->  execute
```

### Gate A — How should we approach this run?

Immediately after `propose` finishes and BEFORE the agent opens the
JSON for per-row review, the agent uses `ask_user` to pick the review
strategy. Present the propose-summary counts first
(`N link / M create / K already-synced`), then offer these choices:

| Choice | What the agent does next |
|---|---|
| **Walk every row one at a time** (default, conservative) | Per-row `ask_user` for each non-`skip` proposal |
| **Walk only the `create` rows; auto-accept `link` defaults** | Same as above but agent silently keeps `decision.action = "link"` for high-confidence matches (score >= 0.30) |
| **Walk only P0 / P1 rows; skip the rest this week** | Agent sets `decision.action = "skip"` on every P2 row before review, then walks only P0/P1 |
| **Walk only a specific row I name** | Agent asks "which subtopic?", sets all others to `skip`, walks the named one |
| **Just open the JSON, I'll edit it myself** | Agent prints the JSON path and stops — user hand-edits, then says "ok, run execute" |
| **Don't sync this week — cancel** | Agent exits the skill cleanly without touching ADO or the CSV |

The chosen strategy controls how many rows the agent walks; it does NOT
change the script behavior. The script always honors whatever
`decision.action` ends up in the JSON.

### Gate B — Final write-count confirmation

After per-row review is complete and BEFORE the agent runs `execute`,
it MUST surface the exact write counts to the user via `ask_user`. The
proposals JSON has already been updated to reflect every per-row
decision; the agent reads it back and counts:

> Ready to apply: **N create**, **M link**, **K skip** -> **N+M ADO writes**.
> Proceed?
>
> - Yes, apply all
> - No, let me re-review (returns to Gate A)
> - Cancel

Only if the user picks "Yes, apply all" does the agent invoke
`execute`. The script ALSO enforces this gate: if you run
`../shared/ado_sync.py execute` without `--yes`, it prints the same
plan and waits for `yes` on stdin before any ADO call. **The agent
must never pass `--yes` without first clearing Gate B with the user
via `ask_user`.**

For previewing without committing, `--dry-run` prints the same plan
and exits without touching ADO. Useful sanity check before the real
run.

### Phase 1 — propose

```bash
python ../shared/ado_sync.py propose \
  --subtopics data/ocv_<area>_<to>_subtopics.csv \
  --dash-csv  data/dash_ocv_<date>.csv
```

The `--dash-csv` argument is optional but **strongly preferred**: when
provided, the script joins each OCV item URL to its corresponding
Copilot Dash ticket and (a) embeds Dash links in the ADO body, (b)
includes them in the eligibility decision, (c) writes a `Dash links`
column back to the subtopics CSV.

Defaults (override per flag if the user requests):

- `--area-path "Outlook Web\Outlook Copilot Service\Outlook Agent"`
- `--iteration "Outlook Web"`
- `--tag "OutlookAgent"` (used both as a candidate-filter and as the tag
  applied to NEW items)
- `--work-item-type "Bug"`
- `--owners-config ../shared/configs/ado_owners_outlook-agent.json` — owner-routing
  rules used to populate `decision.assignee` on every CREATE row. First
  matching rule wins; rows that match no rule are left unassigned (the
  agent can still hand-edit `decision.assignee` in the JSON). Pass an
  empty string (`--owners-config ""`) to disable auto-assignment.

The script:

1. Reads every row of the subtopics CSV and filters to the **eligible
   rows** (see Eligibility rule below).
2. Runs a WIQL query against the project to fetch open work items
   under the Area Path (and optionally tagged), then batch-fetches
   their Title/State/Tags.
3. For each subtopic row, ranks the candidate items by a
   **title-only Jaccard + surface keyword boost** (e.g., a candidate
   whose title contains "calendar" or "meeting" gets +0.20 when the
   subtopic's Category is `Scheduling`) and keeps the top **8**.
   For the **default `decision.action`**, the script also computes
   a raw-Jaccard `link_score` (no surface boost) and extracts the
   **failure-mode verbs** (move, modify, create, send, schedule,
   archive, apply, draft, refuse, ignore, …) from each title so the
   "is this the same bug?" decision is verb-aware, not just noun-aware.
3. Writes `data/ado_proposals_ocv_<area>_<to>.json` with one entry
   per row: subtopic metadata, the top candidate matches with their
   IDs and URLs, `dash_links`, the **`prior_ado`** block if the
   subtopics CSV already records a previous sync for that row, and
   a `decision` block.

### Default `decision.action` (overridable per row by the agent)

The default uses the raw-Jaccard `link_score` (no surface boost) and
the failure-mode verb sets to avoid auto-linking same-noun /
different-verb collisions like "Rules: Inbox rule created — doesn't
apply to existing emails" being silently linked to "Inbox Rules:
Refuses to modify existing rule".

| Condition                                                                                | Default action |
|------------------------------------------------------------------------------------------|----------------|
| Row already has `ADO URL` in the subtopics CSV                                           | `skip` (notes explain) |
| `link_score` ≥ 0.65 (STRONG_MATCH_OVERRIDE — titles nearly identical)                    | `link` to that candidate |
| 0.40 ≤ `link_score` < 0.65 AND subtopic + candidate **share at least one verb** (or neither title has any failure-mode verb) | `link` to that candidate |
| 0.40 ≤ `link_score` < 0.65 AND verb sets are non-empty but **disjoint**                  | `create` (notes explain the verb mismatch) |
| `link_score` < 0.40                                                                      | `create` |

`decision.notes` is populated with a short explanation whenever the
default lands on `link` (showing the shared verbs / strong overlap) or
on `create` after a verb-mismatch override, so the human reviewer can
see why.

The agent is still required to read the candidate list before accepting
any default — see "Interactive review" below.

### Eligibility rule

Not every subtopic is worth filing. The script keeps a row only if:

- Priority is **P0** or **P1**, OR
- `Count > 1` (multiple OCV items reported the same issue), OR
- The row has at least one matched **Dash ticket**

P2 / P3 rows with a single OCV item and no Dash backing are skipped to
keep noise out of the backlog.

### Interactive review (your job, between phases)

**CRITICAL**: The default `decision.action` and the candidate score are
**hints, not verdicts**. For every row, the Copilot agent MUST read the
full `candidates` array and reason semantically before accepting the
default. A candidate with score 0.15 that shares the same surface
(e.g., both about Calendar) is a better link than a 0.40 candidate
that just shares stopwords. The score is biased toward title-overlap;
it cannot tell that "Calendar: Meeting not created" and "Scheduling:
Calendar action acknowledged but no event created" are the same bug.

**When showing each row, surface the failure-mode verbs explicitly**
so the reviewer can spot same-noun / different-verb collisions at a
glance. Each proposal carries:
- `verbs` on the proposal — the failure-mode verbs extracted from the
  subtopic title.
- `verbs` on every candidate — the same extraction applied to that
  candidate's ADO title.

If `decision.notes` already cites a verb mismatch (e.g., `subtopic=
['apply','created'] vs candidate=['modify','refuses']`), the default
was deliberately downgraded to `create` — only flip it back to `link`
if you have a specific reason the verbs are interchangeable in
context.

Open the JSON. For each entry, the Copilot agent walks the user
through it row-by-row using the `ask_user` tool. Per row, present:

- Subtopic title, priority, count, topic, surface, **and the subtopic's
  failure-mode verbs**
- 1-line of `issue_description`
- Number of OCV + Dash items
- The top candidate matches as a numbered list with title + state +
  URL + **its failure-mode verbs** (always show at least the top 3,
  regardless of score)
- A choice prompt with options:
  - `Link to match #1 (id 12345)` (one per candidate, capped at 3 to
    keep the menu compact)
  - `Create a new <type>` (uses the row's `Brief title` + description)
  - `Skip this row`

When the user picks "Link to match #N", the agent edits the JSON to set
`decision.action = "link"` and `decision.match_id = <that id>`.
When the user picks "Create", set `decision.action = "create"`.
When the user picks "Skip", set `decision.action = "skip"`.

If the user prefers, they can also edit the JSON manually before
execute — both paths work. **What is not acceptable** is bulk-setting
`action: "create"` on a script's top-N rows without reading their
candidate lists; that is exactly how duplicates get filed.

### Phase 2 — execute

```bash
# Standard run — script prompts for 'yes' before any ADO write
python ../shared/ado_sync.py execute \
  --proposals data/ado_proposals_ocv_<area>_<to>.json

# Preview only — print the plan, never touch ADO
python ../shared/ado_sync.py execute \
  --proposals data/ado_proposals_ocv_<area>_<to>.json \
  --dry-run

# Bypass interactive prompt (ONLY after the agent has cleared Gate B
# with the user via ask_user)
python ../shared/ado_sync.py execute \
  --proposals data/ado_proposals_ocv_<area>_<to>.json \
  --yes
```

The script's first action is always to print the **Gate B plan**: per-row
list of every ticket it is about to CREATE and every existing item it is
about to APPEND to, plus a total write count. Without `--yes` (and
without `--dry-run`), it blocks on stdin waiting for the literal
string `yes`.

For each entry, once Gate B is cleared:

- `action: "link"` → GETs the existing item, then PATCHes it:
  appends the OCV + Dash links **into `System.Description`** (so they
  are visible in the body, not buried in Discussion) and writes a
  short `System.History` audit note.
- `action: "create"` → POST a new work item of the configured type
  (default `Bug`) with:
  - `System.Title` = decision.new_title (defaults to subtopic Brief title)
  - `System.AreaPath` = configured area path
  - `System.IterationPath` = configured iteration
  - `Microsoft.VSTS.Common.Priority` = numeric from P0..P3 mapping
    (1, 2, 3, 4)
  - `System.Tags` = configured tag
  - `Microsoft.VSTS.TCM.ReproSteps` + `System.Description` = HTML body
    containing the issue description, a date stamp (the sync date),
    and a flat link list — one bullet per OCV item, rendered as
    `<OCV link> - <Dash link> [<Path>: <model>, <model>, ...]` when a
    matching Dash ticket exists and the dash CSV carried resolved-model
    info, `<OCV link> - <Dash link>` when models were not extracted,
    `<OCV link>` when no Dash link exists (and `<Dash link> [<Path>:
    <models>]` for any Dash orphan). No per-section headers, no
    auto-created footer. The ADO body does **not** include the subtopic's
    topic/category/count metadata — those are bookkeeping for the
    analysis, not signal for engineering.
- `action: "skip"` → no-op, logged

After all actions complete, the script appends five columns to the
subtopics CSV (parallel-aligned by index, ` | ` joined):

| Column            | Value                                                       |
|-------------------|-------------------------------------------------------------|
| `ADO URL`         | `https://outlookweb.visualstudio.com/.../_workitems/edit/N` |
| `ADO action`      | `created`, `linked`, or empty (skipped rows)                |
| `Dash links`      | ` | ` joined Copilot Dash ticket URLs (when --dash-csv used) |
| `Resolved Models` | ` | ` joined, parallel to `Dash links`; each cell is a `;`-joined list of model names that ran on that conversation |
| `Path`            | ` | ` joined, parallel to `Dash links`; each cell is the diagnosed routing path slug (`CodeGen-Claude`, `Sydney-Tools+WorkBerry`, etc.) |

`ocv-publish-report` automatically renders an `ADO ↗` button, a `Dash:`
link row on each ticket card (with a small `[<Path>: <models>]` chip
next to each Dash link), and a "Routing path × surface" aggregate table
when these columns are populated.

## Owner auto-assignment

Created bugs are auto-assigned to an engineer at creation time so the
right person gets the ADO email notification.

- Routing rules live in `../shared/configs/ado_owners_outlook-agent.json`
  (gitignored; the area-team-private config). Each rule has:
  `match.category` (e.g. `Search`, `Scheduling`), `match.topic` (parent
  topic like `"4. Constraints ignored"`), and/or
  `match.title_keywords` (matched against the subtopic Brief title
  ONLY — never the description; PM-paraphrased descriptions are too
  noisy and produced mis-routes during initial testing).
- **Rule order matters.** First match wins. Place the more specific
  topic/category-based rules BEFORE keyword catch-alls. For example,
  `Drafting + Topic-4 (Constraints ignored) → Ethan` must come before
  `Drafting + "handoff" keyword → Vivek`.
- Rows that match no rule are surfaced as **unmapped** and stay
  unassigned (no silent assignment to a fallback). Either extend the
  config or pass `--override ID=email` on the retro-patch.
- `propose` reads the config and writes `decision.assignee`,
  `decision.assignee_name`, `decision.assignee_rule` on each `create`
  row. Existing items (`link` rows) keep their current assignee.
- `execute` Gate B prints the per-row plan including the assignee.
  `build_create_payload` adds a `System.AssignedTo` JSON-patch op so
  the bug is filed already assigned.

### Retroactively assigning owners to already-created items

Use the `assign-owners` subcommand when bugs were filed before owner
routing was set up, or when you need to fix-up assignees:

```bash
# Dry-run (always do this first to verify the per-row plan)
python ../shared/ado_sync.py assign-owners \
  --subtopics data/ocv_<area>_<to>_subtopics.csv \
  --dry-run

# With per-ID overrides for rows that have no rule
python ../shared/ado_sync.py assign-owners \
  --subtopics data/ocv_<area>_<to>_subtopics.csv \
  --override 432566=yohnathanc@microsoft.com \
  --override 432576=yohnathanc@microsoft.com

# Apply (agent MUST show user the plan via ask_user before passing --yes)
python ../shared/ado_sync.py assign-owners \
  --subtopics data/ocv_<area>_<to>_subtopics.csv \
  --yes
```

Behavior:

- Walks rows where `ADO action == "created"` (linked rows are left
  alone; their owner already exists).
- By default, items that already have an assignee in ADO are
  **skipped** (no stomp). Pass `--reassign` to overwrite existing
  assignees.
- Writes a `System.History` note on every PATCH for audit:
  `"Auto-assigned to <email> by ocv-extraction/ocv-ticket-sync
  owner-routing (rule: <label>)."`
- Reports per-owner totals + a final
  `N patched, M skipped (already assigned), K failed` summary.

## Workflow inside the orchestrator

When called as step 3.5 of `ocv-weekly`:

1. `ocv-analyze-and-ticket` finishes (steps 3) → subtopics CSV is ready
2. Orchestrator pauses for subtopics review (existing behavior)
3. **New: ask "Sync to ADO now?"** — if yes, run this skill
   (propose → interactive review → execute), then continue
4. `ocv-publish-report` runs (step 4) — picks up the `ADO URL` column
   automatically

If the user says "no, just publish", skip ADO sync and continue
straight to step 4 — the dashboard will simply not show ADO links.

## What to surface to the user

**During phase 1 (propose):**
- Number of P0/P1/P2 rows
- Suggested action counts (`N link, M create`)
- Path to the JSON

**During interactive review:**
- One row at a time via `ask_user`
- Never batch more than 5 rows in a row without giving the user a
  status checkpoint ("3 of 12 done — keep going?")

**During phase 2 (execute):**
- Per-row log: `[new] #12345 ← <title>` or `[link] #67890 ← <title>`
- Final summary: created / linked / skipped counts
- Updated CSV path
- Reminder: "re-run ocv-publish-report (or the orchestrator step 4) to
  render the new ADO links in the dashboard"

## Errors and recovery

- **`az login` not done** → DefaultAzureCredential throws; surface the
  error verbatim and tell the user to `az login` then re-run
- **HTTP 401/403** → likely insufficient permissions to write to that
  Area Path; show the WIQL response body so the user can diagnose
- **HTTP 400 on create** → usually missing required field (e.g., the
  team's Bug template requires Severity); show the response body, ask
  the user which field to add, then edit `build_create_payload()` in
  `../shared/ado_sync.py`
- **Partial execute failure** (e.g., row 7 of 12 fails): the script
  already wrote rows 1–6 back to the CSV; user can edit the JSON to
  set already-handled rows to `action: "skip"` and re-run execute

## Compliance

- The OCV item links in the subtopics CSV go straight into the ADO
  Description / History — these point to OCV (Customer Content). ADO
  is the canonical destination for these per existing team practice.
- No raw user verbatim is written to ADO — only the PM-paraphrased
  `Brief title` and `Issue description` from the subtopics CSV.
- Auth uses `DefaultAzureCredential` → no PAT or secret stored in the
  repo.
- Run only via GitHub Copilot CLI, per `AGENTS.md`.

## Files

- `../shared/ado_sync.py` — the script (propose / execute / assign-owners
  subcommands)
- `../shared/configs/ado_owners_outlook-agent.json` — owner-routing rules used by
  `propose` and `assign-owners` to populate `System.AssignedTo`. Gitignored.
- `data/ado_proposals_ocv_<area>_<to>.json` — per-run proposals,
  reviewed between phases; safe to delete after the report is
  published
- `data/ocv_<area>_<to>_subtopics.csv` — updated in place with the
  `ADO URL` and `ADO action` columns

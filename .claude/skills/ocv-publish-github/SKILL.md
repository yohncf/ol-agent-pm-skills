---
name: ocv-publish-github
description: >
  Upload the freshly-built OCV weekly HTML report to the
  gim-home/OCV-Weekly GitHub repo and update the landing-page manifest
  (reports.json) so it appears on the index. Copies the HTML into
  reports/<week>.html, upserts the manifest entry by week_of, then
  commits and pushes to origin/main. Always prints a publish plan and
  waits for explicit confirmation before any filesystem write or git
  push. Use when the user asks to "publish the report to GitHub",
  "upload the weekly HTML to the OCV-Weekly repo", "push the dashboard
  to Pages", "publish to gim-home/OCV-Weekly", or as the optional
  final step of the ocv-weekly orchestrator.
---

# Publish OCV Report to GitHub Pages

This skill mirrors `ocv-ticket-sync`'s two-gate doctrine: nothing is ever
written silently. The user always sees the plan first, and the script
itself blocks on stdin until the literal string `yes` is typed (unless
the agent passes `--yes` after having cleared confirmation in chat).

## When to invoke

Triggers include:

- "Publish the report to GitHub"
- "Upload this week's HTML to the OCV-Weekly repo"
- "Push the dashboard to Pages"
- "Publish to gim-home/OCV-Weekly"
- "Make the weekly report public on the OCV-Weekly site"
- Optional final step (step 5) of the `ocv-weekly` orchestrator

Do **not** invoke for:

- "Just generate the HTML" → call `ocv-publish-report` directly; don't push
- "Edit the landing page" → hand-edit `index.html` in
  `_ocv_weekly_repo` and commit manually; this skill only touches
  `reports/<week>.html` + `reports.json`

## Prerequisites

1. A built HTML report from `ocv-publish-report`
   (e.g. `output/ocv_outlook-agent_<week>.html`)
2. The matching analyze manifest at
   `data/manifests/ocv_outlook-agent_<week>_manifest.json` (for
   auto-deriving `week`, `range`, `negatives`, `subtopics`)
3. A local clone of the OCV-Weekly repo. Default location:
   `_ocv_weekly_repo/` (sibling of the `ocv-extraction` working directory)
   The script auto-clones from `https://github.com/gim-home/OCV-Weekly.git`
   if the clone is missing.
4. Git credentials for github.com configured in Windows Credential
   Manager (one-time; already set up via the GitHub Pages PAT flow).

## Workflow

### 1. Gather inputs

Before invoking the script, **ask the user for the one editorial input
the script cannot derive**: the **highlights** line for the landing
card. Pull a draft from the report MD's TL;DR if available, but always
confirm with the user — this is the single sentence that summarizes the
week for leadership scanning the index.

Use `ask_user` with three options:

- Use my suggested highlight: "<one-line summary>"
- Let me write it (freeform)
- Skip — card will show without a highlight

### 2. Dry-run preview (recommended)

```bash
python scripts/publish_to_github.py \
    --manifest data/manifests/ocv_outlook-agent_<week>_manifest.json \
    --html     output/ocv_outlook-agent_<week>.html \
    --highlights "<the agreed line>" \
    --dry-run
```

Print the plan output to the user. It shows:

- Source HTML path + size
- Target file path inside the local clone
- Whether `reports.json` is `added` or `replaced` for this `week_of`
  (replaced = there is already a report card for that week; the
  script will overwrite it)
- Card preview: label, range, negatives, subtopics, highlights, published
- The first line of the commit message
- The expected live URL once Pages is enabled

### 3. Confirmation gate

After showing the dry-run plan, `ask_user`:

> "Ready to publish: copy <html> -> reports/<week>.html, upsert
> reports.json (action), then commit + push to origin/main. Proceed?"

Options:

- Yes, publish
- No, let me revise the highlights/label first
- Cancel

Only on "Yes, publish" → run the real command.

### 4. Real publish

```bash
python scripts/publish_to_github.py \
    --manifest data/manifests/ocv_outlook-agent_<week>_manifest.json \
    --html     output/ocv_outlook-agent_<week>.html \
    --highlights "<the agreed line>"
```

The script's own stdin prompt also fires here (defense in depth). Type
`yes` to clear it. If the agent has just cleared confirmation in chat,
it MAY pass `--yes` to skip the script-level prompt — but never pass
`--yes` without first running the dry-run preview + ask_user gate.

The script will:

1. `git pull --rebase origin main` (sync with remote first)
2. Copy the HTML into `reports/<week>.html`
3. Insert/replace the entry in `reports.json` (sorted by `week_of` desc)
4. `git add -A`
5. `git commit -m "<auto message>"`
6. `git push origin main`

On success, print the live URLs:

```
Landing page : https://gim-home.github.io/OCV-Weekly/
This report  : https://gim-home.github.io/OCV-Weekly/reports/<week>.html
```

## Overriding defaults

All of the following are optional but useful:

| Flag | What it does |
|---|---|
| `--week 2026-05-18` | Override the reporting week (else from manifest) |
| `--label "Week of May 18, 2026"` | Override card title (else derived from `--week`) |
| `--range "May 12 - May 18, 2026"` | Override card eyebrow (else from manifest's `date_range`) |
| `--negatives 88` | Override the metric (else from manifest) |
| `--subtopics 54` | Override the metric (else counted from the subtopics CSV) |
| `--repo <path>` | Use a different local clone path |
| `--remote-url <url>` | Push to a different remote (e.g. fork) |
| `--branch <name>` | Push to a different branch (else `main`) |
| `--commit-message "..."` | Override the auto-generated commit message |

## Errors and recovery

| Failure | Fix |
|---|---|
| `git pull --rebase` rejected (local has commits that diverge) | The script logs the error; user runs `git pull --rebase` manually in the clone, resolves any conflict, then re-runs publish |
| `git push` rejected (auth) | Open Windows Credential Manager, confirm `git:https://github.com` entry is valid; re-run |
| Same `week_of` already in `reports.json` | Treated as `replaced` (intentional re-publish). Surface this in the plan so the user notices. |
| Highlights forgotten | Card renders without the description line; rerun with `--highlights "..."` to amend |
| Wrong file pushed | Run again with the correct `--html`; the script overwrites `reports/<week>.html` and updates `published` date in `reports.json` |

## Relationship to other skills

- **Consumes**: `ocv-publish-report` output (the HTML) + the
  `ocv-analyze-and-ticket` manifest (for metadata)
- **Updates**: `_ocv_weekly_repo/reports/<week>.html` and
  `_ocv_weekly_repo/reports.json`
- **Pushes to**: `github.com/gim-home/OCV-Weekly`
- **Renders at**: `https://gim-home.github.io/OCV-Weekly/`

This skill never modifies `index.html` or any other file in the repo —
those are templating concerns owned by the user, not the pipeline.

## Compliance

- The pushed HTML is the same artifact `ocv-publish-report` declares
  safe to share with leadership: aggregate stats, OCV item IDs, and
  PM-paraphrased issue descriptions — **no raw verbatim**.
- The GitHub Pages site is currently behind GitHub auth (`gim-home`
  org membership). Treat the URL as Microsoft FTE-only until/unless
  the user explicitly enables public access.
- Push uses Git Credential Manager (Windows Credential Store) — no
  PAT stored in the repo.

## Files

- `scripts/publish_to_github.py` — the script
- `_ocv_weekly_repo/` — local clone (created on first run if missing)
- `_ocv_weekly_repo/reports/<week>.html` — published reports
- `_ocv_weekly_repo/reports.json` — landing-page manifest

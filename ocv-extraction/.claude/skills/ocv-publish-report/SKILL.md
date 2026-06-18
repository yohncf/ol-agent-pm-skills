---
name: ocv-publish-report
description: |
  Render a self-contained, dark-themed HTML dashboard from an
  ocv-analyze-and-ticket manifest JSON (+ optional subtopics CSV, prior-week
  manifest, and markdown report). Output is a single .html file suitable for
  pasting a link into Loop, attaching to an exec email, or hosting on a
  SharePoint site. Use when the user asks to "publish the OCV report",
  "make an HTML version of this week's OCV", "build the LT dashboard",
  "share this OCV analysis with my leadership team", "render the manifest
  as a webpage", or has just run `ocv-analyze-and-ticket` and wants a
  presentable artifact.
---

# Publish OCV Report

Turn the weekly OCV manifest into a single self-contained HTML dashboard
that an LT can read in a browser without any local dependencies, Loop
plugins, or PowerPoint.

The HTML page uses Google's public-site font stack
(`Google Sans Display` / `Google Sans Text` / `Google Sans Code`) over a
dark theme, with a sticky sidebar TOC, KPI grid, topic ranking, WoW table,
category breakdown, an expandable P0 / P1 / P2 ticket queue, and (when
the manifest carries a `paths` block) a "Routing path × surface" table
attributing negatives to the agent path that produced them
(`CodeGen-Claude`, `CodeGen-GHCP`, `Sydney-Tools`, `Sydney-Tools+WorkBerry`,
…). Each Dash link on a ticket card also gets a small `[<Path>: <models>]`
chip when the subtopics CSV carries `Resolved Models` + `Path` columns.

## When to invoke

Triggers include: "publish the OCV report", "make an HTML version of the
weekly OCV", "render the manifest as a webpage", "build the LT dashboard",
"share this with my leadership", "create a single-file report for Loop",
"turn the analyze-and-ticket output into something I can email".

This skill is the **presentation layer** for the `ocv-analyze-and-ticket`
skill. It does not classify or summarize. It only renders what
`ocv-analyze-and-ticket` produced.

## Inputs

| Input | Required | Source |
|-------|----------|--------|
| `--manifest <path>` | **Yes** (unless `--demo`) | `data/manifests/ocv_<area>_<YYYY-MM-DD>_manifest.json` from `ocv-analyze-and-ticket` |
| `--subtopics <path>` | Recommended | `data/ocv_<area>_<YYYY-MM-DD>_subtopics.csv` — populates the P0/P1/P2 ticket queue. Auto-discovered as a sibling file if omitted. If the CSV contains an `ADO URL` column (populated by the `ocv-ticket-sync` skill), each ticket card renders an `ADO ↗` button linking to the work item. |
| `--prior-manifest <path>` | Optional | Earlier-week manifest for the same area. Auto-discovered via `wow_basis` field, then via filename pattern in `data/manifests/`. If absent, the WoW section renders a note explaining how to enable it. |
| `--report-md <path>` | Optional | The TL;DR + Key Findings markdown report (if `ocv-analyze-and-ticket` emits one). If absent, the script synthesizes a brief TL;DR from manifest aggregates and omits the Key Findings card grid. |
| `--out <path>` | Optional | Output path. Default: `output/ocv/reports/ocv_<area>_<week>.html`. |
| `--no-open` | Optional | Skip the auto-launch in the default browser. |
| `--demo` | Optional | Render with synthetic data; ignores `--manifest`. Used to regenerate `output/ocv/reports/ocv_weekly_dashboard_sample.html`. |
| `--area-label <text>` | Optional | Friendly display label (default: title-cased area slug). |

## Prerequisites

1. A manifest produced by `ocv-analyze-and-ticket` at
   `data/manifests/ocv_<area>_<YYYY-MM-DD>_manifest.json`.
2. Python 3.10+ available on PATH (the script is pure Python — no
   third-party packages are required for rendering).
3. (Recommended) The sibling subtopics CSV at
   `data/ocv_<area>_<YYYY-MM-DD>_subtopics.csv` so the P0/P1/P2 ticket
   queue renders.
4. (Optional) A prior-week manifest in `data/manifests/` so the WoW
   section can compute deltas. Auto-discovered when absent.

## How to run

End-to-end weekly flow (PM perspective):

```bash
# 1. (Once) extract this week's OCV CSV
node scripts/extract_standalone.js --config configs/outlook-agent.json \
  --start 2026-05-11 --end 2026-05-18

# 2. Classify + emit manifest + subtopics CSV (+ optional report MD)
# (done by the agent invoking the `ocv-analyze-and-ticket` skill)

# 3. Render the HTML report
python scripts/publish_ocv_report.py \
  --manifest data/manifests/ocv_outlook-agent_2026-05-18_manifest.json \
  --out output/ocv/reports/ocv_outlook-agent_2026-05-18.html
```

The script auto-discovers the prior manifest and the sibling subtopics CSV,
so step 3 is usually a single flag. The browser opens the result.

To regenerate the sample dashboard (useful when iterating on the template):

```bash
python scripts/publish_ocv_report.py --demo \
  --out output/ocv/reports/ocv_weekly_dashboard_sample.html
```

## Output

A single `.html` file (~50–60 KB) that:

- Is fully self-contained (no JS framework, no build step, no external assets
  beyond Google Fonts via CDN)
- Renders dark-only with a navy + gold + terracotta accent palette
- Has a sticky sidebar TOC with scroll-spy on the right-margin layout
- Surfaces the same sections as the markdown report (TL;DR with caveats,
  dataset summary, key findings, topic ranking with inline bars, WoW table
  with ▲/▼/● deltas, category breakdown grid, routing-path × surface
  attribution table, P0/P1/P2 ticket queue)

The HTML can be:

- **Opened directly** (the script does this by default, opening it in the system's default browser)
- **Pasted as a link in Loop / Teams / email** by hosting on SharePoint or a
  share drive (file is self-contained — drop and link)
- **Archived per-week** in the `output/ocv/reports/` folder for historical lookback

## Compliance & data handling

The manifest JSON is a permanent, **safe-to-publish** artifact: it contains
**no raw verbatim**, only OCV item IDs, counts, AI-generated short
rationales, and aggregate breakdowns (per the analyze-and-ticket doctrine).

The subtopics CSV `Issue description` column contains **PM-paraphrased**
issue descriptions (not user verbatim) and OCV item URLs. These are also
considered safe to share with engineering and leadership.

This skill performs **no** AI inference on customer content. It only reads
already-produced artifacts and renders them. It is safe to run outside of
GitHub Copilot CLI for this reason, but the upstream skills it depends on
(`ocv-extract-feedback`, `ocv-analyze-and-ticket`) must be run under Copilot CLI.

## Relationship to other skills

- **Upstream:** `ocv-analyze-and-ticket` is the only source of valid
  manifest JSONs for this skill. If the user asks for an HTML report
  without first running analyze-and-ticket, run that skill first.
- **Downstream / siblings:** None. This is a leaf skill.
- **Do not invoke:** Do not call `ocv-analyze` (the older single-pass
  analysis skill). Its output schema is incompatible.

## Design lock-ins

The following are deliberate and should not be changed without user input:

- **Dark theme only.** The user explicitly requested dark-only; do not add
  light-mode CSS.
- **Google's font stack.** `Google Sans Display`, `Google Sans Text`, and
  `Google Sans Code` are loaded from Google Fonts to match the
  visual feel of Google's own public product pages. Fallbacks are
  `Roboto` / `Roboto Mono` / system UI.
- **Navy + gold + terracotta accents.** Carried over from the editorial
  reference design; works in dark mode with the muted Google Material
  variants (`#8ab4f8`, `#fdd663`, `#f28b82`).
- **Self-contained single file.** No external JS frameworks, no build
  step. The page must work when opened directly from disk and when copied
  to a different machine.

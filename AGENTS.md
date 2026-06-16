# OLAgentWork — Agent Guidance

This repository root is **OLAgentWork**, a monorepo holding two related skill
suites and the code they share. Read this file first, then the project-specific
`AGENTS.md` for whichever project you are working in.

> **⚠️ Approved AI tool: GitHub Copilot CLI only.**
> Parts of this repo process **Customer Content** (OCV verbatim feedback, ODS
> ticket data). Per E+D Data Use Guidance (March 2026), only AI tools backed by
> AOAI or Anthropic models via GitHub Copilot may analyze that data. **Claude
> Code is not approved** for customer-data processing. Do not run extraction or
> analysis skills against customer data from a non-approved tool. See
> [`ocv-extraction/AGENTS.md`](ocv-extraction/AGENTS.md) for the full policy.

## Layout

```
OLAgentWork/
├── ocv-extraction/   # OCV / ODS / Dash feedback extraction, analysis, publishing, ADO ticketing
├── seval-analysis/   # SEVAL eval synthesis, regression analysis, publishing, ADO ticketing
└── shared/           # ado_sync.py, lib/, agent_models_reference.json, configs/ (owners)
```

## Cross-project map

The two projects are siblings. They are **aware of each other** and share code:

- **`ocv-extraction/`** owns the OCV/ODS/Dash pipeline and the `ocv-*` skills.
  Its `ocv-ticket-sync` skill drives `../shared/ado_sync.py`.
- **`seval-analysis/`** owns the `seval-*` skills. It grew out of OCV tooling:
  `seval-regression-ticket-sync` imports `../shared/ado_sync.py` and reuses the
  same owners-routing config; `seval-synthesize-queries-from-ocv` consumes the
  CSVs produced by OCV extraction. `seval-regression-publish` publishes to the
  same OCV-Weekly GitHub Pages site that `ocv-publish-github` uses.
- **`shared/`** holds the code both depend on:
  - `ado_sync.py` — ADO match/create engine + owner routing (used by both ticket-sync skills)
  - `lib/` — `csv_parser.js`, `manifest_writer.js`, `pii_scrub.py`, `model_path.py`
  - `agent_models_reference.json` — canonical model/routing-path catalog
  - `configs/ado_owners_outlook-agent.json` — shared owners-routing config (git-ignored; user-local)

## How skills find each other

Skills are organized per project under `<project>/.claude/skills/`. When you
work in a project directory, that project's skills are in scope. Shared scripts
are invoked via `../shared/...` from either project root and resolve their own
internal imports relative to their file location, so the current working
directory does not matter for shared-code resolution.

## Conventions that apply repo-wide

- **HTML reports use a dark theme.** OCV dashboards, SEVAL regression reports,
  and the auto-generated listing pages all default to dark mode.
- **Analysis voice (OCV/ODS):** write as a PM — quantify, don't editorialize;
  neutral framing; paraphrase in PM voice; acknowledge sampling bias. Full
  rubric in `ocv-extraction/AGENTS.md` § "Analysis Tone and Voice".
- **Shared owners config:** `../shared/configs/ado_owners_outlook-agent.json`
  is the single owners-routing source for both ticket-sync skills.

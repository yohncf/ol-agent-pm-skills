# OLAgentWork

Workspace for Outlook Agent (OL Agent) PM tooling. It groups two related skill
suites plus the common code they share.

```
OLAgentWork/
├── ocv-extraction/    # OCV / ODS / Copilot Dash feedback extraction + analysis + ticketing
├── seval-analysis/    # SEVAL eval-query synthesis + regression analysis + ticketing
└── shared/            # Common code reused by both (ADO sync, owners config, lib utilities)
```

## The two projects

| Project | What it does | Skills |
|---------|--------------|--------|
| [`ocv-extraction/`](ocv-extraction/AGENTS.md) | Extract verbatim customer feedback from **OCV**, **ODS** tickets, and **Copilot Dash**; analyze into themes/taxonomy; publish HTML dashboards; sync to Azure DevOps. | `ocv-setup`, `ocv-extract-feedback`, `ocv-extract-ods`, `ocv-extract-dash`, `ocv-analyze`, `ocv-analyze-and-ticket`, `ocv-ticket-sync`, `ocv-publish-report`, `ocv-publish-github`, `ocv-weekly` |
| [`seval-analysis/`](seval-analysis/AGENTS.md) | Synthesize eval queries from real utterances; compare two SEVAL HeroEval runs; render regression reports; publish them; file ADO bugs per regression cluster. | `seval-synthesize-queries-from-ocv`, `seval-regression-analyze`, `seval-regression-publish`, `seval-regression`, `seval-regression-ticket-sync` |

The SEVAL suite grew out of the OCV tooling — it reuses the same ADO sync
engine, owners-routing config, and the 13-topic taxonomy. That common code now
lives in [`shared/`](shared/README.md) so both projects depend on one copy.

## Working in this repo

Each project is self-contained: run its skills / scripts with that project
folder as the working directory (e.g. `cd ocv-extraction` then `npm run check`).
Shared code is referenced via `../shared/...` and resolves regardless of the
current directory because the shared scripts locate their dependencies relative
to their own file location.

See [`AGENTS.md`](AGENTS.md) for agent-facing guidance (compliance, the
cross-project map, and where each skill set lives).

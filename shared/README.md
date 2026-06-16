# shared/

Common code reused by both [`ocv-extraction/`](../ocv-extraction/AGENTS.md) and
[`seval-analysis/`](../seval-analysis/AGENTS.md). Keep one copy here rather than
duplicating it per project.

## Contents

| Path | What it is | Consumed by |
|------|------------|-------------|
| `ado_sync.py` | Azure DevOps match-or-create engine + owner routing (propose / execute / assign-owners). | OCV `ocv-ticket-sync`; SEVAL `seval-regression-ticket-sync` (`from ado_sync import ...`) |
| `lib/csv_parser.js` | RFC 4180 CSV parser. | OCV `cleanup_csvs.js`, `compose_scenario_analysis.js` |
| `lib/manifest_writer.js` | Manifest read/write helper. | OCV analyze + cleanup |
| `lib/pii_scrub.py` | PII scrubbing at write time. | OCV `ods_api_extract.py` |
| `lib/model_path.py` | Diagnoses routing/model path; reads `agent_models_reference.json`. | `ado_sync.py`, OCV `dash_ocv_extract.py` |
| `agent_models_reference.json` | Canonical model + routing-path catalog. | `lib/model_path.py` |
| `configs/ado_owners_outlook-agent.json` | Owners-routing config (area path, iteration, tags, assignee map). **Git-ignored / user-local.** | both ticket-sync skills (default `--owners-config`) |

## How it's referenced

- **Python:** consumer scripts add `shared/` to `sys.path` (via a `../../shared`
  path relative to their own file) and `import ado_sync` / `from lib... import`.
  `ado_sync.py` resolves its `lib/` import and its default owners config relative
  to its own location, so it works no matter the caller's working directory.
- **Node:** consumer scripts `require('../../shared/lib/<module>')`.

## Owners config

`configs/ado_owners_outlook-agent.json` is intentionally **not committed** (it
maps real people to areas). Create it locally; both `ocv-ticket-sync` and
`seval-regression-ticket-sync` default to this shared path. Only `.gitkeep` is
tracked in `configs/`.

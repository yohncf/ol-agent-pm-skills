# seval-analysis

SEVAL eval-query synthesis and HeroEval regression analysis, reporting, and ADO
ticketing. Sibling of [`../ocv-extraction`](../ocv-extraction/README.md) under
the **OLAgentWork** repo root; shares common code in
[`../shared`](../shared/README.md).

## Skills

- `seval-synthesize-queries-from-ocv` — utterances → eval queries + assertions (YAML)
- `seval-regression-analyze` — compare two HeroEval runs → dark-themed HTML regression report
- `seval-regression-publish` — publish the report to the OCV-Weekly GitHub Pages site
- `seval-regression-ticket-sync` — file one ADO Bug per regression cluster
- `seval-regression` — orchestrator (analyze → publish → optional ticket-sync)
- `seval-run-triage` — single-run failure triage → fingerprint manifest + markdown + HTML report

## Quick start

Work with this folder as the current directory. Example (analyze):

```bash
python scripts/eval_regression_extract.py --control <control.csv> --experiment <exp.csv> \
  --control-json <control_settings.json> --experiment-json <exp_settings.json> ...
python scripts/eval_regression_render.py --manifest data/eval-manifests/<name>_manifest.json
```

Ticket-sync reuses the shared ADO engine in `../shared/ado_sync.py` and the
shared owners config `../shared/configs/ado_owners_outlook-agent.json`.

See [`AGENTS.md`](AGENTS.md) for the full skill catalog, scripts, shared
dependencies, and conventions. Eval-query authoring follows
[`docs/EVAL_DOCTRINE.md`](docs/EVAL_DOCTRINE.md).

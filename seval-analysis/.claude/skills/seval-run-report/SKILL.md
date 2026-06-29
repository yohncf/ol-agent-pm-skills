---
name: seval-run-report
description: >
  Render a self-contained dark-themed HTML report from the NEW unified
  single-run SEVAL report JSON — the all-in-one format the eval team emits as
  eval_report_<name>_<DD_MM_YYYY_HHMM>.json. Unlike seval-run-triage (which
  joins four separate run artifacts: Assertions CSV, Queries TSV, doctrine YAML,
  Settings JSON), this format bundles the summary aggregates AND every
  per-query result (assertion judge rationales, execution dimensions, generated
  code, execution result, performance metrics) into ONE file, so a single
  deterministic script produces the report. Use when the user hands you a single
  eval_report_*.json and asks to "create a report from this eval", "render this
  eval JSON", "make the standalone eval report", or "report on this single run".
  Do NOT use for two-run regression comparisons (use seval-regression-analyze)
  or for the legacy 4-artifact run folder (use seval-run-triage). To put the
  rendered report on the OCV-Weekly site, hand off to seval-run-publish.
---

# SEVAL Single-Run Report (new unified JSON)

The eval team is moving to a **single self-contained report JSON** that bundles
everything the old four-artifact run-triage needed. This skill renders that file
straight into a dark-themed, self-contained HTML report with **one deterministic
script** — no LLM classification, no artifact joining.

If you are instead handed a SEVAL run **folder** with four separate files
(`[Assertions]_*.csv` + `*.tsv` + `*.yaml` + `Settings.json`), this is the wrong
skill — use **`seval-run-triage`**. If you have **two** runs to compare, use
**`seval-regression-analyze`**.

## Input format

One file, named like `eval_report_outlook_seval_22_06_2026_0640.json`. Top-level
keys:

| Key | Notes |
|---|---|
| `dataset` | e.g. `./eval/datasets/HeroPrompt_V27.json` |
| `evaluation_name` | e.g. `outlook_seval` |
| `evaluation_models` | judge model id |
| `expected_results_count` / `generated_results_count` | query counts |
| `summary` | `{ "<evaluator>": { total_evaluations, total_assertions, assertions_passed, assertion_score_pct, queries_passed, query_pass_rate_pct, level_breakdown{critical,expected,aspirational}, tag_breakdown } }` |
| `evaluators` | list of evaluator names, e.g. `["outlook"]` |
| `results` | **a DICT keyed by evaluator** → `{ "<evaluator>": [ row, ... ] }`. **NOT a list.** |

Each `results[<evaluator>][i]` row carries: `test_id`, `user_prompt`, `tags`,
`main_agent_model`, `agent_response_preview`, `assertion_results[]`
(each `{ assertion_id, level, judge_result{criteria, rationale, score} }`),
`judge_result{assertions_passed, assertions_total, assertion_score}`,
`dimensions{tool_call_made, code_generated, execution_success, has_return_value,
answer_generated, answer_correct}`, `execution_trace{code_generated,
execution_result, iteration_count}`, and `performance_metrics{total_tokens,
cost_usd, overall_latency_ms, tool_call_count, llm_turns, ...}`.

> **Gotcha:** `results` is a **dict keyed by evaluator name**, not a list. The
> renderer flattens `results[evaluator]` (and falls back to a list-of-blocks
> shape if a future export changes it). PowerShell's `ConvertFrom-Json` makes it
> *look* like an array with `.Count`; Python sees the dict. Trust the dict shape.

## How to run

```bash
# from the seval-analysis project root
python scripts/eval_single_run_report.py \
  --in  "<path>\eval_report_<name>_<DD_MM_YYYY_HHMM>.json" \
  [--out data/eval-reports/<name>.html] \
  [--run-name "Friendly label"] \
  [--run-date 2026-06-22]
```

Defaults:

- `--out` &rarr; `data/eval-reports/<input-basename>.html`.
- `--run-date` &rarr; parsed from the `DD_MM_YYYY` in the filename, else today.
- `--run-name` &rarr; `<evaluation_name> — <run-date>`.

The script is **deterministic and dependency-free** (Python stdlib only). It is
safe to re-run; it overwrites the output.

## What the report contains

- **KPI cards** — query pass rate (queries_passed / total), assertion score
  (assertions_passed / total), queries failed, total cost (sum of `cost_usd`),
  avg / max latency. Headline figures are taken from the authoritative `summary`
  block when present, and recomputed from rows otherwise.
- **Assertion score by level** — critical / expected / aspirational table with
  pass bars (critical is the headline KPI).
- **Execution dimensions** — aggregate true-rate across the six `dimensions`
  booleans (tool call made, code generated, execution success, has return value,
  answer generated, answer correct).
- **Per-query results** — one merged, filterable, collapsible list **sorted by
  assertion score ascending** (worst first):
  - Each row header shows **ID · Prompt · Assertions (x/y) · Score (+ bar)** plus
    the **dimension pills** (red ✗ flags failures at a glance, no expand needed).
  - **Filter** buttons: All / Failing only / Passing only (do **not** auto-expand
    rows — the user opens what they want).
  - Expanding a row reveals: the agent response, every assertion (pass/fail badge
    + level + judge criteria + rationale, failed ones highlighted), and two
    nested dropdowns — **Generated code** and **Execution result** — plus a perf
    line (tokens, cost, latency, tool calls, LLM turns, iterations).

The output is **dark-themed and self-contained** (inline CSS/JS, no external
assets), per the repo-wide HTML convention.

## Validate

After rendering, sanity-check the numbers against the file's own `summary` and
confirm the `<details>` blocks balance and equal the query count. The HTML's KPI
cards must match the summary (e.g. `28 / 65 queries`, `255 / 325 assertions`).

## Hand-off

- To **publish** the rendered report to the OCV-Weekly GitHub Pages site (the
  **EVAL Run Analysis** / `eval-runs.html` section), pass it to
  **`seval-run-publish`** (copies into `eval-runs/<slug>.html`, upserts
  `eval-runs.json`, regenerates the listing, two-gate push + mirror).
- For **run-over-run** comparison, this report is the single-run companion to
  **`seval-regression-analyze`**.

## Compliance

- HTML is **dark-themed** and self-contained.
- SEVAL replies / judge rationales are model output but **untrusted** when
  embedded — the renderer HTML-escapes every injected string via `esc()`.
- Analysis voice (if you summarize): PM voice — quantify, state the denominator,
  neutral framing, no crisis language.

---
name: seval-fetch-regression-inputs
description: >
  Auto-download the four input files needed by `seval-regression-analyze`
  straight from the SEVAL portal, given a single descriptor JSON that lists
  the two runs to compare. For EACH run it drives seval.microsoft.com
  (Playwright + Edge, persistent SSO profile), opens the **LM Checklist** tab,
  switches the view dropdown to **Assertion View**, downloads the assertions
  results CSV, then opens the **JSON config** tab and downloads the SEVAL
  Settings JSON. Files are renamed by run id so the two runs never collide,
  and every download is validated by file extension before it is saved. Use
  when the user says "fetch the SEVAL regression inputs", "download the eval
  results for these two runs", "grab the assertions CSV and config for run X
  and Y", or supplies a `regression.json` descriptor with a `sevals[]` array.
  Do NOT use to analyze or compare runs — that is `seval-regression-analyze`.
---

# SEVAL Fetch Regression Inputs

Download the **2 assertions CSVs + 2 Settings JSONs** that
`seval-regression-analyze` needs, directly from the SEVAL portal, from a
single descriptor JSON. This removes the manual "click around the SEVAL UI
and rename four files" step at the front of the regression pipeline.

This skill is the **acquisition layer** of the SEVAL regression pipeline. It
does not analyze anything — it only lands validated input files on disk, then
prints the exact `seval-regression-analyze` argument block to run next.

## When to invoke

Triggers include:

- "Fetch / download the SEVAL regression inputs for these two runs"
- "Grab the assertions CSV and JSON config for run `<id-A>` and `<id-B>`"
- "I have a `regression.json`, get me the eval files"
- "Pull the LM-checklist assertion results for these runs"

Do **not** invoke for:

- Comparing / analyzing two runs you already have files for → `seval-regression-analyze`
- End-to-end run (fetch → analyze → publish) → `seval-regression` orchestrator
- A single run's failure triage → `seval-run-triage`

## Inputs

| Input | Source | Notes |
|-------|--------|-------|
| Descriptor JSON | Path (`--input`) | Must contain a `sevals` array of **exactly two** entries; see shape below |

The descriptor shape (the first entry is the **control** / baseline run, the
second is the **experiment** run):

```json
{
  "sevals": [
    { "date": "06/11/26", "queryset": "Hero V2.7",
      "control": "Mainline", "experiment": "CodeGen",
      "url": "https://seval.microsoft.com/detail/559151" },
    { "date": "06/12/26", "queryset": "Hero V2.7",
      "control": "Mainline", "experiment": "CodeGen",
      "url": "https://seval.microsoft.com/detail/561048" }
  ]
}
```

- `url` — any `https://seval.microsoft.com/detail/<id>` link; the run id is parsed from it.
- `control` / `experiment` — the within-run arm labels (passed through to
  `--control-name` / `--experiment-name` downstream).
- `date` — used to emit copy-paste-ready `--control-date` / `--experiment-date`
  (auto-converted to `YYYY-MM-DD`).

If the descriptor is missing, malformed, or does not have exactly two
`sevals[]` entries, **ask the user** — do not guess.

## How to run

```bash
python scripts/seval_fetch_regression_inputs.py \
  --input "C:\path\to\regression.json"
```

Optional flags:

- `--out-dir <path>` — override the destination folder.
- `--login-timeout <seconds>` — how long to wait for SSO / SPA render (default 300).

The **first** run opens a headed Edge window using the persistent profile
`.browser-profile-seval/` (git-ignored). If Microsoft SSO is prompted,
complete it once; the session is reused on later runs.

## Outputs

Default destination: `seval-input/regression_<control-id>_<experiment-id>/`
(relative to the repo root; `seval-input/` is git-ignored). Four files:

```
<control-id>_assertions.csv     # ~1.5 MB, LM-checklist Assertion View export
<control-id>_settings.json      # ~20 KB,  SEVAL JSON config
<experiment-id>_assertions.csv
<experiment-id>_settings.json
```

On success the script prints the ready-to-run `seval-regression-analyze`
argument block (with ISO dates already converted), e.g.:

```
--control-csv  .../559151_assertions.csv
--control-json .../559151_settings.json
--control-id 559151 --control-name Mainline --control-date 2026-06-11
--experiment-csv  .../561048_assertions.csv
--experiment-json .../561048_settings.json
--experiment-id 561048 --experiment-name CodeGen --experiment-date 2026-06-12
```

Hand those straight to `seval-regression-analyze`.

## How it works (and why it is robust)

For each run the driver:

1. Navigates to the detail URL and waits for the SPA tablist to render.
2. Polls for the **LM Checklist** tab (it lazy-mounts a few seconds after the
   initial tablist) and clicks it via `get_by_role("tab", ...)` only — never a
   text fallback, which previously mis-matched stray body text.
3. Switches the view dropdown from *Query View* to **Assertion View**.
4. Clicks the assertions grid's button with `aria-label="Download"` and saves
   the file **only if** its name ends in `.csv`.
5. Opens the **JSON config** tab and tries each unlabeled viewer-toolbar icon,
   keeping the one that yields a `.json` download.

The extension validation in steps 4–5 is the key guard: the assertions
`Download` button persists in the DOM after switching tabs, so a naive
"last download button" selector would silently save the CSV bytes into the
JSON file. Every save is rejected unless the suggested filename matches the
expected extension.

## Notes

- The two runs of the same scheduled comparison usually share an **identical**
  Settings JSON (same queryset/variants); that is expected, not a collision —
  the per-run difference lives in the assertions CSVs.
- Run this skill with `seval-analysis/` as the working directory.

## Compliance

This skill only touches SEVAL eval artifacts (assertions, model replies,
config flags) — **not** OCV/ODS customer verbatim content — so the
customer-data tooling restriction does not apply. Standard repo rules still
hold: never commit the downloaded files (they live under git-ignored
`seval-input/`), and never commit the `.browser-profile-seval/` SSO profile.

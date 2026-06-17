---
name: seval-regression-publish
description: >
  Publish a freshly-rendered SEVAL regression HTML report to the
  gim-home/OCV-Weekly GitHub repo, then auto-mirror to
  yohncf/OCV-Weekly_temp via mirror_to_personal_v2.ps1. Copies the HTML into
  eval-reports/<date>_<control-id>_vs_<experiment-id>.html, upserts the
  eval-reports.json manifest, regenerates the eval.html listing page,
  and (on first run only) injects a discrete dropdown into the existing
  index.html so visitors can switch between OCV Weekly and EVAL Analysis.
  Always prints a publish plan and waits for explicit user confirmation
  before any filesystem write or git push (two-gate doctrine). Use when
  the user asks to "publish the regression report to GitHub", "upload
  this eval analysis", "push the SEVAL regression report to Pages", or
  as the optional final step of the seval-regression orchestrator. Does not
  analyze CSVs — use `seval-regression-analyze` for that. Does not publish OCV
  weekly reports — that is `ocv-publish-github`.
---

# Publish Eval Regression Report

This skill mirrors `ocv-publish-github`'s two-gate doctrine: nothing is
ever written silently. The user always sees the plan first, and the
script itself blocks on stdin until the literal string `yes` is typed
(unless the agent passes `--yes` after having cleared confirmation
explicitly via `ask_user` in chat).

## When to invoke

Triggers include:

- "Publish the regression report to GitHub"
- "Upload this SEVAL eval to the OCV-Weekly site"
- "Push the eval HTML to Pages"
- "Share this regression report via URL"
- "Make this eval public"
- Optional final step of the `seval-regression` orchestrator

Do **not** invoke for:

- "Just render the HTML" → call `seval-regression-analyze` directly
- "Edit the eval listing page by hand" → edit
  `_ocv_weekly_repo/eval.html` manually; this skill is the
  managed-template owner of that file and will overwrite hand edits on
  the next publish

## Prerequisites

1. A built HTML report from `seval-regression-analyze`
   (e.g. `output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html`)
2. The matching manifest at
   `data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json`
3. **Every `regressions[].why_failed` is non-empty.** The script
   refuses to publish otherwise.
4. **`publish_safety.reviewed_for_publish` must be `true`** in the
   manifest. The agent flips this from `false` → `true` only after
   the user has reviewed the report locally (the orchestrator handles
   this gate).
5. A local clone of the OCV-Weekly repo at the **monorepo root**
   `OLAgentWork/_ocv_weekly_repo/` (the script anchors this to the repo
   root regardless of the working directory). The script auto-clones from
   `https://github.com/gim-home/OCV-Weekly.git` if
   missing. `origin` pushes to `gim-home/OCV-Weekly` only. The
   personal public mirror at `yohncf/OCV-Weekly_temp` is kept in
   sync by `mirror_to_personal_v2.ps1` at the **monorepo root**
   (`OLAgentWork/`) — the publish script invokes it automatically as a post-push
   step (pass `--skip-mirror` to opt out).
6. Git credentials configured in Windows Credential Manager (one-time;
   already set up via `setup_personal_mirror.ps1`).

## Workflow

### 1. Gather inputs

Before invoking the script, **ask the user for the one editorial
input the script cannot derive**: the **highlights** line for the
landing card. Pull a draft from the manifest's `highlights` field if
present, but always confirm with the user — this is the single
sentence that summarizes the regression set for someone scanning the
EVAL listing page.

Use `ask_user` with three options:

- Use my suggested highlight: "<one-line summary>"
- Let me write it (freeform)
- Skip — card will show without a highlight

### 2. Dry-run preview (REQUIRED before real push)

```bash
python scripts/publish_eval_regression_report.py \
  --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --html     output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html \
  --highlights "<the agreed line>" \
  --dry-run
```

The dry-run prints:

- Target paths: which HTML file, which manifest, which listing page
- Slug + collision status: `new`, `replace` (same slug exists), or
  `new suffixed copy` (use `--new-version` to opt into `-2`/`-3`
  suffix; default is `replace`)
- `eval-reports.json` diff: which entry will be added or updated
- Whether `eval.html` will be created or regenerated
- Whether `index.html` will be touched (first run only injects the
  dropdown; subsequent runs leave it alone unless the markers were
  removed)
- Commit message preview
- Expected live URLs

**Gate A** — show this plan and `ask_user`:

- Yes, looks right — apply
- No, let me change something
- Cancel

### 3. Apply (real run)

Only if Gate A returned "Yes":

```bash
python scripts/publish_eval_regression_report.py \
  --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --html     output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html \
  --highlights "<the agreed line>" \
  --yes
```

The script:

1. **Strict-validates** the HTML against the manifest:
   - Refuses if any `regressions[].why_failed` is blank
   - Refuses if `publish_safety.reviewed_for_publish` is not `true`
   - Refuses if the HTML is older than the manifest (stale render)
2. **Pulls + rebases** the local clone (`git pull --rebase origin main`)
3. Copies the HTML into
   `_ocv_weekly_repo/eval-reports/<slug>.html` where slug is
   `<date>_<control-id>_vs_<experiment-id>` (with `-2`, `-3` suffix
   only if `--new-version` was passed and a same-slug file exists)
4. Upserts the entry in `_ocv_weekly_repo/eval-reports.json` keyed by
   `slug`. Sorts entries by date descending. Schema:
   ```json
   {
     "title": "OL Agent EVAL Analysis",
     "subtitle": "Regression reports for SEVAL HeroEval runs",
     "owner": "OCV Weekly Owner",
     "reports": [
       {
         "slug": "2026-06-03_538053_vs_538953",
         "label": "Mainline 538053 vs CodeGen 538953 (2026-06-03)",
         "date": "2026-06-03",
         "file": "eval-reports/2026-06-03_538053_vs_538953.html",
         "control":    { "id": "538053", "name": "Mainline", "run_date": "2026-06-03" },
         "experiment": { "id": "538953", "name": "CodeGen",  "run_date": "2026-06-03" },
         "regressions": { "control_vs_control": 13, "experiment_vs_experiment": 29, "total": 42, "comparable": 135 },
         "pass_rate_delta_pp": -0.7,
         "highlights": "…",
         "published": "2026-06-03T13:00:00Z"
       }
     ]
   }
   ```
5. **Re-renders** `_ocv_weekly_repo/eval.html` from a managed
   template (M3 dark theme, same look as `index.html`) so the listing
   always reflects the current `eval-reports.json`. The file is
   marked at the top with `<!-- EVAL-HTML-GENERATED: do not hand-edit -->`.
   At the top of the page (above the report cards) it renders a
   **Performance trend** panel — inline-SVG charts of regressions per
   run (total / Mainline-side / CodeGen-side) and the CodeGen-side
   pass-rate change vs base run (pp), plus latest-run stat callouts.
   Because the panel is built by `render_trend()` from the **full**
   `reports` list every time `eval.html` is regenerated, **every new
   published report automatically extends the trend** — no separate
   step. The panel needs ≥1 run and is fully self-contained (no JS /
   external chart library). `build_entry()` also records absolute
   per-slot pass rates and the Mainline-side delta so future charts can
   plot absolute performance once enough runs accumulate.
6. **Idempotently injects** a discrete dropdown into
   `_ocv_weekly_repo/index.html`:
   - Looks for marker comments
     `<!-- EVAL-NAV-START -->` / `<!-- EVAL-NAV-END -->`
   - If both found → replace the block between them
   - If neither found → insert just inside the top header element
     using a deterministic anchor (e.g. just after the
     `<h1>`-equivalent title)
   - If only one marker found, **or** a `.eval-nav` element exists
     without markers → abort with a clear message asking for manual
     cleanup (this protects against accidental duplication when
     someone has hand-edited index.html)
7. **Commits**: stages the new `eval-reports/<slug>.html`, the
   updated `eval-reports.json`, the regenerated `eval.html`, and the
   modified `index.html` (only on first run). Commit message format:
   `eval: publish <slug> regression report`.
8. **Pushes**: `git push origin main` to gim-home/OCV-Weekly.
9. **Mirrors to personal repo**: invokes
   `mirror_to_personal_v2.ps1` (in the ocv-extraction repo root) which
   robocopies the gim-home working tree into the
   `yohncf/OCV-Weekly_temp` clone, rewrites `gim-home → yohncf`
   back-links in the report HTML, commits, and pushes. Pass
   `--skip-mirror` to opt out, or `--mirror-script <path>` to
   override the script location. If the mirror step fails, the
   gim-home push is still kept and a warning instructs the user to
   re-run the mirror script manually.
10. Surfaces:
   - Live URL on `gim-home`:
     `https://gim-home.github.io/OCV-Weekly/eval-reports/<slug>.html`
   - Listing page URL:
     `https://gim-home.github.io/OCV-Weekly/eval.html`
   - Personal mirror URL (informational):
     `https://yohncf.github.io/OCV-Weekly_temp/eval-reports/<slug>.html`
   - Commit hash

**Gate B** — the script itself blocks on stdin for the literal `yes`
before any git push. The agent must only pass `--yes` once the user
has cleared Gate A in chat.

### 4. On cancel at any gate

Exit cleanly without touching the repo. The local artifacts in
`data/` and `output/seval/regression/` are left as-is; the user can re-run later.

## Dropdown injection block

On first publish, the script injects this block into `index.html`
inside the top header element:

```html
<!-- EVAL-NAV-START -->
<nav class="eval-nav" aria-label="Site sections">
  <details class="eval-nav-dropdown">
    <summary>Dashboards <span aria-hidden="true">▾</span></summary>
    <ul>
      <li><a href="./index.html" aria-current="page">OCV Weekly</a></li>
      <li><a href="./eval.html">EVAL Analysis</a></li>
    </ul>
  </details>
</nav>
<!-- EVAL-NAV-END -->
```

Plus a small style block between
`<!-- EVAL-NAV-CSS-START -->` / `<!-- EVAL-NAV-CSS-END -->` markers
inside `<head>` so the dropdown styling is self-contained and does not
depend on existing OCV CSS classes. The script regenerates both blocks
on every run; manual edits inside the markers are overwritten.

The same injection happens in the generated `eval.html`, just with
`aria-current="page"` moved to the EVAL Analysis link.

## Relationship to other skills

- Consumes the output of **`seval-regression-analyze`**.
- Called as the optional final step of the **`seval-regression`**
  orchestrator (after the user-review pause).
- **Does not** overlap with `ocv-publish-github`: that skill owns
  `reports/<week>.html` + `reports.json`; this skill owns
  `eval-reports/<slug>.html` + `eval-reports.json` + `eval.html`. The
  two coexist in the same repo without conflict.

## Compliance

- HTML is **dark-themed** (M3 dark palette).
- Two-gate confirmation is non-negotiable — no `--yes` without
  prior Gate A clearance.
- `publish_safety.reviewed_for_publish` must be `true` before push.
- `why_failed` strict-validation prevents publishing half-finished
  reports.
- The script is idempotent: re-running the same publish (same slug,
  no `--new-version`) replaces the existing entry rather than
  duplicating.

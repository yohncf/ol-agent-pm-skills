---
name: seval-regression-ticket-sync
description: >
  Open Azure DevOps Bugs for each regression cluster from a SEVAL HeroEval
  regression report. Reads the manifest produced by `seval-regression-analyze`,
  classifies every regression into the OCV 13-topic taxonomy + Category,
  clusters by (failing_side, topic, category), and files one Bug per cluster.
  Bugs are tagged `OutlookAgent` and `SevalRegression`, auto-assigned via
  `../shared/configs/ado_owners_outlook-agent.json`, and include base + latest SEVAL
  job URLs, failing model, every rolled-up (query, assertion, why_failed,
  failed model reply, judge rationale), and a link to the published HTML
  report. Always creates new Bugs — unlike the `ocv-ticket-sync` skill this skill
  never links to existing items. Use when the user asks to file ADO bugs
  for the regression report, open bugs for SEVAL regressions, ticket the
  eval regressions, or as the optional final step of `seval-regression`.
---

# SEVAL Regression -> ADO Ticket Sync

Files engineering-ready ADO Bugs for the regressions identified by
`seval-regression-analyze`. The bugs land in the same
`Outlook Web\Outlook Copilot Service\Outlook Agent` area path and reuse
the same owner-routing config as the OCV `ocv-ticket-sync` skill, so a
single config covers both pipelines.

This skill is the **ADO layer** of the SEVAL regression pipeline. It
does *not* re-render or re-publish the HTML report -- that already
happened upstream.

## When to invoke

Triggers include:

- "Open ADO bugs for these regressions"
- "File the SEVAL regression bugs"
- "Ticket the eval regression report"
- "Sync the SEVAL regressions to ADO"
- "Create the bugs for the regression run"
- Optional final step of the `seval-regression` orchestrator (after
  publish)

Do **not** invoke for:

- "Just render the HTML" -> `seval-regression-analyze`
- "Push the report to Pages" -> `seval-regression-publish`
- "File OCV subtopic bugs" -> `ocv-ticket-sync` (the OCV pipeline)

## Prerequisites

1. A regression manifest written by `seval-regression-analyze`:
   `data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json`. Every
   `regressions[].why_failed` must be non-empty (the script refuses
   otherwise) and `publish_safety.reviewed_for_publish` must be
   `true`.
2. The matching report HTML must already be published via
   `seval-regression-publish` (the `report_url` lands inside
   every bug body).
3. `az login` -- the script uses `DefaultAzureCredential` against the
   Azure DevOps resource (same pattern as `ocv-extract-ods` and the OCV
   `ocv-ticket-sync`).
4. `pip install --only-binary=:all: azure-identity` (the
   `--only-binary` flag is required on ARM64 Windows to avoid a
   source-build of `cryptography`).

## Policy (must hold across runs)

- **Always create**, never link. SEVAL regression bugs are always
  net-new even when the cluster looks similar to an existing item;
  duplication is preferred over silently re-opening an unrelated
  triage thread. (The OCV pipeline keeps its existing match-or-create
  behavior; that decision does not apply here.)
- **Tags:** every created Bug carries both `OutlookAgent` and
  `SevalRegression`.
- **Priority:** any cluster containing at least one `critical`-level
  assertion -> **P1**; clusters made entirely of `expected`-level
  assertions -> **P2**.
- **Granularity:** one Bug per `(failing_side, topic_num, category)`
  cluster. A "failing side" is either `Mainline` (when
  `comparison == "control_vs_control"`) or `CodeGen` (when
  `comparison == "experiment_vs_experiment"`). The two
  flow-of-regressions are kept in separate Bugs -- a Mainline-side
  Drafting/T4 regression is a different engineering signal from a
  CodeGen-side Drafting/T4 regression even when the assertion text
  matches.
- **Cap inline assertions per Bug:** 25. Clusters with more than 25
  rolled-up assertions show the first 25 and add a "+ N additional
  in the full report" line that points at the published HTML.

## Workflow (three steps; two confirmation gates)

```
classify-template  -->  agent classifies every regression
                                 |
                                 v
propose  --->  [Gate A: per-cluster review]  --->  execute
                                                       ^
                                                       |
                                              [Gate B: write count]
```

### Step 1 -- classify-template (script, deterministic)

```bash
python scripts/seval_regression_ado_sync.py classify-template \
  --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --out      data/eval-ado/<date>_<cid>_vs_<eid>_classifications.json
```

The script dumps every regression into a JSON template with:

- `comparison`, `failing_side`, `level`, `segment`, `query`,
  `assertion`, `why_failed` (read-only signal for the agent)
- `topic_num`, `topic`, `category`, `sub_mode`, `notes` (blank --
  agent must fill these in)

### Step 2 -- author classifications (agent, doctrine-driven)

For each regression, the agent reads `query` + `assertion` +
`why_failed` + the manifest's `reply_failed` and `rationale_failed`
fields, then writes:

- **`topic_num`** -- the one-best topic from the OCV 13-topic
  taxonomy (`1`..`13`). Same definitions as
  `ocv-analyze-and-ticket`; do not improvise.
- **`topic`** -- the exact topic name string (e.g.
  `"Constraints ignored"`). Must match `topic_taxonomy[topic_num]`
  in the template.
- **`category`** -- one of `Drafting | Replying | Scheduling |
  Search | Summarization | Triage | Rules | Settings | Tasks |
  Reminders | Translation | File | General | Unknown`. Be
  consistent: a "draft a reply to..." failure is `Replying`, not
  `Drafting`. Use `General` only for cross-tool surface complaints,
  `Unknown` only when the signal is genuinely insufficient.
- **`sub_mode`** -- required when `topic_num` is `6`
  (`blank|error_string|hang`) or `10` (`input|output`).
- **`notes`** -- optional, <= 20 words. Use only if a clarification
  helps the engineer triaging the cluster understand why the failure
  landed under this topic (e.g. `"Both runs returned the same drafted
  email; failed run rewrote subject."`).

The same priority-order tie-breakers from `ocv-analyze-and-ticket`
apply (Topic 1 wins for action-bearing prompts; Topic 8 beats Topic
7 when the source is wrong; etc.). Re-read the OCV taxonomy section
of `ocv-analyze-and-ticket/SKILL.md` if in doubt.

### Step 3 -- propose (script, deterministic)

```bash
python scripts/seval_regression_ado_sync.py propose \
  --manifest        data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
  --classifications data/eval-ado/<date>_<cid>_vs_<eid>_classifications.json \
  --report-url      "https://yohncf.github.io/OCV-Weekly_temp/eval-reports/<slug>.html" \
  --out             data/eval-ado/<date>_<cid>_vs_<eid>_proposals.json
```

The `--report-url` is the live URL of the published HTML report (use
the personal mirror URL when filing into FTE-internal ADO so external
collaborators can still open it).

The script:

1. Validates every regression in the manifest has a non-empty
   classification with a valid `topic_num` and `category` (and
   `sub_mode` when required). Refuses to run otherwise.
2. Refuses to run if `publish_safety.reviewed_for_publish` is not
   `true` -- bugs should only be filed against a report the user has
   reviewed.
3. Groups regressions by `(failing_side, topic_num, category)` and
   writes one cluster proposal per group with:
   - Deterministic 10-char `cluster_id`
   - `failing_run` and `passed_run` blocks (id, name, run_date,
     `seval.microsoft.com/job/<id>` URL)
   - Computed `priority` (P1 if any critical, else P2)
   - Computed default `decision.new_title`:
     `[SEVAL Regression] <side> - <Category>: T<n> <Topic> (N assertions)`
   - Computed `decision.assignee` via `compute_assignee()` from
     `ado_sync.py`, keyed off (`new_title`, `category`,
     `topic_num`). Unmapped clusters are left `assignee = null`.
   - Pre-rendered `body_html` containing the base + latest SEVAL
     URLs, the failing model, per-assertion blocks (query, assertion,
     why_failed, failed-reply, judge rationale), and a link to
     `report_url`.

### Gate A -- per-cluster review (agent, with the user)

After `propose`, the agent **must** walk every cluster with the
user via `ask_user` before invoking `execute`. For each cluster,
surface:

- Cluster summary line: side, topic, category, priority, regression
  count, default title, default assignee
- Optionally: the list of unique queries in the cluster (cap at 5;
  show "+ N more" if there are more)
- Choice prompt:
  - `Accept this cluster as-is`
  - `Re-title (I will provide a new title)` -> agent updates
    `decision.new_title`
  - `Reassign (I will provide an email)` -> agent updates
    `decision.assignee` and `decision.assignee_name`
  - `Skip this cluster (do not file)` -> agent sets
    `decision.action = "skip"`

For runs with more than ~8 clusters, the agent should first offer a
"strategy" prompt analogous to `ocv-ticket-sync`'s Gate A:

- Walk every cluster one at a time (default, conservative)
- Walk only P1 clusters; auto-accept P2 defaults
- Walk only a specific cluster I name
- Just open the JSON, I will hand-edit
- Cancel -- do not file anything this run

### Gate B -- final write-count confirmation

Before passing `--yes` to `execute`, the agent **must** clear Gate B
via `ask_user`. Read the post-review proposals JSON and surface the
exact write counts:

> Ready to file: **N create** (X unassigned), **M skip** -> N bugs
> will be created. Proceed?
>
> - Yes, file all
> - No, let me re-review (returns to Gate A)
> - Cancel

The script ALSO enforces Gate B: running `execute` without `--yes`
prints the plan and blocks on stdin for the literal `yes`. The agent
must never pass `--yes` without first clearing Gate B in chat.

### Step 4 -- execute (script, REST writes)

```bash
# Standard (script prompts for 'yes' on stdin)
python scripts/seval_regression_ado_sync.py execute \
  --proposals data/eval-ado/<date>_<cid>_vs_<eid>_proposals.json

# Preview only (no ADO writes)
python scripts/seval_regression_ado_sync.py execute \
  --proposals data/eval-ado/<date>_<cid>_vs_<eid>_proposals.json \
  --dry-run

# Apply (ONLY after the agent has cleared Gate B with the user)
python scripts/seval_regression_ado_sync.py execute \
  --proposals data/eval-ado/<date>_<cid>_vs_<eid>_proposals.json \
  --yes
```

For each non-skipped cluster, the script POSTs a Bug with:

- `System.Title` = `decision.new_title`
- `System.AreaPath` = `Outlook Web\Outlook Copilot Service\Outlook Agent`
- `System.IterationPath` = `Outlook Web`
- `Microsoft.VSTS.Common.Priority` = 2 (P1) or 3 (P2)
- `System.Tags` = `OutlookAgent; SevalRegression`
- `System.AssignedTo` = `decision.assignee` (when present)
- `System.Description` + `Microsoft.VSTS.TCM.ReproSteps` =
  pre-rendered `body_html`

After completion, the script writes `execution.{status, ado_id,
ado_url, created_at}` back into each cluster in the proposals JSON
so the run is auditable.

## Outputs

| Artifact | Path | Lifecycle |
|----------|------|-----------|
| Classifications template | `data/eval-ado/<slug>_classifications.json` | Permanent. Reusable if the manifest is re-rendered. |
| Proposals JSON | `data/eval-ado/<slug>_proposals.json` | Permanent. After `execute` it also records the created ADO IDs/URLs. |
| ADO Bugs | `https://dev.azure.com/outlookweb/Outlook%20Web/_workitems/edit/<id>` | Permanent in ADO. |

Surface the per-cluster created-ID list and a per-owner summary to
the user when done.

## Relationship to other skills

- Consumes the manifest written by **`seval-regression-analyze`**.
- Consumes the published URL emitted by
  **`seval-regression-publish`** (used as the in-body link).
- Called as the optional final step of the **`seval-regression`**
  orchestrator (after the publish step clears).
- Reuses constants and helpers (`compute_assignee`, `ado_request`,
  `esc`, area path, iteration, owners config) from
  **`../shared/ado_sync.py`** so the OCV `ocv-ticket-sync` skill and this
  skill stay in lockstep on ADO conventions.

## Errors and recovery

- **`az login` not done** -- `DefaultAzureCredential` throws; surface
  the error verbatim and tell the user to `az login` then re-run.
- **HTTP 401/403** -- likely insufficient permissions to write into
  the configured Area Path; show the response body and ask the user
  to confirm the area path / iteration.
- **HTTP 400 on create** -- usually a missing required field (e.g.,
  the team's Bug template requires Severity); show the response body
  and either extend `_build_create_payload` or add the field to the
  proposals JSON before re-running `execute`.
- **Partial execute failure** -- the script writes per-cluster
  `execution.status` back to the proposals JSON as it goes. To resume,
  set `decision.action = "skip"` on the already-created clusters and
  re-run `execute`.

## Compliance

- SEVAL replies are model outputs, not customer content, but the body
  HTML is HTML-escaped on every field before being sent to ADO (the
  `esc()` helper from `ado_sync.py`).
- Two-gate confirmation is non-negotiable -- never pass `--yes`
  without first clearing Gate B with the user via `ask_user`.
- The script refuses to file bugs against a manifest where
  `publish_safety.reviewed_for_publish` is not `true` -- in practice
  this means the report should already have been published (or at
  minimum reviewed locally) before bugs go out.

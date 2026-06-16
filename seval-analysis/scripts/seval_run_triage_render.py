"""
seval_run_triage_render.py — Render a SEVAL run-triage fingerprint manifest
into a PM-voice Markdown summary and a self-contained dark-themed HTML
report.

Inputs:
  --manifest   Path to the fingerprint JSON produced by
               seval_run_triage_extract.py
  --out-md     Path to write the Markdown summary (.md)
  --out-html   Path to write the HTML report (.html)
  --strict     Fail when any failure has empty failure_label / failure_evidence
               (use before sharing).

The renderer never mutates the manifest. It computes aggregate views
(family rollup, cluster table, inferred gaps) on the fly from the
agent-authored labels. Family mapping is hard-coded here and in SKILL.md
— keep them in sync if you extend the taxonomy.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


LABEL_TO_FAMILY: Dict[str, str] = {
    # Assertion family
    "Presentation nuance": "Assertion",
    "Over strict assertion": "Assertion",
    "Assertion misalignment": "Assertion",
    # Missing data family
    "Missing grounding data": "Missing data",
    # Model family (rolled up from former 'Agent performance' + 'Model'.
    # Splitting agent perf vs base-model error is useful at the label
    # level but noisy at the rollup level — both are owned by engineering.)
    "Tool invocation error": "Model",
    "Capability refusal": "Model",
    "Partial execution": "Model",
    "Failed task": "Model",
    "Hallucination": "Model",
    "Factual error": "Model",
    "Format violation": "Model",
    # Unclassifiable
    "Unclear": "Unclear",
}

FAMILY_ORDER = ["Missing data", "Assertion", "Model", "Unclear"]
FAMILY_COLOR = {
    "Missing data": "#7AA2F7",  # blue
    "Assertion":    "#E0AF68",  # amber
    "Model":        "#F7768E",  # red
    "Unclear":      "#9AA5CE",  # muted lavender
    "Unlabeled":    "#565F89",  # grey
}

LEVEL_ORDER = ["critical", "expected", "aspirational", "unspecified"]
LEVEL_BADGE_COLOR = {
    "critical": "#F7768E",
    "expected": "#E0AF68",
    "aspirational": "#9AA5CE",
    "unspecified": "#565F89",
}

PLACEHOLDER_LABEL = "Pending analysis"
PLACEHOLDER_EVIDENCE = "Pending analysis — agent has not yet authored failure_evidence."

# Display labels for the internal arm keys. SEVAL's CSV columns are
# score_control / score_experiment (canonical), but for human-facing output
# the convention in this organisation is:
#   control    -> "Mainline"  (slot 0, is_baseline true)
#   experiment -> "CodeGen"   (slot 1)
# The renderer prefers per-slot side_label from feature_flags.slots[*]
# when present and falls back to this map.
DEFAULT_ARM_LABELS: Dict[str, str] = {
    "control": "Mainline",
    "experiment": "CodeGen",
}


def build_arm_labels(manifest: Dict[str, Any]) -> Dict[str, str]:
    """Return {internal_arm_key: human_label} merging (in priority order):
    1. manifest['arm_labels'] (preferred — explicit top-level mapping),
    2. slot side_labels from feature_flags.slots[*],
    3. DEFAULT_ARM_LABELS fallback (control->Mainline, experiment->CodeGen)."""
    labels = dict(DEFAULT_ARM_LABELS)
    for slot in manifest.get("feature_flags", {}).get("slots", []) or []:
        exp = slot.get("exp_name")
        side = slot.get("side_label")
        if exp and side:
            labels[str(exp)] = str(side)
    for k, v in (manifest.get("arm_labels") or {}).items():
        if k and v:
            labels[str(k)] = str(v)
    return labels


def arm_display(arm: str, arm_labels: Dict[str, str]) -> str:
    """Render an arm as 'Mainline (control)' / 'CodeGen (experiment)'
    so the human label leads but the internal key remains traceable."""
    label = arm_labels.get(arm, arm)
    if label and label != arm:
        return f"{label} ({arm})"
    return arm

PM_VOICE_TAG = "Based on a single eval run; pass-rate movement against the SEVAL set, not against production usage."


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def family_of(label: str, taxonomy_extensions: List[Dict[str, Any]]) -> str:
    if not label:
        return "Unlabeled"
    if label in LABEL_TO_FAMILY:
        return LABEL_TO_FAMILY[label]
    for ext in taxonomy_extensions:
        if str(ext.get("label", "")) == label:
            return str(ext.get("family", "Unclear"))
    return "Unclear"


def compute_family_rollup(failures: List[Dict[str, Any]],
                          taxonomy_extensions: List[Dict[str, Any]]
                          ) -> Dict[str, Dict[str, int]]:
    """Returns {arm: {family: count}} including 'Unlabeled' for blanks."""
    rollup: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f in failures:
        fam = family_of(f.get("failure_label", ""), taxonomy_extensions)
        rollup[f["arm"]][fam] += 1
    return {arm: dict(d) for arm, d in rollup.items()}


def compute_clusters(failures: List[Dict[str, Any]],
                     taxonomy_extensions: List[Dict[str, Any]]
                     ) -> List[Dict[str, Any]]:
    """Group failures by (arm, label). Each cluster carries:
      - count
      - top_segments: top 3 segments as [(segment, count), ...]
      - representative_queries: up to 3 (only when count >= 2;
        singletons get an empty list to keep the table compact)
      - family (derived from label)
    Sorted by count desc.
    """
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for f in failures:
        label = f.get("failure_label") or PLACEHOLDER_LABEL
        buckets[(f["arm"], label)].append(f)
    out: List[Dict[str, Any]] = []
    for (arm, label), rows in buckets.items():
        fam = family_of(label, taxonomy_extensions) if label != PLACEHOLDER_LABEL else "Unlabeled"
        seg_counts: Dict[str, int] = defaultdict(int)
        for r in rows:
            seg = r.get("segment") or "unspecified"
            seg_counts[seg] += 1
        top_segments = sorted(seg_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        # Reps only when this cluster aggregates 2+ failures
        reps: List[str] = []
        if len(rows) >= 2:
            seen_q: set = set()
            for r in rows:
                q = r["query"]
                if q in seen_q:
                    continue
                seen_q.add(q)
                reps.append(q)
                if len(reps) >= 3:
                    break
        out.append({
            "arm": arm,
            "family": fam,
            "label": label,
            "count": len(rows),
            "top_segments": top_segments,
            "representative_queries": reps,
        })
    out.sort(key=lambda r: (-r["count"], r["arm"], r["family"], r["label"]))
    # Drop singletons (count < 2) and cap to the top 10 for skim-ability.
    out = [c for c in out if c["count"] >= 2][:10]
    return out


def compute_suspect_labels(failures: List[Dict[str, Any]],
                           taxonomy_extensions: List[Dict[str, Any]] | None = None
                           ) -> List[Dict[str, Any]]:
    """Cross-arm guardrail. Surface failures whose `Missing data` label
    is contradicted by the other arm. Two suspect classes:

      (a) other_passed: the other arm passed this exact (query, assertion),
          so the data was demonstrably available.
      (b) asymmetric label: both arms failed the same (query, assertion),
          but the other arm's label is in the Model family — meaning the
          other arm acknowledged it as an agent-side issue, so the data
          was available there too.

    Both classes are almost certainly mislabels and should be moved out
    of the Missing data family.
    """
    tax_ext = taxonomy_extensions or []
    # Index for asymmetric detection
    pair_index: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for f in failures:
        key = (f.get("query"), f.get("assertion"))
        pair_index.setdefault(key, {})[f["arm"]] = f
    out: List[Dict[str, Any]] = []
    for f in failures:
        label = (f.get("failure_label") or "").strip()
        if family_of(label, tax_ext) != "Missing data":
            continue
        status = f.get("cross_arm_status")
        if status == "other_passed":
            f2 = dict(f)
            f2["suspect_reason"] = "other_passed"
            f2["other_arm_label"] = None
            out.append(f2)
            continue
        # Asymmetric label check
        other_arm = "experiment" if f["arm"] == "control" else "control"
        other = pair_index.get((f.get("query"), f.get("assertion")), {}).get(other_arm)
        if not other:
            continue
        other_label = other.get("failure_label")
        if family_of(other_label or "", tax_ext) == "Model":
            f2 = dict(f)
            f2["suspect_reason"] = "other_arm_model_label"
            f2["other_arm_label"] = other_label
            out.append(f2)
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render_markdown(manifest: Dict[str, Any]) -> str:
    run = manifest["run"]
    summary = manifest["summary"]
    failures = manifest.get("failures", [])
    tax_ext = manifest.get("taxonomy_extensions", [])
    arm_labels = build_arm_labels(manifest)

    rollup = compute_family_rollup(failures, tax_ext)
    clusters = compute_clusters(failures, tax_ext)
    inferred_gaps = manifest.get("inferred_gaps", [])
    caveats = manifest.get("caveats", [])
    flag_slots = manifest.get("feature_flags", {}).get("slots", [])

    lines: List[str] = []
    lines.append(f"# SEVAL Run Triage — {run.get('name', run.get('id'))}")
    lines.append("")
    lines.append(f"- **Run ID:** {run.get('id')}")
    lines.append(f"- **Run date:** {run.get('date')}")
    lines.append(f"- **Scenario:** {run.get('scenario', '—')}")
    lines.append(f"- **Eval time override:** {run.get('eval_time_override', '—')}")
    lines.append(f"- **Tenant user_id:** {run.get('user_id', '—')}")
    lines.append("")
    lines.append("## Overall pass rates")
    lines.append("")
    lines.append("| Arm | Rows | Passed | Failed | Pass rate | Critical pass rate |")
    lines.append("|-----|-----:|-------:|-------:|----------:|-------------------:|")
    for arm in ("control", "experiment"):
        s = summary["arms"].get(arm, {})
        crit = s.get("pass_rate_by_level", {}).get("critical", {"rate": 0.0, "passed": 0, "total": 0})
        lines.append(
            f"| {arm_display(arm, arm_labels)} | {s.get('rows', 0)} | {s.get('passed', 0)} | {s.get('failed', 0)} "
            f"| {_pct(s.get('pass_rate', 0.0))} "
            f"| {_pct(crit['rate'])} ({crit['passed']}/{crit['total']}) |"
        )
    lines.append("")
    lines.append("> Headline = **critical pass rate**. Aspirational failures are demoted; they "
                 "do not invalidate a run whose critical assertions hold.")
    lines.append("")

    # Pass rate by level
    lines.append("## Pass rate by assertion level")
    lines.append("")
    for arm in ("control", "experiment"):
        s = summary["arms"].get(arm, {})
        levels = s.get("pass_rate_by_level", {})
        if not levels:
            continue
        lines.append(f"### {arm_display(arm, arm_labels)}")
        lines.append("")
        lines.append("| Level | Passed / Total | Pass rate |")
        lines.append("|---|---:|---:|")
        for lvl in LEVEL_ORDER:
            if lvl not in levels:
                continue
            v = levels[lvl]
            lines.append(f"| {lvl} | {v['passed']}/{v['total']} | {_pct(v['rate'])} |")
        lines.append("")

    # Inferred gaps — top action item, leads the report. Rendered as a compact
    # table for skimmability; the long-form "evidence" stays on a single row.
    lines.append("## Inferred capability gaps")
    lines.append("")
    if not inferred_gaps:
        lines.append("_No gaps inferred yet. Agent has not authored this section._")
    else:
        lines.append("| # | Scope | Gap | n |")
        lines.append("|---|-------|-----|---:|")
        for i, g in enumerate(inferred_gaps, start=1):
            scope = g.get("scope") or _infer_gap_scope(g)
            headline = g.get("gap", "?")
            if g.get("tentative"):
                headline += " _(tentative)_"
            n = g.get("evidence_row_count", "?")
            lines.append(f"| {i} | {scope} | **{headline}** | {n} |")
        # Evidence block — bullet list below the table so the gist is
        # readable without scrolling, but the detail is one screen away.
        lines.append("")
        lines.append("<details><summary>Evidence (click to expand)</summary>")
        lines.append("")
        for i, g in enumerate(inferred_gaps, start=1):
            lines.append(f"{i}. **{g.get('gap','?')}** — {g.get('evidence','')}")
        lines.append("")
        lines.append("</details>")
    lines.append("")

    # Cross-arm contradictions — label-quality guardrail (widened).
    suspect = compute_suspect_labels(failures, tax_ext)
    lines.append("## Cross-arm contradictions (label quality)")
    lines.append("")
    if not suspect:
        lines.append("_No `Missing data` failures contradicted by the other arm. "
                     "Missing-data labels are internally consistent._")
    else:
        n_other_passed = sum(1 for f in suspect if f.get("suspect_reason") == "other_passed")
        n_asym = len(suspect) - n_other_passed
        lines.append(f"**{len(suspect)} failures** carry a `Missing data` label but are contradicted "
                     "by the other arm. Both arms run against the same environment.")
        lines.append("")
        lines.append(f"- `{n_other_passed}` — other arm **passed** the same `(query, assertion)`.")
        lines.append(f"- `{n_asym}` — other arm **failed** the same `(query, assertion)` "
                     "but labeled it as a Model-family issue (data was demonstrably available there).")
        lines.append("")
        lines.append("| Arm | Reason | Other-arm label | Segment | Query |")
        lines.append("|---|---|---|---|---|")
        for f in suspect[:25]:
            reason = ("other arm passed" if f.get("suspect_reason") == "other_passed"
                      else f"other arm: {f.get('other_arm_label','?')}")
            lines.append(f"| {arm_display(f['arm'], arm_labels)} | {reason} "
                         f"| {f.get('other_arm_label') or '—'} | {f.get('segment','—')} "
                         f"| {_md_truncate(f.get('query',''), 70)} |")
        if len(suspect) > 25:
            lines.append(f"| _… and {len(suspect)-25} more_ | | | | |")
    lines.append("")

    # Failure family rollup
    lines.append("## Failure family rollup")
    lines.append("")
    lines.append("Distribution of failures by root-cause family. The four families answer "
                 "*\"was it the data, the assertion, the model, or unclear?\"*")
    lines.append("")
    lines.append("| Arm | Total failures | " + " | ".join(FAMILY_ORDER) + " | Unlabeled |")
    lines.append("|-----|---------------:|" + "|".join(["---:"] * (len(FAMILY_ORDER) + 1)) + "|")
    for arm in ("control", "experiment"):
        fams = rollup.get(arm, {})
        total = sum(fams.values())
        if total == 0:
            lines.append(f"| {arm_display(arm, arm_labels)} | 0 | " + " | ".join(["0"] * (len(FAMILY_ORDER) + 1)) + " |")
            continue
        cells = [str(fams.get(fam, 0)) for fam in FAMILY_ORDER]
        cells.append(str(fams.get("Unlabeled", 0)))
        lines.append(f"| {arm_display(arm, arm_labels)} | {total} | " + " | ".join(cells) + " |")
    lines.append("")

    # Top clusters — grouped by (arm, label) only. Family + segments dropped
    # per user feedback: too much detail; the label already conveys the family.
    lines.append("## Top failure clusters")
    lines.append("")
    if not clusters:
        lines.append("_No clusters with count ≥ 2._")
    else:
        lines.append("Grouped by **(arm, label)**. Top 10 shown. "
                     "Singleton clusters (count = 1) are dropped.")
        lines.append("")
        lines.append("| # | Arm | Label | Count | Representative queries |")
        lines.append("|---|-----|-------|------:|------------------------|")
        for i, c in enumerate(clusters, start=1):
            reps = "<br>".join(_md_truncate(q, 90) for q in c["representative_queries"]) or "—"
            lines.append(f"| {i} | {arm_display(c['arm'], arm_labels)} | {c['label']} "
                         f"| {c['count']} | {reps} |")
    lines.append("")

    # Feature flags
    lines.append("## Feature flag set")
    lines.append("")
    for slot in flag_slots:
        lines.append(f"### {slot.get('side_label', slot.get('exp_name'))} "
                     f"(slot {slot.get('slot_index')}, {slot.get('count')} flags)")
        lines.append("")
        flags = slot.get("flags") or []
        if not flags:
            lines.append("_No flags extracted._")
        else:
            lines.append("<details><summary>Show all flags</summary>")
            lines.append("")
            for f in flags:
                lines.append(f"- `{f}`")
            lines.append("")
            lines.append("</details>")
        lines.append("")

    # Caveats
    lines.append("## Caveats")
    lines.append("")
    lines.append(f"- {PM_VOICE_TAG}")
    unlabeled = sum(rollup.get(arm, {}).get("Unlabeled", 0) for arm in ("control", "experiment"))
    total_fails = sum(sum(d.values()) for d in rollup.values())
    if total_fails and unlabeled / total_fails > 0.10:
        lines.append(f"- **{unlabeled}/{total_fails} failures ({_pct(unlabeled / total_fails)}) "
                     "are unlabeled.** Re-run the agent labeling pass or sample for manual review.")
    unclear = sum(rollup.get(arm, {}).get("Unclear", 0) for arm in ("control", "experiment"))
    if total_fails and unclear / total_fails > 0.10:
        lines.append(f"- **{unclear}/{total_fails} failures ({_pct(unclear / total_fails)}) "
                     "classified `Unclear`.** Consider providing more rationale data.")
    for c in caveats:
        lines.append(f"- {c}")
    lines.append("")

    lines.append("---")
    lines.append(f"_Generated from `{Path(run.get('csv_path', '')).name}` "
                 f"on {manifest.get('generated_at')} — fingerprint manifest "
                 f"schema `{manifest.get('schema_version')}`._")

    return "\n".join(lines) + "\n"


def _md_truncate(s: str, n: int) -> str:
    s = (s or "").strip().replace("|", "\\|").replace("\n", " ")
    if len(s) <= n:
        return s
    return s[:n - 1].rstrip() + "…"


def _infer_gap_scope(g: Dict[str, Any]) -> str:
    """Best-effort scope tag for a gap when not explicitly set on the dict.
    Inspects the gap text for arm names; returns 'CodeGen', 'Mainline',
    'Both arms', or 'General'.
    """
    text = ((g.get("gap") or "") + " " + (g.get("evidence") or "")).lower()
    has_codegen = "codegen" in text
    has_mainline = "mainline" in text
    if has_codegen and has_mainline:
        return "Both arms"
    if has_codegen:
        return "CodeGen"
    if has_mainline:
        return "Mainline"
    return "General"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _h(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def render_html(manifest: Dict[str, Any]) -> str:
    run = manifest["run"]
    summary = manifest["summary"]
    failures = manifest.get("failures", [])
    tax_ext = manifest.get("taxonomy_extensions", [])
    arm_labels = build_arm_labels(manifest)

    rollup = compute_family_rollup(failures, tax_ext)
    clusters = compute_clusters(failures, tax_ext)
    inferred_gaps = manifest.get("inferred_gaps", [])
    caveats = manifest.get("caveats", [])
    flag_slots = manifest.get("feature_flags", {}).get("slots", [])

    # Hydrate each failure with family for client-side filtering.
    for f in failures:
        f["_family"] = family_of(f.get("failure_label", ""), tax_ext)

    # Pre-compute family rollup percentages for the stacked bars
    bars_html = ""
    for arm in ("control", "experiment"):
        fams = rollup.get(arm, {})
        total = sum(fams.values())
        arm_h = _h(arm_display(arm, arm_labels))
        if total == 0:
            bars_html += f"""
              <div class="rollup-row">
                <div class="rollup-label">{arm_h}</div>
                <div class="rollup-bar"><div class="rollup-empty">No failures</div></div>
                <div class="rollup-total">0</div>
              </div>"""
            continue
        segments_html = ""
        for fam in FAMILY_ORDER + ["Unlabeled"]:
            n = fams.get(fam, 0)
            if not n:
                continue
            pct = (n / total) * 100
            color = FAMILY_COLOR.get(fam, "#888")
            segments_html += (
                f'<div class="rollup-seg" style="width:{pct:.2f}%;background:{color};" '
                f'title="{_h(fam)}: {n} ({pct:.1f}%)">'
                f'<span class="rollup-seg-label">{_h(fam[:1])}{n}</span></div>'
            )
        bars_html += f"""
          <div class="rollup-row">
            <div class="rollup-label">{arm_h}</div>
            <div class="rollup-bar">{segments_html}</div>
            <div class="rollup-total">{total}</div>
          </div>"""

    # KPI cards
    kpi_html = ""
    for arm in ("control", "experiment"):
        s = summary["arms"].get(arm, {})
        crit = s.get("pass_rate_by_level", {}).get("critical", {"rate": 0.0, "passed": 0, "total": 0})
        exp = s.get("pass_rate_by_level", {}).get("expected", {"rate": 0.0, "passed": 0, "total": 0})
        asp = s.get("pass_rate_by_level", {}).get("aspirational", {"rate": 0.0, "passed": 0, "total": 0})
        kpi_html += f"""
          <div class="kpi-card">
            <div class="kpi-arm">{_h(arm_display(arm, arm_labels))}</div>
            <div class="kpi-headline">
              <div class="kpi-num">{_pct(s.get('pass_rate', 0.0))}</div>
              <div class="kpi-sub">overall ({s.get('passed', 0)}/{s.get('rows', 0)})</div>
            </div>
            <div class="kpi-grid">
              <div class="kpi-cell critical">
                <div class="kpi-cell-label">critical</div>
                <div class="kpi-cell-val">{_pct(crit['rate'])}</div>
                <div class="kpi-cell-sub">{crit['passed']}/{crit['total']}</div>
              </div>
              <div class="kpi-cell expected">
                <div class="kpi-cell-label">expected</div>
                <div class="kpi-cell-val">{_pct(exp['rate'])}</div>
                <div class="kpi-cell-sub">{exp['passed']}/{exp['total']}</div>
              </div>
              <div class="kpi-cell aspirational">
                <div class="kpi-cell-label">aspirational</div>
                <div class="kpi-cell-val">{_pct(asp['rate'])}</div>
                <div class="kpi-cell-sub">{asp['passed']}/{asp['total']}</div>
              </div>
            </div>
          </div>"""

    # Cluster rows — grouped by (arm, label) only. Top 10 with count>=2 only
    # (filtering happens in compute_clusters).
    cluster_rows = ""
    for i, c in enumerate(clusters, start=1):
        if c["representative_queries"]:
            reps = "".join(
                f'<div class="cluster-rep">{_h(_md_truncate(q, 120))}</div>'
                for q in c["representative_queries"]
            )
        else:
            reps = '<span class="cluster-singleton">—</span>'
        fam_color = FAMILY_COLOR.get(c["family"], "#888")
        cluster_rows += f"""
          <tr>
            <td class="num">{i}</td>
            <td>{_h(arm_display(c['arm'], arm_labels))}</td>
            <td><span class="family-chip" style="background:{fam_color}22;color:{fam_color};border-color:{fam_color}55;">{_h(c['label'])}</span></td>
            <td class="num">{c['count']}</td>
            <td>{reps}</td>
          </tr>"""

    # Cross-arm contradiction section removed from HTML per user request.
    # The compute_suspect_labels() function remains for markdown + relabel scripts.

    # Gaps — render as a compact table for skimmability. Long-form evidence
    # lives in a collapsible details block below the table.
    if inferred_gaps:
        gap_rows = ""
        for i, g in enumerate(inferred_gaps, start=1):
            scope = _h(g.get("scope") or _infer_gap_scope(g))
            headline = _h(g.get("gap", "?"))
            tag = ' <span class="gap-tag">tentative</span>' if g.get("tentative") else ""
            n = _h(g.get("evidence_row_count", "?"))
            gap_rows += f"""
              <tr>
                <td class="num">{i}</td>
                <td><span class="gap-scope">{scope}</span></td>
                <td class="gap-headline">{headline}{tag}</td>
                <td class="num">{n}</td>
              </tr>"""
        gap_evidence = ""
        for i, g in enumerate(inferred_gaps, start=1):
            gap_evidence += (
                f'<li><strong>{_h(g.get("gap","?"))}</strong> — '
                f'{_h(g.get("evidence",""))}</li>'
            )
        gaps_html = f"""
          <table class="std gap-table">
            <thead><tr><th>#</th><th>Scope</th><th>Gap</th><th>n</th></tr></thead>
            <tbody>{gap_rows}</tbody>
          </table>
          <details class="gap-evidence-fold">
            <summary>Evidence ({len(inferred_gaps)} gaps)</summary>
            <ol class="gap-evidence-list">{gap_evidence}</ol>
          </details>"""
    else:
        gaps_html = '<div class="gap-empty">No gaps inferred yet. Agent has not authored this section.</div>'

    # Failure detail cards + filter facet collection. Cards-not-tables because
    # the model reply is markdown — formatting matters for readability. The
    # filter wiring still works against [data-arm]/[data-family]/[data-level]/
    # [data-segment]/[data-search] attributes on each card.
    detail_cards = ""
    families_seen: set = set()
    segments_seen: set = set()
    levels_seen: set = set()
    arms_seen: set = set()
    for f in failures:
        fam = f["_family"]
        seg = f.get("segment") or "unspecified"
        lvl = f.get("level") or "unspecified"
        arm = f["arm"]
        arm_h = arm_display(arm, arm_labels)
        families_seen.add(fam)
        segments_seen.add(seg)
        levels_seen.add(lvl)
        arms_seen.add(arm_h)
        fam_color = FAMILY_COLOR.get(fam, "#888")
        lvl_color = LEVEL_BADGE_COLOR.get(lvl, "#565F89")
        label_display = f.get("failure_label") or PLACEHOLDER_LABEL
        evidence_display = f.get("failure_evidence") or PLACEHOLDER_EVIDENCE
        # Reply + rationale are markdown strings; render via marked.js on load
        # using the data-md attribute (HTML-escaped for attribute safety).
        reply_md = html.escape(f.get("reply", "") or "", quote=True)
        rationale_md = html.escape(f.get("rationale", "") or "", quote=True)
        # data-search supports the existing free-text filter input.
        search_blob = (f.get("query", "") + " " + f.get("assertion", "")
                       + " " + label_display).lower()
        detail_cards += f"""
          <details class="failure-card"
                   data-arm="{_h(arm_h)}"
                   data-family="{_h(fam)}"
                   data-segment="{_h(seg)}"
                   data-level="{_h(lvl)}"
                   data-search="{_h(search_blob)}">
            <summary class="failure-card-header">
              <div class="failure-card-badges">
                <span class="arm-chip">{_h(arm_h)}</span>
                <span class="level-badge" style="background:{lvl_color}22;color:{lvl_color};border-color:{lvl_color}55;">{_h(lvl)}</span>
                <span class="family-chip" style="background:{fam_color}22;color:{fam_color};border-color:{fam_color}55;">{_h(label_display)}</span>
                <span class="segment-chip">{_h(seg)}</span>
              </div>
              <div class="failure-card-query">{_h(f.get('query', ''))}</div>
              <div class="failure-card-assertion">{_h(f.get('assertion', ''))}</div>
            </summary>
            <div class="failure-card-body">
              <div class="why">
                <div class="why-label">Why it failed</div>
                <div class="why-text">{_h(evidence_display)}</div>
              </div>
              <div class="reply-block">
                <div class="reply-header">Model reply</div>
                <div class="reply-body md-target" data-md="{reply_md}"></div>
              </div>
              <details class="rationale-fold">
                <summary>Judge rationale</summary>
                <div class="rationale-body md-target" data-md="{rationale_md}"></div>
              </details>
            </div>
          </details>"""

    # Filter pills
    def pill_group(name: str, options) -> str:
        pills = "".join(
            f'<button class="pill" data-facet="{_h(name)}" data-value="{_h(o)}">{_h(o)}</button>'
            for o in sorted(options)
        )
        return (
            f'<div class="filter-group">'
            f'<div class="filter-label">{_h(name)}</div>{pills}</div>'
        )

    filters_html = (
        pill_group("arm", arms_seen)
        + pill_group("family", families_seen)
        + pill_group("level", levels_seen)
        + pill_group("segment", segments_seen)
    )

    # Feature flag sections
    flag_sections = ""
    for slot in flag_slots:
        flags = slot.get("flags") or []
        flags_html = "".join(f'<div class="flag-token">{_h(t)}</div>' for t in flags)
        flag_sections += f"""
          <details class="flag-slot">
            <summary>
              <strong>{_h(slot.get('side_label'))}</strong>
              <span class="flag-meta">slot {_h(slot.get('slot_index'))} · exp_name=<code>{_h(slot.get('exp_name'))}</code> · {_h(slot.get('count'))} flags</span>
            </summary>
            <input class="flag-search" type="text" placeholder="Filter flags…" />
            <div class="flag-list">{flags_html}</div>
          </details>"""

    # Caveats
    caveat_items = [PM_VOICE_TAG]
    unlabeled = sum(rollup.get(arm, {}).get("Unlabeled", 0) for arm in ("control", "experiment"))
    total_fails = sum(sum(d.values()) for d in rollup.values())
    if total_fails and unlabeled / total_fails > 0.10:
        caveat_items.append(
            f"{unlabeled}/{total_fails} failures ({_pct(unlabeled / total_fails)}) "
            "are unlabeled. Re-run the agent labeling pass or sample for manual review."
        )
    unclear = sum(rollup.get(arm, {}).get("Unclear", 0) for arm in ("control", "experiment"))
    if total_fails and unclear / total_fails > 0.10:
        caveat_items.append(
            f"{unclear}/{total_fails} failures ({_pct(unclear / total_fails)}) "
            "classified Unclear. Consider providing more rationale data."
        )
    caveat_items.extend(caveats)
    caveats_html = "".join(f"<li>{_h(c)}</li>" for c in caveat_items)

    return _HTML_TEMPLATE.format(
        title=_h(run.get("name") or run.get("id")),
        run_id=_h(run.get("id")),
        run_date=_h(run.get("date")),
        scenario=_h(run.get("scenario") or "—"),
        eval_time_override=_h(run.get("eval_time_override") or "—"),
        user_id=_h(run.get("user_id") or "—"),
        rows=_h(summary.get("rows", 0)),
        queries=_h(summary.get("queries", 0)),
        segments_count=_h(len(summary.get("segments", []))),
        kpi_html=kpi_html,
        bars_html=bars_html,
        cluster_rows=cluster_rows or '<tr><td colspan="5" class="empty">No clusters with count ≥ 2.</td></tr>',
        gaps_html=gaps_html,
        flag_sections=flag_sections,
        detail_cards=detail_cards or '<div class="empty">No failures.</div>',
        detail_count=_h(len(failures)),
        filters_html=filters_html,
        caveats_html=caveats_html,
        generated_at=_h(manifest.get("generated_at")),
        schema_version=_h(manifest.get("schema_version")),
        csv_name=_h(Path(run.get("csv_path", "")).name),
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>SEVAL Run Triage — {title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js"></script>
<style>
:root {{
  --bg: #1A1B26;
  --bg-elev: #24283B;
  --bg-card: #1F2335;
  --fg: #C0CAF5;
  --fg-dim: #9AA5CE;
  --fg-muted: #565F89;
  --border: #2C3043;
  --accent: #7AA2F7;
}}
* {{ box-sizing: border-box; }}
html, body {{ background: var(--bg); color: var(--fg); margin: 0; padding: 0; }}
body {{
  font-family: "Google Sans", -apple-system, BlinkMacSystemFont, "Segoe UI",
               Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 14px; line-height: 1.5;
}}
a {{ color: var(--accent); }}
code {{ font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
        background: var(--bg-elev); padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }}
pre {{ font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
       background: var(--bg-elev); padding: 12px; border-radius: 6px;
       overflow-x: auto; white-space: pre-wrap; word-break: break-word; font-size: 0.9em; }}

.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}

.run-header {{
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; padding: 20px 24px; margin-bottom: 20px;
}}
.run-title {{ font-size: 22px; font-weight: 600; margin: 0 0 10px; }}
.run-meta {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 24px;
             color: var(--fg-dim); font-size: 13px; }}
.run-meta-item label {{ color: var(--fg-muted); margin-right: 8px; font-size: 12px; }}

.section {{ background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 8px; padding: 18px 22px; margin-bottom: 18px; }}
.section h2 {{ margin: 0 0 12px; font-size: 16px; font-weight: 600; color: var(--fg); }}
.section .note {{ color: var(--fg-dim); font-size: 12px; margin: -4px 0 12px; }}

.kpi-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
.kpi-card {{ background: var(--bg-elev); border: 1px solid var(--border);
             border-radius: 8px; padding: 14px; }}
.kpi-arm {{ font-size: 11px; font-weight: 600; text-transform: uppercase;
            letter-spacing: 0.08em; color: var(--fg-muted); margin-bottom: 4px; }}
.kpi-headline {{ display: flex; align-items: baseline; gap: 8px; margin-bottom: 12px; }}
.kpi-num {{ font-size: 30px; font-weight: 600; color: var(--accent); }}
.kpi-sub {{ color: var(--fg-dim); font-size: 12px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
.kpi-cell {{ background: var(--bg); border: 1px solid var(--border);
             border-radius: 6px; padding: 8px; text-align: center; }}
.kpi-cell-label {{ color: var(--fg-muted); font-size: 10px; text-transform: uppercase;
                   letter-spacing: 0.08em; margin-bottom: 4px; }}
.kpi-cell-val {{ font-size: 16px; font-weight: 600; }}
.kpi-cell-sub {{ color: var(--fg-dim); font-size: 11px; }}
.kpi-cell.critical .kpi-cell-val {{ color: #F7768E; }}
.kpi-cell.expected .kpi-cell-val {{ color: #E0AF68; }}
.kpi-cell.aspirational .kpi-cell-val {{ color: #9AA5CE; }}

.rollup-row {{ display: grid; grid-template-columns: 100px 1fr 60px;
               align-items: center; gap: 10px; margin: 6px 0; }}
.rollup-label {{ color: var(--fg-dim); font-size: 13px; }}
.rollup-bar {{ display: flex; height: 26px; border-radius: 6px;
               overflow: hidden; background: var(--bg-elev); }}
.rollup-empty {{ color: var(--fg-muted); font-size: 11px; padding: 0 10px;
                 align-self: center; }}
.rollup-seg {{ display: flex; align-items: center; justify-content: center;
               color: rgba(0,0,0,0.7); font-size: 11px; font-weight: 600;
               overflow: hidden; }}
.rollup-seg-label {{ white-space: nowrap; padding: 0 4px; }}
.rollup-total {{ color: var(--fg-dim); font-size: 13px; text-align: right; }}
.rollup-legend {{ display: flex; flex-wrap: wrap; gap: 10px;
                  margin-top: 12px; font-size: 12px; }}
.rollup-legend-item {{ display: flex; align-items: center; gap: 5px; color: var(--fg-dim); }}
.rollup-legend-swatch {{ width: 10px; height: 10px; border-radius: 2px; }}

table.std {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
table.std th {{ text-align: left; padding: 8px 10px; color: var(--fg-muted);
                font-weight: 600; font-size: 11px; text-transform: uppercase;
                letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }}
table.std td {{ padding: 10px; border-bottom: 1px solid var(--border);
                vertical-align: top; }}
table.std td.num, table.std th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
table.std td.empty {{ color: var(--fg-muted); text-align: center; padding: 20px; }}

.family-chip {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
                font-size: 11px; font-weight: 600; border: 1px solid; }}
.level-badge {{ display: inline-block; padding: 1px 7px; border-radius: 8px;
                font-size: 10px; font-weight: 600; border: 1px solid;
                text-transform: uppercase; letter-spacing: 0.04em; }}
.cluster-rep {{ color: var(--fg-dim); font-size: 12px; margin: 2px 0; }}
.cluster-singleton {{ color: var(--fg-muted); font-style: italic; font-size: 12px; }}
.seg-chip {{ display: inline-block; padding: 2px 8px; margin: 2px 4px 2px 0;
             border-radius: 8px; background: #2C3043; color: var(--fg-dim);
             font-size: 11px; }}
.seg-chip .seg-n {{ color: var(--accent); font-weight: 600; margin-left: 4px; }}

.section-subhead {{ color: var(--fg-muted); font-size: 13px; font-weight: 400;
                    margin-left: 8px; }}

.section.suspect {{ border-left: 3px solid #E0AF68; padding-left: 16px; }}
.suspect-headline {{ background: #E0AF6822; color: var(--fg); padding: 12px 16px;
                     border-radius: 6px; border: 1px solid #E0AF6855;
                     margin: 8px 0 14px 0; line-height: 1.55; }}
.suspect-headline code {{ background: #E0AF6833; color: #E0AF68;
                          padding: 1px 6px; border-radius: 4px; }}
.suspect-empty {{ color: #9ECE6A; background: #9ECE6A18; padding: 10px 14px;
                  border-radius: 6px; border: 1px solid #9ECE6A55; }}
table.suspect-table tbody tr {{ border-bottom: 1px solid var(--border); }}
table.suspect-table td.suspect-query {{ font-weight: 500; max-width: 320px; }}
table.suspect-table td.suspect-assertion {{ color: var(--fg-dim); max-width: 480px;
                                            font-style: italic; font-size: 13px; }}
.suspect-more {{ color: var(--fg-muted); font-size: 12px;
                 padding: 6px 0 0 0; text-align: right; }}
.suspect-breakdown {{ display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }}
.suspect-pill {{ background: #E0AF6833; color: #E0AF68; padding: 4px 10px;
                 border-radius: 12px; border: 1px solid #E0AF6855;
                 font-size: 12px; }}
table.suspect-table td.suspect-reason {{ color: var(--fg-dim); font-size: 12px;
                                          white-space: nowrap; }}
table.suspect-table td.suspect-reason code {{ background: var(--bg-elev);
                                               padding: 1px 4px; border-radius: 3px;
                                               font-size: 11px; }}

details.detail-fold {{ background: var(--bg-elev); border: 1px solid var(--border);
                       border-radius: 6px; padding: 12px 18px; }}
details.detail-fold > summary {{ cursor: pointer; outline: none;
                                  list-style: revert; }}
details.detail-fold > summary > h2 {{ margin: 0; }}

table.gap-table {{ width: 100%; margin: 8px 0; }}
table.gap-table td.gap-headline {{ font-weight: 500; }}
.gap-scope {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
              background: var(--bg-elev); border: 1px solid var(--border);
              color: var(--fg-dim); font-size: 11px; font-weight: 500;
              white-space: nowrap; }}
details.gap-evidence-fold {{ margin-top: 10px; background: var(--bg-elev);
                              border: 1px solid var(--border); border-radius: 6px;
                              padding: 10px 16px; }}
details.gap-evidence-fold > summary {{ cursor: pointer; outline: none;
                                        color: var(--fg-dim); font-size: 13px; }}
ol.gap-evidence-list {{ margin: 10px 0 0 0; padding-left: 22px;
                         color: var(--fg-dim); line-height: 1.55; }}
ol.gap-evidence-list li {{ padding: 4px 0; }}
.gap-empty {{ color: var(--fg-muted); font-style: italic; padding: 10px 0; }}
.gap-tag {{ background: #565F8933; color: var(--fg-dim); padding: 1px 6px;
            border-radius: 8px; font-size: 10px; margin-left: 6px; }}

details.flag-slot {{ background: var(--bg-elev); border: 1px solid var(--border);
                     border-radius: 6px; padding: 10px 14px; margin: 8px 0; }}
details.flag-slot > summary {{ cursor: pointer; outline: none; }}
details.flag-slot .flag-meta {{ color: var(--fg-muted); margin-left: 8px; font-size: 12px; }}
.flag-search {{ display: block; width: 100%; max-width: 400px; margin: 10px 0;
                padding: 6px 10px; background: var(--bg); color: var(--fg);
                border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }}
.flag-list {{ display: flex; flex-wrap: wrap; gap: 6px; max-height: 280px;
              overflow-y: auto; padding-right: 6px; }}
.flag-token {{ background: var(--bg); border: 1px solid var(--border);
               border-radius: 4px; padding: 3px 8px; font-family: monospace;
               font-size: 11px; color: var(--fg-dim); }}

.controls {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 14px;
             align-items: flex-end; }}
.search-input {{ flex: 1; min-width: 220px; padding: 8px 10px;
                 background: var(--bg-elev); color: var(--fg);
                 border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }}
.filter-group {{ display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }}
.filter-label {{ color: var(--fg-muted); font-size: 11px;
                 text-transform: uppercase; letter-spacing: 0.06em;
                 margin-right: 4px; }}
.pill {{ background: var(--bg-elev); color: var(--fg-dim);
         border: 1px solid var(--border); border-radius: 12px;
         padding: 3px 10px; cursor: pointer; font-size: 11px;
         font-family: inherit; }}
.pill.active {{ background: var(--accent); color: #1A1B26; border-color: var(--accent); }}

/* Failure cards — eval-regression-style: one card per failure,
   reply rendered as markdown when expanded. */
.failure-cards {{ display: flex; flex-direction: column; gap: 10px;
                  margin-top: 12px; }}
.failure-card {{ background: var(--bg-card); border: 1px solid var(--border);
                 border-radius: 8px; overflow: hidden; }}
.failure-card[open] {{ border-color: var(--accent); }}
.failure-card > summary {{ cursor: pointer; outline: none; padding: 12px 16px;
                            list-style: none; user-select: none; }}
.failure-card > summary::-webkit-details-marker {{ display: none; }}
.failure-card > summary::before {{ content: "▸"; color: var(--fg-muted);
                                    margin-right: 8px; display: inline-block;
                                    transition: transform 0.15s; }}
.failure-card[open] > summary::before {{ transform: rotate(90deg); }}
.failure-card-header {{ display: block; }}
.failure-card-badges {{ display: inline-flex; flex-wrap: wrap; gap: 6px;
                         margin-bottom: 8px; vertical-align: middle; }}
.arm-chip {{ background: var(--bg-elev); border: 1px solid var(--border);
             color: var(--fg); padding: 2px 8px; border-radius: 10px;
             font-size: 11px; font-weight: 600; }}
.segment-chip {{ background: transparent; border: 1px dashed var(--border);
                 color: var(--fg-dim); padding: 2px 8px; border-radius: 10px;
                 font-size: 11px; }}
.failure-card-query {{ font-weight: 600; color: var(--fg); margin: 4px 0 2px;
                        line-height: 1.4; }}
.failure-card-assertion {{ color: var(--fg-dim); font-size: 12.5px;
                            font-style: italic; line-height: 1.45; }}
.failure-card-body {{ padding: 0 16px 14px 16px; border-top: 1px solid var(--border);
                       margin-top: 4px; }}
.why {{ background: #7AA2F71A; border-left: 3px solid var(--accent);
        padding: 8px 12px; border-radius: 0 6px 6px 0; margin: 12px 0; }}
.why-label {{ color: var(--accent); font-size: 10px; text-transform: uppercase;
              letter-spacing: 0.06em; font-weight: 600; margin-bottom: 4px; }}
.why-text {{ color: var(--fg); font-size: 13px; line-height: 1.45; }}
.reply-block {{ margin: 12px 0; background: var(--bg); border: 1px solid var(--border);
                border-radius: 6px; padding: 12px 16px; }}
.reply-header {{ color: var(--fg-muted); font-size: 10px; text-transform: uppercase;
                  letter-spacing: 0.06em; font-weight: 600; margin-bottom: 8px; }}
.reply-body {{ color: var(--fg); font-size: 13.5px; line-height: 1.55; }}
.reply-body h1, .reply-body h2, .reply-body h3, .reply-body h4 {{
  margin: 12px 0 6px; color: var(--fg); }}
.reply-body h1 {{ font-size: 17px; }}
.reply-body h2 {{ font-size: 15px; }}
.reply-body h3 {{ font-size: 14px; }}
.reply-body h4 {{ font-size: 13px; }}
.reply-body p {{ margin: 6px 0; }}
.reply-body ul, .reply-body ol {{ margin: 6px 0; padding-left: 22px; }}
.reply-body li {{ margin: 3px 0; }}
.reply-body code {{ background: var(--bg-elev); padding: 1px 5px;
                     border-radius: 3px; font-size: 12px; }}
.reply-body pre {{ background: var(--bg-elev); padding: 8px 12px;
                    border-radius: 6px; overflow-x: auto; font-size: 12px; }}
.reply-body pre code {{ background: transparent; padding: 0; }}
.reply-body strong {{ color: var(--fg); }}
.reply-body em {{ color: var(--fg-dim); }}
.reply-body blockquote {{ border-left: 3px solid var(--border);
                           margin: 6px 0; padding: 4px 12px; color: var(--fg-dim); }}
.reply-body hr {{ border: none; border-top: 1px solid var(--border); margin: 10px 0; }}
.reply-body table {{ border-collapse: collapse; margin: 8px 0; }}
.reply-body th, .reply-body td {{ border: 1px solid var(--border); padding: 4px 8px;
                                    font-size: 12px; }}
details.rationale-fold {{ margin: 10px 0 0 0; background: var(--bg);
                           border: 1px solid var(--border); border-radius: 6px;
                           padding: 8px 14px; }}
details.rationale-fold > summary {{ cursor: pointer; outline: none;
                                     color: var(--fg-muted); font-size: 12px;
                                     text-transform: uppercase; letter-spacing: 0.06em;
                                     font-weight: 600; }}
.rationale-body {{ margin-top: 8px; color: var(--fg-dim); font-size: 12.5px;
                    line-height: 1.5; }}
.failure-empty {{ color: var(--fg-muted); font-style: italic; padding: 20px;
                   text-align: center; }}

.caveats {{ background: #E0AF6822; border-left: 3px solid #E0AF68; }}
.caveats ul {{ margin: 0; padding-left: 18px; }}
.caveats li {{ margin: 4px 0; color: var(--fg); }}

.footer {{ color: var(--fg-muted); font-size: 11px; text-align: center;
           padding: 16px 0; }}
</style>
</head>
<body>
<div class="container">

  <div class="run-header">
    <div class="run-title">SEVAL Run Triage — {title}</div>
    <div class="run-meta">
      <div class="run-meta-item"><label>Run ID</label>{run_id}</div>
      <div class="run-meta-item"><label>Date</label>{run_date}</div>
      <div class="run-meta-item"><label>Scenario</label>{scenario}</div>
      <div class="run-meta-item"><label>Rows</label>{rows}</div>
      <div class="run-meta-item"><label>Queries</label>{queries}</div>
      <div class="run-meta-item"><label>Segments</label>{segments_count}</div>
      <div class="run-meta-item"><label>Eval time</label>{eval_time_override}</div>
      <div class="run-meta-item"><label>Tenant user</label>{user_id}</div>
    </div>
  </div>

  <div class="section">
    <h2>Pass-rate KPIs</h2>
    <div class="note">Headline = critical pass rate. Aspirational failures are demoted.</div>
    <div class="kpi-row">{kpi_html}</div>
  </div>

  <div class="section">
    <h2>Inferred capability gaps</h2>
    <div class="note">Systemic gaps inferred from corroborating failures (≥3 rows each). <strong>Start here — this is the actionable section.</strong> Expand <em>Evidence</em> below the table for the full reasoning.</div>
    {gaps_html}
  </div>

  <div class="section">
    <h2>Failure family rollup</h2>
    <div class="note">Distribution of failures by root-cause family per arm.
      Answers: <em>was it the data, the assertion, the model, or unclear?</em></div>
    {bars_html}
    <div class="rollup-legend">
      <div class="rollup-legend-item"><span class="rollup-legend-swatch" style="background:#7AA2F7;"></span> Missing data</div>
      <div class="rollup-legend-item"><span class="rollup-legend-swatch" style="background:#E0AF68;"></span> Assertion</div>
      <div class="rollup-legend-item"><span class="rollup-legend-swatch" style="background:#F7768E;"></span> Model</div>
      <div class="rollup-legend-item"><span class="rollup-legend-swatch" style="background:#9AA5CE;"></span> Unclear</div>
      <div class="rollup-legend-item"><span class="rollup-legend-swatch" style="background:#565F89;"></span> Unlabeled</div>
    </div>
  </div>

  <div class="section">
    <h2>Top failure clusters</h2>
    <div class="note">Grouped by <code>(arm, label)</code>, sorted by count. Top 10 shown; singleton clusters (count = 1) are dropped.</div>
    <table class="std">
      <thead><tr>
        <th class="num">#</th><th>Arm</th><th>Label</th>
        <th class="num">Count</th><th>Representative queries</th>
      </tr></thead>
      <tbody>{cluster_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <h2>Feature flag set</h2>
    <div class="note">Per-slot flags extracted from <code>Settings.json</code>. Diffable across runs by the regression skills.</div>
    {flag_sections}
  </div>

  <div class="section">
    <details class="detail-fold">
      <summary><h2 style="display:inline;">Failure detail <span class="section-subhead">({detail_count} failures — click to expand)</span></h2></summary>
      <div class="note">Filter by arm, family, level, or segment. Search across query + assertion + label. Each card expands to show the model reply (rendered markdown) and the judge rationale.</div>
      <div class="controls">
        <input class="search-input" id="failure-search" type="text" placeholder="Search failures…" />
        {filters_html}
        <button class="pill" id="clear-filters" style="background:transparent;color:#9AA5CE;">Clear</button>
      </div>
      <div class="failure-cards" id="failure-cards">{detail_cards}</div>
      <div class="failure-empty" id="failure-empty" style="display:none;">No failures match the current filters.</div>
    </details>
  </div>

  <div class="section caveats">
    <h2>Caveats</h2>
    <ul>{caveats_html}</ul>
  </div>

  <div class="footer">
    Generated from <code>{csv_name}</code> on {generated_at} ·
    fingerprint schema <code>{schema_version}</code>
  </div>

</div>

<script>
(function() {{
  const facets = {{ arm: null, family: null, level: null, segment: null }};
  const search = document.getElementById("failure-search");
  const cards = Array.from(document.querySelectorAll("#failure-cards .failure-card"));
  const emptyMsg = document.getElementById("failure-empty");
  const pills = Array.from(document.querySelectorAll(".pill[data-facet]"));

  function apply() {{
    const q = (search.value || "").trim().toLowerCase();
    let anyVisible = false;
    for (const card of cards) {{
      let visible = true;
      for (const [k, v] of Object.entries(facets)) {{
        if (v && card.dataset[k] !== v) {{ visible = false; break; }}
      }}
      if (visible && q) {{
        if ((card.dataset.search || "").indexOf(q) === -1) visible = false;
      }}
      card.style.display = visible ? "" : "none";
      if (visible) anyVisible = true;
    }}
    if (emptyMsg) emptyMsg.style.display = anyVisible ? "none" : "";
  }}

  for (const pill of pills) {{
    pill.addEventListener("click", () => {{
      const facet = pill.dataset.facet;
      const value = pill.dataset.value;
      if (facets[facet] === value) {{
        facets[facet] = null;
        pill.classList.remove("active");
      }} else {{
        facets[facet] = value;
        for (const p of pills) {{
          if (p.dataset.facet === facet) p.classList.remove("active");
        }}
        pill.classList.add("active");
      }}
      apply();
    }});
  }}

  search.addEventListener("input", apply);

  document.getElementById("clear-filters").addEventListener("click", () => {{
    for (const k of Object.keys(facets)) facets[k] = null;
    for (const p of pills) p.classList.remove("active");
    search.value = "";
    apply();
  }});

  // Lazy markdown rendering — render once on first expand to keep initial
  // page load fast even when there are 200+ failure cards.
  function renderMd(el) {{
    if (el.dataset.rendered === "1") return;
    const src = el.getAttribute("data-md") || "";
    try {{
      const parsed = window.marked ? marked.parse(String(src)) : String(src);
      el.innerHTML = window.DOMPurify ? DOMPurify.sanitize(parsed) : parsed;
    }} catch (e) {{
      el.textContent = src;
    }}
    el.dataset.rendered = "1";
  }}
  for (const card of cards) {{
    card.addEventListener("toggle", () => {{
      if (card.open) {{
        card.querySelectorAll(".md-target").forEach(renderMd);
      }}
    }});
  }}
  // Also render nested rationale folds on demand.
  document.querySelectorAll("details.rationale-fold").forEach(d => {{
    d.addEventListener("toggle", () => {{
      if (d.open) d.querySelectorAll(".md-target").forEach(renderMd);
    }});
  }});

  // Per-slot flag search
  document.querySelectorAll(".flag-slot").forEach(slot => {{
    const input = slot.querySelector(".flag-search");
    const tokens = Array.from(slot.querySelectorAll(".flag-token"));
    if (!input) return;
    input.addEventListener("input", () => {{
      const q = (input.value || "").trim().toLowerCase();
      for (const t of tokens) {{
        t.style.display = (!q || t.textContent.toLowerCase().indexOf(q) !== -1) ? "" : "none";
      }}
    }});
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _check_strict(manifest: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    for f in manifest.get("failures", []):
        if not (f.get("failure_label") or "").strip():
            issues.append(f"failure {f.get('id')} has empty failure_label")
        if not (f.get("failure_evidence") or "").strip():
            issues.append(f"failure {f.get('id')} has empty failure_evidence")
    return issues


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path,
                   help="Fingerprint manifest from seval_run_triage_extract.py")
    p.add_argument("--out-md", type=Path, default=None,
                   help="Path for the markdown summary. Optional.")
    p.add_argument("--out-html", type=Path, default=None,
                   help="Path for the HTML report. Optional.")
    p.add_argument("--strict", action="store_true",
                   help="Fail if any failure row has empty failure_label or failure_evidence.")
    args = p.parse_args()

    if not args.out_md and not args.out_html:
        sys.exit("[render] Provide at least one of --out-md or --out-html.")

    with args.manifest.open("r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    if args.strict:
        issues = _check_strict(manifest)
        if issues:
            print(f"[render] Strict mode found {len(issues)} unblanked failures:",
                  file=sys.stderr)
            for issue in issues[:10]:
                print(f"  - {issue}", file=sys.stderr)
            if len(issues) > 10:
                print(f"  ... and {len(issues) - 10} more", file=sys.stderr)
            sys.exit(1)

    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(render_markdown(manifest), encoding="utf-8")
        print(f"[render] Markdown: {args.out_md}")

    if args.out_html:
        args.out_html.parent.mkdir(parents=True, exist_ok=True)
        args.out_html.write_text(render_html(manifest), encoding="utf-8")
        print(f"[render] HTML: {args.out_html}")


if __name__ == "__main__":
    main()

"""SEVAL Regression -> ADO Bug Sync.

Mirrors the doctrine of `scripts/ado_sync.py` (the `ocv-ticket-sync` skill) but
adapted for SEVAL HeroEval regression manifests produced by
`scripts/eval_regression_extract.py`.

Pipeline:

    1. classify-template
         Dumps every regression in the manifest into a JSON template
         where the agent fills in:
             topic       (one of the 13 OCV topic names)
             topic_num   (1..13)
             category    (Drafting | Replying | Scheduling | ...)
             sub_mode    (only when topic is 6 or 10; optional otherwise)
             notes       (optional, <= 20 words)

    2. propose
         Reads the manifest + the filled classifications. Clusters the
         regressions by (failing_side, topic_num, category) and writes a
         proposals JSON with one entry per cluster: title, priority,
         assignee (via configs/ado_owners_outlook-agent.json), and a
         pre-rendered HTML body. Always uses action="create" -- unlike
         the `ocv-ticket-sync` skill, this pipeline NEVER links to existing
         items (per explicit user policy: regression bugs are always
         net-new).

    3. execute
         Reads the (possibly user-edited) proposals JSON and POSTs one
         Bug per cluster to ADO. Two-gate doctrine: the script always
         prints the per-cluster plan first and blocks on stdin for the
         literal string 'yes' unless --yes is passed. The agent must
         only pass --yes after clearing Gate B via ask_user in chat.

Auth: DefaultAzureCredential (uses `az login`). The work item type,
area path, iteration, tags, and owner routing config all default to
the `ocv-ticket-sync` skill values so a single owners config covers both
pipelines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Reuse auth + owner-routing helpers from the `ocv-ticket-sync` skill script so we
# stay in lockstep with that pipeline. ado_sync.py lives next to this file.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared"))
from ado_sync import (  # noqa: E402
    ADO_ORG,
    ADO_PROJECT_ENC,
    API_BASE,
    API_VERSION,
    DEFAULT_AREA_PATH,
    DEFAULT_ITERATION,
    DEFAULT_OWNERS_CONFIG,
    DEFAULT_WORK_ITEM_TYPE,
    ado_request,
    compute_assignee,
    esc,
    load_owner_config,
    web_url,
)


# Force UTF-8 console output on Windows.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tags applied to every SEVAL regression bug (in addition to OutlookAgent
# which the `ocv-ticket-sync` skill also uses).
SEVAL_TAGS = ["OutlookAgent", "SevalRegression"]

# Priority mapping per cluster:
#   any critical-level assertion in the cluster -> P1 (ADO Priority 2)
#   else (all expected-level)                   -> P2 (ADO Priority 3)
PRIORITY_CRITICAL = "P1"
PRIORITY_EXPECTED = "P2"
PRIORITY_TO_ADO = {"P0": 1, "P1": 2, "P2": 3, "P3": 4}

# Truncation knobs for the bug body so a single cluster with many
# rolled-up assertions does not blow past ADO's field size limits.
REPLY_TRUNC_CHARS = 2500
RATIONALE_TRUNC_CHARS = 1200
MAX_ASSERTIONS_INLINE = 25  # per-cluster cap; the rest become a summary line

# The 13-topic taxonomy (kept in lockstep with ocv-analyze-and-ticket).
TOPIC_NAMES = {
    "1":  "Action not executed",
    "2":  "HitL (Human-in-the-loop) violation",
    "3":  "Output doesn't match intent",
    "4":  "Constraints ignored",
    "5":  "Unnecessary clarifying question",
    "6":  "Reliability failure",
    "7":  "Inaccurate or fabricated content",
    "8":  "Wrong context / grounding",
    "9":  "Tone, language, or format quality",
    "10": "File I/O failure",
    "11": "Calendar correctness",
    "12": "Capability refusal for in-scope request",
    "13": "Intrusive Outlook Agent UI",
}

ALLOWED_CATEGORIES = {
    "Drafting", "Replying", "Scheduling", "Search", "Summarization",
    "Triage", "Rules", "Settings", "Tasks", "Reminders", "Translation",
    "File", "General", "Unknown",
}


# ---------------------------------------------------------------------------
# Side label helpers (Mainline | CodeGen | other)
# ---------------------------------------------------------------------------

def _side_label_for_comparison(comparison: str, manifest: Dict[str, Any]) -> str:
    """Map a regression's `comparison` field to the friendly side label.

    `control_vs_control`        -> Mainline (control side regressed)
    `experiment_vs_experiment`  -> CodeGen  (experiment side regressed)

    Pulls the actual labels from the manifest so non-standard runs
    (e.g. a future "Sydney" vs "CodeGen" comparison) still produce
    accurate side names instead of hard-coded strings.
    """
    ctrl_name = manifest["control"]["name"]
    exp_name = manifest["experiment"]["name"]
    # Slot 0 of every run = control side (Mainline); slot 1 = experiment (CodeGen).
    # `control_vs_control` compares slot 0 across the two runs, so the failing
    # side is the slot-0 label, which is ctrl_name (Mainline by convention).
    if comparison == "control_vs_control":
        return ctrl_name
    if comparison == "experiment_vs_experiment":
        return exp_name
    return comparison


def seval_run_url(run_id: str) -> str:
    return f"https://seval.microsoft.com/job/{run_id}"


# ---------------------------------------------------------------------------
# classify-template subcommand
# ---------------------------------------------------------------------------

def cmd_classify_template(args: argparse.Namespace) -> None:
    manifest = _load_manifest(args.manifest)
    _apply_selection(manifest, getattr(args, "selection", None))
    regs = manifest["regressions"]
    template: Dict[str, Any] = {
        "manifest_path": os.path.relpath(args.manifest),
        "manifest_generated_at": manifest.get("generated_at"),
        "control_run": {
            "id": str(manifest["control"]["id"]),
            "name": manifest["control"]["name"],
            "run_date": manifest["control"]["run_date"],
        },
        "experiment_run": {
            "id": str(manifest["experiment"]["id"]),
            "name": manifest["experiment"]["name"],
            "run_date": manifest["experiment"]["run_date"],
        },
        "topic_taxonomy": TOPIC_NAMES,
        "allowed_categories": sorted(ALLOWED_CATEGORIES),
        "instructions": (
            "For each regression below, fill `topic_num` (1..13), `topic` "
            "(exact name from topic_taxonomy), `category` (from "
            "allowed_categories), and optionally `sub_mode` "
            "(only required for topic 6 = blank|error_string|hang or "
            "topic 10 = input|output). `notes` is free-form, <=20 words. "
            "Leave nothing blank -- propose will refuse to run on an "
            "unclassified regression."
        ),
        "regressions": {},
    }
    for r in regs:
        template["regressions"][r["id"]] = {
            "comparison": r["comparison"],
            "failing_side": _side_label_for_comparison(r["comparison"], manifest),
            "level": r["level"],
            "segment": r["segment"],
            "query": r["query"],
            "assertion": r["assertion"],
            "why_failed": r.get("why_failed", ""),
            "topic_num": "",
            "topic": "",
            "category": "",
            "sub_mode": "",
            "notes": "",
        }
    _write_json(args.out, template)
    print(f"[seval-ado] Wrote classification template -> {args.out}")
    print(f"[seval-ado] {len(regs)} regression(s) need classification.")


# ---------------------------------------------------------------------------
# propose subcommand
# ---------------------------------------------------------------------------

def cmd_propose(args: argparse.Namespace) -> None:
    manifest = _load_manifest(args.manifest)
    _apply_selection(manifest, getattr(args, "selection", None))
    classifications = _load_classifications(args.classifications)
    owners_cfg = load_owner_config(args.owners_config)

    # Validate classifications cover every regression in the manifest.
    reg_index = {r["id"]: r for r in manifest["regressions"]}
    missing = [rid for rid in reg_index if rid not in classifications["regressions"]]
    if missing:
        raise SystemExit(
            f"[seval-ado] {len(missing)} regression(s) missing from "
            f"classifications: {missing[:5]}{'...' if len(missing) > 5 else ''}"
        )

    # Validate each classification has the required fields filled in.
    unclassified: List[str] = []
    bad: List[str] = []
    for rid, c in classifications["regressions"].items():
        if not c.get("topic_num") or not c.get("category"):
            unclassified.append(rid)
            continue
        tnum = str(c["topic_num"]).strip()
        if tnum not in TOPIC_NAMES:
            bad.append(f"{rid}: topic_num={tnum!r} not in 1..13")
        if c["category"] not in ALLOWED_CATEGORIES:
            bad.append(f"{rid}: category={c['category']!r} not allowed")
        if tnum in ("6", "10") and not c.get("sub_mode"):
            bad.append(f"{rid}: topic {tnum} requires sub_mode")
    if unclassified:
        raise SystemExit(
            f"[seval-ado] {len(unclassified)} regression(s) unclassified "
            f"(topic_num/category blank): {unclassified[:5]}"
            f"{'...' if len(unclassified) > 5 else ''}"
        )
    if bad:
        raise SystemExit("[seval-ado] classification errors:\n  " + "\n  ".join(bad))

    # Cluster by (failing_side, topic_num, category).
    clusters_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in manifest["regressions"]:
        c = classifications["regressions"][r["id"]]
        side = _side_label_for_comparison(r["comparison"], manifest)
        tnum = str(c["topic_num"]).strip()
        cat = c["category"].strip()
        key = (side, tnum, cat)
        bucket = clusters_by_key.setdefault(key, {
            "failing_side": side,
            "topic_num": tnum,
            "topic": TOPIC_NAMES[tnum],
            "category": cat,
            "sub_modes": set(),
            "regressions": [],
        })
        if c.get("sub_mode"):
            bucket["sub_modes"].add(c["sub_mode"].strip())
        bucket["regressions"].append({
            "id": r["id"],
            "comparison": r["comparison"],
            "level": r["level"],
            "segment": r["segment"],
            "query": r["query"],
            "assertion": r["assertion"],
            "why_failed": r.get("why_failed", "") or "",
            "reply_failed": r.get("reply_failed", "") or "",
            "rationale_failed": r.get("rationale_failed", "") or "",
            "classification_notes": (c.get("notes") or "").strip(),
        })

    # Build the per-cluster proposals.
    ctrl = manifest["control"]
    exp = manifest["experiment"]
    report_url = args.report_url
    publish_safety = manifest.get("publish_safety", {})
    if not publish_safety.get("reviewed_for_publish"):
        raise SystemExit(
            "[seval-ado] Refusing to build proposals -- manifest's "
            "publish_safety.reviewed_for_publish is not true. The "
            "regression report should be published (or at least reviewed) "
            "before bugs are filed."
        )

    clusters: List[Dict[str, Any]] = []
    for (side, tnum, cat), bucket in sorted(clusters_by_key.items()):
        regs = bucket["regressions"]
        # Side -> which run is the failing one (which is always the
        # experiment run = the newer of the two HeroEval runs).
        failing_run = exp
        passed_run = ctrl
        n_crit = sum(1 for r in regs if r["level"] == "critical")
        n_exp = sum(1 for r in regs if r["level"] == "expected")
        priority = PRIORITY_CRITICAL if n_crit > 0 else PRIORITY_EXPECTED

        # Deterministic cluster ID.
        cluster_id = hashlib.sha1(
            f"{side}|t{tnum}|{cat}|{failing_run['id']}".encode("utf-8")
        ).hexdigest()[:10]

        sub_modes = sorted(bucket["sub_modes"])
        sub_mode_label = f" / {','.join(sub_modes)}" if sub_modes else ""

        # Default title; the agent may overwrite via decision.new_title
        # during Gate A review.
        default_title = (
            f"[SEVAL Regression] {side} - {cat}: T{tnum} "
            f"{bucket['topic']}{sub_mode_label} "
            f"({len(regs)} assertion{'s' if len(regs) != 1 else ''})"
        )

        # Owner routing -- pass the default title in so the title_keywords
        # rules in the owners config can match (e.g. 'codegen', 'shared mailbox',
        # 'oof', etc.).
        topic_with_label = f"{tnum}. {bucket['topic']}"
        assignee, assignee_name, rule_label = compute_assignee(
            default_title, cat, topic_with_label, owners_cfg
        )

        body_html = _render_cluster_body(
            cluster_id=cluster_id,
            failing_side=side,
            topic_num=tnum,
            topic_name=bucket["topic"],
            category=cat,
            sub_modes=sub_modes,
            regressions=regs,
            ctrl_run=ctrl,
            exp_run=exp,
            report_url=report_url,
        )

        clusters.append({
            "cluster_id": cluster_id,
            "failing_side": side,
            "failing_run": {
                "id": str(failing_run["id"]),
                "name": failing_run["name"],
                "run_date": failing_run["run_date"],
                "url": seval_run_url(str(failing_run["id"])),
            },
            "passed_run": {
                "id": str(passed_run["id"]),
                "name": passed_run["name"],
                "run_date": passed_run["run_date"],
                "url": seval_run_url(str(passed_run["id"])),
            },
            "topic_num": tnum,
            "topic": bucket["topic"],
            "category": cat,
            "sub_modes": sub_modes,
            "priority": priority,
            "n_regressions": len(regs),
            "n_critical": n_crit,
            "n_expected": n_exp,
            "regression_ids": [r["id"] for r in regs],
            "queries": sorted({r["query"] for r in regs}),
            "report_url": report_url,
            "decision": {
                "action": "create",  # SEVAL regression policy: always create
                "new_title": default_title,
                "assignee": assignee,
                "assignee_name": assignee_name,
                "assignee_rule": rule_label,
                "notes": "",
            },
            "body_html": body_html,
        })

    proposals = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_path": os.path.relpath(args.manifest),
        "classifications_path": os.path.relpath(args.classifications),
        "report_url": report_url,
        "ado": {
            "org": ADO_ORG,
            "project": ADO_PROJECT_ENC.replace("%20", " "),
            "area_path": args.area_path,
            "iteration": args.iteration,
            "work_item_type": args.work_item_type,
            "tags": list(SEVAL_TAGS),
        },
        "control": {
            "id": str(ctrl["id"]),
            "name": ctrl["name"],
            "run_date": ctrl["run_date"],
            "url": seval_run_url(str(ctrl["id"])),
        },
        "experiment": {
            "id": str(exp["id"]),
            "name": exp["name"],
            "run_date": exp["run_date"],
            "url": seval_run_url(str(exp["id"])),
        },
        "summary": {
            "n_clusters": len(clusters),
            "n_regressions_total": sum(c["n_regressions"] for c in clusters),
            "by_side": _count_by(clusters, "failing_side"),
            "by_priority": _count_by(clusters, "priority"),
            "unassigned": sum(1 for c in clusters if not c["decision"]["assignee"]),
        },
        "clusters": clusters,
    }
    _write_json(args.out, proposals)
    print(f"[seval-ado] Wrote proposals -> {args.out}")
    print(f"[seval-ado] {proposals['summary']['n_clusters']} cluster(s) "
          f"covering {proposals['summary']['n_regressions_total']} regression(s).")
    if proposals["summary"]["unassigned"]:
        print(f"[seval-ado] WARNING: {proposals['summary']['unassigned']} "
              f"cluster(s) unassigned (no owner rule matched).")


def _count_by(clusters: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in clusters:
        out[c[key]] = out.get(c[key], 0) + 1
    return out


# ---------------------------------------------------------------------------
# Body HTML rendering
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "&hellip; [truncated]"


def _render_cluster_body(*, cluster_id: str, failing_side: str,
                         topic_num: str, topic_name: str, category: str,
                         sub_modes: List[str], regressions: List[Dict[str, Any]],
                         ctrl_run: Dict[str, Any], exp_run: Dict[str, Any],
                         report_url: str) -> str:
    """Render the ADO bug body (System.Description + ReproSteps).

    Layout matches the user's spec:
      - Header card: base + latest SEVAL runs (date + URL), failing model
      - Cluster meta: topic, category, sub_modes, counts
      - Per-assertion block (capped at MAX_ASSERTIONS_INLINE):
          query, assertion, why_failed, failed model reply, judge rationale
      - Footer: link to the published regression report
    """
    ctrl_url = seval_run_url(str(ctrl_run["id"]))
    exp_url = seval_run_url(str(exp_run["id"]))
    sub_mode_html = (
        f" <strong>(sub_mode: {esc(', '.join(sub_modes))})</strong>"
        if sub_modes else ""
    )
    header = (
        f'<h2>SEVAL Regression &mdash; {esc(failing_side)} side</h2>'
        f'<p><strong>Failing model:</strong> {esc(failing_side)} '
        f'(slot {"0 / control" if failing_side == ctrl_run["name"] else "1 / experiment"})</p>'
        f'<p><strong>Base SEVAL:</strong> '
        f'<a href="{esc(ctrl_url)}">#{esc(str(ctrl_run["id"]))}</a> '
        f'({esc(ctrl_run["run_date"])})<br/>'
        f'<strong>Latest SEVAL:</strong> '
        f'<a href="{esc(exp_url)}">#{esc(str(exp_run["id"]))}</a> '
        f'({esc(exp_run["run_date"])})</p>'
        f'<p><strong>Topic:</strong> T{esc(topic_num)} &mdash; {esc(topic_name)}'
        f'{sub_mode_html}<br/>'
        f'<strong>Surface / Category:</strong> {esc(category)}<br/>'
        f'<strong>Cluster:</strong> {len(regressions)} assertion(s) '
        f'(<code>{esc(cluster_id)}</code>)</p>'
        f'<hr/>'
    )

    # Per-assertion blocks.
    blocks: List[str] = []
    to_show = regressions[:MAX_ASSERTIONS_INLINE]
    for i, r in enumerate(to_show, start=1):
        notes_html = (
            f'<p><em>Classifier note:</em> {esc(r["classification_notes"])}</p>'
            if r.get("classification_notes") else ""
        )
        blocks.append(
            f'<h3>Assertion {i} of {len(regressions)} '
            f'(level: {esc(r["level"])}, id: <code>{esc(r["id"])}</code>)</h3>'
            f'<p><strong>Query:</strong><br/>{esc(r["query"])}</p>'
            f'<p><strong>Assertion:</strong><br/>{esc(r["assertion"])}</p>'
            f'<p><strong>Why it failed:</strong><br/>{esc(r["why_failed"])}</p>'
            f'{notes_html}'
            f'<details><summary><strong>Failing model reply</strong> '
            f'(from {esc(failing_side)} @ #{esc(str(exp_run["id"]))})</summary>'
            f'<pre style="white-space:pre-wrap;font-family:Consolas,monospace;">'
            f'{esc(_trunc(r["reply_failed"], REPLY_TRUNC_CHARS))}'
            f'</pre></details>'
            f'<details><summary><strong>Judge rationale '
            f'(failure)</strong></summary>'
            f'<pre style="white-space:pre-wrap;font-family:Consolas,monospace;">'
            f'{esc(_trunc(r["rationale_failed"], RATIONALE_TRUNC_CHARS))}'
            f'</pre></details>'
            f'<hr/>'
        )
    if len(regressions) > MAX_ASSERTIONS_INLINE:
        omitted = len(regressions) - MAX_ASSERTIONS_INLINE
        blocks.append(
            f'<p><em>+ {omitted} additional assertion(s) in this cluster; '
            f'see the full report linked below.</em></p>'
        )

    footer = (
        f'<hr/>'
        f'<p><strong>Full regression report:</strong> '
        f'<a href="{esc(report_url)}">{esc(report_url)}</a></p>'
        f'<p style="color:#888;font-size:11px;">Filed by '
        f'<code>seval-regression-ticket-sync</code> on '
        f'{datetime.now().strftime("%Y-%m-%d")}.</p>'
    )
    return header + "".join(blocks) + footer


# ---------------------------------------------------------------------------
# execute subcommand
# ---------------------------------------------------------------------------

def cmd_execute(args: argparse.Namespace) -> None:
    proposals = _read_json(args.proposals)
    clusters = proposals["clusters"]

    ado = proposals["ado"]
    plan_lines = _format_plan(clusters, ado)
    print("\n".join(plan_lines))
    if args.dry_run:
        print("\n[seval-ado] --dry-run set; no ADO writes performed.")
        return

    if not args.yes:
        sys.stdout.write(
            "\nType 'yes' to proceed with the ADO writes above: ")
        sys.stdout.flush()
        resp = sys.stdin.readline().strip().lower()
        if resp != "yes":
            print("[seval-ado] aborted.")
            return

    # Apply.
    created_urls: List[Tuple[str, str]] = []
    for c in clusters:
        if c["decision"]["action"] == "skip":
            print(f"  [skip] {c['cluster_id']}")
            continue
        ops = _build_create_payload(c, ado)
        url = (f"{API_BASE}/wit/workitems/$"
               f"{urllib.parse.quote(ado['work_item_type'])}"
               f"?api-version={API_VERSION}")
        try:
            wi = ado_request("POST", url, body=ops,
                             content_type="application/json-patch+json")
        except RuntimeError as e:
            print(f"  [ERROR] cluster {c['cluster_id']} failed: {e}")
            c["execution"] = {"status": "error", "error": str(e)}
            continue
        wi_id = wi.get("id")
        wi_url = web_url(wi_id) if wi_id else ""
        c["execution"] = {
            "status": "created",
            "ado_id": wi_id,
            "ado_url": wi_url,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        created_urls.append((c["cluster_id"], wi_url))
        print(f"  [new] #{wi_id} <- {c['decision']['new_title']}")

    # Write proposals back with execution metadata.
    _write_json(args.proposals, proposals)
    print(f"\n[seval-ado] Created {len(created_urls)} bug(s).")
    for cid, url in created_urls:
        print(f"  {cid} -> {url}")


def _build_create_payload(cluster: Dict[str, Any],
                          ado: Dict[str, Any]) -> List[Dict[str, Any]]:
    decision = cluster["decision"]
    title = decision.get("new_title") or (
        f"[SEVAL Regression] {cluster['failing_side']} - "
        f"{cluster['category']} (T{cluster['topic_num']})"
    )
    priority = PRIORITY_TO_ADO.get(cluster["priority"], 3)
    tags = "; ".join(ado.get("tags") or [])
    ops = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.AreaPath", "value": ado["area_path"]},
        {"op": "add", "path": "/fields/System.IterationPath", "value": ado["iteration"]},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": cluster["body_html"]},
        {"op": "add", "path": "/fields/System.Description", "value": cluster["body_html"]},
    ]
    if tags:
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": tags})
    assignee = decision.get("assignee")
    if assignee:
        ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assignee})
    return ops


def _format_plan(clusters: List[Dict[str, Any]],
                 ado: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    out.append("=" * 72)
    out.append("SEVAL REGRESSION -> ADO BUG SYNC PLAN")
    out.append("=" * 72)
    out.append(f"  Area path     : {ado['area_path']}")
    out.append(f"  Iteration     : {ado['iteration']}")
    out.append(f"  Work item type: {ado['work_item_type']}")
    out.append(f"  Tags          : {', '.join(ado.get('tags') or [])}")
    out.append("")
    n_create = sum(1 for c in clusters if c["decision"]["action"] == "create")
    n_skip = sum(1 for c in clusters if c["decision"]["action"] == "skip")
    n_unassigned = sum(1 for c in clusters
                       if c["decision"]["action"] == "create"
                       and not c["decision"].get("assignee"))
    out.append(f"  Clusters      : {len(clusters)} total "
               f"({n_create} create, {n_skip} skip)")
    if n_unassigned:
        out.append(f"  ! Unassigned  : {n_unassigned} cluster(s) "
                   f"will be filed without an assignee")
    out.append("")
    for c in clusters:
        marker = "[NEW]" if c["decision"]["action"] == "create" else "[skip]"
        owner = c["decision"].get("assignee_name") or "(unassigned)"
        out.append(f"  {marker} {c['priority']} {c['failing_side']:>9} "
                   f"T{c['topic_num']:>2} {c['category']:<14} "
                   f"({c['n_regressions']:>2} reg) "
                   f"-> {owner}")
        out.append(f"        {c['decision']['new_title']}")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_manifest(path: str) -> Dict[str, Any]:
    return _read_json(path)


def _load_classifications(path: str) -> Dict[str, Any]:
    return _read_json(path)


def _apply_selection(manifest: Dict[str, Any], selection_path: Optional[str]) -> None:
    """Drop regressions where the selection file marks `include: false`.

    The selection file is the JSON exported from the rendered HTML report's
    "Download ADO selection" button. Schema:

        {
          "manifest_slug": "...",
          "exported_at": "...",
          "selections": [ { "id": "<10-char>", "include": true|false }, ... ]
        }

    Rows whose id is not mentioned default to included (matches the HTML
    default-on doctrine). Mutates `manifest["regressions"]` in place.
    """
    if not selection_path:
        return
    payload = _read_json(selection_path)
    selections = payload.get("selections") or []
    excluded_ids = {
        s["id"] for s in selections
        if isinstance(s, dict) and s.get("id") and s.get("include") is False
    }
    if not excluded_ids:
        print(f"[seval-ado] Selection file {selection_path} had no excludes; "
              f"keeping all {len(manifest['regressions'])} regression(s).")
        return
    before = len(manifest["regressions"])
    kept = [r for r in manifest["regressions"] if r["id"] not in excluded_ids]
    dropped = before - len(kept)
    manifest["regressions"] = kept
    slug = payload.get("manifest_slug", "<no-slug>")
    print(f"[seval-ado] Applied selection from {selection_path} "
          f"(slug={slug}): excluded {dropped} of {before} regression(s); "
          f"{len(kept)} remain.")


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("classify-template",
                        help="Emit a classification JSON template "
                             "(agent fills in topic/category per regression).")
    p1.add_argument("--manifest", required=True,
                    help="Path to the regression manifest JSON.")
    p1.add_argument("--out", required=True,
                    help="Output path for the classifications template JSON.")
    p1.add_argument("--selection", default=None,
                    help="Optional path to an ADO-selection JSON exported "
                         "from the rendered HTML report. Regressions marked "
                         "include=false are dropped before templating.")
    p1.set_defaults(func=cmd_classify_template)

    p2 = sub.add_parser("propose",
                        help="Cluster classified regressions and emit "
                             "a proposals JSON for review.")
    p2.add_argument("--manifest", required=True)
    p2.add_argument("--classifications", required=True,
                    help="Path to the filled classifications JSON.")
    p2.add_argument("--report-url", required=True,
                    help="Published HTML report URL (will be linked in "
                         "every bug body).")
    p2.add_argument("--out", required=True,
                    help="Output path for the proposals JSON.")
    p2.add_argument("--selection", default=None,
                    help="Optional path to an ADO-selection JSON exported "
                         "from the rendered HTML report. Regressions marked "
                         "include=false are dropped before clustering. Must "
                         "match the --selection used for classify-template.")
    p2.add_argument("--area-path", default=DEFAULT_AREA_PATH)
    p2.add_argument("--iteration", default=DEFAULT_ITERATION)
    p2.add_argument("--work-item-type", default=DEFAULT_WORK_ITEM_TYPE)
    p2.add_argument("--owners-config", default=DEFAULT_OWNERS_CONFIG)
    p2.set_defaults(func=cmd_propose)

    p3 = sub.add_parser("execute",
                        help="Create one ADO Bug per cluster.")
    p3.add_argument("--proposals", required=True)
    p3.add_argument("--dry-run", action="store_true",
                    help="Print the plan and exit without touching ADO.")
    p3.add_argument("--yes", action="store_true",
                    help="Skip the stdin 'yes' prompt. The agent must "
                         "have cleared Gate B with the user via "
                         "ask_user before passing this flag.")
    p3.set_defaults(func=cmd_execute)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

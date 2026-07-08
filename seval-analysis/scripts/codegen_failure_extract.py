"""
codegen_failure_extract.py — CodeGen-only failure triage extractor.

Reads a SINGLE combined SEVAL assertions CSV (one row per (query, assertion)
with both control=Mainline and experiment=CodeGen columns), filters to the
CodeGen failures (`score_experiment == 0`), classifies each into a
standardized failure *trend* + root-cause *family* + *tool/primitive*, attaches
the cross-arm status, and writes a manifest JSON.

The combined CSV already carries `segment` and `level` inline, so the
4-artifact `seval-run-triage` extractor is not required for this run.

Classification is deterministic and grounded in the verified trend signal for
run 576292 (EmailTriagePrimitivesGoldenSet). The cross-arm guardrail from
seval-run-triage is applied: a Missing-data / Assertion label is demoted to the
Model family when the other arm (Mainline) passed the same (query, assertion).

Usage:
  python scripts/codegen_failure_extract.py \
    --csv  "<path to [Assertions]_*.csv>" \
    --run-id 576292 \
    --run-name "EmailTriagePrimitivesGoldenSet (Mainline vs CodeGen)" \
    --run-date 2026-06-24 \
    --out  data/eval-manifests/576292_2026-06-24_codegen.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

SCHEMA_VERSION = "codegen-triage-1.0"
SEVAL_JOB_URL = "https://seval.microsoft.com/job/{run_id}"

REQUIRED_COLS = {
    "query", "assertion", "score_control", "score_experiment",
    "rationale_experiment", "sydney_reply_experiment",
}

# Family for each standardized trend. The trend is the observable failure mode
# the user wants surfaced ("topics/trends"); the family answers the rollup
# question "data / assertion / model / unclear".
TREND_FAMILY = {
    "Tool not invoked":             ("Model",       "Tool invocation error"),
    "Missing citation/annotation":  ("Model",       "Format violation"),
    "Write did not persist":        ("Model",       "Tool invocation error"),
    "Target folder missing":        ("Missing data","Missing grounding data"),
    "Mock tool response":           ("Model",       "Factual error"),
    "Capability refusal":           ("Model",       "Capability refusal"),
    "Tool execution error":         ("Model",       "Tool invocation error"),
    "Missing disambiguation list":  ("Model",       "Partial execution"),
    "Missing scope statement":      ("Assertion",   "Presentation nuance"),
    "Missing count statement":      ("Assertion",   "Presentation nuance"),
    "Partial / incorrect execution":("Model",       "Partial execution"),
    "Unclear":                      ("Unclear",     "Unclear"),
}

# Trends in the Missing-data / Assertion families that the cross-arm guardrail
# demotes to a Model label when Mainline passed the same (query, assertion).
GUARDRAIL_DEMOTE = {
    "Target folder missing":   ("Model", "Failed task"),
    "Missing scope statement": ("Model", "Partial execution"),
    "Missing count statement": ("Model", "Partial execution"),
}


def _num(v: Any) -> Optional[int]:
    try:
        if pd.isna(v):
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _txt(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def derive_tool(segment: str, assertion: str) -> str:
    """Primitive under test. Prefer the clean `segment`; fall back to the API
    tool name parsed from an intent_detection.test_tool_invocation assertion."""
    seg = _txt(segment)
    if seg and seg.lower() != "nan":
        return seg
    m = re.search(r"test_tool_invocation\(([^)]+)\)", assertion)
    if m:
        return m.group(1).strip()
    return "Unknown"


def classify(assertion: str, rationale: str, reply: str) -> str:
    """Return the standardized trend for one CodeGen failure row."""
    a = assertion.lower()
    r = (rationale + " " + reply).lower()
    prog = not rationale.strip()

    # Programmatic assertions (judge rationale is empty).
    if "test_tool_invocation" in a:
        return "Tool not invoked"
    if "test_email_in_citation_or_annotation" in a:
        return "Missing citation/annotation"
    if prog:
        # Other programmatic check (e.g. flag due-date) with no rationale text.
        return "Partial / incorrect execution"

    # LLM-judged rows: order matters — most specific tool/data signal first.
    if re.search(r"did ?n.?t stick|remained unchanged|update.*did not (?:appl|persist|take)"
                 r"|not.*persist|update.*didn.?t|read-?back verification|verification read-?back"
                 r"|verification still shows|still shows the email as|still only includes"
                 r"|did not (?:take|go through)", r):
        return "Write did not persist"
    if re.search(r"\[?mock\]?\s*folder|came back as|returned.*\bmock\b|name.*\bmock\b", r):
        return "Mock tool response"
    if re.search(r"did not show|closest available|no .{0,20}folder|folder.*not.*(?:exist|found|available)"
                 r"|could not find.*folder", r):
        return "Target folder missing"
    if re.search(r"not (?:currently )?capable|isn.?t supported|not supported|can.?not |unable to|i can.?t "
                 r"|not exposed|cannot be performed|action is not available", r):
        return "Capability refusal"
    if re.search(r"disambiguation list|did not present a (?:disambiguation|numbered) list"
                 r"|present.*list.*choose|should have asked.*which", r):
        return "Missing disambiguation list"
    if re.search(r"\berror\b|failed to|exception|timeout|server error|did not complete", r):
        return "Tool execution error"
    if re.search(r"future emails|existing emails|only.*future|applies only to|scope of the (?:rule|change)", r):
        return "Missing scope statement"
    if re.search(r"total number|number of (?:emails|messages).*(?:flag|mark|pin)|how many|state.*count", r):
        return "Missing count statement"
    return "Partial / incorrect execution"


def short_evidence(trend: str, rationale: str, reply: str) -> str:
    """<=25-word PM-voice evidence line; quotes a short snippet when useful."""
    src = reply if reply else rationale
    snippet = ""
    for pat in (r"\[?mock\]?\s*folder", r"did ?n.?t stick", r"closest available [A-Za-z]+",
                r"not (?:currently )?capable", r"remained unchanged"):
        m = re.search(pat, src, re.IGNORECASE)
        if m:
            snippet = m.group(0)
            break
    templates = {
        "Tool not invoked": "Expected tool was not invoked; intent_detection tool-invocation check failed.",
        "Missing citation/annotation": "Reply omitted the required email citation/annotation; programmatic citation check failed.",
        "Write did not persist": "Agent reported the write did not apply or read-back still shows the old state.",
        "Target folder missing": "Target folder absent in mailbox; agent fell back to another folder.",
        "Mock tool response": "Tool returned a mock/placeholder name instead of the requested value.",
        "Capability refusal": "Agent refused the action as not supported or not exposed in this context.",
        "Tool execution error": "Tool call surfaced an error or did not complete.",
        "Missing disambiguation list": "Agent acted directly instead of presenting the expected disambiguation list.",
        "Missing scope statement": "Reply omitted the future-only scope confirmation the assertion required.",
        "Missing count statement": "Reply omitted the total-count confirmation the assertion required.",
        "Partial / incorrect execution": "Agent completed some but not all required steps, or answered incorrectly.",
        "Unclear": "Insufficient evidence to classify; flagged for human review.",
    }
    base = templates.get(trend, "CodeGen reply did not satisfy the assertion.")
    if snippet:
        base = base.rstrip(".") + f"; reply shows `{snippet}`."
    return base


def main() -> None:
    ap = argparse.ArgumentParser(description="CodeGen-only SEVAL failure extractor")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-name", default="")
    ap.add_argument("--run-date", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"[extract] CSV not found: {csv_path}")

    df = pd.read_csv(csv_path, encoding="utf-8")
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        sys.exit(f"[extract] CSV missing required columns: {sorted(missing)}")

    total_rows = len(df)
    df["_sc"] = df["score_control"].map(_num)
    df["_se"] = df["score_experiment"].map(_num)

    exp_total = int((df["_se"].notna()).sum())
    exp_pass = int((df["_se"] == 1).sum())
    exp_fail = int((df["_se"] == 0).sum())

    failures: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for _, row in df[df["_se"] == 0].iterrows():
        query = _txt(row.get("query"))
        assertion = _txt(row.get("assertion"))
        segment = _txt(row.get("segment"))
        level = _txt(row.get("level")) or "expected"
        rationale = _txt(row.get("rationale_experiment"))
        reply = _txt(row.get("sydney_reply_experiment"))
        sc = _num(row.get("score_control"))

        cross_arm = "other_passed" if sc == 1 else "both_failed"
        tool = derive_tool(segment, assertion)
        trend = classify(assertion, rationale, reply)
        family, label = TREND_FAMILY[trend]

        guardrail_relabel = False
        if cross_arm == "other_passed" and trend in GUARDRAIL_DEMOTE:
            family, label = GUARDRAIL_DEMOTE[trend]
            guardrail_relabel = True

        rid = hashlib.sha1(f"{query}|{assertion}".encode("utf-8")).hexdigest()[:10]
        if rid in seen_ids:
            rid = hashlib.sha1(f"{query}|{assertion}|{len(failures)}".encode("utf-8")).hexdigest()[:10]
        seen_ids.add(rid)

        failures.append({
            "id": rid,
            "arm": "experiment",
            "query": query,
            "segment": segment,
            "tool": tool,
            "assertion": assertion,
            "level": level,
            "cross_arm_status": cross_arm,
            "trend": trend,
            "family": family,
            "failure_label": label,
            "guardrail_relabel": guardrail_relabel,
            "is_programmatic": not rationale,
            "failure_evidence": short_evidence(trend, rationale, reply),
            "rationale_experiment": rationale,
            "reply_experiment": reply,
        })

    # ---- rollups -----------------------------------------------------------
    def rollup(key: str) -> List[Dict[str, Any]]:
        c = Counter(f[key] for f in failures)
        segs = defaultdict(Counter)
        crit = Counter()
        for f in failures:
            segs[f[key]][f["segment"] or "—"] += 1
            if f["level"] == "critical":
                crit[f[key]] += 1
        out = []
        for k, n in c.most_common():
            top_segs = ", ".join(f"{s} ({v})" for s, v in segs[k].most_common(3))
            out.append({"key": k, "count": n, "critical": crit[k], "top_segments": top_segs})
        return out

    trend_rollup = rollup("trend")
    tool_rollup = rollup("tool")
    family_counts = Counter(f["family"] for f in failures)
    crossarm_counts = Counter(f["cross_arm_status"] for f in failures)
    level_counts = Counter(f["level"] for f in failures)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "focus": "experiment (CodeGen) failures only",
        "run": {
            "id": str(args.run_id),
            "name": args.run_name or csv_path.stem,
            "date": args.run_date,
            "control_label": "Mainline",
            "experiment_label": "CodeGen",
            "job_url": SEVAL_JOB_URL.format(run_id=args.run_id),
            "csv_path": str(csv_path),
        },
        "summary": {
            "total_assertions": total_rows,
            "experiment": {
                "evaluated": exp_total, "passed": exp_pass, "failed": exp_fail,
                "pass_rate": round(exp_pass / exp_total, 4) if exp_total else 0.0,
            },
            "failures_by_family": dict(family_counts),
            "failures_by_cross_arm": dict(crossarm_counts),
            "failures_by_level": dict(level_counts),
            "regressions_control_passed": crossarm_counts.get("other_passed", 0),
            "both_failed": crossarm_counts.get("both_failed", 0),
        },
        "trend_rollup": trend_rollup,
        "tool_rollup": tool_rollup,
        "failures": failures,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[extract] wrote {out_path}")
    print(f"[extract] CodeGen failures: {exp_fail} / {exp_total} evaluated "
          f"(pass rate {manifest['summary']['experiment']['pass_rate']:.1%})")
    print(f"[extract] regressions (Mainline passed): {crossarm_counts.get('other_passed', 0)} | "
          f"both failed: {crossarm_counts.get('both_failed', 0)}")
    print("[extract] top trends:")
    for t in trend_rollup[:12]:
        print(f"    {t['count']:4d}  {t['key']}  (crit {t['critical']})")


if __name__ == "__main__":
    main()

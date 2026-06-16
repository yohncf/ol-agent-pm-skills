"""
seval_run_triage_extract.py — Single-SEVAL-run failure triage extractor.

Auto-detects the four artifacts inside a SEVAL run folder, joins them per
(query, assertion), attaches assertion `level` from the YAML doctrine and
`segment` / `query_hash` from the TSV, captures feature-flag sets from
Settings.json, and emits a scaffold "fingerprint" manifest with one entry
per (arm, query, assertion) failure.

The script is deterministic and never invents prose. The agent is
responsible for filling `failure_label` and `failure_evidence` on every
failure row after this script runs. See SKILL.md for the taxonomy and
PM-voice rules.

Outputs `data/eval-manifests/<run-id>_<date>_fingerprint.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover
    sys.exit("[extract] PyYAML is required. pip install pyyaml")


for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


SCHEMA_VERSION = "fingerprint-1.1"
_SLOT_LABEL_OVERRIDES = {"control": "Mainline", "experiment": "CodeGen"}
FLAG_SPLIT = re.compile(r"\s*,\s*")
VALID_ARMS = ("control", "experiment", "both")
VALID_LEVELS = {"critical", "expected", "aspirational"}


REQUIRED_CSV_COLS = {
    "query", "assertion",
    "score_control", "rationale_control", "sydney_reply_control",
    "score_experiment", "rationale_experiment", "sydney_reply_experiment",
}


def derive_side_label(exp_name: str) -> str:
    key = (exp_name or "").strip().lower()
    if key in _SLOT_LABEL_OVERRIDES:
        return _SLOT_LABEL_OVERRIDES[key]
    return (exp_name or "Unknown").strip().title() or "Unknown"


# ---------------------------------------------------------------------------
# Artifact auto-detect
# ---------------------------------------------------------------------------


def find_artifacts(folder: Path) -> Dict[str, Path]:
    """Locate the four SEVAL artifacts inside a run folder.

    Heuristics:
    - CSV: starts with `[Assertions]` (preferred), else the only `*.csv`.
    - TSV: the only `*.tsv`.
    - YAML: the only `*.yaml` or `*.yml`.
    - JSON: filename `Settings.json` (case-insensitive), else the only
      `*.json` in the folder.

    Errors loudly when ambiguous or missing.
    """
    if not folder.exists() or not folder.is_dir():
        sys.exit(f"[extract] Run folder not found: {folder}")

    csvs = sorted(folder.glob("*.csv"))
    tsvs = sorted(folder.glob("*.tsv"))
    yamls = sorted(list(folder.glob("*.yaml")) + list(folder.glob("*.yml")))
    jsons = sorted(folder.glob("*.json"))

    # CSV: prefer [Assertions]_*.csv
    csv = next((c for c in csvs if c.name.lower().startswith("[assertions]")), None)
    if not csv:
        if len(csvs) == 1:
            csv = csvs[0]
        elif not csvs:
            sys.exit(f"[extract] No *.csv found in {folder}")
        else:
            sys.exit(f"[extract] Multiple CSVs found and none start with `[Assertions]`: "
                     f"{[c.name for c in csvs]}. Pass explicit paths.")

    if not tsvs:
        sys.exit(f"[extract] No *.tsv found in {folder}")
    if len(tsvs) > 1:
        sys.exit(f"[extract] Multiple TSVs found: {[t.name for t in tsvs]}.")
    tsv = tsvs[0]

    if not yamls:
        sys.exit(f"[extract] No *.yaml/*.yml found in {folder}")
    if len(yamls) > 1:
        sys.exit(f"[extract] Multiple YAML files found: {[y.name for y in yamls]}.")
    yaml_path = yamls[0]

    settings = next((j for j in jsons if j.name.lower() == "settings.json"), None)
    if not settings:
        if len(jsons) == 1:
            settings = jsons[0]
        elif not jsons:
            sys.exit(f"[extract] No Settings.json found in {folder}")
        else:
            sys.exit(f"[extract] Multiple JSON files found and none named Settings.json: "
                     f"{[j.name for j in jsons]}.")

    return {"csv": csv, "tsv": tsv, "yaml": yaml_path, "settings": settings}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED_CSV_COLS - set(df.columns)
    if missing:
        sys.exit(f"[extract] CSV missing required columns: {sorted(missing)}")
    for col in ("query", "assertion"):
        df[col] = df[col].astype(str).str.strip()
    for col in ("score_control", "score_experiment"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "segment" not in df.columns:
        df["segment"] = ""
    if "level" not in df.columns:
        df["level"] = ""
    df["segment"] = df["segment"].fillna("").astype(str).str.strip()
    df["level"] = df["level"].fillna("").astype(str).str.strip().str.lower()
    return df


def load_tsv(path: Path) -> Dict[str, Dict[str, str]]:
    """Return {normalized_utterance: {segment, query_hash, user_id, timestamp}}.

    The TSV header from SEVAL is:
      Utterance \\t Segment 2 \\t annotation \\t query_hash \\t user_id \\t timestamp

    `Segment 2` is sometimes labeled `segment` in other shapes; we accept
    either by reading the first row and picking the first column that
    contains "segment" (case-insensitive).
    """
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    cols = {c.lower().strip(): c for c in df.columns}

    def _col(*candidates: str) -> Optional[str]:
        for cand in candidates:
            if cand in cols:
                return cols[cand]
        # Fuzzy: pick first col containing the candidate substring.
        for cand in candidates:
            for low, orig in cols.items():
                if cand in low:
                    return orig
        return None

    utt_col = _col("utterance", "query")
    seg_col = _col("segment 2", "segment")
    qhash_col = _col("query_hash", "query hash", "id")
    uid_col = _col("user_id", "user id", "userid")
    ts_col = _col("timestamp", "time")

    if utt_col is None:
        sys.exit(f"[extract] TSV missing an Utterance/Query column: {list(df.columns)}")

    out: Dict[str, Dict[str, str]] = {}
    for _, row in df.iterrows():
        utt = str(row[utt_col]).strip()
        if not utt:
            continue
        out[utt] = {
            "segment": str(row[seg_col]).strip() if seg_col else "",
            "query_hash": str(row[qhash_col]).strip() if qhash_col else "",
            "user_id": str(row[uid_col]).strip() if uid_col else "",
            "timestamp": str(row[ts_col]).strip() if ts_col else "",
        }
    return out


def load_yaml_doctrine(path: Path) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
    """Return two lookups for assertion → level:

    - by_hash: {query_hash: {assertion_text: level}}  (flattened to
      {(query_hash, assertion_text): level} for direct dict-lookup)
    - by_query: {(query_text, assertion_text): level}  fallback when the
      CSV row carries no query_hash.
    """
    with path.open("r", encoding="utf-8") as fh:
        doc = _yaml.safe_load(fh)
    if not isinstance(doc, list):
        sys.exit(f"[extract] YAML doctrine root must be a list: {path}")

    by_hash: Dict[Tuple[str, str], str] = {}
    by_query: Dict[Tuple[str, str], str] = {}

    for entry in doc:
        if not isinstance(entry, dict):
            continue
        qid = str(entry.get("id", "")).strip()
        qtext = str(entry.get("query", "")).strip()
        for a in entry.get("assertions") or []:
            if not isinstance(a, dict):
                continue
            atext = str(a.get("text", "")).strip()
            level = str(a.get("level", "")).strip().lower()
            if not atext or not level:
                continue
            if level not in VALID_LEVELS:
                # Tolerant: keep whatever the doctrine says, but lowercase it.
                pass
            if qid:
                by_hash[(qid, atext)] = level
            if qtext:
                by_query[(qtext, atext)] = level
    return by_hash, by_query  # type: ignore[return-value]


def load_settings(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Feature-flag extraction (mirrors eval_regression_extract.py logic)
# ---------------------------------------------------------------------------


def _get_exp_configs_unified(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(doc.get("exp_configs_unified"), list):
        return doc["exp_configs_unified"]
    payload = doc.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("exp_configs_unified"), list):
        return payload["exp_configs_unified"]
    return []


def extract_flag_slots(settings: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    confs = _get_exp_configs_unified(settings)
    errors: List[str] = []
    if not confs:
        errors.append("missing exp_configs_unified at top level or under payload")
        return [], errors

    slots: List[Dict[str, Any]] = []
    for i, slot in enumerate(confs[:2]):  # SEVAL ships 2 slots: 0=Mainline, 1=CodeGen
        exp_name = str(slot.get("exp_name", f"slot-{i}"))
        sydney = slot.get("sydney") or {}
        variants = sydney.get("variants")
        if variants is None:
            errors.append(f"slot {i} ({exp_name}): sydney.variants missing")
            slots.append({
                "slot_index": i, "side_label": derive_side_label(exp_name),
                "exp_name": exp_name, "flags": [], "count": 0,
            })
            continue
        if isinstance(variants, str):
            variants = [variants]
        if not isinstance(variants, list):
            errors.append(f"slot {i} ({exp_name}): sydney.variants has unexpected shape "
                          f"{type(variants).__name__}")
            slots.append({
                "slot_index": i, "side_label": derive_side_label(exp_name),
                "exp_name": exp_name, "flags": [], "count": 0,
            })
            continue
        tokens: List[str] = []
        seen: set = set()
        for v in variants:
            if not isinstance(v, str):
                continue
            for tok in FLAG_SPLIT.split(v):
                tok = tok.strip()
                if tok and tok not in seen:
                    seen.add(tok)
                    tokens.append(tok)
        tokens.sort()
        slots.append({
            "slot_index": i,
            "side_label": derive_side_label(exp_name),
            "exp_name": exp_name,
            "flags": tokens,
            "count": len(tokens),
        })
    return slots, errors


def _get_global(settings: Dict[str, Any], *path: str) -> Optional[str]:
    """Walk settings.global_config or settings.queries to fetch a string."""
    for root_key in ("global_config", "queries"):
        node: Any = settings.get(root_key) or {}
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            return node
    # Last resort: top-level
    node = settings
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def _pass_rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def summarize_arm(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-arm summary: counts, pass rate, breakdown by level + segment."""
    total = len(rows)
    passed = sum(1 for r in rows if r["score"] == 1)
    failed = sum(1 for r in rows if r["score"] == 0)

    by_level: Dict[str, Dict[str, int]] = {}
    by_segment: Dict[str, Dict[str, int]] = {}
    for r in rows:
        lvl = r.get("level") or "unspecified"
        seg = r.get("segment") or "unspecified"
        by_level.setdefault(lvl, {"total": 0, "passed": 0})
        by_segment.setdefault(seg, {"total": 0, "passed": 0})
        by_level[lvl]["total"] += 1
        by_segment[seg]["total"] += 1
        if r["score"] == 1:
            by_level[lvl]["passed"] += 1
            by_segment[seg]["passed"] += 1

    pass_rate_by_level = {
        lvl: {"rate": _pass_rate(v["passed"], v["total"]),
              "passed": v["passed"], "total": v["total"]}
        for lvl, v in sorted(by_level.items())
    }
    pass_rate_by_segment = {
        seg: {"rate": _pass_rate(v["passed"], v["total"]),
              "passed": v["passed"], "total": v["total"]}
        for seg, v in sorted(by_segment.items())
    }

    return {
        "rows": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": _pass_rate(passed, total),
        "pass_rate_by_level": pass_rate_by_level,
        "pass_rate_by_segment": pass_rate_by_segment,
    }


# ---------------------------------------------------------------------------
# Failure row build
# ---------------------------------------------------------------------------


def failure_id(arm: str, query: str, assertion: str) -> str:
    h = hashlib.sha1(f"{arm}|{query}|{assertion}".encode("utf-8")).hexdigest()
    return h[:10]


def _str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def build_per_arm_rows(
    df: pd.DataFrame,
    arm: str,
    tsv_lookup: Dict[str, Dict[str, str]],
    yaml_by_hash: Dict[Tuple[str, str], str],
    yaml_by_query: Dict[Tuple[str, str], str],
) -> List[Dict[str, Any]]:
    """Flatten the CSV into one row per (arm, query, assertion) with enrichments."""
    score_col = f"score_{arm}"
    reply_col = f"sydney_reply_{arm}"
    rat_col = f"rationale_{arm}"

    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        score = row[score_col]
        if pd.isna(score):
            continue
        query = str(row["query"])
        assertion = str(row["assertion"])
        tsv_meta = tsv_lookup.get(query, {})
        qhash = tsv_meta.get("query_hash", "")

        # CSV's segment first; fall back to TSV.
        segment = str(row.get("segment") or "").strip() or tsv_meta.get("segment", "")

        # Level: CSV first; else YAML by (qhash, assertion); else YAML by (query, assertion).
        level = str(row.get("level") or "").strip().lower()
        if not level and qhash:
            level = yaml_by_hash.get((qhash, assertion), "")
        if not level:
            level = yaml_by_query.get((query, assertion), "")

        out.append({
            "arm": arm,
            "query": query,
            "query_hash": qhash,
            "user_id": tsv_meta.get("user_id", ""),
            "segment": segment,
            "assertion": assertion,
            "level": level,
            "score": int(score),
            "reply": _str(row[reply_col]),
            "rationale": _str(row[rat_col]),
        })
    return out


def to_failure_entry(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": failure_id(r["arm"], r["query"], r["assertion"]),
        "arm": r["arm"],
        "query": r["query"],
        "query_hash": r["query_hash"],
        "segment": r["segment"],
        "assertion": r["assertion"],
        "level": r["level"],
        "reply": r["reply"],
        "rationale": r["rationale"],
        "failure_label": "",
        "failure_evidence": "",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-folder", required=True, type=Path,
                   help="Folder containing [Assertions]_*.csv, *.tsv, *.yaml, Settings.json")
    p.add_argument("--run-id", required=True, type=str, help="SEVAL job ID (e.g. 552330)")
    p.add_argument("--run-name", default="", type=str,
                   help="Friendly name. Defaults to folder name.")
    p.add_argument("--run-date", default="", type=str,
                   help="YYYY-MM-DD. Falls back to CSV filename or Settings.eval_time_override.")
    p.add_argument("--arm", default="both", choices=VALID_ARMS,
                   help="Which arm(s) to include in failures[]. Default both.")
    p.add_argument("--out", required=True, type=Path, help="Path to write the fingerprint JSON.")
    args = p.parse_args()

    folder: Path = args.run_folder.resolve()
    artifacts = find_artifacts(folder)
    print(f"[extract] CSV: {artifacts['csv'].name}")
    print(f"[extract] TSV: {artifacts['tsv'].name}")
    print(f"[extract] YAML: {artifacts['yaml'].name}")
    print(f"[extract] Settings: {artifacts['settings'].name}")

    df = load_csv(artifacts["csv"])
    tsv_lookup = load_tsv(artifacts["tsv"])
    yaml_by_hash, yaml_by_query = load_yaml_doctrine(artifacts["yaml"])
    settings = load_settings(artifacts["settings"])

    flag_slots, flag_errors = extract_flag_slots(settings)

    # Derive run-date if not supplied.
    run_date = args.run_date.strip()
    if not run_date:
        # Try CSV filename: ..._YYYYMMDDhhmmss... or _MonthD_*
        m = re.search(r"(\d{4})(\d{2})(\d{2})", artifacts["csv"].stem)
        if m:
            run_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if not run_date:
        # Fall back to Settings.queries.eval_time_override (ISO timestamp).
        override = _get_global(settings, "eval_time_override")
        if override and len(override) >= 10:
            run_date = override[:10]
    if not run_date:
        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    arms_to_include = ("control", "experiment") if args.arm == "both" else (args.arm,)

    summary: Dict[str, Any] = {
        "rows": int(len(df)),
        "queries": int(df["query"].nunique()),
        "segments": sorted({s for s in df["segment"].astype(str).unique() if s and s != "nan"}),
        "arms": {},
    }

    failures: List[Dict[str, Any]] = []
    # Build a cross-arm pass index keyed by (query, assertion) -> {arm: score}
    # so the renderer / labeler can flag failures where the OTHER arm
    # passed the same assertion (those are almost never 'Missing grounding
    # data' — both arms run against the same environment).
    per_arm_index: Dict[str, Dict[Tuple[str, str], int]] = {
        "control": {}, "experiment": {},
    }

    for arm in ("control", "experiment"):
        per_arm_rows = build_per_arm_rows(df, arm, tsv_lookup, yaml_by_hash, yaml_by_query)
        summary["arms"][arm] = summarize_arm(per_arm_rows)
        for r in per_arm_rows:
            per_arm_index[arm][(r["query"], r["assertion"])] = int(r["score"])
        if arm in arms_to_include:
            for r in per_arm_rows:
                if r["score"] == 0:
                    failures.append(to_failure_entry(r))

    # Attach cross_arm_status to every failure.
    for f in failures:
        key = (f["query"], f["assertion"])
        other_arm = "experiment" if f["arm"] == "control" else "control"
        other_score = per_arm_index[other_arm].get(key)
        if other_score is None:
            f["cross_arm_status"] = "other_absent"
            f["cross_arm_other_score"] = None
        elif other_score == 1:
            f["cross_arm_status"] = "other_passed"
            f["cross_arm_other_score"] = 1
        else:
            f["cross_arm_status"] = "both_failed"
            f["cross_arm_other_score"] = 0

    # Stable sort: arm, segment, query, assertion
    failures.sort(key=lambda r: (r["arm"], r["segment"], r["query"], r["assertion"]))

    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": {
            "id": args.run_id,
            "name": (args.run_name or folder.name),
            "date": run_date,
            "scenario": _get_global(settings, "scenario") or "",
            "user_id": _get_global(settings, "user_id") or "",
            "eval_time_override": _get_global(settings, "eval_time_override") or "",
            "csv_path": str(artifacts["csv"]),
            "tsv_path": str(artifacts["tsv"]),
            "yaml_path": str(artifacts["yaml"]),
            "settings_path": str(artifacts["settings"]),
        },
        "summary": summary,
        "arm_labels": {
            slot.get("exp_name", ""): slot.get("side_label", "")
            for slot in flag_slots
            if slot.get("exp_name")
        } or dict(_SLOT_LABEL_OVERRIDES),
        "feature_flags": {
            "schema": "v2-paired-single-run",
            "slots": flag_slots,
            "errors": flag_errors,
        },
        "failures": failures,
        "failure_clusters": [],
        "inferred_gaps": [],
        "taxonomy_extensions": [],
        "caveats": [],
        "publish_safety": {
            "source": "seval-run-triage",
            "contains_customer_content": False,
            "contains_raw_model_outputs": True,
            "reviewed_for_publish": False,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    # Console summary (safe to share — aggregate only).
    print(f"[extract] Manifest: {args.out}")
    print(f"[extract] Rows: {summary['rows']} across {summary['queries']} queries, "
          f"{len(summary['segments'])} segments")
    for arm in ("control", "experiment"):
        s = summary["arms"][arm]
        crit = s["pass_rate_by_level"].get("critical", {})
        crit_str = f"critical {crit.get('passed', 0)}/{crit.get('total', 0)}" if crit else "no critical rows"
        print(f"[extract]   {arm:11s} pass_rate={s['pass_rate']:.2%}  "
              f"({s['passed']}/{s['rows']})  {crit_str}")
    print(f"[extract] Failures pending classification: {len(failures)} "
          f"(arms: {','.join(arms_to_include)})")
    if flag_errors:
        print(f"[extract] Feature-flag warnings: {flag_errors}", file=sys.stderr)


if __name__ == "__main__":
    main()

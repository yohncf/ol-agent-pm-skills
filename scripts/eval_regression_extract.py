"""
eval_regression_extract.py — Compute SEVAL regressions and feature-flag diff
between two HeroEval runs (a control-side run and an experiment-side run).

Inputs:
  --control-csv      Path to control-run HeroEval results CSV
  --experiment-csv   Path to experiment-run HeroEval results CSV
  --control-json     Path to control-run SEVAL Settings JSON
  --experiment-json  Path to experiment-run SEVAL Settings JSON
  --control-name     Friendly name for the control side (e.g. "Mainline")
  --experiment-name  Friendly name for the experiment side (e.g. "CodeGen")
  --control-id       Numeric ID of the control SEVAL run (e.g. 538053)
  --experiment-id    Numeric ID of the experiment SEVAL run (e.g. 538953)
  --control-date     Run date of the control run (YYYY-MM-DD)
  --experiment-date  Run date of the experiment run (YYYY-MM-DD)
  --out              Path to write the manifest JSON
  --highlights       (optional) One-sentence summary for the landing card

Output (single manifest JSON):
  - Per-run summary stats
  - Feature-flag diff between experiment-side variants of the two runs
  - regressions[] with stable IDs and `why_failed = ""` (agent fills in)
  - improvements[] and unmatched-row counts for transparency
  - publish_safety block (reviewed_for_publish defaults to false)

The script is deterministic and never invents prose. The agent is
responsible for writing `why_failed` after this script runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


# Force UTF-8 console output on Windows (cp1252 default crashes on
# non-ASCII characters in summary lines). No-op where stdout is already
# utf-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


SCHEMA_VERSION = "1.1"

# Map a slot's upstream `exp_name` (from exp_configs_unified[*].exp_name) to a
# friendly side label used in the report UI. SEVAL ships slot 0 as the
# baseline ("control" exp_name → "Mainline" baseline) and slot 1 as the
# variant under test ("experiment" exp_name → "CodeGen" variant). Both slots
# exist in every HeroEval run; the per-run JSONs differ in which flags each
# slot carries, and the report compares the two runs side-by-side per slot.
_SLOT_LABEL_OVERRIDES = {"control": "Mainline", "experiment": "CodeGen"}


def derive_side_label(exp_name: str) -> str:
    key = (exp_name or "").strip().lower()
    if key in _SLOT_LABEL_OVERRIDES:
        return _SLOT_LABEL_OVERRIDES[key]
    return (exp_name or "Unknown").strip().title() or "Unknown"


# ---------------------------------------------------------------------------
# CSV side
# ---------------------------------------------------------------------------

REQUIRED_COLS = {
    "query", "assertion",
    "score_control", "rationale_control", "sydney_reply_control",
    "score_experiment", "rationale_experiment", "sydney_reply_experiment",
}


def load_csv(path: Path, side_label: str) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"[extract] {side_label} CSV not found: {path}")
    df = pd.read_csv(path)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        sys.exit(f"[extract] {side_label} CSV missing required columns: {sorted(missing)}")
    # Normalize text columns we key on.
    for col in ("query", "assertion"):
        df[col] = df[col].astype(str).str.strip()
    # Score columns coerce to numeric (1 = pass, 0 = fail, NaN = not run).
    for col in ("score_control", "score_experiment"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Optional columns.
    if "segment" not in df.columns:
        df["segment"] = ""
    if "level" not in df.columns:
        df["level"] = ""
    return df


def summarize_run(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        "rows": int(len(df)),
        "queries": int(df["query"].nunique()),
        "segments": sorted({s for s in df["segment"].astype(str).unique() if s and s != "nan"}),
        "control_side_pass_rate": _pass_rate(df["score_control"]),
        "experiment_side_pass_rate": _pass_rate(df["score_experiment"]),
    }


def _pass_rate(series: pd.Series) -> float:
    valid = series.dropna()
    if len(valid) == 0:
        return 0.0
    return round(float((valid == 1).sum()) / float(len(valid)), 4)


def regression_id(comparison: str, query: str, assertion: str) -> str:
    h = hashlib.sha1(f"{comparison}|{query}|{assertion}".encode("utf-8")).hexdigest()
    return h[:10]


def compute_regressions(
    df_ctrl: pd.DataFrame,
    df_exp: pd.DataFrame,
) -> Dict[str, Any]:
    """Join on (query, assertion) after dedup, compute both comparisons."""
    # Dedup keep-first inside each source (HeroEval CSVs can have repeats).
    ctrl = df_ctrl.drop_duplicates(subset=["query", "assertion"], keep="first").copy()
    exp = df_exp.drop_duplicates(subset=["query", "assertion"], keep="first").copy()
    ctrl["_key"] = ctrl["query"] + "||" + ctrl["assertion"]
    exp["_key"] = exp["query"] + "||" + exp["assertion"]

    ctrl_keys = set(ctrl["_key"])
    exp_keys = set(exp["_key"])
    shared = ctrl_keys & exp_keys
    only_ctrl = ctrl_keys - exp_keys
    only_exp = exp_keys - ctrl_keys

    merged = ctrl.merge(
        exp,
        on=["query", "assertion"],
        how="inner",
        suffixes=("_a", "_b"),  # _a = control run, _b = experiment run
    )

    regressions: List[Dict[str, Any]] = []
    improvements: List[Dict[str, Any]] = []
    counts = {
        "control_vs_control": {"regressions": 0, "improvements": 0},
        "experiment_vs_experiment": {"regressions": 0, "improvements": 0},
    }

    # control_vs_control: score_control in run-A vs score_control in run-B
    # experiment_vs_experiment: score_experiment in run-A vs score_experiment in run-B
    SLOTS = [
        ("control_vs_control",
         "score_control_a", "score_control_b",
         "sydney_reply_control_a", "sydney_reply_control_b",
         "rationale_control_a", "rationale_control_b"),
        ("experiment_vs_experiment",
         "score_experiment_a", "score_experiment_b",
         "sydney_reply_experiment_a", "sydney_reply_experiment_b",
         "rationale_experiment_a", "rationale_experiment_b"),
    ]

    for comparison, sa, sb, ra, rb, ja, jb in SLOTS:
        for _, row in merged.iterrows():
            a, b = row[sa], row[sb]
            if pd.isna(a) or pd.isna(b):
                continue
            base = {
                "id": regression_id(comparison, row["query"], row["assertion"]),
                "comparison": comparison,
                "query": str(row["query"]),
                "segment": str(row.get("segment_a") or row.get("segment_b") or ""),
                "assertion": str(row["assertion"]),
                "level": str(row.get("level_a") or row.get("level_b") or ""),
            }
            if a == 1 and b == 0:
                base.update({
                    "reply_passed": _str(row[ra]),
                    "reply_failed": _str(row[rb]),
                    "rationale_passed": _str(row[ja]),
                    "rationale_failed": _str(row[jb]),
                    "why_failed": "",
                })
                regressions.append(base)
                counts[comparison]["regressions"] += 1
            elif a == 0 and b == 1:
                base.update({
                    "reply_failed_in_control_run": _str(row[ra]),
                    "reply_passed_in_experiment_run": _str(row[rb]),
                    "rationale_failed_in_control_run": _str(row[ja]),
                    "rationale_passed_in_experiment_run": _str(row[jb]),
                })
                improvements.append(base)
                counts[comparison]["improvements"] += 1

    # Sort: by comparison, then by query+assertion for stable ordering.
    regressions.sort(key=lambda r: (r["comparison"], r["query"], r["assertion"]))
    improvements.sort(key=lambda r: (r["comparison"], r["query"], r["assertion"]))

    return {
        "matched_pairs": len(shared),
        "missing_in_experiment": len(only_ctrl),
        "missing_in_control": len(only_exp),
        "regression_counts": counts,
        "regressions": regressions,
        "improvements": improvements,
    }


def _str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


# ---------------------------------------------------------------------------
# Feature-flag side
# ---------------------------------------------------------------------------

FLAG_SPLIT = re.compile(r"\s*,\s*")


def _get_exp_configs_unified(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tolerant lookup: top-level exp_configs_unified first, then payload.*."""
    if isinstance(doc.get("exp_configs_unified"), list):
        return doc["exp_configs_unified"]
    payload = doc.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("exp_configs_unified"), list):
        return payload["exp_configs_unified"]
    return []


def extract_variants(settings: Dict[str, Any], slot_index: int, json_label: str) -> Tuple[set, str]:
    """Return (set of flag tokens, exp_name) from exp_configs_unified[slot_index].sydney.variants."""
    confs = _get_exp_configs_unified(settings)
    if not confs:
        sys.exit(f"[extract] {json_label}: missing exp_configs_unified at top level or under payload.")
    if slot_index >= len(confs):
        sys.exit(f"[extract] {json_label}: exp_configs_unified has only {len(confs)} entries; need index {slot_index}.")
    slot = confs[slot_index]
    exp_name = str(slot.get("exp_name", f"slot-{slot_index}"))
    sydney = slot.get("sydney") or {}
    variants = sydney.get("variants")
    # SEVAL Settings JSON ships `variants` as either a list of comma-separated
    # strings or, in newer payloads, as a single comma-separated string.
    # Normalize to a list.
    if variants is None:
        sys.exit(f"[extract] {json_label} slot {slot_index} ({exp_name}): "
                 f"sydney.variants is missing.")
    if isinstance(variants, str):
        variants = [variants]
    if not isinstance(variants, list) or not variants:
        sys.exit(f"[extract] {json_label} slot {slot_index} ({exp_name}): "
                 f"sydney.variants has unexpected shape: {type(variants).__name__}.")
    tokens: set = set()
    for v in variants:
        if not isinstance(v, str):
            continue
        for tok in FLAG_SPLIT.split(v):
            tok = tok.strip()
            if tok:
                tokens.add(tok)
    if not tokens:
        sys.exit(f"[extract] {json_label} slot {slot_index} ({exp_name}): "
                 f"sydney.variants produced zero tokens after split.")
    return tokens, exp_name


def diff_flags(a: set, b: set) -> Dict[str, Any]:
    """Compute set-diff. Treat 'name=value' as opaque tokens so an A/B
    value change becomes a removed+added pair on the same name."""
    added = sorted(b - a)
    removed = sorted(a - b)
    shared = sorted(a & b)
    return {
        "added": added,
        "removed": removed,
        "shared": shared,
        "added_count": len(added),
        "removed_count": len(removed),
        "unchanged_count": len(shared),
        "total_in_control_run": len(a),
        "total_in_experiment_run": len(b),
    }


def diff_flags_for_slot(
    ctrl_settings: Dict[str, Any],
    exp_settings: Dict[str, Any],
    slot_index: int,
) -> Dict[str, Any]:
    """Compute a per-side flag diff between the two runs at the same slot.

    Returns a dict that includes the set-diff plus identity metadata so the
    renderer can label the card without re-reading the source JSONs."""
    ctrl_tokens, ctrl_slot_name = extract_variants(ctrl_settings, slot_index, "control-run JSON")
    exp_tokens, exp_slot_name = extract_variants(exp_settings, slot_index, "experiment-run JSON")
    diff = diff_flags(ctrl_tokens, exp_tokens)
    # Side label is derived from the control-run slot's exp_name; if the two
    # runs disagree on the slot name we record both and flag the mismatch.
    side_label = derive_side_label(ctrl_slot_name)
    diff["slot_index"] = slot_index
    diff["side_label"] = side_label
    diff["control_run_slot_exp_name"] = ctrl_slot_name
    diff["experiment_run_slot_exp_name"] = exp_slot_name
    if ctrl_slot_name != exp_slot_name:
        diff["slot_name_mismatch"] = True
    return diff


# ---------------------------------------------------------------------------
# Manifest assembly
# ---------------------------------------------------------------------------

def build_manifest(args: argparse.Namespace,
                   ctrl_csv: pd.DataFrame, exp_csv: pd.DataFrame,
                   ctrl_settings: Dict[str, Any], exp_settings: Dict[str, Any]) -> Dict[str, Any]:
    summary_ctrl_run = summarize_run(ctrl_csv)
    summary_exp_run = summarize_run(exp_csv)

    reg = compute_regressions(ctrl_csv, exp_csv)

    # Per-side flag diffs: compare the two RUNS at each slot. This produces
    # one Mainline-side diff (slot 0) and one CodeGen-side diff (slot 1),
    # rendered as peer cards in the report.
    flag_diffs: List[Dict[str, Any]] = []
    flag_diff_errors: List[str] = []
    for slot_index in (0, 1):
        try:
            flag_diffs.append(diff_flags_for_slot(ctrl_settings, exp_settings, slot_index))
        except SystemExit as err:
            flag_diff_errors.append(f"slot {slot_index}: {err}")

    feature_flags: Dict[str, Any] = {
        "schema": "v2-paired",
        "diffs": flag_diffs,
    }
    if flag_diff_errors:
        feature_flags["errors"] = flag_diff_errors

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "control": {
            "id": str(args.control_id),
            "name": args.control_name,
            "run_date": args.control_date,
            "csv_path": str(Path(args.control_csv).resolve()),
            "settings_path": str(Path(args.control_json).resolve()),
            **summary_ctrl_run,
        },
        "experiment": {
            "id": str(args.experiment_id),
            "name": args.experiment_name,
            "run_date": args.experiment_date,
            "csv_path": str(Path(args.experiment_csv).resolve()),
            "settings_path": str(Path(args.experiment_json).resolve()),
            **summary_exp_run,
        },
        "summary": {
            "matched_pairs": reg["matched_pairs"],
            "missing_in_control": reg["missing_in_control"],
            "missing_in_experiment": reg["missing_in_experiment"],
            "regression_counts": reg["regression_counts"],
        },
        "feature_flags": feature_flags,
        "regressions": reg["regressions"],
        "improvements": reg["improvements"],
        "highlights": args.highlights or "",
        "publish_safety": {
            "source": "SEVAL HeroEval",
            "contains_customer_content": False,
            "contains_raw_model_outputs": True,
            "reviewed_for_publish": False,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--control-csv", required=True, type=Path)
    p.add_argument("--experiment-csv", required=True, type=Path)
    p.add_argument("--control-json", required=True, type=Path)
    p.add_argument("--experiment-json", required=True, type=Path)
    p.add_argument("--control-name", required=True)
    p.add_argument("--experiment-name", required=True)
    p.add_argument("--control-id", required=True)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--control-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--experiment-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--out", required=True, type=Path, help="Path to write manifest JSON")
    p.add_argument("--highlights", default="", help="One-sentence summary for the landing card")
    args = p.parse_args()

    for d in (args.control_date, args.experiment_date):
        try:
            datetime.fromisoformat(d)
        except ValueError:
            sys.exit(f"[extract] Invalid date (need YYYY-MM-DD): {d!r}")

    df_ctrl = load_csv(args.control_csv, "control")
    df_exp = load_csv(args.experiment_csv, "experiment")

    with open(args.control_json, "r", encoding="utf-8") as f:
        ctrl_settings = json.load(f)
    with open(args.experiment_json, "r", encoding="utf-8") as f:
        exp_settings = json.load(f)

    manifest = build_manifest(args, df_ctrl, df_exp, ctrl_settings, exp_settings)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Brief summary to stderr-friendly stdout.
    cc = manifest["summary"]["regression_counts"]["control_vs_control"]["regressions"]
    ee = manifest["summary"]["regression_counts"]["experiment_vs_experiment"]["regressions"]
    mp = manifest["summary"]["matched_pairs"]
    fd = manifest["feature_flags"]
    print(f"[extract] Wrote {args.out}")
    print(f"[extract] Regressions: control_vs_control={cc}, experiment_vs_experiment={ee} "
          f"(out of {mp} comparable assertions)")
    for d in fd.get("diffs", []):
        print(f"[extract] Feature-flag diff [{d['side_label']}] "
              f"(slot {d['slot_index']}, "
              f"{d['control_run_slot_exp_name']!r} in control run "
              f"vs {d['experiment_run_slot_exp_name']!r} in experiment run): "
              f"+{d['added_count']} added / -{d['removed_count']} removed "
              f"({d['unchanged_count']} unchanged)")
    for err in fd.get("errors", []):
        print(f"[extract] WARNING flag-diff error: {err}")
    print(f"[extract] All regressions have why_failed = '' — agent must author each one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

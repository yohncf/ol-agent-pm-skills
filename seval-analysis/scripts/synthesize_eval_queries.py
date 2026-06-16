"""
synthesize_eval_queries.py

Helper for the `seval-synthesize-queries-from-ocv` skill.

Two modes:

  1) dump    (default) — read one or more input CSVs, extract user-utterance
              columns (auto-detected: "Utterance" or "PromptInEnglish"),
              normalize (HTML-unescape, trim, dedup), and write a single
              JSON file containing the unified, numbered utterance list
              that the agent will reason over.

  2) write-yaml — read a JSON file containing a curated list of
              {segment, query, assertions:[{text, level}]} entries (built
              by the agent during clustering) and emit a YAML file matching
              the HeroEval format plus a sibling TSV that pairs each query
              with its segment for downstream HeroEval merge.

Doctrine: see `docs/EVAL_DOCTRINE.md`. The DOCTRINE_VERSION constant below
must match the doctrine doc's `Doctrine-Version` header — bump both
together when assertion rules or segment taxonomy change.

Usage:

  # Mode 1 — dump utterances from N CSVs into one normalized list
  python scripts/synthesize_eval_queries.py \\
      dump <csv1> [<csv2> ...] [--out data/.synth_input_<ts>.json]

  # Mode 2 — emit final YAML (+ sibling TSV) from agent-curated JSON
  python scripts/synthesize_eval_queries.py \\
      write-yaml <queries.json> --out <queries.yaml>

The script is intentionally minimal for the clustering step — all
clustering and query-drafting is done by the calling agent. This file
does the deterministic plumbing (CSV parsing, normalization, YAML/TSV
emission) plus doctrine enforcement (level taxonomy, segment shape,
assertion anti-pattern detection).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pin this to the doctrine doc's `Doctrine-Version`. Bump both together.
DOCTRINE_VERSION = "2026-06-04"

# Columns we will auto-detect, in priority order. Case-insensitive match.
CANDIDATE_COLUMNS = [
    "Utterance",
    "PromptInEnglish",
    "Prompt In English",
    "prompt_in_english",
    "prompt",
]

ALLOWED_LEVELS = {"critical", "expected", "aspirational"}
LEGACY_LEVELS = {"critical", "expected"}  # used when --level-mode=legacy

# ---- Segment taxonomy (mirrors docs/EVAL_DOCTRINE.md) ---------------------

KNOWN_SEGMENTS = frozenset({
    # Inbox bare
    "Search", "Delete", "Junk", "Unflag", "Unread", "Archive",
    "Categorize", "Rules", "Folders", "Triage", "Quota", "Unsubscribe",
    "ActionItems", "Summarize", "Draft", "Reply",
    # Inbox 2-token combos
    "Pin_Flag", "Flag_Reminder", "Categorize_Summarize", "Categorize_Move",
    "Delete_Rules", "Rules_Folders", "Reply_Receipts", "Forward_Receipts",
    # Calendar
    "Calendar_OOO", "Calendar_WorkingHours",
    # Meetings
    "Meetings_ActionItems", "Meetings_Decisions", "Meetings_Prep",
    # Contacts
    "Contacts_Create", "Contacts_Update",
    # Tasks
    "Tasks_Create", "Tasks_Update", "Tasks_Subtasks",
    # V2.6 test marker
    "Inbox_noSend",
})
SEGMENT_PATTERN = re.compile(r"^[A-Z][A-Za-z]+(_[A-Za-z]+)?$")

# ---- Assertion doctrine: hard-fail substrings at critical level ----------

HARD_FAIL_CRITICAL_SUBSTRS = [
    "or asks for clarification",
    "or requests more information",
    "or asks the user to provide",
    "or states more details are needed",
    "OR demonstrates clear intent",
    "OR clearly describes the plan",
    "OR clearly describing",
]

# ---- Assertion doctrine: warning substrings at critical level ------------

# Format-as-primary-verb patterns. Warning unless the assertion ALSO
# contains a substance verb.
WARN_FORMAT_PRIMARY = [
    "organizes", "is organized", "formats", "presents as a list",
    "presents the items as", "uses bullets", "uses a bulleted",
    "groups them", "separates them", "orders them",
    "is structured", "is scannable", "is concise",
]

# Substance verbs that license format-y assertions (because the assertion
# is now substance + format, in which case atomicity check still applies).
SUBSTANCE_VERBS = [
    "identifies", "lists", "names", "returns", "retrieves",
    "states the count", "reports", "summarizes the",
    "confirms .* by", "shows .* sender", "shows .* subject",
    "includes the count", "enumerates",
]
SUBSTANCE_RE = re.compile("|".join(SUBSTANCE_VERBS), re.IGNORECASE)

# Vacuous "confirms" at critical without identifiers
VACUOUS_CONFIRM_PATTERNS = [
    re.compile(r"\bconfirms the action\b", re.IGNORECASE),
    re.compile(r"\bconfirms the request\b", re.IGNORECASE),
    re.compile(r"\bconfirms it was done\b", re.IGNORECASE),
    re.compile(r"\bconfirms completion\b", re.IGNORECASE),
]

# Subjective quality words used as the primary claim
WARN_SUBJECTIVE = [
    "is helpful", "is clear", "is appropriate", "is reasonable",
    "is comprehensive", "is actionable",
]

# Plan-only / future-promise language as the primary claim
WARN_PLAN_ONLY = [
    "provides next steps", "plans to ", "will reach out",
    "will follow up", "would draft", "can summarize",
]

# Increase CSV field-size limit — Verbatim/Response can be huge.
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)

# Increase CSV field-size limit — Verbatim/Response can be huge.
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)

# ---------------------------------------------------------------------------
# Mode 1 — dump
# ---------------------------------------------------------------------------


def detect_utterance_column(headers: list[str], override: str | None) -> str:
    if override:
        for h in headers:
            if h.lower() == override.lower():
                return h
        raise SystemExit(
            f"--column {override!r} not found in headers: {headers}"
        )
    lower_map = {h.lower(): h for h in headers}
    for cand in CANDIDATE_COLUMNS:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    raise SystemExit(
        "Could not auto-detect utterance column. "
        f"Headers were: {headers}. "
        "Re-run with --column <name>."
    )


def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = html.unescape(s)
    # Collapse all whitespace (incl. CRLF) into single spaces for dedup keys,
    # but keep the original for display.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.strip()
    return s


def dedup_key(s: str) -> str:
    # Lowercase, collapse all internal whitespace, strip trailing punctuation
    # for grouping near-duplicates ("Help me" vs "help me.")
    key = re.sub(r"\s+", " ", s.lower()).strip()
    key = key.rstrip(".!?,;:- ")
    return key


def iter_csv_utterances(path: Path, column_override: str | None) -> Iterable[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return
        col = detect_utterance_column(list(reader.fieldnames), column_override)
        for row_idx, row in enumerate(reader, start=2):  # 1 = header
            text = normalize_text(row.get(col, ""))
            if not text:
                continue
            yield {
                "text": text,
                "source_file": path.name,
                "source_column": col,
                "source_row": row_idx,
            }


def cmd_dump(args: argparse.Namespace) -> int:
    inputs = [Path(p) for p in args.csv]
    for p in inputs:
        if not p.exists():
            raise SystemExit(f"Input CSV not found: {p}")

    out_path = Path(args.out) if args.out else _default_dump_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    raw: list[dict] = []
    per_file_counts: dict[str, int] = {}
    for p in inputs:
        count_before = len(raw)
        for item in iter_csv_utterances(p, args.column):
            raw.append(item)
        per_file_counts[p.name] = len(raw) - count_before

    # Dedup by normalized key, keeping the first occurrence and merging sources.
    by_key: dict[str, dict] = {}
    for item in raw:
        key = dedup_key(item["text"])
        if not key:
            continue
        if key not in by_key:
            by_key[key] = {
                "text": item["text"],
                "sources": [
                    {
                        "file": item["source_file"],
                        "column": item["source_column"],
                        "row": item["source_row"],
                    }
                ],
            }
        else:
            by_key[key]["sources"].append(
                {
                    "file": item["source_file"],
                    "column": item["source_column"],
                    "row": item["source_row"],
                }
            )

    deduped = list(by_key.values())
    # Number them so the agent can reference items by ID in clustering.
    for i, item in enumerate(deduped, start=1):
        item["id"] = i
        item["occurrences"] = len(item["sources"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [
            {"file": p.name, "path": str(p), "rows_with_text": per_file_counts[p.name]}
            for p in inputs
        ],
        "total_raw": len(raw),
        "total_unique": len(deduped),
        "utterances": deduped,
    }

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"  Inputs: {len(inputs)}")
    for p in inputs:
        print(f"    - {p.name}: {per_file_counts[p.name]} non-empty utterances")
    print(f"  Total raw: {len(raw)}")
    print(f"  Total unique (after dedup): {len(deduped)}")
    return 0


def _default_dump_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("data") / f".synth_input_{ts}.json"


# ---------------------------------------------------------------------------
# Mode 2 — write-yaml
# ---------------------------------------------------------------------------


def _yaml_quote_block(text: str, indent: int) -> str:
    """
    Emit a YAML scalar that survives any input — uses a double-quoted scalar
    with escapes so we don't have to worry about leading dashes, colons,
    block scalar indicators, etc. Newlines are escaped as \\n.
    """
    # Escape backslashes first, then double quotes, then control characters.
    s = text.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{s}"'


def _validate_segment(seg: object, qidx: int) -> tuple[str, list[str]]:
    """Validate a segment string. Returns (segment, warnings).
    Hard-fails missing/malformed; warns on unknown-but-shape-valid.
    """
    warnings: list[str] = []
    if not seg or not isinstance(seg, str):
        raise SystemExit(
            f"queries[{qidx}].segment must be a non-empty string. "
            f"See docs/EVAL_DOCTRINE.md for the segment taxonomy."
        )
    if not SEGMENT_PATTERN.match(seg):
        raise SystemExit(
            f"queries[{qidx}].segment {seg!r} does not match shape "
            f"{SEGMENT_PATTERN.pattern}. Use CapCase, max 2 tokens joined "
            f"by underscore (e.g. 'Search', 'Pin_Flag', 'Calendar_OOO')."
        )
    if seg not in KNOWN_SEGMENTS:
        warnings.append(
            f"queries[{qidx}].segment {seg!r} is not in the known list "
            f"({len(KNOWN_SEGMENTS)} known). New segments are allowed but "
            f"should be validated with the user before landing in HeroEval."
        )
    return seg, warnings


def _check_assertion_doctrine(
    text: str, level: str, qidx: int, aidx: int
) -> tuple[list[str], list[str]]:
    """Check an assertion against doctrine. Returns (errors, warnings).
    Errors abort. Warnings are printed but not blocking (unless
    --strict-doctrine upgrades them to errors at the caller).
    """
    errors: list[str] = []
    warnings: list[str] = []
    loc = f"queries[{qidx}].assertions[{aidx}]"

    # Formula check — warning for any level. Allows the standard
    # "The response …" / "The <tool> call …" patterns and conditional
    # aspirational patterns (doctrine §6).
    stripped = text.lstrip()
    formula_ok = (
        stripped.startswith("The response")
        or re.match(r"^The \w+ call\b", stripped)
        or re.match(r"^When .+,\s*(the response|they)\b", stripped, re.IGNORECASE)
        or stripped.startswith("When the response")
    )
    if not formula_ok:
        warnings.append(
            f"{loc}: assertion does not start with 'The response ...', "
            f"'The <tool> call ...', or 'When ..., the response/they ...' "
            f"per doctrine."
        )

    if level != "critical":
        return errors, warnings

    text_lower = text.lower()

    # Hard-fail substrings at critical level.
    for bad in HARD_FAIL_CRITICAL_SUBSTRS:
        if bad.lower() in text_lower:
            errors.append(
                f"{loc}: critical assertion contains hard-fail anti-pattern "
                f"'{bad}'. See docs/EVAL_DOCTRINE.md → anti-patterns."
            )

    # Format-as-primary-verb warning unless a substance verb is present.
    has_substance = bool(SUBSTANCE_RE.search(text))
    for fmt in WARN_FORMAT_PRIMARY:
        if fmt.lower() in text_lower and not has_substance:
            warnings.append(
                f"{loc}: critical assertion uses format-as-primary-verb "
                f"'{fmt}' without a substance verb. Consider moving to "
                f"'aspirational' or rephrasing with a substance verb."
            )
            break

    # Vacuous confirm at critical
    for pat in VACUOUS_CONFIRM_PATTERNS:
        if pat.search(text):
            warnings.append(
                f"{loc}: critical assertion uses vacuous 'confirms …' "
                f"language. Reference named identifiers (subjects, senders, "
                f"counts) so it fails when the response is just an apology."
            )
            break

    # Subjective quality at critical
    for s in WARN_SUBJECTIVE:
        if s.lower() in text_lower:
            warnings.append(
                f"{loc}: critical assertion uses subjective quality word "
                f"'{s}'. Judge LLMs default to 'yes' on these. Use a binary "
                f"outcome check instead."
            )
            break

    # Plan-only language at critical
    for p in WARN_PLAN_ONLY:
        if p.lower() in text_lower:
            warnings.append(
                f"{loc}: critical assertion uses plan/future-promise "
                f"language '{p.strip()}'. Promise of action passes "
                f"substantive failure now."
            )
            break

    # Atomicity: rough heuristic — flag "X and Y" where both sides have
    # an action verb (very rough; warning only).
    if re.search(r"\bidentifies\b.*\band\b.*\borganizes\b", text, re.IGNORECASE) or \
       re.search(r"\blists\b.*\band\b.*\b(organizes|formats|groups)\b", text, re.IGNORECASE):
        warnings.append(
            f"{loc}: critical assertion combines substance + format with "
            f"'and'. Split into critical (substance) + aspirational (format)."
        )

    return errors, warnings


def _validate_query_obj(
    q: dict, idx: int, level_mode: str
) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(q, dict):
        return [f"queries[{idx}] is not an object"], warnings
    if not q.get("query") or not isinstance(q["query"], str):
        errors.append(f"queries[{idx}].query must be a non-empty string")

    # Segment is required by doctrine.
    try:
        _, seg_warns = _validate_segment(q.get("segment"), idx)
        warnings.extend(seg_warns)
    except SystemExit as e:
        errors.append(str(e))

    assertions = q.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        errors.append(f"queries[{idx}].assertions must be a non-empty list")
        return errors, warnings

    allowed = LEGACY_LEVELS if level_mode == "legacy" else ALLOWED_LEVELS

    for j, a in enumerate(assertions):
        if not isinstance(a, dict):
            errors.append(f"queries[{idx}].assertions[{j}] is not an object")
            continue
        if not a.get("text") or not isinstance(a["text"], str):
            errors.append(
                f"queries[{idx}].assertions[{j}].text must be a non-empty string"
            )
            continue
        lvl = a.get("level", "expected")
        if lvl not in ALLOWED_LEVELS:
            errors.append(
                f"queries[{idx}].assertions[{j}].level must be one of "
                f"{sorted(ALLOWED_LEVELS)}, got {lvl!r}"
            )
            continue
        # In legacy mode aspirational is auto-mapped at emit time; warn here.
        if lvl == "aspirational" and level_mode == "legacy":
            warnings.append(
                f"queries[{idx}].assertions[{j}]: level 'aspirational' will "
                f"be downgraded to 'expected' for legacy harness output."
            )

        a_errs, a_warns = _check_assertion_doctrine(a["text"], lvl, idx, j)
        errors.extend(a_errs)
        warnings.extend(a_warns)

    return errors, warnings


# ---------------------------------------------------------------------------
# TSV emission (paired with YAML — same IDs, single source of truth)
# ---------------------------------------------------------------------------

TSV_HEADER = ["Utterance", "Segment 2", "annotation", "query_hash",
              "user_id", "timestamp"]


def _emit_tsv(rows: list[dict], out_path: Path,
              user_id: str, timestamp: str) -> None:
    """rows: [{'id': uuid, 'query': str, 'segment': str}, ...]"""
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", quotechar='"',
                       quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(TSV_HEADER)
        for r in rows:
            w.writerow([r["query"], r["segment"], "",
                        r["id"], user_id, timestamp])


def cmd_write_yaml(args: argparse.Namespace) -> int:
    src_path = Path(args.queries_json)
    if not src_path.exists():
        raise SystemExit(f"Input JSON not found: {src_path}")
    data = json.loads(src_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "queries" in data:
        queries = data["queries"]
    elif isinstance(data, list):
        queries = data
    else:
        raise SystemExit(
            "Input JSON must be a list of query objects, "
            "or an object with a 'queries' list."
        )

    if not queries:
        raise SystemExit("No queries to emit.")
    if len(queries) > args.max_count:
        raise SystemExit(
            f"{len(queries)} queries exceeds --max-count={args.max_count}. "
            "Trim the list or pass a higher cap."
        )

    # Validate all entries; collect errors and warnings.
    all_errors: list[str] = []
    all_warnings: list[str] = []
    for i, q in enumerate(queries):
        errs, warns = _validate_query_obj(q, i, args.level_mode)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    if all_warnings:
        sys.stderr.write(
            f"Doctrine warnings ({len(all_warnings)}):\n"
        )
        for w in all_warnings:
            sys.stderr.write(f"  WARN: {w}\n")
        if args.strict_doctrine:
            raise SystemExit(
                "--strict-doctrine: warnings treated as errors; aborting."
            )

    if all_errors:
        sys.stderr.write("Validation errors:\n")
        for e in all_errors:
            sys.stderr.write(f"  ERROR: {e}\n")
        raise SystemExit(
            f"{len(all_errors)} doctrine violation(s); aborting. "
            "See docs/EVAL_DOCTRINE.md."
        )

    # Generate IDs once, share with TSV — IDs must match exactly.
    materialized: list[dict] = []
    for q in queries:
        qid = q.get("id") or str(uuid.uuid4())
        materialized.append({
            "id": qid,
            "query": q["query"],
            "segment": q["segment"],
            "assertions": q["assertions"],
        })

    # Emit YAML.
    lines: list[str] = []
    if args.header:
        lines.append(f"# Doctrine-Version: {DOCTRINE_VERSION}")
        lines.append("# See ocv-extraction/docs/EVAL_DOCTRINE.md")
        lines.append("")
    for q in materialized:
        lines.append(f"- id: {q['id']}")
        lines.append(f"  query: {_yaml_quote_block(q['query'], 2)}")
        lines.append("  assertions:")
        for a in q["assertions"]:
            lvl = a.get("level", "expected")
            # In legacy mode, downgrade aspirational → expected at emit time.
            if args.level_mode == "legacy" and lvl == "aspirational":
                lvl = "expected"
            lines.append(f"  - text: {_yaml_quote_block(a['text'], 4)}")
            lines.append(f"    level: {lvl}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"  Queries: {len(materialized)}")
    total_assertions = sum(len(q["assertions"]) for q in materialized)
    print(f"  Assertions (total): {total_assertions}")
    by_level = {"critical": 0, "expected": 0, "aspirational": 0}
    for q in materialized:
        for a in q["assertions"]:
            by_level[a.get("level", "expected")] += 1
    print(f"  By level: critical={by_level['critical']} "
          f"expected={by_level['expected']} aspirational={by_level['aspirational']}")
    print(f"  Doctrine-Version: {DOCTRINE_VERSION} (mode={args.level_mode})")

    # Emit sibling TSV unless suppressed.
    if not args.no_tsv:
        tsv_path = Path(args.tsv) if args.tsv else out_path.with_suffix(".tsv")
        ts = args.timestamp or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        _emit_tsv(materialized, tsv_path, args.user_id, ts)
        print(f"Wrote {tsv_path}  ({len(materialized)} rows)")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Helper for seval-synthesize-queries-from-ocv skill (dump + write-yaml).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("dump", help="Dump deduped utterances from N CSVs to JSON.")
    pd.add_argument("csv", nargs="+", help="One or more input CSV paths.")
    pd.add_argument(
        "--column",
        default=None,
        help=(
            "Explicit column name to extract. If omitted, auto-detect "
            "'Utterance' or 'PromptInEnglish'."
        ),
    )
    pd.add_argument(
        "--out",
        default=None,
        help="Output JSON path. Default: data/.synth_input_<timestamp>.json",
    )
    pd.set_defaults(func=cmd_dump)

    py = sub.add_parser(
        "write-yaml",
        help="Emit final eval YAML (+ sibling TSV) from a curated JSON list.",
    )
    py.add_argument("queries_json", help="Path to JSON with curated queries/assertions.")
    py.add_argument("--out", required=True, help="Output YAML path.")
    py.add_argument(
        "--max-count",
        type=int,
        default=10,
        help="Cap on number of queries (default 10).",
    )
    py.add_argument(
        "--level-mode",
        choices=("doctrine", "legacy"),
        default="doctrine",
        help=(
            "doctrine (default): emit critical/expected/aspirational as-is. "
            "legacy: downgrade aspirational → expected for older harnesses."
        ),
    )
    py.add_argument(
        "--strict-doctrine",
        action="store_true",
        help="Treat doctrine warnings as errors (CI mode).",
    )
    py.add_argument(
        "--header",
        action="store_true",
        help="Emit Doctrine-Version comment header at top of YAML.",
    )
    py.add_argument(
        "--no-tsv",
        action="store_true",
        help="Suppress sibling TSV emission (default: emit alongside YAML).",
    )
    py.add_argument(
        "--tsv",
        default=None,
        help="Explicit TSV output path (default: <out>.tsv).",
    )
    py.add_argument(
        "--user-id",
        default="jennifertaylor@Outlook_SDF_v1@SyntheticTenant",
        help="TSV user_id column value (default: HeroEval synthetic tenant).",
    )
    py.add_argument(
        "--timestamp",
        default=None,
        help="TSV timestamp value in ISO-8601 (default: current UTC).",
    )
    py.set_defaults(func=cmd_write_yaml)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

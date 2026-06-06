"""
publish_to_github.py — push a freshly-built OCV weekly HTML report into
the gim-home/OCV-Weekly GitHub repo and update the landing-page manifest
(reports.json) so the new report appears in the listing.

Two-gate doctrine (same shape as ado_sync.execute):
  - Prints the planned filesystem + JSON changes ("Gate B" equivalent)
  - Blocks on stdin for the literal string `yes` unless --yes
  - --dry-run skips both git and filesystem writes

Usage:

  python scripts/publish_to_github.py \\
      --manifest data/manifests/ocv_outlook-agent_2026-05-18_manifest.json \\
      --html     output/ocv_outlook-agent_2026-05-18.html \\
      --highlights "Drafting & Scheduling dominate; 1 new Dash<->OCV join surfaced."

If --manifest is provided, week/range/negatives/topics are auto-derived.
You can still override any of those flags.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_REPO = (Path(__file__).resolve().parent.parent / "_ocv_weekly_repo")
DEFAULT_GITHUB_URL = "https://github.com/gim-home/OCV-Weekly.git"
DEFAULT_LIVE_BASE = "https://gim-home.github.io/OCV-Weekly"


def _default_owner() -> str:
    """Best-effort owner name for a freshly seeded reports.json.
    Reads `git config user.name`; falls back to "Owner" if unavailable."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        name = (result.stdout or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "Owner"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command, returning the CompletedProcess.
    Streams stdout/stderr through to the caller for visibility."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          check=check, text=True, capture_output=True)


def load_manifest(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def label_from_week(week_iso: str) -> str:
    """'2026-05-18' -> 'Week of May 18, 2026'."""
    d = datetime.fromisoformat(week_iso)
    return d.strftime("Week of %B %d, %Y").replace(" 0", " ")


def humanize_range(date_range: str, week_iso: str) -> str:
    """Pass through the manifest's date_range if present; otherwise synthesize."""
    return date_range or label_from_week(week_iso)


def derive_topics_count(manifest: Dict[str, Any]) -> int:
    """Count taxonomy topics that actually surfaced (>0 negatives) this week.

    Card metric: shows the spread of pain across the 13-topic taxonomy
    (e.g. 10 of 13 topics had at least one negative item)."""
    tc = manifest.get("topic_counts", {}) or {}
    try:
        return sum(1 for v in tc.values() if isinstance(v, (int, float)) and v > 0)
    except Exception:
        return 0


def derive_top_topic(manifest: Dict[str, Any]) -> str:
    """Return the human-readable name of the topic with the most negatives this week."""
    tc = manifest.get("topic_counts", {}) or {}
    if not tc:
        return ""
    TOPIC_NAMES = {
        "1": "Action not executed", "2": "HitL violation",
        "3": "Output doesn't match intent", "4": "Constraints ignored",
        "5": "Unnecessary clarifying question", "6": "Reliability failure",
        "7": "Inaccurate or fabricated content", "8": "Wrong context / grounding",
        "9": "Tone / language / format", "10": "File I/O failure",
        "11": "Calendar correctness", "12": "Capability refusal",
        "13": "Intrusive Outlook Agent UI",
    }
    try:
        ranked = sorted(
            ((str(k), int(v)) for k, v in tc.items() if isinstance(v, (int, float))),
            key=lambda kv: kv[1], reverse=True,
        )
    except Exception:
        return ""
    if not ranked or ranked[0][1] <= 0:
        return ""
    return TOPIC_NAMES.get(ranked[0][0], f"Topic {ranked[0][0]}")


def derive_rating_counts(manifest: Dict[str, Any]) -> tuple[int, int]:
    """Pull (thumbs_down, thumbs_up) from manifest['rating'].

    Rating is the raw user signal (thumbs reaction on the OCV verbatim),
    independent of sentiment classification. ThumbsDown count drives the
    OCV ingest and is the cleanest WoW pulse on user dissatisfaction."""
    r = manifest.get("rating", {}) or {}
    try:
        return int(r.get("ThumbsDown", 0) or 0), int(r.get("ThumbsUp", 0) or 0)
    except Exception:
        return 0, 0


def upsert_report_entry(reports: list, entry: Dict[str, Any]) -> tuple[list, str]:
    """Insert or replace the entry by week_of. Returns (new_list, action)."""
    out = []
    action = "added"
    for r in reports:
        if r.get("week_of") == entry["week_of"]:
            action = "replaced"
            continue
        out.append(r)
    out.append(entry)
    out.sort(key=lambda r: r.get("week_of", ""), reverse=True)
    return out, action


def git_status_clean(repo: Path) -> bool:
    r = _run(["git", "status", "--porcelain"], cwd=repo, check=False)
    return r.returncode == 0 and not r.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path,
                        help="Path to ocv-analyze manifest JSON (auto-fills week, range, "
                             "negatives, topics)")
    parser.add_argument("--html", type=Path, required=True,
                        help="Path to the ocv-publish-report HTML output to upload")
    parser.add_argument("--week", help="Reporting week (YYYY-MM-DD). Defaults to manifest['week'].")
    parser.add_argument("--label", help="Card title (e.g. 'Week of May 18, 2026'). Derived from --week if omitted.")
    parser.add_argument("--range", dest="date_range",
                        help="Card eyebrow (e.g. 'May 12 - May 18, 2026'). Defaults to manifest['date_range'].")
    parser.add_argument("--negatives", type=int,
                        help="Negatives count for the card metric. Defaults to manifest['negative_items'].")
    parser.add_argument("--topics", type=int,
                        help="Topics count for the card metric (taxonomy topics with >0 "
                             "negatives this week). Defaults to derived from manifest.")
    parser.add_argument("--thumbs-down", type=int,
                        help="ThumbsDown rating count. Defaults to manifest['rating']['ThumbsDown'].")
    parser.add_argument("--thumbs-up", type=int,
                        help="ThumbsUp rating count. Defaults to manifest['rating']['ThumbsUp'].")
    parser.add_argument("--highlights", default="",
                        help="One-line summary on the card. Strongly recommended.")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO,
                        help=f"Local clone of the OCV-Weekly repo (default: {DEFAULT_REPO})")
    parser.add_argument("--remote-url", default=DEFAULT_GITHUB_URL,
                        help="GitHub remote URL (used only if --repo doesn't exist yet, for a fresh clone)")
    parser.add_argument("--branch", default="main",
                        help="Branch to push (default: main)")
    parser.add_argument("--commit-message", default=None,
                        help="Override the auto-generated commit message")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the publish plan and exit; never touch the filesystem or push.")
    parser.add_argument("--yes", action="store_true",
                        help="Bypass the interactive confirmation. ONLY for automated re-runs after "
                             "the agent has shown the plan to the user via ask_user.")
    args = parser.parse_args()

    if not args.html.exists():
        sys.exit(f"HTML not found: {args.html}")

    # Auto-derive fields from manifest if provided.
    manifest: Dict[str, Any] = {}
    if args.manifest:
        if not args.manifest.exists():
            sys.exit(f"Manifest not found: {args.manifest}")
        manifest = load_manifest(args.manifest)

    week = args.week or manifest.get("week")
    if not week:
        sys.exit("Could not determine reporting week. Pass --week YYYY-MM-DD or --manifest.")
    try:
        datetime.fromisoformat(week)
    except ValueError:
        sys.exit(f"Invalid --week (need YYYY-MM-DD): {week!r}")

    label = args.label or label_from_week(week)
    date_range = humanize_range(args.date_range or manifest.get("date_range", ""), week)
    negatives = args.negatives if args.negatives is not None else manifest.get("negative_items", 0)
    topics = args.topics if args.topics is not None else (
        derive_topics_count(manifest) if manifest else 0
    )
    derived_td, derived_tu = derive_rating_counts(manifest) if manifest else (0, 0)
    thumbs_down = args.thumbs_down if args.thumbs_down is not None else derived_td
    thumbs_up = args.thumbs_up if args.thumbs_up is not None else derived_tu
    highlights = args.highlights.strip()
    top_topic = derive_top_topic(manifest) if manifest else ""

    repo = args.repo.resolve()
    if not repo.exists() or not (repo / ".git").exists():
        print(f"[ocv-publish-github] No clone at {repo}. Cloning {args.remote_url} ...")
        if args.dry_run:
            print("[ocv-publish-github] --dry-run set; skipping clone.")
        else:
            repo.parent.mkdir(parents=True, exist_ok=True)
            _run(["git", "clone", args.remote_url, str(repo)])

    dest_html = repo / "reports" / f"{week}.html"
    reports_json = repo / "reports.json"

    # Build the entry that will be inserted/replaced.
    new_entry = {
        "week_of": week,
        "label": label,
        "range": date_range,
        "file": f"reports/{week}.html",
        "negatives": int(negatives) if negatives else 0,
        "topics": int(topics) if topics else 0,
        "thumbs_down": int(thumbs_down) if thumbs_down else 0,
        "thumbs_up": int(thumbs_up) if thumbs_up else 0,
        "top_topic": top_topic,
        "highlights": highlights,
        "published": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    # Load existing manifest (or seed if missing).
    if reports_json.exists():
        with open(reports_json, "r", encoding="utf-8") as f:
            site_manifest = json.load(f)
    else:
        site_manifest = {
            "title": "OL Agent OCV Dashboard",
            "subtitle": "Weekly customer-feedback signal for the Outlook AI Agent",
            "owner": _default_owner(),
            "reports": [],
        }
    existing_reports = list(site_manifest.get("reports", []))
    new_reports, upsert_action = upsert_report_entry(existing_reports, new_entry)

    commit_msg = args.commit_message or (
        f"Publish weekly report for {label}\n\n"
        f"- reports/{week}.html ({args.html.stat().st_size // 1024} KB)\n"
        f"- reports.json: {upsert_action} entry for week_of={week}\n"
        f"- Negatives: {negatives}, Topics: {topics}, Rating: {thumbs_down}-down / {thumbs_up}-up\n"
        + (f"- Highlights: {highlights}\n" if highlights else "")
    )

    # ----- Plan output (confirmation gate) -----
    print()
    print("=" * 62)
    print("OCV-PUBLISH-GITHUB PLAN")
    print("=" * 62)
    print(f"  Source HTML  : {args.html}  ({args.html.stat().st_size // 1024} KB)")
    print(f"  Target file  : {dest_html.relative_to(repo)}  (in {repo})")
    print(f"  Remote       : {args.remote_url}")
    print(f"  Branch       : {args.branch}")
    print(f"  reports.json : {upsert_action} entry for week_of={week}")
    print("-" * 62)
    print(f"  Card preview:")
    print(f"    label       : {label}")
    print(f"    range       : {date_range}")
    print(f"    negatives   : {negatives}")
    print(f"    topics      : {topics}")
    print(f"    rating      : {thumbs_down} down / {thumbs_up} up")
    print(f"    highlights  : {highlights or '(none provided -- card will be sparse)'}")
    print(f"    published   : {new_entry['published']}")
    print("-" * 62)
    print(f"  Commit message (first line): {commit_msg.splitlines()[0]}")
    print(f"  Live URL (once Pages is enabled):")
    print(f"    {DEFAULT_LIVE_BASE}/reports/{week}.html")
    print("=" * 62)

    if args.dry_run:
        print()
        print("[ocv-publish-github] --dry-run set; NOT touching the filesystem or git.")
        return 0

    if not args.yes:
        print()
        try:
            answer = input("Type 'yes' to copy + commit + push, anything else to abort: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("[ocv-publish-github] aborted by user.")
            return 1
        print("[ocv-publish-github] Confirmed. Publishing...")
    else:
        print("[ocv-publish-github] --yes set; skipping interactive prompt "
              "(the agent must have shown the plan to the user already).")

    # ----- Apply changes -----
    dest_html.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.html, dest_html)
    print(f"[ocv-publish-github] copied -> {dest_html.relative_to(repo)}")

    site_manifest["reports"] = new_reports
    with open(reports_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(site_manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"[ocv-publish-github] wrote -> reports.json ({upsert_action})")

    # Regenerate metrics_v2.json (consumed by index_v2.html M3 dashboard).
    # Best-effort: any failure here is logged but does not abort the publish.
    metrics_script = Path(__file__).resolve().parent / "gen_metrics_v2.py"
    metrics_dst = repo / "metrics_v2.json"
    if metrics_script.exists():
        print("[ocv-publish-github] regenerating metrics_v2.json ...")
        gen_res = subprocess.run(
            [sys.executable, str(metrics_script), str(metrics_dst)],
            text=True, capture_output=True
        )
        if gen_res.returncode == 0:
            print(f"[ocv-publish-github] wrote -> metrics_v2.json")
        else:
            print(f"[ocv-publish-github] WARNING: metrics_v2.json regen failed (rc={gen_res.returncode}); continuing")
            if gen_res.stdout: print(gen_res.stdout)
            if gen_res.stderr: print(gen_res.stderr, file=sys.stderr)
    else:
        print(f"[ocv-publish-github] metrics generator not found at {metrics_script}; skipping metrics_v2.json refresh")

    # Pull + commit + push.
    print()
    print("[ocv-publish-github] git pull --rebase (sync with remote) ...")
    _run(["git", "pull", "--rebase", "origin", args.branch], cwd=repo, check=False)

    print("[ocv-publish-github] git add + commit ...")
    _run(["git", "add", "-A"], cwd=repo)
    commit_res = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=str(repo), text=True, capture_output=True
    )
    if commit_res.returncode != 0:
        if "nothing to commit" in (commit_res.stdout + commit_res.stderr).lower():
            print("[ocv-publish-github] nothing to commit (file already in sync). Skipping push.")
            return 0
        print(commit_res.stdout)
        print(commit_res.stderr, file=sys.stderr)
        sys.exit("[ocv-publish-github] git commit failed; see error above.")
    print(commit_res.stdout.strip())

    print()
    print("[ocv-publish-github] git push ...")
    push_res = subprocess.run(
        ["git", "push", "origin", args.branch],
        cwd=str(repo), text=True, capture_output=True
    )
    if push_res.returncode != 0:
        print(push_res.stdout)
        print(push_res.stderr, file=sys.stderr)
        sys.exit("[ocv-publish-github] git push failed; see error above.")
    print(push_res.stderr.strip() or push_res.stdout.strip())

    print()
    print("=" * 62)
    print("PUBLISH SUCCEEDED")
    print("=" * 62)
    print(f"  Landing page : {DEFAULT_LIVE_BASE}/")
    print(f"  This report  : {DEFAULT_LIVE_BASE}/reports/{week}.html")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())

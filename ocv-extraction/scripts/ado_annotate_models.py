"""Annotate already-synced ADO tickets with resolved model paths.

For each ADO ticket touched by `ado_sync.py execute` for the week
2026-05-18 → 2026-05-24, append a single "Resolved model paths" block
to System.Description (and a one-line System.History note) showing each
Dash ticket with its `[PathSlug: model, ...]` suffix. Idempotent: any
ticket whose current description already contains the resolved-model
marker is skipped.

Auth: DefaultAzureCredential via `az login`.

Usage:
    python _annotate_ado_models.py --dry-run
    python _annotate_ado_models.py            # prompts before each write
    python _annotate_ado_models.py --yes      # skip per-ticket prompt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from azure.identity import DefaultAzureCredential

REPO = Path(__file__).parent
PROPOSALS = REPO / "data" / "ado_proposals_ocv_outlook-agent_2026-05-24.json"
ORG_URL = "https://dev.azure.com/outlookweb"
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
WEEK_RANGE = "2026-05-18 → 2026-05-24"

PATH_SLUG_RE = re.compile(
    r"\[(?:Sydney-Tools|CodeGen-Claude|CodeGen-GHCP|Unknown)",
    re.IGNORECASE,
)


def esc(s: str) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def models_suffix(label: str) -> str:
    if not label:
        return ""
    return f' <span style="color:#888">[{esc(label)}]</span>'


def render_link_block(link_pairs: List[dict]) -> str:
    items: List[str] = []
    for pair in link_pairs:
        ocv = (pair.get("ocv") or "").strip()
        dash = (pair.get("dash") or "").strip()
        suffix = models_suffix((pair.get("models_label") or "").strip())
        if not suffix:
            continue
        if ocv and dash:
            items.append(
                f'<li><a href="{esc(ocv)}">{esc(ocv)}</a> - '
                f'<a href="{esc(dash)}">{esc(dash)}</a>{suffix}</li>')
        elif dash:
            items.append(f'<li><a href="{esc(dash)}">{esc(dash)}</a>{suffix}</li>')
    if not items:
        return ""
    return "<ul>" + "".join(items) + "</ul>"


def build_annotation(subtopics: List[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    parts = [
        f'<hr><p><strong>OCV weekly sync &mdash; resolved model paths ({today})</strong></p>',
        f'<p><em>Backfilling agent routing path '
        f'(<code>diagnosticContext.resolvedModelName</code>) per Dash ticket for the week '
        f'{esc(WEEK_RANGE)}. Each link is followed by '
        f'<code>[PathSlug: raw model name, ...]</code>.</em></p>',
    ]
    for s in subtopics:
        block = render_link_block(s["link_pairs"])
        if not block:
            continue
        parts.append(f'<p><strong>{esc(s["title"])}</strong></p>{block}')
    return "".join(parts)


def get_token() -> str:
    cred = DefaultAzureCredential()
    return cred.get_token(f"{ADO_RESOURCE}/.default").token


def collect_targets() -> Dict[str, dict]:
    data = json.loads(PROPOSALS.read_text(encoding="utf-8"))
    prop_by_key = {p["subtopic_key"]: p for p in data["proposals"]}
    by_id: Dict[str, dict] = defaultdict(lambda: {"url": "", "subtopics": []})
    for key, info in data.get("execute_results", {}).items():
        p = prop_by_key.get(key)
        if not p:
            continue
        pairs_with_models = [lp for lp in p.get("link_pairs", [])
                              if (lp.get("models_label") or "").strip()]
        if not pairs_with_models:
            continue
        m = re.search(r"/edit/(\d+)", info["url"])
        if not m:
            continue
        wid = m.group(1)
        by_id[wid]["url"] = info["url"]
        by_id[wid]["subtopics"].append({
            "title": p.get("title", key.split("|", 1)[0]),
            "action": info["action"],
            "link_pairs": pairs_with_models,
        })
    return dict(sorted(by_id.items(), key=lambda kv: int(kv[0])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print plan only; make no ADO writes.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip per-batch confirmation prompt.")
    args = ap.parse_args()

    targets = collect_targets()
    print(f"Resolved model-path annotation plan")
    print(f"  Source proposals : {PROPOSALS.name}")
    print(f"  Week             : {WEEK_RANGE}")
    print(f"  ADO tickets      : {len(targets)} (each may carry 1+ subtopic block)")
    print()
    for wid, t in targets.items():
        sub_count = len(t["subtopics"])
        dash_count = sum(len(s["link_pairs"]) for s in t["subtopics"])
        print(f"  #{wid}  {sub_count} subtopic(s), {dash_count} dash link(s) annotated")
        for s in t["subtopics"]:
            print(f"     - [{s['action']}] {s['title'][:80]}")
    print()

    if args.dry_run:
        print("Dry run — no ADO writes performed.")
        return 0

    if not args.yes:
        ans = input(f"Proceed with PATCH against {len(targets)} ticket(s)? Type 'yes': ").strip().lower()
        if ans != "yes":
            print("Aborted by user.")
            return 1

    token = get_token()
    headers_get = {"Authorization": f"Bearer {token}"}
    headers_patch = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json-patch+json",
    }

    written: List[dict] = []
    skipped: List[dict] = []
    failed: List[dict] = []

    for wid, t in targets.items():
        url = f"{ORG_URL}/_apis/wit/workitems/{wid}?api-version=7.1"
        try:
            r = requests.get(url, headers=headers_get, timeout=60)
            r.raise_for_status()
            fields = r.json().get("fields", {}) or {}
        except Exception as e:
            print(f"  #{wid}  FETCH FAILED: {e}")
            failed.append({"id": wid, "stage": "fetch", "error": str(e)})
            continue
        current_desc = fields.get("System.Description") or ""

        if PATH_SLUG_RE.search(current_desc):
            print(f"  #{wid}  SKIP (already has resolved-model annotation)")
            skipped.append({"id": wid, "reason": "already_annotated"})
            continue

        annotation = build_annotation(t["subtopics"])
        if not annotation:
            print(f"  #{wid}  SKIP (no renderable link pairs)")
            skipped.append({"id": wid, "reason": "no_pairs"})
            continue

        new_desc = current_desc + annotation
        sub_count = len(t["subtopics"])
        dash_count = sum(len(s["link_pairs"]) for s in t["subtopics"])
        history_html = (
            f'<p><strong>OCV weekly sync</strong> &mdash; appended resolved model paths for '
            f'{sub_count} subtopic(s) / {dash_count} Dash ticket(s) (week {esc(WEEK_RANGE)}).</p>'
        )
        patch = [
            {"op": "add", "path": "/fields/System.Description", "value": new_desc},
            {"op": "add", "path": "/fields/System.History", "value": history_html},
        ]
        try:
            pr = requests.patch(url, headers=headers_patch, json=patch, timeout=60)
            pr.raise_for_status()
        except Exception as e:
            body = getattr(pr, "text", "")[:300] if "pr" in dir() else ""
            print(f"  #{wid}  PATCH FAILED: {e} {body}")
            failed.append({"id": wid, "stage": "patch", "error": str(e)})
            continue
        print(f"  #{wid}  OK (appended {sub_count} subtopic / {dash_count} dash)")
        written.append({"id": wid, "subtopics": sub_count, "dash": dash_count})

    print()
    print(f"Done. written={len(written)} skipped={len(skipped)} failed={len(failed)}")
    out = REPO / "data" / "ado_model_annotations_2026-05-24.json"
    out.write_text(json.dumps({
        "ran_at": datetime.now().isoformat(),
        "week_range": WEEK_RANGE,
        "written": written,
        "skipped": skipped,
        "failed": failed,
    }, indent=2), encoding="utf-8")
    print(f"  receipt: {out}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

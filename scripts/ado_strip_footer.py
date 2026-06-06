"""Remove the legacy "Auto-created by ocv-extraction/ticket-sync skill." footer
from existing ADO work items. Idempotent: tickets without the marker are left
untouched.

Targets: every distinct ADO work-item ID listed in
data/ado_proposals_ocv_outlook-agent_2026-05-24.json's `execute_results`.

Usage:
    python scripts/ado_strip_footer.py --dry-run
    python scripts/ado_strip_footer.py --yes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential

REPO = Path(__file__).resolve().parent.parent
PROPOSALS = REPO / "data" / "ado_proposals_ocv_outlook-agent_2026-05-24.json"
ORG_URL = "https://dev.azure.com/outlookweb"
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"

FOOTER_RE = re.compile(
    r"\s*<p>\s*<em>\s*Auto-created by ocv-extraction/ticket-sync skill\.\s*</em>\s*</p>\s*",
    re.IGNORECASE,
)


def get_token() -> str:
    return DefaultAzureCredential().get_token(f"{ADO_RESOURCE}/.default").token


def collect_ids() -> list[str]:
    data = json.loads(PROPOSALS.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for info in data.get("execute_results", {}).values():
        m = re.search(r"/edit/(\d+)", info["url"])
        if m:
            ids.add(m.group(1))
    return sorted(ids, key=int)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    ids = collect_ids()
    print(f"Strip-footer plan ({len(ids)} candidate tickets):")
    for wid in ids:
        print(f"  #{wid}")
    print()

    if not args.dry_run and not args.yes:
        if input("Proceed? type 'yes': ").strip().lower() != "yes":
            print("Aborted.")
            return 1

    token = get_token()
    h_get = {"Authorization": f"Bearer {token}"}
    h_patch = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json-patch+json"}

    written, clean, failed = [], [], []
    for wid in ids:
        url = f"{ORG_URL}/_apis/wit/workitems/{wid}?fields=System.Description,Microsoft.VSTS.TCM.ReproSteps&api-version=7.1"
        try:
            r = requests.get(url, headers=h_get, timeout=60)
            r.raise_for_status()
            fields = r.json().get("fields", {}) or {}
        except Exception as e:
            print(f"  #{wid}  FETCH FAILED: {e}")
            failed.append({"id": wid, "stage": "fetch", "error": str(e)})
            continue

        ops = []
        for field in ("System.Description", "Microsoft.VSTS.TCM.ReproSteps"):
            current = fields.get(field) or ""
            if FOOTER_RE.search(current):
                cleaned = FOOTER_RE.sub("", current)
                ops.append({"op": "add", "path": f"/fields/{field}", "value": cleaned})

        if not ops:
            print(f"  #{wid}  clean (no footer present)")
            clean.append(wid)
            continue

        if args.dry_run:
            print(f"  #{wid}  WOULD STRIP from {len(ops)} field(s)")
            continue

        # Add a single-line history note for auditability
        ops.append({"op": "add", "path": "/fields/System.History",
                    "value": "<p><em>Removed legacy auto-created footer.</em></p>"})
        try:
            patch_url = f"{ORG_URL}/_apis/wit/workitems/{wid}?api-version=7.1"
            pr = requests.patch(patch_url, headers=h_patch, json=ops, timeout=60)
            pr.raise_for_status()
        except Exception as e:
            print(f"  #{wid}  PATCH FAILED: {e}")
            failed.append({"id": wid, "stage": "patch", "error": str(e)})
            continue
        print(f"  #{wid}  stripped from {len(ops)-1} field(s)")
        written.append(wid)

    print()
    print(f"Done. stripped={len(written)} already_clean={len(clean)} failed={len(failed)}")
    out = REPO / "data" / "ado_footer_strip_2026-05-24.json"
    out.write_text(json.dumps({
        "ran_at": datetime.now().isoformat(),
        "stripped": written, "clean": clean, "failed": failed,
    }, indent=2), encoding="utf-8")
    print(f"  receipt: {out}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

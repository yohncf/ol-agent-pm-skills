"""Preview owner assignment for already-created ADO bugs without touching ADO.
Reads subtopics CSV + owners config; prints a per-bug mapping table.
"""
from __future__ import annotations
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_config(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_assignee(row, cfg):
    """Return (assignee_email, owner_name, rule_label) or (None, None, None)."""
    title = (row.get("Brief Title") or "").lower()
    cat = (row.get("Category") or "").strip()
    topic_raw = (row.get("Parent Topic") or "").strip()
    topic_num = ""
    t_clean = topic_raw[1:] if topic_raw.startswith("T") else topic_raw
    if "." in t_clean:
        topic_num = t_clean.split(".", 1)[0].strip()
    for rule in cfg.get("rules", []):
        m = rule.get("match", {})
        if "category" in m and cat not in m["category"]:
            continue
        if "topic" in m and topic_num not in m["topic"]:
            continue
        if "title_keywords" in m:
            # Title-only match: description is PM paraphrase and too noisy
            if not any(kw.lower() in title for kw in m["title_keywords"]):
                continue
        return rule.get("assignee"), rule.get("owner_name"), rule.get("label")
    return cfg.get("default_assignee"), cfg.get("default_assignee_name"), "default"


def main():
    subtopics = ROOT / "data" / "ocv_outlook-agent_2026-05-24_subtopics.csv"
    cfg_path = ROOT / "configs" / "ado_owners_outlook-agent.json"
    cfg = load_config(cfg_path)
    rows = list(csv.DictReader(open(subtopics, "r", encoding="utf-8-sig")))
    created = [r for r in rows if (r.get("ADO action") or "").strip().lower() == "created"]
    print(f"Created bugs this week: {len(created)}")
    print()
    print(f"{'ADO #':<8} {'Prio':<5} {'Surface':<14} {'Owner':<22} {'Email':<28} Title")
    print("-" * 130)
    unmapped = []
    by_owner = {}
    for r in created:
        url = r.get("ADO URL") or ""
        aid = url.rsplit("/", 1)[-1] if url else "?"
        email, name, rule_label = compute_assignee(r, cfg)
        if not email:
            unmapped.append((aid, r))
            owner = "(unmapped)"
            email = ""
        else:
            owner = name or ""
            by_owner.setdefault(email, []).append(aid)
        print(f"#{aid:<7} {r.get('Priority',''):<5} {r.get('Category',''):<14} "
              f"{owner:<22} {email:<28} {r.get('Brief Title','')[:60]}")
    print()
    print("Per-owner counts:")
    for email, items in sorted(by_owner.items(), key=lambda kv: -len(kv[1])):
        print(f"  {email:<28} {len(items)} item(s): {', '.join('#' + i for i in items)}")
    if unmapped:
        print()
        print(f"Unmapped ({len(unmapped)}) — will be left unassigned:")
        for aid, r in unmapped:
            print(f"  #{aid} [{r.get('Category','')}] {r.get('Brief Title','')[:80]}")


if __name__ == "__main__":
    main()

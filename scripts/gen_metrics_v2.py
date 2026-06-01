"""Generate metrics_v2.json for the OCV-Weekly M3 dashboard.

Reads every weekly manifest in data/manifests/ and the matching subtopics
CSV in data/, then emits one aggregate JSON the dashboard consumes
(per-week sentiment %, rating %, topic counts, priority counts, WoW
deltas, emerging-topic risers, and P0/P1/P2 subtopic rows grouped by
topic for the hover popovers).

Run manually:
    python scripts/gen_metrics_v2.py

Called automatically by scripts/publish_to_github.py after each publish.
"""
import csv as csv_mod
import glob
import json
import os
import re
import sys
from collections import Counter

csv_mod.field_size_limit(10**8)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TOPIC_NAMES = {
    1: "Action not executed", 2: "HitL violation",
    3: "Output doesn't match intent", 4: "Constraints ignored",
    5: "Unnecessary clarifying question", 6: "Reliability failure",
    7: "Inaccurate or fabricated content", 8: "Wrong context / grounding",
    9: "Tone / language / format", 10: "File I/O failure",
    11: "Calendar correctness", 12: "Capability refusal",
    13: "Intrusive Outlook Agent UI",
}


def build():
    manifest_glob = os.path.join(REPO, "data/manifests/ocv_outlook-agent_*_manifest.json")
    manifests = sorted(glob.glob(manifest_glob))
    out = {"weeks": [], "topic_names": {str(k): v for k, v in TOPIC_NAMES.items()}}

    for mp in manifests:
        m = json.load(open(mp, encoding="utf-8"))
        week = m.get("week")
        if not week:
            continue
        sp = os.path.join(REPO, f"data/ocv_outlook-agent_{week}_subtopics.csv")
        sub_rows = list(csv_mod.DictReader(open(sp, encoding="utf-8-sig"))) if os.path.exists(sp) else []

        pri = Counter(r.get("Priority", "") for r in sub_rows)
        ticketable = sum(1 for r in sub_rows if r.get("Ticket Worthy", "").strip().lower() == "yes")
        ado_synced = sum(1 for r in sub_rows if r.get("ADO URL", "").strip())

        subtopics_by_topic = {}
        for r in sub_rows:
            pr = r.get("Priority", "").strip().upper()
            if pr not in ("P0", "P1", "P2"):
                continue
            parent = r.get("Parent Topic", "").strip()
            mtch = re.match(r"^(\d+)\.\s*(.+)$", parent)
            if not mtch:
                continue
            tid = int(mtch.group(1))
            subtopics_by_topic.setdefault(str(tid), []).append({
                "priority": pr,
                "brief_title": (r.get("Brief Title", "") or "").strip(),
                "item_count": int(r.get("Item Count", 0) or 0),
                "ado_url": (r.get("ADO URL", "") or "").strip() or None,
            })
        pri_rank = {"P0": 0, "P1": 1, "P2": 2}
        for tid, rows in subtopics_by_topic.items():
            rows.sort(key=lambda x: (pri_rank.get(x["priority"], 99), -x["item_count"]))

        rating = m.get("rating", {}) or {}
        tu = int(rating.get("ThumbsUp", 0) or 0)
        td = int(rating.get("ThumbsDown", 0) or 0)
        total_ratings = tu + td

        sentiment = m.get("sentiment", {}) or {}
        neg = int(sentiment.get("Negative", 0) or 0)
        pos = int(sentiment.get("Positive", 0) or 0)
        neu = int(sentiment.get("Neutral", 0) or 0)
        scored_total = neg + pos + neu

        topic_counts = {str(k): int(v) for k, v in (m.get("topic_counts", {}) or {}).items()}
        ranked = sorted(((int(k), v) for k, v in topic_counts.items() if v > 0), key=lambda kv: -kv[1])
        top5 = [{"topic_id": k, "name": TOPIC_NAMES.get(k, f"Topic {k}"), "count": v} for k, v in ranked[:5]]

        out["weeks"].append({
            "week_of": week,
            "date_range": m.get("date_range", ""),
            "total_items": int(m.get("total_items", 0) or 0),
            "verbatim_items": int(m.get("verbatim_items", 0) or 0),
            "structured_only_items": int(m.get("structured_only_items", 0) or 0),
            "negative_items": int(m.get("negative_items", 0) or 0),
            "rating": {
                "up": tu, "down": td, "total": total_ratings,
                "positive_pct": round(100.0 * tu / total_ratings, 1) if total_ratings else None,
            },
            "sentiment": {
                "negative": neg, "positive": pos, "neutral": neu,
                "negative_pct_of_scored": round(100.0 * neg / scored_total, 1) if scored_total else None,
            },
            "topic_counts": topic_counts,
            "top_topics": top5,
            "priority": {
                "p0": pri.get("P0", 0), "p1": pri.get("P1", 0),
                "p2": pri.get("P2", 0), "p3": pri.get("P3", 0),
            },
            "ticket_worthy_rows": ticketable,
            "ado_synced_rows": ado_synced,
            "subtopic_rows": len(sub_rows),
            "subtopics_by_topic": subtopics_by_topic,
            "clients": m.get("clients", {}) or {},
            "languages": m.get("languages", {}) or {},
        })

    out["weeks"].sort(key=lambda w: w["week_of"])

    for i, w in enumerate(out["weeks"]):
        if i == 0:
            w["wow"] = None
            w["emerging"] = []
            continue
        prev = out["weeks"][i - 1]
        pos_now = w["rating"]["positive_pct"] or 0
        pos_prev = prev["rating"]["positive_pct"] or 0
        w["wow"] = {
            "rating_pp": round(pos_now - pos_prev, 1),
            "volume_pct": round(100.0 * (w["total_items"] - prev["total_items"]) / prev["total_items"], 1) if prev["total_items"] else None,
            "negatives_pct": round(100.0 * (w["negative_items"] - prev["negative_items"]) / prev["negative_items"], 1) if prev["negative_items"] else None,
            "p0p1_delta": (w["priority"]["p0"] + w["priority"]["p1"]) - (prev["priority"]["p0"] + prev["priority"]["p1"]),
        }
        deltas = []
        for tid, cnt in w["topic_counts"].items():
            prev_cnt = prev["topic_counts"].get(tid, 0)
            d = cnt - prev_cnt
            if d > 0:
                deltas.append({
                    "topic_id": int(tid),
                    "name": TOPIC_NAMES.get(int(tid), f"Topic {tid}"),
                    "this_week": cnt, "last_week": prev_cnt, "delta": d,
                })
        deltas.sort(key=lambda x: -x["delta"])
        w["emerging"] = deltas[:3]

    return out


def main(argv=None):
    out = build()
    default_dst = os.path.join(REPO, "_ocv_weekly_repo", "metrics_v2.json")
    dst = (argv or sys.argv)[1] if len(argv or sys.argv) > 1 else default_dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"[gen_metrics_v2] wrote {dst} ({len(out['weeks'])} weeks)")


if __name__ == "__main__":
    main()

"""publish_ocv_report.py

Render a self-contained, dark-themed HTML dashboard from an
ocv-analyze-and-ticket manifest JSON (+ optional subtopics CSV / prior
manifest / report markdown).

Usage:
    python scripts/publish_ocv_report.py \
        --manifest data/manifests/ocv_outlook-agent_2026-05-18_manifest.json \
        [--subtopics data/ocv_outlook-agent_2026-05-18_subtopics.csv] \
        [--prior-manifest data/manifests/ocv_outlook-agent_2026-05-11_manifest.json] \
        [--report-md data/reports/ocv_outlook-agent_2026-05-18_report.md] \
        [--out output/ocv_outlook-agent_2026-05-18.html] \
        [--no-open]

If --subtopics is omitted, the script looks for a sibling CSV at
`data/ocv_<area>_<week>_subtopics.csv`.

If --prior-manifest is omitted, the script picks the most-recent earlier
manifest for the same `<area>` in `data/manifests/`.

Compliance: This script reads manifest JSON + (optional) Issue-Description
strings from the subtopics CSV. Both are PM-summarized derivatives and
already considered safe to publish per the analyze-and-ticket doctrine.
Run only under GitHub Copilot CLI.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOPIC_NAMES: Dict[int, str] = {
    1: "Action not executed",
    2: "HitL violation",
    3: "Output doesn't match intent",
    4: "Constraints ignored",
    5: "Unnecessary clarifying question",
    6: "Reliability failure",
    7: "Inaccurate or fabricated content",
    8: "Wrong context / grounding",
    9: "Tone, language, or format quality",
    10: "File I/O failure",
    11: "Calendar correctness",
    12: "Capability refusal (in-scope)",
    13: "Intrusive Outlook Agent UI",
}

# Stable per-topic accent classes for the topic ranking bars.
TOPIC_ACCENT: Dict[int, str] = {
    1: "red",
    2: "red",
    3: "amber",
    4: "amber",
    5: "amber",
    6: "red",
    7: "amber",
    8: "navy",
    9: "navy",
    10: "amber",
    11: "red",
    12: "navy",
    13: "navy",
}

# Surface categories we render in the category grid. Order matters.
CATEGORY_ORDER = [
    "Drafting",
    "Replying",
    "Summarization",
    "Search",
    "Scheduling",
    "Triage",
    "File",
    "Settings",
    "Rules",
    "Tasks",
    "Reminders",
    "Translation",
    "General",
    "Unknown",
]

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_manifest(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_prior_manifest(area: str, current_week: str, manifests_dir: Path) -> Optional[Path]:
    if not manifests_dir.is_dir():
        return None
    candidates: List[Tuple[str, Path]] = []
    pattern = re.compile(rf"^ocv_{re.escape(area)}_(\d{{4}}-\d{{2}}-\d{{2}})_manifest\.json$")
    for p in manifests_dir.iterdir():
        m = pattern.match(p.name)
        if m and m.group(1) < current_week:
            candidates.append((m.group(1), p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def find_subtopics_csv(area: str, week: str, data_dir: Path) -> Optional[Path]:
    for stem in (f"ocv_{area}_{week}_subtopics.csv",):
        p = data_dir / stem
        if p.exists():
            return p
    return None


def parse_subtopics_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k.strip(): (v or "").strip() for k, v in row.items()})
    return rows


# ---------------------------------------------------------------------------
# Priority + WoW derivation
# ---------------------------------------------------------------------------

CORE_INFO_SURFACES = {"Summarization", "Triage", "Search"}
ACTION_SURFACES = {"Drafting", "Replying", "Scheduling", "Triage"}


def derive_priority(row: Dict[str, str]) -> str:
    """Apply the doctrine's first-match priority rules when the CSV lacks
    a Priority column (older runs). Falls back to P3."""
    if row.get("Priority"):
        return row["Priority"].upper()

    try:
        count = int(row.get("Count") or row.get("Item Count") or "1")
    except ValueError:
        count = 1
    topic_str = row.get("Parent Topic", "")
    topic_num = 0
    m = re.match(r"\s*(\d+)", topic_str)
    if m:
        topic_num = int(m.group(1))
    category = row.get("Category", "")
    submode = (row.get("SubMode") or "").lower()

    # P0
    if topic_num == 2:
        return "P0"
    if topic_num == 11 and count >= 2:
        return "P0"
    # P1
    if count >= 4:
        return "P1"
    if topic_num == 1 and category in ACTION_SURFACES and count >= 2:
        return "P1"
    if topic_num == 6 and "error_string" in submode and category in CORE_INFO_SURFACES and count >= 2:
        return "P1"
    if topic_num == 10 and count >= 2:
        return "P1"
    # P2
    if count >= 2:
        return "P2"
    if topic_num in (4, 8):
        return "P2"
    return "P3"


def compute_topic_shares(topic_counts: Dict[str, int], negative: int) -> Dict[int, Tuple[int, float]]:
    out: Dict[int, Tuple[int, float]] = {}
    for k, v in topic_counts.items():
        try:
            tid = int(k)
        except ValueError:
            continue
        pct = (v / negative * 100.0) if negative else 0.0
        out[tid] = (v, pct)
    return out


def compute_wow(
    current: Dict[str, int], current_neg: int,
    prior: Optional[Dict[str, int]], prior_neg: int,
) -> List[Dict[str, Any]]:
    """Return rows merged across both sets, sorted by current share desc."""
    keys = set(current.keys())
    if prior:
        keys |= set(prior.keys())
    rows: List[Dict[str, Any]] = []
    for k in keys:
        try:
            tid = int(k)
        except ValueError:
            continue
        cur_n = current.get(k, 0)
        cur_p = (cur_n / current_neg * 100.0) if current_neg else 0.0
        if prior is not None:
            pr_n = prior.get(k, 0)
            pr_p = (pr_n / prior_neg * 100.0) if prior_neg else 0.0
        else:
            pr_n = None
            pr_p = None
        rows.append({
            "topic": tid,
            "topic_name": TOPIC_NAMES.get(tid, f"Topic {tid}"),
            "cur_n": cur_n,
            "cur_p": cur_p,
            "pr_n": pr_n,
            "pr_p": pr_p,
            "delta_pp": (cur_p - pr_p) if pr_p is not None else None,
        })
    rows.sort(key=lambda r: r["cur_p"], reverse=True)
    return rows


def compute_category_topics(per_item: List[Dict[str, Any]]) -> Dict[str, List[Tuple[int, int]]]:
    """category -> [(topic, count)] sorted by count desc, top 3."""
    accum: Dict[str, Dict[int, int]] = {}
    for it in per_item:
        cat = it.get("category") or "Unknown"
        try:
            tid = int(it.get("topic"))
        except (TypeError, ValueError):
            continue
        accum.setdefault(cat, {})
        accum[cat][tid] = accum[cat].get(tid, 0) + 1
    out: Dict[str, List[Tuple[int, int]]] = {}
    for cat, tcounts in accum.items():
        ranked = sorted(tcounts.items(), key=lambda kv: kv[1], reverse=True)[:3]
        out[cat] = ranked
    return out


# ---------------------------------------------------------------------------
# Report MD parsing (optional)
# ---------------------------------------------------------------------------


def parse_report_md(text: str) -> Dict[str, Any]:
    """Extract `## TL;DR` (paragraph form) and `## Key Findings` (numbered/bulleted)
    sections from the analyze-and-ticket markdown report. Both are optional;
    returns empty strings/lists if not present."""
    out = {"tldr": "", "findings": []}
    # TL;DR: capture text after `## TL;DR` until next `## ` or `### ` heading or EOF.
    # The `### ` stop is intentional: prior versions of the MD report nested
    # subsections (e.g. "### Dataset Summary", "**Required caveats:**") inside
    # the TL;DR block, which then leaked into the HTML hero. Anything that
    # belongs in the dataset section should live under its own `## ` heading
    # downstream of TL;DR, not under it.
    m = re.search(r"^##\s+TL;DR\s*\n(.*?)(?=\n##\s|\n###\s|\Z)", text, flags=re.S | re.M | re.I)
    if m:
        out["tldr"] = m.group(1).strip()
    m = re.search(r"^##\s+Key Findings\s*\n(.*?)(?=\n##\s|\Z)", text, flags=re.S | re.M | re.I)
    if m:
        block = m.group(1).strip()
        # Split on top-level numbered or bullet markers
        items = re.split(r"^\s*(?:\d+\.|[-*])\s+", block, flags=re.M)
        out["findings"] = [s.strip() for s in items if s.strip()]
    return out


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def fmt_pct(p: float, digits: int = 1) -> str:
    return f"{p:.{digits}f}%"


def fmt_delta_pp(delta: Optional[float]) -> Tuple[str, str]:
    """Return (text, direction-class). direction-class in {up, down, flat, none}."""
    if delta is None:
        return ("—", "none")
    if abs(delta) < 0.5:
        return (f"{delta:+.1f} pp", "flat")
    if delta > 0:
        return (f"+{delta:.1f} pp", "up")
    return (f"{delta:.1f} pp", "down")


def fmt_delta_count(cur: int, prior: Optional[int]) -> Tuple[str, str]:
    if prior is None:
        return ("—", "none")
    d = cur - prior
    if d == 0:
        return ("flat", "flat")
    if d > 0:
        return (f"+{d}", "up")
    return (f"{d}", "down")


def markdown_lite(text: str) -> str:
    """Lightweight markdown -> HTML: paragraphs, bold, code, links.
    Used for TL;DR + key findings paragraphs from the report MD."""
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )
    paras = re.split(r"\n\s*\n", text.strip())
    return "\n".join(f"<p>{p.strip().replace(chr(10), '<br>')}</p>" for p in paras if p.strip())


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def render_header(manifest: Dict[str, Any], area_label: str) -> str:
    week = manifest.get("week", "")
    date_range = manifest.get("date_range", week)
    source = manifest.get("source_csv", "")
    analysis_date = manifest.get("analysis_date", "")
    tax_ver = manifest.get("taxonomy_version", "")
    return f"""<header>
  <a href="https://gim-home.github.io/OCV-Weekly" class="back-link">&larr; All weekly reports</a>
  <p class="eyebrow">OCV Weekly Report &middot; <em>{esc(area_label)}</em></p>
  <h1>Week of <em>{esc(date_range)}</em></h1>
  <div class="header__meta">
    <span><strong>Analysis date:</strong> {esc(analysis_date)}</span>
  </div>
</header>"""


def render_tldr(manifest: Dict[str, Any], findings_text: str, caveats: List[str],
                methodology: List[str]) -> str:
    lead_html = ""
    if findings_text:
        lead_html = f'<div class="tldr__body">{findings_text}</div>'
    else:
        # Synthesize a lightweight TL;DR from manifest aggregates if no MD report.
        total = manifest.get("total_items", 0)
        neg = manifest.get("negative_items", 0)
        top_topic = None
        tc = manifest.get("topic_counts", {}) or {}
        if tc:
            top_topic_id, top_topic_n = max(
                ((int(k), v) for k, v in tc.items() if k.isdigit()),
                key=lambda kv: kv[1],
                default=(0, 0),
            )
            top_topic = TOPIC_NAMES.get(top_topic_id, f"Topic {top_topic_id}")
            top_topic_share = (top_topic_n / neg * 100.0) if neg else 0.0
        else:
            top_topic_share = 0.0
            top_topic_n = 0
        cc = manifest.get("category_counts", {}) or {}
        top_cat = None
        if cc:
            top_cat_name, top_cat_n = max(cc.items(), key=lambda kv: kv[1])
            top_cat = top_cat_name
            top_cat_share = (top_cat_n / neg * 100.0) if neg else 0.0
        else:
            top_cat_share = 0.0
            top_cat_n = 0
        lead_html = f"""
        <div class="tldr__body">
          <p class="tldr__lead"><em>{esc(neg)} negative items</em> classified into the 13-topic taxonomy
          ({esc(total)} total items captured by the OCV pipeline this week).</p>
          <p>The dominant failure mode is <strong>{esc(top_topic)}</strong> at
          {fmt_pct(top_topic_share)} of negatives ({esc(top_topic_n)} items).
          The most-affected surface is <strong>{esc(top_cat)}</strong> at
          {fmt_pct(top_cat_share)} ({esc(top_cat_n)} items).</p>
          <p class="tldr__hint">Pass <code>--report-md</code> with the analyze-and-ticket
          markdown report to render the curated TL;DR and Key Findings sections.</p>
        </div>
        """

    caveat_html = "".join(f"<li>{esc(c)}</li>" for c in caveats) if caveats else ""
    method_html = "".join(f"<li>{esc(m)}</li>" for m in methodology) if methodology else ""

    method_block = (
        f'<details class="tldr__method"><summary>Methodology notes</summary>'
        f'<ul>{method_html}</ul></details>'
    ) if methodology else ""

    return f"""<section id="tldr">
  <p class="section-label">01 · TL;DR</p>
  <h2>tl;dr</h2>
  <div class="tldr">
    {lead_html}
  </div>
</section>"""


def fmt_pct_change(cur: int, prior: Optional[int]) -> Tuple[str, str]:
    """Return (text, direction-class) for the relative % change between cur and prior."""
    if prior is None or prior == 0:
        return ("—", "none")
    pct = (cur - prior) / prior * 100.0
    if abs(pct) < 0.5:
        return (f"{pct:+.1f}%", "flat")
    if pct > 0:
        return (f"+{pct:.1f}%", "up")
    return (f"{pct:.1f}%", "down")


def render_kpi_grid(manifest: Dict[str, Any], prior: Optional[Dict[str, Any]],
                    subtopic_rows: List[Dict[str, Any]]) -> str:
    neg = manifest.get("negative_items", 0)
    topic_counts = manifest.get("topic_counts", {}) or {}
    topics_surfaced = sum(1 for v in topic_counts.values() if v > 0)

    rating = manifest.get("rating", {}) or {}
    rate_down = rating.get("ThumbsDown", 0)
    rate_up = rating.get("ThumbsUp", 0)

    pr = prior or {}
    pr_neg = pr.get("negative_items") if prior else None
    if prior:
        pr_topics_surfaced = sum(1 for v in (pr.get("topic_counts") or {}).values() if v > 0)
    else:
        pr_topics_surfaced = None
    pr_rate = pr.get("rating", {}) or {}

    neg_delta_txt, neg_delta_cls = fmt_pct_change(neg, pr_neg)
    top_delta_txt, top_delta_cls = fmt_pct_change(topics_surfaced, pr_topics_surfaced)

    def _was_block(prior_val: Optional[int], delta_txt: str, delta_cls: str) -> str:
        if prior_val is None:
            return '<div class="kpi__was kpi__was--none">no prior baseline</div>'
        return (
            f'<div class="kpi__was">'
            f'<span class="kpi__was-label">Last week</span>'
            f'<span class="kpi__was-value">{fmt_int(prior_val)}</span>'
            f'<span class="delta delta--{delta_cls}">{esc(delta_txt)}</span>'
            f'</div>'
        )

    def _row_pct_delta(cur: int, prior_val: Optional[int]) -> str:
        if prior_val is None:
            return ""
        txt, cls = fmt_pct_change(cur, prior_val)
        return f'<span class="row-delta row-delta--{cls}">{esc(txt)}</span>'

    return f"""<section id="kpis">
  <p class="section-label">02 · Headline numbers</p>
  <h2>What changed this week</h2>
  <div class="kpi-grid kpi-grid--three">
    <div class="kpi kpi--hero">
      <div class="kpi__label">Verbatim negative items</div>
      <div class="kpi__value">{esc(neg)}</div>
      {_was_block(pr_neg, neg_delta_txt, neg_delta_cls)}
    </div>
    <div class="kpi">
      <div class="kpi__label">Topics surfaced</div>
      <div class="kpi__value">{esc(topics_surfaced)}</div>
      {_was_block(pr_topics_surfaced, top_delta_txt, top_delta_cls)}
    </div>
    <div class="kpi kpi--breakdown">
      <div class="kpi__label">Rating <span class="kpi__sublabel">now · was · Δ%</span></div>
      <div class="kpi__thumbs">
        <div class="thumb thumb--down">
          <span class="thumb__icon" aria-hidden="true">👎</span>
          <strong>{fmt_int(rate_down)}</strong>
          <span class="was">{fmt_int(pr_rate.get('ThumbsDown', 0)) if prior else '—'}</span>
          {_row_pct_delta(rate_down, pr_rate.get('ThumbsDown') if prior else None)}
        </div>
        <div class="thumb thumb--up">
          <span class="thumb__icon" aria-hidden="true">👍</span>
          <strong>{fmt_int(rate_up)}</strong>
          <span class="was">{fmt_int(pr_rate.get('ThumbsUp', 0)) if prior else '—'}</span>
          {_row_pct_delta(rate_up, pr_rate.get('ThumbsUp') if prior else None)}
        </div>
      </div>
    </div>
  </div>
</section>"""


def render_dataset(manifest: Dict[str, Any]) -> str:
    sentiment = manifest.get("sentiment", {}) or {}
    languages = manifest.get("languages", {}) or {}
    clients = manifest.get("clients", {}) or {}
    total = manifest.get("total_items", 0) or 1

    SENTIMENT_LABEL_REMAP = {
        "Unscored (structured-only, no verbatim)": "No verbatim (unscored)",
    }

    def rows_for(d: Dict[str, int], top: Optional[int] = None, label_remap: Optional[Dict[str, str]] = None) -> str:
        items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        if not items:
            return '<tr><td colspan="3" class="dim">no data</td></tr>'
        truncated = False
        if top is not None and len(items) > top:
            remaining = sum(v for _, v in items[top:])
            remaining_count = len(items) - top
            items = items[:top]
            truncated = True
        body = "".join(
            f'<tr><td>{esc((label_remap or {}).get(k, k))}</td><td class="num">{fmt_int(v)}</td>'
            f'<td class="num dim">{fmt_pct(v / total * 100.0, 1)}</td></tr>'
            for k, v in items
        )
        if truncated:
            body += (
                f'<tr class="micro-table__more"><td class="dim">+ {remaining_count} more</td>'
                f'<td class="num dim">{fmt_int(remaining)}</td>'
                f'<td class="num dim">{fmt_pct(remaining / total * 100.0, 1)}</td></tr>'
            )
        return body

    return f"""<section id="dataset">
  <p class="section-label">03 · Dataset summary</p>
  <h2>What was sampled</h2>
  <div class="dataset-grid dataset-grid--three">
    <div class="dataset-card">
      <h4>Sentiment</h4>
      <table class="micro-table">{rows_for(sentiment, label_remap=SENTIMENT_LABEL_REMAP)}</table>
    </div>
    <div class="dataset-card">
      <h4>Language <span class="dataset-card__hint">top 4</span></h4>
      <table class="micro-table">{rows_for(languages, top=4)}</table>
    </div>
    <div class="dataset-card">
      <h4>Client</h4>
      <table class="micro-table">{rows_for(clients)}</table>
    </div>
  </div>
</section>"""


def render_findings(findings: List[str]) -> str:
    if not findings:
        # Hide entirely; the TL;DR already covers high-level signal.
        return ""
    cards = []
    for i, f in enumerate(findings, start=1):
        cards.append(f"""
        <div class="finding-card">
          <div class="finding-card__num">{i:02d}</div>
          <div class="finding-card__body">{markdown_lite(f)}</div>
        </div>
        """)
    return f"""<section id="findings">
  <p class="section-label">04 · Key findings</p>
  <h2>What the data is telling us</h2>
  <div class="finding-grid">
    {''.join(cards)}
  </div>
</section>"""


def render_topic_shifts(manifest: Dict[str, Any], prior: Optional[Dict[str, Any]]) -> str:
    """Merged topic ranking + WoW table.
    Sorted by this-week count (descending). Each row carries volume, share,
    a colored distribution bar, last-week values, and the Δ pp pill.
    Gracefully degrades when no prior manifest is available (Δ column shown as —).
    """
    cur_neg = manifest.get("negative_items", 0)
    cur_counts = manifest.get("topic_counts", {}) or {}
    if not cur_counts:
        return ""

    pr_neg = (prior or {}).get("negative_items", 0)
    pr_counts = (prior or {}).get("topic_counts", {}) or {}

    rows: List[Dict[str, Any]] = []
    for tid_str, cnt in cur_counts.items():
        try:
            tid = int(tid_str)
        except (TypeError, ValueError):
            continue
        cur_n = int(cnt or 0)
        pr_n = int(pr_counts.get(tid_str, 0) or 0)
        cur_p = (100.0 * cur_n / cur_neg) if cur_neg else 0.0
        pr_p = (100.0 * pr_n / pr_neg) if pr_neg else 0.0
        delta_pp = (cur_p - pr_p) if prior else None
        rows.append({
            "tid": tid,
            "name": TOPIC_NAMES.get(tid, f"Topic {tid}"),
            "cur_n": cur_n, "cur_p": cur_p,
            "pr_n": pr_n if prior else None,
            "pr_p": pr_p if prior else None,
            "delta_pp": delta_pp,
        })
    # Include topics absent from current week but present in prior
    if prior:
        for tid_str, pcnt in pr_counts.items():
            if tid_str in cur_counts:
                continue
            try:
                tid = int(tid_str)
            except (TypeError, ValueError):
                continue
            pr_n = int(pcnt or 0)
            pr_p = (100.0 * pr_n / pr_neg) if pr_neg else 0.0
            rows.append({
                "tid": tid,
                "name": TOPIC_NAMES.get(tid, f"Topic {tid}"),
                "cur_n": 0, "cur_p": 0.0,
                "pr_n": pr_n, "pr_p": pr_p,
                "delta_pp": -pr_p,
            })

    rows.sort(key=lambda r: (r["cur_n"], r["pr_n"] or 0), reverse=True)
    max_pct = max((r["cur_p"] for r in rows if r["cur_n"] > 0), default=1.0) or 1.0

    body = []
    for r in rows:
        if r["cur_n"] == 0 and (r["pr_n"] or 0) == 0:
            continue
        bar_w = (r["cur_p"] / max_pct) * 100.0 if r["cur_n"] > 0 else 0
        accent = TOPIC_ACCENT.get(r["tid"], "navy")
        bar_html = (f'<div class="bar bar--{accent}" style="width:{bar_w:.1f}%"></div>'
                    if r["cur_n"] > 0 else "")
        if r["pr_n"] is None:
            pr_cell = "—"
        else:
            pr_cell = f'{fmt_int(r["pr_n"])} · {fmt_pct(r["pr_p"])}'
        d_txt, d_cls = fmt_delta_pp(r["delta_pp"])
        body.append(f"""
        <tr>
          <td class="topic-id"><code>{r['tid']:02d}</code></td>
          <td class="topic-name">{esc(r['name'])}</td>
          <td class="num">{fmt_int(r['cur_n'])} · {fmt_pct(r['cur_p'])}</td>
          <td class="bar-cell">{bar_html}</td>
          <td class="num dim">{pr_cell}</td>
          <td class="num"><span class="delta delta--{d_cls}">{esc(d_txt)}</span></td>
        </tr>""")

    if prior:
        pr_label = prior.get("week", "prior")
        cur_label = manifest.get("week", "current")
        heading = (f"WoW shifts: <em>{esc(pr_label)}</em> → <em>{esc(cur_label)}</em>")
        note = (f"Sorted by this-week volume. Δ shown in percentage points (pp) "
                f"of share-of-negative. Prior baseline: n={fmt_int(pr_neg)} negative.")
    else:
        heading = "Where the negatives are concentrating"
        note = ("Sorted by this-week volume. No prior manifest available — "
                "pass <code>--prior-manifest</code> to populate the Δ pp column.")

    return f"""<section id="topics">
  <p class="section-label">05 · Topic shifts (week over week)</p>
  <h2>{heading}</h2>
  <p class="note">{note}</p>
  <table class="data-table topic-table">
    <thead>
      <tr>
        <th class="num">#</th>
        <th>Topic</th>
        <th class="num">This week</th>
        <th>Distribution</th>
        <th class="num">Last week</th>
        <th class="num">Δ pp</th>
      </tr>
    </thead>
    <tbody>{''.join(body)}</tbody>
  </table>
</section>"""


def render_topic_ranking(manifest: Dict[str, Any]) -> str:
    neg = manifest.get("negative_items", 0)
    topic_counts = manifest.get("topic_counts", {}) or {}
    shares = compute_topic_shares(topic_counts, neg)
    rows_sorted = sorted(shares.items(), key=lambda kv: kv[1][0], reverse=True)
    if not rows_sorted:
        return ""
    max_pct = max((p for _, (_, p) in rows_sorted), default=1.0) or 1.0
    rows_html = []
    for tid, (n, pct) in rows_sorted:
        bar_w = (pct / max_pct) * 100.0
        accent = TOPIC_ACCENT.get(tid, "navy")
        rows_html.append(f"""
        <tr>
          <td class="topic-id"><code>{tid:02d}</code></td>
          <td class="topic-name">{esc(TOPIC_NAMES.get(tid, f'Topic {tid}'))}</td>
          <td class="num">{fmt_int(n)}</td>
          <td class="num">{fmt_pct(pct)}</td>
          <td class="bar-cell">
            <div class="bar bar--{accent}" style="width:{bar_w:.1f}%"></div>
          </td>
        </tr>""")
    return f"""<section id="topics">
  <p class="section-label">05 · Primary topic ranking</p>
  <h2>Where the negatives are concentrating</h2>
  <table class="data-table topic-table">
    <thead>
      <tr>
        <th class="num">#</th>
        <th>Topic</th>
        <th class="num">Items</th>
        <th class="num">Share</th>
        <th>Distribution</th>
      </tr>
    </thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
</section>"""


def render_wow_table(manifest: Dict[str, Any], prior: Optional[Dict[str, Any]]) -> str:
    if not prior:
        return f"""<section id="wow">
  <p class="section-label">06 · Week over week</p>
  <h2>WoW comparison</h2>
  <p class="note">No prior manifest available for this area. Pass
  <code>--prior-manifest</code> or add an earlier manifest to <code>data/manifests/</code>
  to enable WoW deltas.</p>
</section>"""
    cur_neg = manifest.get("negative_items", 0)
    pr_neg = prior.get("negative_items", 0)
    cur_counts = manifest.get("topic_counts", {}) or {}
    pr_counts = prior.get("topic_counts", {}) or {}
    rows = compute_wow(cur_counts, cur_neg, pr_counts, pr_neg)
    rows_html = []
    for r in rows:
        d_txt, d_cls = fmt_delta_pp(r["delta_pp"])
        pr_cell = f'{fmt_int(r["pr_n"])} · {fmt_pct(r["pr_p"])}' if r["pr_n"] is not None else "—"
        rows_html.append(f"""
        <tr>
          <td class="topic-id"><code>{r['topic']:02d}</code></td>
          <td class="topic-name">{esc(r['topic_name'])}</td>
          <td class="num">{fmt_int(r['cur_n'])} · {fmt_pct(r['cur_p'])}</td>
          <td class="num dim">{pr_cell}</td>
          <td class="num"><span class="delta delta--{d_cls}">{esc(d_txt)}</span></td>
        </tr>""")
    pr_label = prior.get("week", "prior")
    cur_label = manifest.get("week", "current")
    return f"""<section id="wow">
  <p class="section-label">06 · Week over week</p>
  <h2>WoW shifts: <em>{esc(pr_label)}</em> → <em>{esc(cur_label)}</em></h2>
  <p class="note">Δ shown in percentage points (pp) of share-of-negative.
  Prior baseline: <code>{esc(prior.get('source_csv', ''))}</code>
  (n={fmt_int(pr_neg)} negative).</p>
  <table class="data-table wow-table">
    <thead>
      <tr>
        <th class="num">#</th>
        <th>Topic</th>
        <th class="num">This week</th>
        <th class="num">Last week</th>
        <th class="num">Δ pp</th>
      </tr>
    </thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
</section>"""


SLICE_COLORS = [
    "#8ab4f8",  # navy
    "#fdd663",  # gold
    "#f28b82",  # terracotta
    "#81c995",  # sage
    "#c58af9",  # plum
    "#aecbfa",  # navy-light
    "#fde293",  # gold-light
    "#5f6368",  # text-faint (used for "Other")
]


def render_routing_paths(manifest: Dict[str, Any]) -> str:
    paths = manifest.get("paths") or {}
    by_path = paths.get("by_path") or {}
    by_pxs = paths.get("by_path_x_surface") or {}
    unknown_missing = int(paths.get("unknown_or_missing") or 0)
    total_classified = sum(by_path.values())

    if total_classified == 0 and unknown_missing == 0:
        return ""

    # Sort paths by total count desc; unknown last
    path_order = sorted(by_path.keys(), key=lambda p: (-by_path[p], p.lower()))
    # Union of all surfaces seen, sorted by total negative count desc
    surface_totals: Dict[str, int] = {}
    for p, cats in by_pxs.items():
        for cat, n in cats.items():
            surface_totals[cat] = surface_totals.get(cat, 0) + n
    surfaces = sorted(surface_totals.keys(), key=lambda s: (-surface_totals[s], s.lower()))

    # Build table
    head = "<tr><th>Path</th>" + "".join(f"<th>{esc(s)}</th>" for s in surfaces) + "<th>Total</th></tr>"
    body_rows: List[str] = []
    for p in path_order:
        cells = []
        for s in surfaces:
            n = (by_pxs.get(p, {}) or {}).get(s, 0)
            css = ' class="cell--zero"' if n == 0 else ""
            cells.append(f"<td{css}>{n}</td>")
        body_rows.append(
            f'<tr><td><span class="path-pill">{esc(p)}</span></td>'
            + "".join(cells)
            + f"<td>{by_path[p]}</td></tr>"
        )

    # Totals row across surfaces
    total_row_cells = []
    for s in surfaces:
        total_row_cells.append(f"<td>{surface_totals[s]}</td>")
    body_rows.append(
        f'<tr class="path-row--total"><td>Total (classified)</td>'
        + "".join(total_row_cells)
        + f"<td>{total_classified}</td></tr>"
    )

    pct_classified = (100.0 * total_classified / (total_classified + unknown_missing)
                      if (total_classified + unknown_missing) else 0)
    note = (
        f"{total_classified} of {total_classified + unknown_missing} negative items had "
        f"path attribution available this week ({pct_classified:.0f}%). "
        f"{unknown_missing} item(s) had no Dash record."
    )

    return f"""<section id="routing">
  <p class="section-label">06b · Routing path</p>
  <h2>Which routing path is generating the most issues</h2>
  <p class="note">{note}</p>
  <table class="path-table">
    <thead>{head}</thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
</section>"""


def render_category_grid(manifest: Dict[str, Any], prior: Optional[Dict[str, Any]]) -> str:
    cat_counts = manifest.get("category_counts", {}) or {}
    pr_cat_counts = (prior or {}).get("category_counts", {}) or {}
    neg = manifest.get("negative_items", 0) or 1
    pr_neg = (prior or {}).get("negative_items", 0) or 1

    items_all = sorted(cat_counts.items(), key=lambda kv: kv[1], reverse=True)
    if not items_all:
        return ""

    # Group anything past top 6 into "Other" so the chart stays readable.
    TOP_N = 6
    other_categories: List[str] = []
    if len(items_all) > TOP_N + 1:
        top_items = items_all[:TOP_N]
        other_count = sum(n for _, n in items_all[TOP_N:])
        other_categories = [name for name, _ in items_all[TOP_N:]]
        items = top_items + [("Other", other_count)]
    else:
        items = items_all

    # SVG geometry: viewBox is wider than tall so labels have horizontal room.
    cx, cy = 350.0, 220.0
    r_stroke_center = 105.0
    stroke_w = 50.0
    r_outer = r_stroke_center + stroke_w / 2  # 130
    circumference = 2.0 * math.pi * r_stroke_center

    slices_svg: List[str] = []
    callouts: List[Tuple[float, str]] = []  # (label_y, svg) for vertical sort
    cumulative = 0.0

    # Pre-compute slice geometry so we can rebalance label positions.
    slice_data = []
    for i, (cat, n) in enumerate(items):
        share = n / neg
        color = SLICE_COLORS[i % len(SLICE_COLORS)] if cat != "Other" else SLICE_COLORS[-1]
        slice_data.append({"cat": cat, "n": n, "share": share, "color": color,
                           "start": cumulative, "mid": cumulative + share / 2.0})
        cumulative += share

    # Build slice strokes.
    for sd in slice_data:
        arc_len = sd["share"] * circumference
        dash = f'{arc_len:.2f} {circumference - arc_len:.2f}'
        offset = -sd["start"] * circumference
        slices_svg.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r_stroke_center}" fill="none" '
            f'stroke="{sd["color"]}" stroke-width="{stroke_w}" '
            f'stroke-dasharray="{dash}" stroke-dashoffset="{offset:.2f}" '
            f'transform="rotate(-90 {cx} {cy})" />'
        )

    # Resolve label positions; nudge to prevent vertical overlap (min 38px gap).
    # Separate into left and right groups by slice mid-angle.
    LABEL_GAP = 38.0
    left_labels: List[Dict[str, Any]] = []
    right_labels: List[Dict[str, Any]] = []
    for sd in slice_data:
        angle = sd["mid"] * 2.0 * math.pi - math.pi / 2.0
        anchor_x = cx + r_outer * math.cos(angle)
        anchor_y = cy + r_outer * math.sin(angle)
        bend_x = cx + (r_outer + 18.0) * math.cos(angle)
        bend_y = cy + (r_outer + 18.0) * math.sin(angle)
        is_right = math.cos(angle) >= 0
        record = {
            "sd": sd, "angle": angle,
            "anchor_x": anchor_x, "anchor_y": anchor_y,
            "bend_x": bend_x, "bend_y": bend_y,
            "label_y": bend_y, "is_right": is_right,
        }
        (right_labels if is_right else left_labels).append(record)

    def _resolve_overlap(group: List[Dict[str, Any]]) -> None:
        if not group:
            return
        # Sort top-to-bottom by current label_y.
        group.sort(key=lambda r: r["label_y"])
        for i in range(1, len(group)):
            min_y = group[i - 1]["label_y"] + LABEL_GAP
            if group[i]["label_y"] < min_y:
                group[i]["label_y"] = min_y

    _resolve_overlap(left_labels)
    _resolve_overlap(right_labels)

    callout_svg: List[str] = []
    for rec in left_labels + right_labels:
        sd = rec["sd"]
        cat = sd["cat"]
        n = sd["n"]
        cur_share_pct = sd["share"] * 100.0
        # pp delta vs prior
        if cat == "Other":
            pr_n = sum(pr_cat_counts.get(c, 0) for c in other_categories) if other_categories else 0
        else:
            pr_n = pr_cat_counts.get(cat)
        if pr_n and pr_neg:
            pr_share = pr_n / pr_neg * 100.0
            d_txt, d_cls = fmt_delta_pp(cur_share_pct - pr_share)
        else:
            d_txt, d_cls = ("new", "up") if (prior and (pr_n or 0) == 0) else ("—", "none")

        is_right = rec["is_right"]
        label_x_end = (cx + 220.0) if is_right else (cx - 220.0)
        text_anchor = "start" if is_right else "end"
        text_x = (label_x_end - 4.0) if is_right else (label_x_end + 4.0)
        text_x = text_x  # alias for clarity
        # label x for text positioning relative to horizontal line end
        line_h_end_x = label_x_end
        # bend_y is the original; label_y is the adjusted y for the label/horizontal line.
        bend_y = rec["bend_y"]
        label_y = rec["label_y"]

        callout_svg.append(f"""
        <polyline points="{rec['anchor_x']:.1f},{rec['anchor_y']:.1f} {rec['bend_x']:.1f},{bend_y:.1f} {rec['bend_x']:.1f},{label_y:.1f} {line_h_end_x:.1f},{label_y:.1f}"
          fill="none" stroke="var(--border-strong)" stroke-width="1" />
        <text x="{text_x:.1f}" y="{label_y - 4.0:.1f}" text-anchor="{text_anchor}" class="callout__name">{esc(cat)} <tspan class="callout__count">{fmt_int(n)}</tspan></text>
        <text x="{text_x:.1f}" y="{label_y + 12.0:.1f}" text-anchor="{text_anchor}" class="callout__delta callout__delta--{d_cls}">{esc(d_txt)}{'' if d_txt in ('—', 'new') else ' vs last week'}</text>
        """)

    center_svg = f"""
    <text x="{cx}" y="{cy - 8.0}" text-anchor="middle" class="donut-center__label">Negatives</text>
    <text x="{cx}" y="{cy + 22.0}" text-anchor="middle" class="donut-center__value">{fmt_int(int(neg))}</text>
    """

    svg = f"""<svg viewBox="0 0 700 460" preserveAspectRatio="xMidYMid meet" class="cat-chart" role="img" aria-label="Category breakdown donut chart">
      <g>{''.join(slices_svg)}</g>
      <g>{center_svg}</g>
      <g>{''.join(callout_svg)}</g>
    </svg>"""

    return f"""<section id="categories">
  <p class="section-label">07 · Category breakdown</p>
  <h2>Which surfaces are bearing the load</h2>
  <p class="note">Slice size = share of this week's negative items. Callouts show the WoW change in share (percentage points) vs the prior manifest.</p>
  <div class="cat-chart-wrap">{svg}</div>
</section>"""


def render_ticket_queue(subtopic_rows: List[Dict[str, Any]]) -> str:
    if not subtopic_rows:
        return f"""<section id="tickets">
  <p class="section-label">08 · Ticket queue</p>
  <h2>P0 / P1 / P2 rows</h2>
  <p class="note">No subtopics CSV provided. Pass <code>--subtopics
  data/ocv_&lt;area&gt;_&lt;week&gt;_subtopics.csv</code> to render the ticket cards.</p>
</section>"""

    # Filter & sort P0/P1/P2; cap at 12.
    keep = [r for r in subtopic_rows if r["_priority"] in ("P0", "P1", "P2")]
    keep.sort(key=lambda r: (PRIORITY_RANK[r["_priority"]], -r["_count"]))
    keep = keep[:12]

    if not keep:
        return f"""<section id="tickets">
  <p class="section-label">08 · Ticket queue</p>
  <h2>P0 / P1 / P2 rows</h2>
  <p class="note">No P0/P1/P2 rows in this week's subtopics.</p>
</section>"""

    cards = []
    for r in keep:
        prio = r["_priority"]
        prio_cls = prio.lower()
        title = r.get("Brief title") or r.get("Brief Title") or r.get("Issue description") or "Untitled"
        topic = r.get("Parent Topic", "")
        category = r.get("Category", "")
        submode = r.get("SubMode", "")
        count = r["_count"]
        desc = r.get("Issue description") or r.get("Issue Description") or ""
        # OCV links field is sometimes "OCV links" (lowercase) or "OCV Item Links"
        raw_links = r.get("OCV links") or r.get("OCV Item Links") or ""
        links = [l.strip() for l in re.split(r"[|;,\s]+", raw_links) if l.strip().startswith("http")]
        links_html = "".join(
            f'<a href="{esc(l)}" target="_blank" rel="noopener">item {i+1}</a>'
            for i, l in enumerate(links[:5])
        ) or '<span class="dim">no links</span>'

        raw_dash = r.get("Dash links") or r.get("Dash Links") or ""
        # Parallel-aligned arrays from ado_sync write-back (separator: " | ")
        dash_links = [l.strip() for l in re.split(r"\s*\|\s*", raw_dash) if l.strip().startswith("http")]
        raw_paths = r.get("Path") or ""
        path_per_dash = [p.strip() for p in re.split(r"\s*\|\s*", raw_paths)]
        raw_models = r.get("Resolved Models") or ""
        models_per_dash = [m.strip() for m in re.split(r"\s*\|\s*", raw_models)]

        def _model_chip(i: int) -> str:
            slug = path_per_dash[i] if i < len(path_per_dash) else ""
            mraw = models_per_dash[i] if i < len(models_per_dash) else ""
            # within a single Dash cell, models are ";"-joined
            models = ", ".join(m for m in (mraw.split(";") if mraw else []) if m)
            label = ""
            if slug and models:
                label = f"{slug}: {models}"
            elif slug:
                label = slug
            elif models:
                label = models
            if not label:
                return ""
            return f' <span class="model-tag" title="diagnosed path + resolved models">[{esc(label)}]</span>'

        if dash_links:
            dash_html = "".join(
                f'<a href="{esc(l)}" target="_blank" rel="noopener">dash {i+1}</a>{_model_chip(i)}'
                for i, l in enumerate(dash_links[:5])
            )
            dash_row_html = (f'<div class="ticket-card__links">'
                             f'<span class="ticket-card__links-label">Dash:</span> '
                             f'{dash_html}</div>')
        else:
            dash_row_html = ""

        submode_html = f' · <code>{esc(submode)}</code>' if submode else ""

        ado_url = (r.get("ADO URL") or r.get("ADO Url") or "").strip()
        ado_action = (r.get("ADO action") or "").strip().lower()
        if ado_url:
            ado_label = {
                "created": "ADO (new)",
                "linked": "ADO (linked)",
            }.get(ado_action, "ADO")
            ado_html = (
                f'<a class="ado-link ado-link--{esc(ado_action) or "open"}" '
                f'href="{esc(ado_url)}" target="_blank" rel="noopener">'
                f'{esc(ado_label)} <span class="ado-link__arrow">↗</span></a>'
            )
        else:
            ado_html = '<span class="ado-link ado-link--missing">no ADO item</span>'

        cards.append(f"""
        <details class="ticket-card ticket-card--{prio_cls}">
          <summary class="ticket-card__head">
            <span class="ticket-card__prio prio--{prio_cls}">{esc(prio)}</span>
            <span class="ticket-card__title">{esc(title)}</span>
            <span class="ticket-card__count">×{esc(count)}</span>
          </summary>
          <div class="ticket-card__body">
            <div class="ticket-card__meta">
              <span><strong>Topic:</strong> {esc(topic)}</span>
              <span><strong>Surface:</strong> {esc(category)}{submode_html}</span>
              {ado_html}
            </div>
            <p class="ticket-card__desc">{esc(desc)}</p>
            <div class="ticket-card__links">
              <span class="ticket-card__links-label">OCV items:</span> {links_html}
            </div>
            {dash_row_html}
          </div>
        </details>
        """)

    return f"""<section id="tickets">
  <p class="section-label">08 · Ticket queue</p>
  <h2>P0 / P1 / P2 rows worth engineering attention</h2>
  <p class="note">Showing top {len(keep)} rows by priority then prevalence. Click any row to expand.</p>
  <div class="ticket-list">{''.join(cards)}</div>
</section>"""


# ---------------------------------------------------------------------------
# CSS + page shell
# ---------------------------------------------------------------------------

CSS = r"""
:root {
  --bg: #0f1115;
  --bg-tint: #14171d;
  --surface: #1a1d24;
  --surface-elevated: #20242c;
  --surface-recessed: #14171d;
  --text: #e8eaed;
  --text-dim: #9aa0a6;
  --text-faint: #5f6368;
  --border: rgba(232, 234, 237, 0.08);
  --border-strong: rgba(232, 234, 237, 0.16);

  /* Accent palette: muted navy + gold + terracotta, dark-mode tuned. */
  --navy: #8ab4f8;
  --navy-dim: rgba(138, 180, 248, 0.14);
  --gold: #fdd663;
  --gold-dim: rgba(253, 214, 99, 0.14);
  --terracotta: #f28b82;
  --terracotta-dim: rgba(242, 139, 130, 0.14);
  --sage: #81c995;
  --sage-dim: rgba(129, 201, 149, 0.14);
  --plum: #c58af9;
  --plum-dim: rgba(197, 138, 249, 0.14);

  --p0: #f28b82;
  --p0-bg: rgba(242, 139, 130, 0.14);
  --p1: #fdd663;
  --p1-bg: rgba(253, 214, 99, 0.14);
  --p2: #8ab4f8;
  --p2-bg: rgba(138, 180, 248, 0.14);
  --p3: #9aa0a6;
  --p3-bg: rgba(154, 160, 166, 0.12);

  --up: #f28b82;
  --down: #81c995;
  --flat: #9aa0a6;

  /* Google's public-site font stack: Google Sans Display / Text / Code. */
  --font-display: 'Google Sans Display', 'Google Sans', 'Roboto', system-ui, -apple-system, sans-serif;
  --font-body: 'Google Sans Text', 'Google Sans', 'Roboto', system-ui, -apple-system, sans-serif;
  --font-mono: 'Google Sans Code', 'Roboto Mono', ui-monospace, 'JetBrains Mono', monospace;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  background-image:
    radial-gradient(ellipse 900px 600px at 80% -10%, var(--gold-dim), transparent 60%),
    radial-gradient(ellipse 700px 500px at -10% 20%, var(--navy-dim), transparent 60%);
  background-attachment: fixed;
}

a { color: var(--navy); text-decoration: none; border-bottom: 1px solid transparent; transition: border-color 0.12s; }
a:hover { border-bottom-color: currentColor; }
code { font-family: var(--font-mono); font-size: 0.9em; background: var(--surface-recessed); padding: 1px 6px; border-radius: 4px; color: var(--text); }
em { font-style: italic; color: var(--navy); }

/* ============== LAYOUT ============== */
.page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 56px 40px 96px;
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 56px;
}
@media (max-width: 900px) {
  .page { grid-template-columns: 1fr; gap: 24px; padding: 32px 20px; }
  .toc { position: static; }
}

/* ============== SIDEBAR TOC ============== */
.toc {
  position: sticky;
  top: 32px;
  align-self: start;
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.toc__label { color: var(--text-faint); margin: 0 0 12px; font-weight: 500; }
.toc__list {
  list-style: none; margin: 0; padding: 0;
  border-left: 1px solid var(--border-strong);
}
.toc__list a {
  display: block;
  padding: 7px 0 7px 16px;
  margin-left: -1px;
  border-left: 2px solid transparent;
  color: var(--text-dim);
  border-bottom: none;
  transition: color 0.15s, border-color 0.15s;
}
.toc__list a:hover { color: var(--text); }
.toc__list a.active {
  color: var(--gold);
  border-left-color: var(--gold);
  font-weight: 600;
}

/* ============== HEADER ============== */
header { border-bottom: 1px solid var(--border); padding-bottom: 28px; margin-bottom: 36px; }
.back-link {
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-dim);
  text-decoration: none;
  border-bottom: 1px solid transparent;
  display: inline-block;
  margin: 0 0 22px;
  transition: color .15s, border-color .15s;
}
.back-link:hover { color: var(--text); border-bottom-color: var(--border-strong); }
.eyebrow {
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  color: var(--gold);
  margin: 0 0 14px;
  display: flex; align-items: center; gap: 12px;
}
.eyebrow em { color: var(--gold); font-style: normal; font-weight: 600; }
.eyebrow::before {
  content: ''; width: 24px; height: 1px; background: var(--gold); display: inline-block;
}
h1 {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: clamp(40px, 5vw, 56px);
  line-height: 1.05;
  letter-spacing: -0.015em;
  margin: 0 0 18px;
  color: var(--text);
}
h1 em { color: var(--navy); font-style: normal; font-weight: 500; }
.header__meta {
  display: flex; gap: 28px; flex-wrap: wrap;
  font-family: var(--font-mono); font-size: 12px; color: var(--text-dim);
}
.header__meta span { display: inline-flex; align-items: center; gap: 6px; }
.header__meta strong { color: var(--text); font-weight: 500; }

/* ============== SECTIONS ============== */
section { margin-bottom: 64px; scroll-margin-top: 24px; }
.section-label {
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  color: var(--text-faint);
  margin: 0 0 8px;
  display: flex; align-items: center; gap: 10px;
}
.section-label::before {
  content: ''; width: 6px; height: 6px; border-radius: 50%;
  background: var(--navy); display: inline-block;
}
h2 {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 32px;
  line-height: 1.15;
  margin: 0 0 24px;
  color: var(--text);
  letter-spacing: -0.012em;
}
h2 em { color: var(--gold); font-style: normal; font-weight: 500; }
h3 {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 20px;
  margin: 32px 0 14px;
  color: var(--text);
}
h4 {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  margin: 0 0 12px;
}
p { margin: 0 0 14px; }
p.note {
  font-size: 13px; color: var(--text-dim); margin-top: -8px; margin-bottom: 20px;
}

/* ============== TL;DR HERO ============== */
.tldr {
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 36px 40px;
  position: relative;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.25);
}
.tldr::before {
  content: ''; position: absolute;
  left: 0; top: 24px; bottom: 24px;
  width: 3px; background: var(--gold); border-radius: 2px;
}
.tldr__lead {
  font-family: var(--font-display);
  font-size: 22px; line-height: 1.4; font-weight: 400;
  color: var(--text); margin: 0 0 16px;
}
.tldr__lead em { color: var(--gold); font-style: normal; font-weight: 500; }
.tldr__body p { font-size: 15px; color: var(--text); }
.tldr__hint { font-size: 13px; color: var(--text-dim); font-style: italic; }
.tldr__caveats {
  margin-top: 24px; padding-top: 20px;
  border-top: 1px dashed var(--border-strong);
}
.tldr__caveats h4 { color: var(--terracotta); }
.tldr__caveats ul {
  margin: 0; padding-left: 18px; font-size: 13px; color: var(--text-dim);
}
.tldr__caveats li { margin-bottom: 6px; }
.tldr__method {
  margin-top: 18px; font-size: 13px; color: var(--text-dim);
}
.tldr__method summary {
  cursor: pointer; font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faint);
  padding: 4px 0;
}
.tldr__method ul { margin: 10px 0 0; padding-left: 18px; }
.tldr__method li { margin-bottom: 6px; }

/* ============== KPI GRID ============== */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.kpi-grid--three { grid-template-columns: 1.4fr 1fr 1.4fr; }
@media (max-width: 900px) { .kpi-grid, .kpi-grid--three { grid-template-columns: repeat(2, 1fr); } }
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px 22px;
  min-width: 0;
}
.kpi--hero {
  background: var(--surface-elevated);
  border-color: var(--navy);
  box-shadow: 0 0 0 1px var(--navy-dim);
}
.kpi--p0 { border-color: rgba(242, 139, 130, 0.35); }
.kpi__label {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--text-faint);
  margin-bottom: 12px;
}
.kpi__value {
  font-family: var(--font-display);
  font-size: 48px;
  font-weight: 500;
  line-height: 1;
  color: var(--text);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.kpi--hero .kpi__value { color: var(--navy); }
.kpi--p0 .kpi__value { color: var(--p0); }
.kpi__divider { color: var(--text-faint); font-weight: 400; padding: 0 2px; }
.kpi__meta {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 12px;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.kpi__meta strong { color: var(--text); font-weight: 500; }

/* "was X last week" subtitle on every KPI card */
.kpi__was {
  display: flex; align-items: baseline; gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  flex-wrap: wrap;
}
.kpi__was-label {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-faint);
}
.kpi__was-value {
  color: var(--text);
  font-size: 14px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.kpi__was--none { color: var(--text-faint); font-style: italic; }
.kpi__sublabel {
  font-weight: 400;
  text-transform: uppercase;
  color: var(--text-faint);
  letter-spacing: 0.08em;
  margin-left: 6px;
}

/* breakdown variant (sentiment / rating) */
.kpi--breakdown { padding-top: 22px; padding-bottom: 22px; }
.kpi__rows {
  display: flex; flex-direction: column; gap: 2px;
  margin-top: 4px;
}
.kpi__row {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
  gap: 12px;
}
.kpi__row:last-child { border-bottom: none; }
.kpi__row > span:first-child {
  font-family: var(--font-mono); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-dim);
}
.kpi__row-vals {
  display: inline-flex; align-items: baseline; gap: 8px;
  font-variant-numeric: tabular-nums;
}
.kpi__row-vals strong {
  font-family: var(--font-display);
  font-size: 20px; font-weight: 500;
  color: var(--text);
  line-height: 1;
}
.kpi__row--neg .kpi__row-vals strong { color: var(--terracotta); }
.was {
  font-family: var(--font-mono); font-size: 11px;
  color: var(--text-faint);
}
.row-delta {
  font-family: var(--font-mono); font-size: 10px;
  padding: 1px 5px; border-radius: 3px;
  font-variant-numeric: tabular-nums;
  color: var(--text-dim);
  background: var(--surface-recessed);
}
.row-delta--up::before, .row-delta--down::before, .row-delta--flat::before { content: ''; }
.row-delta--flat { color: var(--text-faint); }

.kpi__thumbs {
  display: flex; flex-direction: column; gap: 14px;
  margin-top: 6px;
}
.thumb {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--font-display);
  flex-wrap: wrap;
}
.thumb__icon { font-size: 22px; line-height: 1; }
.thumb strong {
  font-size: 24px; font-weight: 500;
  font-variant-numeric: tabular-nums; color: var(--text);
  line-height: 1;
}
.thumb--down strong { color: var(--terracotta); }
.thumb--up strong { color: var(--sage); }

/* ============== DELTA INDICATORS ============== */
.delta {
  display: inline-flex; align-items: center; gap: 5px;
  font-family: var(--font-mono); font-size: 13px; font-weight: 700;
  padding: 4px 10px; border-radius: 5px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.3px;
}
.delta--up { color: var(--up); background: rgba(242, 139, 130, 0.18); }
.delta--up::before { content: '▲'; font-size: 11px; }
.delta--down { color: var(--down); background: rgba(129, 201, 149, 0.18); }
.delta--down::before { content: '▼'; font-size: 11px; }
.delta--flat { color: var(--flat); background: rgba(154, 160, 166, 0.12); }
.delta--flat::before { content: '●'; font-size: 9px; }
.delta--none { color: var(--text-faint); }

/* ============== DATASET GRID ============== */
.dataset-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.dataset-grid--two { grid-template-columns: repeat(2, 1fr); }
.dataset-grid--three { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { .dataset-grid, .dataset-grid--two, .dataset-grid--three { grid-template-columns: repeat(2, 1fr); } }
.dataset-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  min-width: 0;
}
.micro-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.micro-table td {
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
.micro-table tr:last-child td { border-bottom: none; }
.micro-table td.num {
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text);
}
.micro-table td.dim { color: var(--text-faint); font-size: 12px; }
.micro-table__more td { font-family: var(--font-mono); font-size: 11px; padding-top: 8px; }
.dataset-card__hint {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: none;
  letter-spacing: 0.04em;
  color: var(--text-faint);
  margin-left: 6px;
  font-weight: 400;
}

/* ============== CATEGORY DONUT ============== */
.cat-chart-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px 16px;
  display: flex;
  justify-content: center;
}
.cat-chart { width: 100%; max-width: 720px; height: auto; }
.donut-center__label {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.12em;
}
.donut-center__value {
  font-family: var(--font-display);
  font-size: 40px;
  font-weight: 500;
  fill: var(--text);
}
.callout__name {
  font-family: var(--font-display);
  font-size: 13px;
  fill: var(--text);
  font-weight: 500;
}
.callout__count {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--text-faint);
  font-weight: 400;
}
.callout__delta {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--text-dim);
}
.callout__delta--up { fill: var(--terracotta); }
.callout__delta--down { fill: var(--sage); }
.callout__delta--flat { fill: var(--text-dim); }
.callout__delta--none { fill: var(--text-faint); }

/* ============== FINDINGS ============== */
.finding-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}
@media (max-width: 900px) { .finding-grid { grid-template-columns: 1fr; } }
.finding-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px;
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 16px;
  min-width: 0;
}
.finding-card__num {
  font-family: var(--font-mono); font-size: 12px;
  color: var(--gold);
  letter-spacing: 0.05em;
  padding-top: 4px;
}
.finding-card__body { font-size: 14px; color: var(--text); line-height: 1.55; }
.finding-card__body p:last-child { margin-bottom: 0; }

/* ============== TABLES (topic / wow) ============== */
.data-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 14px;
}
.data-table thead th {
  text-align: left;
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-faint);
  font-weight: 500;
  padding: 14px 16px;
  background: var(--surface-recessed);
  border-bottom: 1px solid var(--border-strong);
}
.data-table thead th.num { text-align: right; }
.data-table tbody td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--surface-elevated); }
.data-table td.num {
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.data-table td.dim { color: var(--text-dim); }
.topic-id code { background: transparent; color: var(--text-faint); padding: 0; }
.topic-name { color: var(--text); font-weight: 500; }
.bar-cell { width: 18%; }
.bar {
  height: 6px;
  border-radius: 3px;
  background: var(--navy);
  min-width: 2px;
}
.bar--red { background: var(--terracotta); }
.bar--amber { background: var(--gold); }
.bar--navy { background: var(--navy); }
.bar--green { background: var(--sage); }

/* ============== CATEGORY GRID ============== */
.cat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
@media (max-width: 900px) { .cat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px) { .cat-grid { grid-template-columns: 1fr; } }
.cat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px;
  min-width: 0;
}
.cat-card__head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px;
}
.cat-card__name {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 500;
  color: var(--text);
}
.cat-card__count {
  display: flex; align-items: baseline; gap: 8px;
  margin-bottom: 10px;
}
.cat-card__n {
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 500;
  color: var(--gold);
  font-variant-numeric: tabular-nums;
}
.cat-card__pct { font-family: var(--font-mono); font-size: 12px; color: var(--text-dim); }
.cat-card__tops {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.cat-card__tops code { font-size: 10px; padding: 1px 4px; }
.cat-card__tops .dim { color: var(--text-faint); }

/* ============== TICKET CARDS ============== */
.ticket-list { display: flex; flex-direction: column; gap: 10px; }
.ticket-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-faint);
  border-radius: 6px;
  overflow: hidden;
}
.ticket-card--p0 { border-left-color: var(--p0); }
.ticket-card--p1 { border-left-color: var(--p1); }
.ticket-card--p2 { border-left-color: var(--p2); }
.ticket-card__head {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 18px;
  cursor: pointer;
  list-style: none;
}
.ticket-card__head::-webkit-details-marker { display: none; }
.ticket-card__head::after {
  content: '+'; margin-left: auto;
  color: var(--text-faint); font-family: var(--font-mono); font-size: 16px;
}
.ticket-card[open] .ticket-card__head::after { content: '−'; }
.ticket-card__prio {
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  padding: 3px 8px; border-radius: 4px; min-width: 28px; text-align: center;
}
.prio--p0 { color: var(--p0); background: var(--p0-bg); }
.prio--p1 { color: var(--p1); background: var(--p1-bg); }
.prio--p2 { color: var(--p2); background: var(--p2-bg); }
.prio--p3 { color: var(--p3); background: var(--p3-bg); }
.ticket-card__title {
  font-family: var(--font-display);
  font-size: 15px; color: var(--text); font-weight: 500;
  flex: 1; min-width: 0;
}
.ticket-card__count {
  font-family: var(--font-mono); font-size: 12px; color: var(--text-dim);
  background: var(--surface-recessed); padding: 2px 8px; border-radius: 4px;
}
.ticket-card__body {
  padding: 4px 18px 18px;
  border-top: 1px solid var(--border);
}
.ticket-card__meta {
  display: flex; flex-wrap: wrap; gap: 16px;
  align-items: center;
  font-size: 12px; color: var(--text-dim);
  padding: 12px 0;
}
.ticket-card__meta strong { color: var(--text); font-weight: 500; }
.tw { font-family: var(--font-mono); font-size: 11px; padding: 1px 6px; border-radius: 3px; }
.tw--yes { color: var(--sage); background: rgba(129, 201, 149, 0.12); }
.tw--no { color: var(--text-faint); background: rgba(154, 160, 166, 0.08); }
.tw--needs { color: var(--gold); background: var(--gold-dim); }
.ticket-card__desc {
  font-size: 14px; color: var(--text);
  line-height: 1.55; margin: 6px 0 12px;
}
.ticket-card__links {
  font-size: 12px; color: var(--text-dim);
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.ticket-card__links-label {
  font-family: var(--font-mono); font-size: 10px;
  text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-faint);
}
.ticket-card__links a {
  font-family: var(--font-mono); font-size: 11px;
  padding: 2px 8px; border-radius: 3px;
  background: var(--surface-recessed); color: var(--navy);
}
.ticket-card__links .dim { color: var(--text-faint); font-style: italic; }
.model-tag {
  font-family: var(--font-mono); font-size: 10px;
  color: var(--text-faint);
  padding: 1px 6px;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: rgba(255,255,255,0.03);
  white-space: nowrap;
}

/* Routing path × surface table */
.path-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono); font-size: 12px;
  margin-top: 12px;
}
.path-table th, .path-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  text-align: right;
  color: var(--text-dim);
}
.path-table th {
  color: var(--text-faint);
  font-weight: normal;
  text-transform: uppercase; letter-spacing: 0.08em;
  font-size: 10px;
  background: var(--surface-recessed);
}
.path-table td:first-child, .path-table th:first-child {
  text-align: left;
  color: var(--text);
}
.path-table tr.path-row--total td { color: var(--text); border-top: 1px solid var(--border); font-weight: 600; }
.path-table td.cell--zero { color: var(--text-faint); opacity: 0.5; }
.path-table .path-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 10px;
  background: var(--surface-recessed);
  color: var(--navy);
}

/* ADO link badge in ticket card head */
.ado-link {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 3px 9px;
  border-radius: 12px;
  border: 1px solid var(--border);
  text-decoration: none;
  white-space: nowrap;
  margin-left: auto;
}
.ado-link--created {
  color: var(--sage);
  border-color: rgba(129, 201, 149, 0.4);
  background: rgba(129, 201, 149, 0.08);
}
.ado-link--linked {
  color: var(--navy);
  border-color: rgba(138, 180, 248, 0.4);
  background: rgba(138, 180, 248, 0.08);
}
.ado-link--open {
  color: var(--text);
  background: var(--surface-recessed);
}
.ado-link--missing {
  color: var(--text-faint);
  background: transparent;
  border-style: dashed;
  font-style: italic;
}
.ado-link__arrow { opacity: 0.7; margin-left: 2px; }
.ado-link:hover:not(.ado-link--missing) {
  filter: brightness(1.15);
  text-decoration: none;
}

/* ============== FOOTER ============== */
footer {
  margin-top: 80px; padding-top: 28px;
  border-top: 1px solid var(--border);
  font-family: var(--font-mono); font-size: 11px;
  color: var(--text-faint);
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 16px;
}
footer a { color: var(--text-dim); }
"""

JS = r"""
(function () {
  const links = document.querySelectorAll('.toc__list a');
  const sections = Array.from(links).map(a => document.querySelector(a.getAttribute('href')));
  const observer = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        const id = '#' + e.target.id;
        links.forEach(l => l.classList.toggle('active', l.getAttribute('href') === id));
      }
    });
  }, { rootMargin: '-30% 0px -65% 0px', threshold: 0 });
  sections.forEach(s => s && observer.observe(s));
})();
"""


def render_page(manifest: Dict[str, Any], prior: Optional[Dict[str, Any]],
                subtopic_rows: List[Dict[str, Any]], report: Dict[str, Any],
                area_label: str) -> str:
    # TL;DR uses the report's TL;DR if available; else synthesized in render_tldr.
    tldr_html_block = ""
    if report.get("tldr"):
        tldr_html_block = markdown_lite(report["tldr"])
    caveats = manifest.get("caveats", []) or []
    methodology = manifest.get("methodology_notes", []) or []

    toc_items = [
        ("tldr", "01 · TL;DR"),
        ("kpis", "02 · Headlines"),
        ("dataset", "03 · Dataset"),
        ("findings", "04 · Findings"),
        ("topics", "05 · Topic shifts"),
        ("categories", "06 · Categories"),
        ("routing", "06b · Routing path"),
        ("tickets", "07 · Ticket queue"),
    ]
    if not report.get("findings"):
        toc_items = [t for t in toc_items if t[0] != "findings"]
    if not (manifest.get("paths") or {}).get("by_path"):
        toc_items = [t for t in toc_items if t[0] != "routing"]

    toc_html = "\n".join(
        f'      <li><a href="#{sid}">{esc(label)}</a></li>'
        for sid, label in toc_items
    )

    week = manifest.get("week", "")
    date_range = manifest.get("date_range", week)
    title = f"OCV Weekly · {area_label} · {date_range}"

    body_sections = [
        render_header(manifest, area_label),
        render_tldr(manifest, tldr_html_block, caveats, methodology),
        render_kpi_grid(manifest, prior, subtopic_rows),
        render_dataset(manifest),
        render_findings(report.get("findings") or []),
        render_topic_shifts(manifest, prior),
        render_category_grid(manifest, prior),
        render_routing_paths(manifest),
        render_ticket_queue(subtopic_rows),
    ]
    main_html = "\n".join(s for s in body_sections if s)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Display:wght@400;500;700&family=Google+Sans+Text:wght@400;500;600&family=Google+Sans+Code:wght@400;500&family=Roboto:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>{CSS}</style>
</head>
<body>
  <div class="page">
    <aside class="toc" aria-label="On this page">
      <p class="toc__label">On this page</p>
      <ul class="toc__list">
{toc_html}
      </ul>
    </aside>
    <main>
      {main_html}
      <footer>
        <span>Generated {esc(generated)} · publish-ocv-report · {esc(area_label)} · {esc(week)}</span>
        <span><a href="https://ocv.microsoft.com/" target="_blank" rel="noopener">OCV →</a></span>
      </footer>
    </main>
  </div>
  <script>{JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Synthetic-data demo (regenerates the sample dashboard)
# ---------------------------------------------------------------------------


def demo_manifest() -> Dict[str, Any]:
    """A synthetic-but-realistic manifest used by --demo."""
    return {
        "schema_version": "1.0",
        "taxonomy_version": "13-topic-2026-05-19",
        "week": "2026-05-18",
        "date_range": "May 11–16, 2026",
        "source_csv": "data/ocv_outlook-agent_2026-05-18_range.csv (demo)",
        "analysis_date": "2026-05-18",
        "classification_method": "model-assisted manual, demo data",
        "methodology_notes": [
            "Demo data only. Numbers are illustrative and do not represent real OCV traffic.",
            "Use --manifest with a real ocv-analyze-and-ticket manifest for production reports.",
        ],
        "caveats": [
            "Sampling bias: OCV feedback is self-selected and skews toward dissatisfied users.",
            "Long-tail topics with n<3 are not statistically meaningful.",
            "Negative-share reflects the OCV pipeline's sentiment-filtered ingestion, not user satisfaction.",
        ],
        "total_items": 150,
        "negative_items": 88,
        "sentiment": {"Negative": 88, "Positive": 35, "Neutral": 27},
        "rating": {"ThumbsDown": 107, "ThumbsUp": 43},
        "languages": {"en": 135, "es": 7, "ja": 2, "sv": 1, "de": 1, "nl": 1, "fr": 1, "ko": 1, "zh-Hans": 1},
        "clients": {"Desktop (Monarch)": 84, "Desktop (Win32)": 48, "OWA": 18},
        "topic_counts": {"1": 26, "2": 4, "3": 8, "4": 6, "6": 12, "7": 9, "8": 10, "9": 2, "10": 9, "11": 1, "13": 1},
        "topic_percentages": {},
        "category_counts": {
            "Drafting": 25, "Search": 16, "Scheduling": 14, "File": 9, "Summarization": 7,
            "Replying": 4, "Triage": 3, "General": 2, "Settings": 2, "Unknown": 2,
            "Rules": 1, "Tasks": 1, "Reminders": 1, "Translation": 1,
        },
        "submode_counts": {"6.error_string": 7, "10.input": 6, "6.hang": 4, "2.no_preview": 3, "10.output": 3},
        "per_item_classifications": [],
        "wow_basis": "data/manifests/ocv_outlook-agent_2026-05-11_manifest.json",
    }


def demo_prior() -> Dict[str, Any]:
    return {
        "week": "2026-05-11",
        "source_csv": "data/ocv_outlook-agent_2026-05-11_range.csv (demo)",
        "negative_items": 246,
        "topic_counts": {"1": 69, "2": 27, "3": 36, "4": 13, "5": 4, "6": 19, "7": 24, "8": 16, "9": 5, "10": 12, "11": 3, "12": 5, "13": 13},
        "category_counts": {
            "Drafting": 87, "Replying": 44, "Summarization": 21, "Search": 19, "File": 15,
            "Scheduling": 15, "Triage": 11, "Unknown": 11, "Settings": 10, "Rules": 8,
            "General": 3, "Tasks": 1, "Translation": 1,
        },
    }


def demo_subtopics() -> List[Dict[str, str]]:
    return [
        {"Parent Topic": "2 - HitL violation", "Category": "Triage", "SubMode": "",
         "Brief title": "Triage: archived without approval",
         "Issue description": "User asked Copilot to suggest emails to archive; Copilot archived them with no preview or undo path. Trust/approval boundary broken.",
         "Count": "3",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_a1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_a2 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_a3"},
        {"Parent Topic": "11 - Calendar correctness", "Category": "Scheduling", "SubMode": "",
         "Brief title": "Scheduling: claimed accept; invite still tentative",
         "Issue description": "User asked Copilot to accept a recurring meeting. Copilot replied 'accepted'; calendar state remained tentative.",
         "Count": "2",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_c1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_c2"},
        {"Parent Topic": "1 - Action not executed", "Category": "Drafting", "SubMode": "",
         "Brief title": "Drafting: rewrite + generic error",
         "Issue description": "Rewrite request + generic error; Yes draft now + generic error; HE write email + blank response. Action-not-executed on the dominant surface.",
         "Count": "26",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_d1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_d2 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_d3 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_d4 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_d5"},
        {"Parent Topic": "1 - Action not executed", "Category": "Replying", "SubMode": "",
         "Brief title": "Replying: blank response on reply intent",
         "Issue description": "Forward draft request + blank response; Draft reply + add attendees + blank; ES reply request + generic error.",
         "Count": "12",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_r1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_r2"},
        {"Parent Topic": "6 - Reliability failure", "Category": "Summarization", "SubMode": "error_string",
         "Brief title": "Summarization: 'something went wrong' on inbox summary",
         "Issue description": "Inbox summary requests returning canned error_during_execution string across Monarch + Win32.",
         "Count": "5",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_s1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_s2 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_s3"},
        {"Parent Topic": "10 - File I/O failure", "Category": "File", "SubMode": "input",
         "Brief title": "File: cannot ingest documented-supported attachment",
         "Issue description": "DOCX + PDF attachments returning 'unable to read file' on Monarch despite being documented as supported.",
         "Count": "6",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_f1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_f2"},
        {"Parent Topic": "8 - Wrong context / grounding", "Category": "Summarization", "SubMode": "",
         "Brief title": "Summarization: pulled from wrong thread",
         "Issue description": "Summary cited content from an adjacent unrelated thread; clear grounding miss.",
         "Count": "2",
         "OCV links": "https://ocv.microsoft.com/#/item/fdcl_v4_demo_g1 | https://ocv.microsoft.com/#/item/fdcl_v4_demo_g2"},
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def extract_area_from_manifest_path(path: Path) -> str:
    m = re.match(r"^ocv_(.+?)_\d{4}-\d{2}-\d{2}_manifest\.json$", path.name)
    return m.group(1) if m else "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", type=Path, help="Path to ocv-analyze manifest JSON")
    parser.add_argument("--subtopics", type=Path, help="Path to subtopics CSV (optional)")
    parser.add_argument("--prior-manifest", type=Path, help="Path to prior-week manifest JSON (optional; auto-detected)")
    parser.add_argument("--report-md", type=Path, help="Path to analyze-and-ticket report MD (optional)")
    parser.add_argument("--out", type=Path, help="Output HTML path (default: output/ocv_<area>_<week>.html)")
    parser.add_argument("--no-open", action="store_true", help="Do not open in browser")
    parser.add_argument("--demo", action="store_true", help="Render with synthetic demo data; ignores --manifest")
    parser.add_argument("--area-label", default=None, help="Display label for area (default derived from filename)")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    if args.demo:
        manifest = demo_manifest()
        prior = demo_prior()
        subtopic_rows_raw = demo_subtopics()
        report = {"tldr": "", "findings": []}
        area = "outlook-agent"
    else:
        if not args.manifest:
            parser.error("--manifest is required unless --demo is passed")
        if not args.manifest.exists():
            parser.error(f"manifest not found: {args.manifest}")
        manifest = load_manifest(args.manifest)
        area = extract_area_from_manifest_path(args.manifest)

        # Prior manifest
        if args.prior_manifest and args.prior_manifest.exists():
            prior = load_manifest(args.prior_manifest)
        else:
            wow_basis = manifest.get("wow_basis")
            if isinstance(wow_basis, dict):
                wow_basis = wow_basis.get("prior_manifest")
            if wow_basis:
                pb = (args.manifest.parent.parent.parent / wow_basis).resolve() \
                    if not Path(wow_basis).is_absolute() else Path(wow_basis)
                # Fallback: try relative to repo root (manifest is in data/manifests/)
                if not pb.exists():
                    pb = (args.manifest.parent.parent.parent / wow_basis).resolve()
                if not pb.exists():
                    pb = Path(wow_basis)
                prior = load_manifest(pb) if pb.exists() else None
            else:
                prior = None
            if prior is None:
                pp = find_prior_manifest(area, manifest.get("week", ""), args.manifest.parent)
                if pp:
                    prior = load_manifest(pp)

        # Subtopics CSV
        subtopic_path = args.subtopics
        if not subtopic_path:
            data_dir = args.manifest.parent.parent
            subtopic_path = find_subtopics_csv(area, manifest.get("week", ""), data_dir)
        subtopic_rows_raw = parse_subtopics_csv(subtopic_path) if (subtopic_path and subtopic_path.exists()) else []

        # Report MD (optional)
        report = {"tldr": "", "findings": []}
        if args.report_md and args.report_md.exists():
            report = parse_report_md(args.report_md.read_text(encoding="utf-8"))

    # Enrich subtopic rows with derived priority + parsed count
    subtopic_rows: List[Dict[str, Any]] = []
    for r in subtopic_rows_raw:
        r["_priority"] = derive_priority(r)
        try:
            r["_count"] = int(r.get("Count") or r.get("Item Count") or "1")
        except ValueError:
            r["_count"] = 1
        subtopic_rows.append(r)

    area_label = args.area_label or area.replace("-", " ").title()

    html_out = render_page(manifest, prior, subtopic_rows, report, area_label)

    if args.out:
        out_path = args.out
    else:
        out_dir = Path.cwd() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"ocv_{area}_{manifest.get('week', 'unknown')}.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024.0
    print(f"[publish-ocv-report] wrote {out_path}  ({size_kb:.1f} KB)")
    print(f"[publish-ocv-report] area={area} week={manifest.get('week')} "
          f"negatives={manifest.get('negative_items')} subtopics={len(subtopic_rows)}")
    if prior:
        print(f"[publish-ocv-report] prior baseline: {prior.get('week')} "
              f"(n={prior.get('negative_items')} negative)")
    else:
        print("[publish-ocv-report] no prior baseline (WoW section will note this)")

    if not args.no_open:
        webbrowser.open(out_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    sys.exit(main())

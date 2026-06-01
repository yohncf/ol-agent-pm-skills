"""publish_ocv_report_v2.py

Material Design 3 redesign of the weekly OCV report.
PROPOSAL / PREVIEW: writes <out>.html (suggested suffix: _v2.html).
Does not replace publish_ocv_report.py.

Usage:
    python scripts/publish_ocv_report_v2.py \
        --manifest data/manifests/ocv_outlook-agent_2026-05-31_manifest.json \
        [--subtopics data/ocv_outlook-agent_2026-05-31_subtopics.csv] \
        [--prior-manifest data/manifests/ocv_outlook-agent_2026-05-24_manifest.json] \
        [--report-md data/ocv_outlook-agent_2026-05-31_report.md] \
        [--out _ocv_weekly_repo/reports/2026-05-31_v2.html]

Reads the same inputs as publish_ocv_report.py; emits an M3-styled HTML
that matches the M3 dashboard (Google Sans + Material Symbols + the
same surface tokens).
"""
from __future__ import annotations

import argparse
import csv as csv_mod
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

csv_mod.field_size_limit(10**8)

TOPIC_NAMES: Dict[int, str] = {
    1: "Action not executed", 2: "HitL violation",
    3: "Output doesn't match intent", 4: "Constraints ignored",
    5: "Unnecessary clarifying question", 6: "Reliability failure",
    7: "Inaccurate or fabricated content", 8: "Wrong context / grounding",
    9: "Tone / language / format", 10: "File I/O failure",
    11: "Calendar correctness", 12: "Capability refusal",
    13: "Intrusive Outlook Agent UI",
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_subtopics(p: Optional[Path]) -> List[dict]:
    if not p or not p.exists():
        return []
    return list(csv_mod.DictReader(p.open(encoding="utf-8-sig")))


def load_markdown_sections(p: Optional[Path]) -> Dict[str, str]:
    """Split the report.md by H2 (## Title) into a dict of {title: raw markdown}."""
    if not p or not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    sections: Dict[str, str] = {}
    parts = re.split(r"^##\s+(.+)$", text, flags=re.MULTILINE)
    if not parts:
        return {}
    # parts = [preface, h2_1, body_1, h2_2, body_2, ...]
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections[title] = body
    return sections


# ---------------------------------------------------------------------------
# Lightweight markdown → HTML for the slice we use (bullets, paragraphs,
# bold, links, inline code). Tables are pulled directly from the manifest,
# not the markdown.
# ---------------------------------------------------------------------------
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE_RE = re.compile(r"`([^`]+)`")


def md_inline(s: str) -> str:
    s = html.escape(s)
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', s)
    s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _ITALIC_RE.sub(r"<em>\1</em>", s)
    s = _CODE_RE.sub(r"<code>\1</code>", s)
    return s


def md_to_html(md: str) -> str:
    """Render bullets + paragraphs. Skip markdown tables (we render those from data)."""
    if not md:
        return ""
    lines = md.splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        # Skip markdown tables — we render structured data from the manifest
        if line.lstrip().startswith("|"):
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                i += 1
            continue
        # H3 (### Subheading)
        if line.startswith("### "):
            out.append(f'<h3 class="md-h3 title-large">{md_inline(line[4:].strip())}</h3>')
            i += 1
            continue
        # Bullet list
        if line.lstrip().startswith("- "):
            out.append('<ul class="md-list">')
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                item = lines[i].lstrip()[2:].strip()
                out.append(f'<li class="body-medium">{md_inline(item)}</li>')
                i += 1
            out.append('</ul>')
            continue
        # Paragraph
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("- ", "|", "### ")):
            para_lines.append(lines[i].strip())
            i += 1
        if para_lines:
            out.append(f'<p class="md-p body-large">{md_inline(" ".join(para_lines))}</p>')
    return "\n".join(out)


# ---------------------------------------------------------------------------
# KPI computations
# ---------------------------------------------------------------------------
def compute_hero(manifest: dict, prior: Optional[dict]) -> List[dict]:
    rating = manifest.get("rating") or {}
    tu = int(rating.get("ThumbsUp", 0) or 0)
    td = int(rating.get("ThumbsDown", 0) or 0)
    total = tu + td
    pos_pct = (100.0 * tu / total) if total else None

    neg = int(manifest.get("negative_items", 0) or 0)

    topic_counts = {int(k): int(v) for k, v in (manifest.get("topic_counts") or {}).items()}
    ranked = sorted(((k, v) for k, v in topic_counts.items() if v > 0), key=lambda kv: -kv[1])
    top_tid, top_count = (ranked[0] if ranked else (None, 0))
    top_name = TOPIC_NAMES.get(top_tid, "—") if top_tid else "—"

    # Prior comparisons
    prior_pos = None
    prior_neg = None
    prior_top = None
    if prior:
        pr = prior.get("rating") or {}
        ptu = int(pr.get("ThumbsUp", 0) or 0)
        ptd = int(pr.get("ThumbsDown", 0) or 0)
        ptot = ptu + ptd
        prior_pos = (100.0 * ptu / ptot) if ptot else None
        prior_neg = int(prior.get("negative_items", 0) or 0)
        ptopics = {int(k): int(v) for k, v in (prior.get("topic_counts") or {}).items()}
        if top_tid is not None:
            prior_top = ptopics.get(top_tid, 0)

    def chip(delta, fmt_label, lower_is_better=False):
        if delta is None:
            return {"cls": "kpi__sub--flat", "icon": "horizontal_rule", "label": "no prior week"}
        improved = (delta < 0) if lower_is_better else (delta > 0)
        worsened = (delta > 0) if lower_is_better else (delta < 0)
        if abs(delta) < 0.05:
            return {"cls": "kpi__sub--flat", "icon": "horizontal_rule", "label": "flat vs last week"}
        icon = ("trending_down" if lower_is_better else "trending_up") if improved else \
               ("trending_up" if lower_is_better else "trending_down")
        cls = "kpi__sub--pos" if improved else "kpi__sub--neg"
        return {"cls": cls, "icon": icon, "label": fmt_label(delta) + " vs last week"}

    pos_delta = (pos_pct - prior_pos) if (pos_pct is not None and prior_pos is not None) else None
    neg_delta_pct = (100.0 * (neg - prior_neg) / prior_neg) if (prior_neg and prior_neg > 0) else None
    top_delta = (top_count - prior_top) if (prior_top is not None) else None

    return [
        {
            "icon": "sentiment_satisfied",
            "label": "Positive rating",
            "value": (f"{pos_pct:.1f}%" if pos_pct is not None else "—"),
            "chip": chip(pos_delta, lambda d: f"{'+' if d >= 0 else ''}{d:.1f} pp", lower_is_better=False),
            "caption": f"{tu:,} up · {td:,} down",
        },
        {
            "icon": "comment",
            "label": "Verbatim negatives",
            "value": f"{neg:,}",
            "chip": chip(neg_delta_pct, lambda d: f"{'+' if d >= 0 else ''}{d:.1f}%", lower_is_better=True),
            "caption": f"of {int(manifest.get('verbatim_items', 0)):,} verbatim, {int(manifest.get('total_items', 0)):,} total submissions",
        },
        {
            "icon": "warning",
            "label": "Top pain topic",
            "value": top_name,
            "value_class": "kpi__value--text",
            "chip": chip(top_delta, lambda d: f"{'+' if d >= 0 else ''}{int(d)} items", lower_is_better=True),
            "caption": f"{top_count} negatives this week",
        },
    ]


def compute_priority_card(sub_rows: List[dict], prior_sub_rows: List[dict]) -> dict:
    def queue_counts(rows):
        c = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for r in rows:
            p = (r.get("Priority") or "").strip().upper()
            if p in c:
                c[p] += 1
        return c
    cur = queue_counts(sub_rows)
    pri = queue_counts(prior_sub_rows)
    p0p1 = cur["P0"] + cur["P1"]
    p0p1_prior = pri["P0"] + pri["P1"]
    p0p1_delta = (p0p1 - p0p1_prior) if (prior_sub_rows is not None and any(pri.values())) else None
    synced = sum(1 for r in sub_rows if (r.get("ADO URL") or "").strip())
    chip_cls = "kpi__sub--flat"
    chip_icon = "horizontal_rule"
    chip_label = "no prior week"
    if p0p1_delta is not None:
        if p0p1_delta < 0:
            chip_cls, chip_icon = "kpi__sub--pos", "trending_down"
            chip_label = f"{p0p1_delta} vs last week"
        elif p0p1_delta > 0:
            chip_cls, chip_icon = "kpi__sub--neg", "trending_up"
            chip_label = f"+{p0p1_delta} vs last week"
        else:
            chip_label = "flat vs last week"
    return {
        "icon": "engineering",
        "label": "P0 + P1 queue",
        "value": str(p0p1),
        "chip": {"cls": chip_cls, "icon": chip_icon, "label": chip_label},
        "caption": f"{cur['P0']} P0 · {cur['P1']} P1 · {synced} synced to ADO",
    }


# ---------------------------------------------------------------------------
# Section renderers (each returns HTML for a single <section>)
# ---------------------------------------------------------------------------
def render_dataset_card(manifest: dict) -> str:
    total = int(manifest.get("total_items", 0) or 0)
    verb = int(manifest.get("verbatim_items", 0) or 0)
    struct = int(manifest.get("structured_only_items", 0) or 0)
    sentiment = manifest.get("sentiment") or {}
    rating = manifest.get("rating") or {}
    clients = manifest.get("clients") or {}
    languages = manifest.get("languages") or {}

    def pct(n, base): return f"{(100.0*n/base):.1f}%" if base else "—"

    sent_scored = sum(int(v) for v in sentiment.values())
    rating_total = int(rating.get("ThumbsUp", 0) or 0) + int(rating.get("ThumbsDown", 0) or 0)

    def row(label, items):
        cells = "".join(f'<td class="cell">{html.escape(k)}<br><span class="cell-sub">{v:,} · {pct(v, base)}</span></td>'
                        for (k, v, base) in items)
        return f"<tr><th class='row-head'>{label}</th>{cells}</tr>"

    sent_items = []
    if sent_scored:
        for k in ("Negative", "Positive", "Neutral"):
            v = int(sentiment.get(k, 0) or 0)
            if v: sent_items.append((k, v, sent_scored))

    client_items = [(k, int(v), total) for k, v in sorted(clients.items(), key=lambda kv: -int(kv[1]))]
    lang_items   = [(k, int(v), total) for k, v in sorted(languages.items(), key=lambda kv: -int(kv[1]))[:4]]

    rows_html = [
        f"<tr><th class='row-head'>Total submissions</th><td class='cell'>{total:,}</td></tr>",
        f"<tr><th class='row-head'>With verbatim</th><td class='cell'>{verb:,}<br><span class='cell-sub'>{pct(verb,total)}</span></td><td class='cell'>No verbatim (unscored)<br><span class='cell-sub'>{struct:,} · {pct(struct,total)}</span></td></tr>",
    ]
    if sent_items:
        rows_html.append(row("Sentiment (verbatim slice)", sent_items))
    if rating_total:
        rt = [("Thumbs up", int(rating.get('ThumbsUp', 0) or 0), rating_total),
              ("Thumbs down", int(rating.get('ThumbsDown', 0) or 0), rating_total)]
        rows_html.append(row("Rating (all submissions)", rt))
    if client_items: rows_html.append(row("Client", client_items))
    if lang_items:   rows_html.append(row("Language", lang_items))

    return f"""
<section id="dataset" class="report-card">
  <div class="section-eyebrow label-medium">03 · Dataset summary</div>
  <h2 class="section-title headline-medium">What was sampled</h2>
  <div class="dataset-table-wrap">
    <table class="dataset-table">
      <tbody>{"".join(rows_html)}</tbody>
    </table>
  </div>
</section>
"""


def render_topic_shifts(manifest: dict, prior: Optional[dict]) -> str:
    cur = {int(k): int(v) for k, v in (manifest.get("topic_counts") or {}).items()}
    pri = {int(k): int(v) for k, v in ((prior or {}).get("topic_counts") or {}).items()} if prior else {}
    cur_total = sum(cur.values()) or 1
    pri_total = sum(pri.values()) or 1

    all_tids = sorted(set(cur.keys()) | set(pri.keys()), key=lambda t: -cur.get(t, 0))
    if not all_tids:
        return ""

    body = []
    for tid in all_tids:
        c = cur.get(tid, 0)
        p = pri.get(tid, 0)
        c_pct = 100.0 * c / cur_total
        p_pct = 100.0 * p / pri_total if p else 0.0
        delta_pp = c_pct - p_pct
        if abs(delta_pp) < 0.01: delta_cls, sign = "delta-flat", ""
        elif delta_pp > 0:       delta_cls, sign = "delta-up", "+"
        else:                    delta_cls, sign = "delta-down", ""
        body.append(f"""
          <tr>
            <td class="topic-cell"><span class="topic-id">{tid}</span> {html.escape(TOPIC_NAMES.get(tid, f'Topic {tid}'))}</td>
            <td class="num">{c} <span class="cell-sub">({c_pct:.1f}%)</span></td>
            <td class="num">{p} <span class="cell-sub">({p_pct:.1f}%)</span></td>
            <td class="num {delta_cls}">{sign}{delta_pp:.1f} pp</td>
          </tr>
        """)

    prior_week = (prior or {}).get("week", "—") if prior else "—"
    return f"""
<section id="topics" class="report-card">
  <div class="section-eyebrow label-medium">05 · Topic shifts (week over week)</div>
  <h2 class="section-title headline-medium">WoW shifts vs week of {html.escape(prior_week)}</h2>
  <div class="data-table-wrap">
    <table class="data-table">
      <thead><tr><th>Topic</th><th class="num">This week</th><th class="num">Last week</th><th class="num">Δ (pp)</th></tr></thead>
      <tbody>{"".join(body)}</tbody>
    </table>
  </div>
</section>
"""


def render_category_breakdown(manifest: dict) -> str:
    cats = manifest.get("category_counts") or {}
    if not cats:
        return ""
    total = sum(int(v) for v in cats.values()) or 1
    rows = []
    for k, v in sorted(cats.items(), key=lambda kv: -int(kv[1])):
        rows.append(f"""
          <tr>
            <td>{html.escape(k)}</td>
            <td class="num">{int(v):,}</td>
            <td class="num"><span class="cell-sub">{100.0*int(v)/total:.1f}%</span></td>
          </tr>
        """)
    return f"""
<section id="categories" class="report-card">
  <div class="section-eyebrow label-medium">07 · Category breakdown</div>
  <h2 class="section-title headline-medium">Which surfaces are bearing the load</h2>
  <div class="data-table-wrap">
    <table class="data-table">
      <thead><tr><th>Category</th><th class="num">Count</th><th class="num">Share</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>
"""


def render_routing(manifest: dict) -> str:
    paths_block = manifest.get("paths") or {}
    # Manifest stores routing as {"by_path": {...}, "unknown_or_missing": N}
    if isinstance(paths_block, dict) and "by_path" in paths_block:
        paths = paths_block.get("by_path") or {}
    else:
        paths = paths_block
    if not paths:
        return ""
    total = sum(int(v) for v in paths.values()) or 1
    rows = []
    for k, v in sorted(paths.items(), key=lambda kv: -int(kv[1])):
        rows.append(f"""
          <tr>
            <td class="mono">{html.escape(k)}</td>
            <td class="num">{int(v):,}</td>
            <td class="num"><span class="cell-sub">{100.0*int(v)/total:.1f}%</span></td>
          </tr>
        """)
    return f"""
<section id="routing" class="report-card">
  <div class="section-eyebrow label-medium">06b · Routing path</div>
  <h2 class="section-title headline-medium">Which routing path is generating the most issues</h2>
  <div class="data-table-wrap">
    <table class="data-table">
      <thead><tr><th>Path</th><th class="num">Count</th><th class="num">Share</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>
"""


def render_tickets(sub_rows: List[dict]) -> str:
    pri_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    keep = [r for r in sub_rows if (r.get("Priority") or "").strip().upper() in ("P0", "P1", "P2")]
    keep.sort(key=lambda r: (pri_rank.get(r["Priority"].strip().upper(), 99), -int(r.get("Item Count", 0) or 0)))
    if not keep:
        return ""

    def parse_links(field):
        return [u.strip() for u in (field or "").split(";") if u.strip()]

    cards = []
    for r in keep[:12]:
        pr = r["Priority"].strip().upper()
        parent = (r.get("Parent Topic") or "").strip()
        surface = (r.get("Category") or "").strip()
        title = (r.get("Brief Title") or "").strip()
        desc = (r.get("Issue Description") or "").strip()
        count = int(r.get("Item Count", 0) or 0)
        ado_url = (r.get("ADO URL") or "").strip()
        ado_act = (r.get("ADO action") or "").strip().lower()
        ocv_links = parse_links(r.get("OCV Item Links"))
        dash_links = parse_links(r.get("Dash links"))
        path = (r.get("Path") or "").strip()
        models = (r.get("Resolved Models") or "").strip()

        ado_html = ""
        if ado_url:
            label = f"ADO ({ado_act})" if ado_act else "ADO"
            ado_html = f'<a class="m3-btn m3-btn--outlined" href="{html.escape(ado_url)}" target="_blank" rel="noopener"><span>{html.escape(label)}</span><span class="material-symbols-rounded">open_in_new</span></a>'

        ocv_html = ""
        if ocv_links:
            chips = " ".join(f'<a class="m3-chip m3-chip--link" href="{html.escape(u)}" target="_blank" rel="noopener">item {i+1}</a>' for i, u in enumerate(ocv_links))
            ocv_html = f'<div class="ticket-links"><span class="ticket-links__label label-medium">OCV items</span>{chips}</div>'

        dash_html = ""
        if dash_links:
            chips = " ".join(f'<a class="m3-chip m3-chip--link" href="{html.escape(u)}" target="_blank" rel="noopener">dash {i+1}</a>' for i, u in enumerate(dash_links))
            extra = ""
            if path or models:
                extra = f'<span class="ticket-model-tag">{html.escape(path)}{": " if path and models else ""}{html.escape(models)}</span>'
            dash_html = f'<div class="ticket-links"><span class="ticket-links__label label-medium">Dash</span>{chips}{extra}</div>'

        chips_html = []
        if parent:  chips_html.append(f'<span class="m3-chip m3-chip--assist"><span class="material-symbols-rounded">label</span>{html.escape(parent)}</span>')
        if surface: chips_html.append(f'<span class="m3-chip m3-chip--assist"><span class="material-symbols-rounded">apps</span>{html.escape(surface)}</span>')

        cards.append(f"""
        <details class="ticket-card ticket-card--{pr.lower()}">
          <summary class="ticket-card__head">
            <span class="prio-badge prio-{pr.lower()}">{pr}</span>
            <span class="ticket-card__title title-medium">{html.escape(title)}</span>
            <span class="ticket-card__count mono">×{count}</span>
            <span class="material-symbols-rounded ticket-card__chevron">expand_more</span>
          </summary>
          <div class="ticket-card__body">
            <div class="ticket-chips">{"".join(chips_html)}{ado_html}</div>
            <p class="ticket-card__desc body-medium">{html.escape(desc)}</p>
            {ocv_html}
            {dash_html}
          </div>
        </details>
        """)

    return f"""
<section id="tickets">
  <div class="section-eyebrow label-medium">08 · Ticket queue</div>
  <h2 class="section-title headline-medium">P0 / P1 / P2 rows worth engineering attention</h2>
  <p class="section-note body-medium">Showing top 12 rows by priority then prevalence. Click any row to expand.</p>
  <div class="ticket-list">{"".join(cards)}</div>
</section>
"""


# ---------------------------------------------------------------------------
# Main template
# ---------------------------------------------------------------------------
HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Display:wght@400;500;700&family=Google+Sans+Text:wght@400;500;600&family=Roboto+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
  <style>
{css}
  </style>
</head>
"""

CSS = """
:root {
  --md-sys-color-surface: #101418;
  --md-sys-color-surface-container-low: #161a1f;
  --md-sys-color-surface-container: #1a1e23;
  --md-sys-color-surface-container-high: #22272d;
  --md-sys-color-surface-container-highest: #2a2f36;
  --md-sys-color-on-surface: #e3e2e6;
  --md-sys-color-on-surface-variant: #c4c6cf;
  --md-sys-color-on-surface-faint: #8a8d94;
  --md-sys-color-outline-variant: #43474e;
  --md-sys-color-primary: #a8c7fa;
  --md-sys-color-primary-container: rgba(168, 199, 250, 0.12);
  --md-sys-color-tertiary: #a4ccaf;
  --md-sys-color-tertiary-container: rgba(164, 204, 175, 0.14);
  --md-sys-color-error: #ffb4ab;
  --md-sys-color-error-container: rgba(255, 180, 171, 0.14);
  --md-sys-color-secondary: #bbc7db;
  --md-sys-color-secondary-container: rgba(187, 199, 219, 0.10);
  --md-shape-corner-small: 8px;
  --md-shape-corner-medium: 12px;
  --md-shape-corner-large: 16px;
  --md-shape-corner-extra-large: 28px;
  --md-elevation-1: 0 1px 3px rgba(0,0,0,0.30), 0 1px 2px rgba(0,0,0,0.40);
  --md-elevation-2: 0 2px 6px rgba(0,0,0,0.30), 0 1px 2px rgba(0,0,0,0.40);
  --md-easing-emphasized: cubic-bezier(0.2, 0, 0, 1);
  --font-display: 'Google Sans Display','Google Sans','Roboto',system-ui,sans-serif;
  --font-body: 'Google Sans Text','Google Sans','Roboto',system-ui,sans-serif;
  --font-mono: 'Roboto Mono',ui-monospace,monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--md-sys-color-surface);
  color: var(--md-sys-color-on-surface);
  font-family: var(--font-body);
  font-size: 14px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
a { color: var(--md-sys-color-primary); text-decoration: none; }
a:hover { text-decoration: underline; }
code, .mono { font-family: var(--font-mono); }
.num { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.cell-sub { color: var(--md-sys-color-on-surface-faint); font-size: 12px; }

/* ===== Type scale ===== */
.display-medium  { font-family: var(--font-display); font-weight: 400; font-size: 45px; line-height: 52px; }
.headline-large  { font-family: var(--font-display); font-weight: 500; font-size: 32px; line-height: 40px; }
.headline-medium { font-family: var(--font-display); font-weight: 500; font-size: 28px; line-height: 36px; }
.title-large     { font-family: var(--font-body); font-weight: 500; font-size: 22px; line-height: 28px; }
.title-medium    { font-family: var(--font-body); font-weight: 500; font-size: 16px; line-height: 24px; }
.body-large      { font-family: var(--font-body); font-weight: 400; font-size: 16px; line-height: 24px; }
.body-medium     { font-family: var(--font-body); font-weight: 400; font-size: 14px; line-height: 20px; }
.body-small      { font-family: var(--font-body); font-weight: 400; font-size: 12px; line-height: 16px; }
.label-medium    { font-family: var(--font-body); font-weight: 500; font-size: 12px; line-height: 16px; letter-spacing: 0.5px; text-transform: uppercase; }

/* ===== Top app bar ===== */
.top-bar {
  position: sticky; top: 0; z-index: 10;
  background: var(--md-sys-color-surface-container);
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  padding: 12px 32px;
  display: flex; align-items: center; gap: 16px;
}
.top-bar__back {
  display: inline-flex; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 50%;
  color: var(--md-sys-color-on-surface);
  transition: background 200ms var(--md-easing-emphasized);
}
.top-bar__back:hover { background: var(--md-sys-color-surface-container-high); text-decoration: none; }
.top-bar__title { color: var(--md-sys-color-on-surface); }
.top-bar__subtitle { color: var(--md-sys-color-on-surface-variant); margin-top: 2px; }
.top-bar__spacer { flex: 1; }
.top-bar__chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border-radius: var(--md-shape-corner-small);
  background: var(--md-sys-color-tertiary-container);
  color: var(--md-sys-color-tertiary);
}
.top-bar__chip-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--md-sys-color-tertiary);
}

/* ===== Layout: TOC rail + content ===== */
.page {
  max-width: 1400px; margin: 0 auto;
  padding: 32px;
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 32px;
  align-items: start;
}
.toc {
  position: sticky; top: 96px;
  background: var(--md-sys-color-surface-container);
  border-radius: var(--md-shape-corner-large);
  padding: 12px;
  display: flex; flex-direction: column; gap: 4px;
}
.toc a {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px;
  border-radius: 100px;
  color: var(--md-sys-color-on-surface-variant);
  font-size: 13px; font-weight: 500;
  transition: background 200ms var(--md-easing-emphasized), color 200ms var(--md-easing-emphasized);
}
.toc a:hover { background: var(--md-sys-color-surface-container-high); color: var(--md-sys-color-on-surface); text-decoration: none; }
.toc a.is-active {
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-primary);
}
.toc a .material-symbols-rounded { font-size: 18px; }
.content { display: flex; flex-direction: column; gap: 28px; }
@media (max-width: 1024px) {
  .page { grid-template-columns: 1fr; padding: 16px; gap: 16px; }
  .toc { position: static; flex-direction: row; overflow-x: auto; flex-wrap: nowrap; }
  .toc a { white-space: nowrap; }
}

/* ===== Hero ===== */
.hero-header {
  display: flex; flex-direction: column; gap: 6px;
}
.hero-header__title { color: var(--md-sys-color-on-surface); }
.hero-header__subtitle { color: var(--md-sys-color-on-surface-variant); }
.hero {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
@media (max-width: 1100px) { .hero { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 600px)  { .hero { grid-template-columns: 1fr; } }
.kpi {
  background: var(--md-sys-color-surface-container-high);
  border-radius: var(--md-shape-corner-extra-large);
  padding: 24px;
  box-shadow: var(--md-elevation-1);
  display: flex; flex-direction: column; gap: 12px;
  transition: background 200ms var(--md-easing-emphasized);
}
.kpi:hover { background: var(--md-sys-color-surface-container-highest); }
.kpi__header { display: flex; align-items: center; gap: 10px; color: var(--md-sys-color-on-surface-variant); }
.kpi__header .material-symbols-rounded { font-size: 20px; color: var(--md-sys-color-primary); }
.kpi__value { color: var(--md-sys-color-on-surface); font-size: 44px; line-height: 52px; font-weight: 500; font-family: var(--font-display); }
.kpi__value--text { font-size: 24px; line-height: 32px; }
.kpi__sub {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  border-radius: var(--md-shape-corner-small);
  width: fit-content;
  font-size: 13px; font-weight: 500;
}
.kpi__sub--pos { background: var(--md-sys-color-tertiary-container); color: var(--md-sys-color-tertiary); }
.kpi__sub--neg { background: var(--md-sys-color-error-container); color: var(--md-sys-color-error); }
.kpi__sub--flat { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-secondary); }
.kpi__sub .material-symbols-rounded { font-size: 16px; }
.kpi__caption { color: var(--md-sys-color-on-surface-faint); font-size: 12px; }

/* ===== Report cards (sections) ===== */
.report-card {
  background: var(--md-sys-color-surface-container);
  border-radius: var(--md-shape-corner-large);
  padding: 28px;
  box-shadow: var(--md-elevation-1);
  scroll-margin-top: 96px;
}
.section-eyebrow {
  color: var(--md-sys-color-on-surface-variant);
  margin-bottom: 6px;
}
.section-title { color: var(--md-sys-color-on-surface); margin: 0 0 20px 0; }
.section-note { color: var(--md-sys-color-on-surface-faint); margin-bottom: 16px; }

/* ===== Markdown content ===== */
.md-p { color: var(--md-sys-color-on-surface); margin: 0 0 12px 0; }
.md-p:last-child { margin-bottom: 0; }
.md-list { color: var(--md-sys-color-on-surface); padding-left: 20px; margin: 0; }
.md-list li { margin-bottom: 8px; }
.md-list li:last-child { margin-bottom: 0; }
.md-list strong, .md-p strong { color: var(--md-sys-color-on-surface); font-weight: 600; }
.md-h3 { color: var(--md-sys-color-on-surface); margin: 20px 0 12px 0; }

/* ===== Tables (data + dataset) ===== */
.data-table-wrap, .dataset-table-wrap {
  overflow-x: auto;
  border-radius: var(--md-shape-corner-medium);
  background: var(--md-sys-color-surface-container-low);
}
table.data-table, table.dataset-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}
.data-table thead th {
  text-align: left;
  padding: 12px 16px;
  color: var(--md-sys-color-on-surface-variant);
  font-family: var(--font-body); font-weight: 500; font-size: 12px;
  letter-spacing: 0.5px; text-transform: uppercase;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
}
.data-table thead th.num { text-align: right; }
.data-table tbody td { padding: 12px 16px; border-bottom: 1px solid var(--md-sys-color-outline-variant); }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--md-sys-color-surface-container-high); }
.topic-cell { color: var(--md-sys-color-on-surface); }
.topic-id {
  display: inline-block; min-width: 22px; text-align: center;
  padding: 2px 6px; border-radius: 6px;
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-primary);
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  margin-right: 6px;
}
.delta-up   { color: var(--md-sys-color-error); }
.delta-down { color: var(--md-sys-color-tertiary); }
.delta-flat { color: var(--md-sys-color-on-surface-faint); }

.dataset-table th.row-head {
  text-align: left; padding: 12px 16px;
  color: var(--md-sys-color-on-surface-variant);
  font-family: var(--font-body); font-weight: 500; font-size: 12px;
  letter-spacing: 0.5px; text-transform: uppercase;
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  vertical-align: top; min-width: 200px;
}
.dataset-table .cell {
  padding: 12px 16px;
  color: var(--md-sys-color-on-surface);
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  vertical-align: top;
}
.dataset-table tbody tr:last-child th, .dataset-table tbody tr:last-child td { border-bottom: none; }

/* ===== Ticket cards ===== */
.ticket-list { display: flex; flex-direction: column; gap: 12px; }
.ticket-card {
  background: var(--md-sys-color-surface-container);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: var(--md-shape-corner-large);
  overflow: hidden;
  transition: border-color 200ms var(--md-easing-emphasized), background 200ms var(--md-easing-emphasized);
}
.ticket-card[open] { background: var(--md-sys-color-surface-container-high); }
.ticket-card--p0[open] { border-color: var(--md-sys-color-error); }
.ticket-card--p1[open] { border-color: #ffd1ac; }
.ticket-card--p2[open] { border-color: var(--md-sys-color-secondary); }
.ticket-card__head {
  display: grid;
  grid-template-columns: auto 1fr auto 28px;
  align-items: center; gap: 12px;
  padding: 16px 20px;
  cursor: pointer;
  list-style: none;
}
.ticket-card__head::-webkit-details-marker { display: none; }
.ticket-card__title { color: var(--md-sys-color-on-surface); }
.ticket-card__count { color: var(--md-sys-color-on-surface-faint); font-size: 13px; }
.ticket-card__chevron {
  color: var(--md-sys-color-on-surface-variant);
  font-size: 22px;
  transition: transform 200ms var(--md-easing-emphasized);
}
.ticket-card[open] .ticket-card__chevron { transform: rotate(180deg); }

.prio-badge {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 4px 10px;
  border-radius: 100px;
  font-family: var(--font-mono); font-weight: 700; font-size: 11px;
  letter-spacing: 0.5px;
}
.prio-p0 { background: var(--md-sys-color-error-container); color: var(--md-sys-color-error); }
.prio-p1 { background: rgba(255, 209, 172, 0.16); color: #ffd1ac; }
.prio-p2 { background: var(--md-sys-color-secondary-container); color: var(--md-sys-color-secondary); }
.prio-p3 { background: var(--md-sys-color-surface-container-highest); color: var(--md-sys-color-on-surface-faint); }

.ticket-card__body {
  padding: 0 20px 20px 20px;
  display: flex; flex-direction: column; gap: 12px;
  border-top: 1px solid var(--md-sys-color-outline-variant);
  padding-top: 16px;
}
.ticket-chips {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
}
.ticket-card__desc {
  color: var(--md-sys-color-on-surface);
  margin: 0;
}
.ticket-links {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
}
.ticket-links__label {
  color: var(--md-sys-color-on-surface-variant);
  margin-right: 4px;
}
.ticket-model-tag {
  font-family: var(--font-mono); font-size: 11px;
  color: var(--md-sys-color-on-surface-faint);
}

/* ===== Material 3 chips & buttons ===== */
.m3-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  border-radius: 100px;
  font-size: 12px; font-weight: 500;
}
.m3-chip .material-symbols-rounded { font-size: 14px; }
.m3-chip--assist {
  background: var(--md-sys-color-surface-container-high);
  color: var(--md-sys-color-on-surface-variant);
  border: 1px solid var(--md-sys-color-outline-variant);
}
.m3-chip--link {
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-primary);
}
.m3-chip--link:hover { background: rgba(168,199,250,0.22); text-decoration: none; }

.m3-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px;
  border-radius: 100px;
  font-family: var(--font-body); font-weight: 500; font-size: 13px;
  transition: background 200ms var(--md-easing-emphasized);
}
.m3-btn .material-symbols-rounded { font-size: 16px; }
.m3-btn--outlined {
  background: transparent;
  color: var(--md-sys-color-primary);
  border: 1px solid var(--md-sys-color-outline-variant);
}
.m3-btn--outlined:hover { background: var(--md-sys-color-primary-container); text-decoration: none; }

/* ===== FAB ===== */
.fab {
  position: fixed; bottom: 32px; right: 32px;
  display: inline-flex; align-items: center; gap: 10px;
  padding: 14px 22px;
  background: var(--md-sys-color-primary);
  color: #002e69;
  border: none; border-radius: 16px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.45), 0 2px 4px rgba(0,0,0,0.3);
  cursor: pointer;
  font-family: var(--font-body); font-weight: 600; font-size: 14px;
  z-index: 30;
  text-decoration: none;
  transition: background 200ms var(--md-easing-emphasized),
              transform 200ms var(--md-easing-emphasized),
              box-shadow 200ms var(--md-easing-emphasized);
}
.fab:hover { transform: translateY(-2px); text-decoration: none; }
.fab .material-symbols-rounded { font-size: 20px; }
@media (max-width: 600px) {
  .fab { bottom: 20px; right: 20px; padding: 12px 16px; }
  .fab__label { display: none; }
}

/* ===== Preview tag (only on v2 preview files) ===== */
.preview-tag {
  padding: 6px 12px;
  border-radius: var(--md-shape-corner-small);
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-primary);
}
"""


def render_kpi(kpi: dict) -> str:
    value_class = kpi.get("value_class", "")
    return f"""
    <div class="kpi">
      <div class="kpi__header">
        <span class="material-symbols-rounded">{kpi['icon']}</span>
        <span class="label-medium">{html.escape(kpi['label'])}</span>
      </div>
      <div class="kpi__value {value_class}">{html.escape(kpi['value'])}</div>
      <div class="kpi__sub {kpi['chip']['cls']}">
        <span class="material-symbols-rounded">{kpi['chip']['icon']}</span>
        {html.escape(kpi['chip']['label'])}
      </div>
      <div class="kpi__caption">{html.escape(kpi['caption'])}</div>
    </div>
    """


def build_html(manifest: dict,
               prior_manifest: Optional[dict],
               subtopics: List[dict],
               prior_subtopics: List[dict],
               md_sections: Dict[str, str],
               preview: bool = True) -> str:
    week = manifest.get("week", "—")
    date_range = manifest.get("date_range", "—")
    title = f"OCV Weekly · Outlook AI Agent · Week of {week}"

    hero_cards = compute_hero(manifest, prior_manifest)
    hero_cards.append(compute_priority_card(subtopics, prior_subtopics))
    hero_html = "".join(render_kpi(k) for k in hero_cards)

    tldr_md = md_sections.get("TL;DR", "")
    findings_md = md_sections.get("Key Findings", "")

    tldr_html = md_to_html(tldr_md) if tldr_md else '<p class="md-p body-medium">No TL;DR provided.</p>'
    findings_html = md_to_html(findings_md) if findings_md else '<p class="md-p body-medium">No findings provided.</p>'

    dataset_html = render_dataset_card(manifest)
    topics_html = render_topic_shifts(manifest, prior_manifest)
    routing_html = render_routing(manifest)
    categories_html = render_category_breakdown(manifest)
    tickets_html = render_tickets(subtopics)

    # TOC items (only show sections that have content)
    toc_items = [("hero", "Headline", "speed")]
    toc_items.append(("tldr", "TL;DR", "summarize"))
    toc_items.append(("dataset", "Dataset", "dataset"))
    if findings_md: toc_items.append(("findings", "Findings", "lightbulb"))
    if topics_html: toc_items.append(("topics", "Topic shifts", "compare_arrows"))
    if routing_html: toc_items.append(("routing", "Routing", "route"))
    if categories_html: toc_items.append(("categories", "Categories", "category"))
    if tickets_html: toc_items.append(("tickets", "Tickets", "assignment"))

    toc_html = "".join(
        f'<a href="#{i[0]}" data-target="{i[0]}"><span class="material-symbols-rounded">{i[2]}</span>{html.escape(i[1])}</a>'
        for i in toc_items
    )

    preview_tag = '<div class="preview-tag label-medium">M3 Preview</div>' if preview else ''

    body = f"""<body>
  <div class="top-bar">
    <a class="top-bar__back" href="../index.html" aria-label="Back to dashboard">
      <span class="material-symbols-rounded">arrow_back</span>
    </a>
    <div>
      <div class="top-bar__title title-large">OCV Weekly · Outlook AI Agent</div>
      <div class="top-bar__subtitle body-small">Week of {html.escape(date_range)}</div>
    </div>
    <div class="top-bar__spacer"></div>
    {preview_tag}
    <div class="top-bar__chip label-medium">
      <span class="top-bar__chip-dot"></span>
      <span>live</span>
    </div>
  </div>

  <div class="page">
    <nav class="toc" id="toc">{toc_html}</nav>

    <main class="content">
      <section id="hero">
        <div class="hero-header" style="margin-bottom: 16px;">
          <div class="hero-header__title headline-large">Week of {html.escape(date_range)}</div>
          <div class="hero-header__subtitle body-large">Customer-feedback signal · classified into 13-topic taxonomy</div>
        </div>
        <div class="hero">{hero_html}</div>
      </section>

      <section id="tldr" class="report-card">
        <div class="section-eyebrow label-medium">01 · TL;DR</div>
        <h2 class="section-title headline-medium">What you need to know</h2>
        <div class="md-content">{tldr_html}</div>
      </section>

      {dataset_html}

      <section id="findings" class="report-card">
        <div class="section-eyebrow label-medium">04 · Key findings</div>
        <h2 class="section-title headline-medium">What the data is telling us</h2>
        <div class="md-content">{findings_html}</div>
      </section>

      {topics_html}
      {routing_html}
      {categories_html}
      {tickets_html}
    </main>
  </div>

  <a class="fab" href="#hero" aria-label="Back to top">
    <span class="material-symbols-rounded">arrow_upward</span>
    <span class="fab__label">Back to top</span>
  </a>

  <script>
    // Active-section highlighting in TOC
    (function () {{
      const links = Array.from(document.querySelectorAll('.toc a'));
      const targets = links.map(a => document.getElementById(a.dataset.target)).filter(Boolean);
      function onScroll() {{
        const fromTop = window.scrollY + 120;
        let activeIdx = 0;
        targets.forEach((t, i) => {{ if (t.offsetTop <= fromTop) activeIdx = i; }});
        links.forEach((a, i) => a.classList.toggle('is-active', i === activeIdx));
      }}
      window.addEventListener('scroll', onScroll, {{ passive: true }});
      onScroll();
    }})();
  </script>
</body>
</html>
"""
    return HEAD.format(title=html.escape(title), css=CSS) + body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def find_prior_manifest(manifest_path: Path, week: str) -> Optional[Path]:
    manifests = sorted(manifest_path.parent.glob("ocv_outlook-agent_*_manifest.json"))
    earlier = [p for p in manifests if (m := re.search(r"ocv_outlook-agent_(\d{4}-\d{2}-\d{2})_manifest\.json", p.name)) and m.group(1) < week]
    return earlier[-1] if earlier else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--subtopics", default=None)
    ap.add_argument("--prior-manifest", default=None)
    ap.add_argument("--prior-subtopics", default=None)
    ap.add_argument("--report-md", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-preview-tag", action="store_true", help="omit the 'M3 Preview' badge in the top bar")
    args = ap.parse_args(argv)

    mp = Path(args.manifest)
    manifest = load_json(mp)
    week = manifest.get("week", "")
    base_dir = mp.parent.parent  # data/

    # auto-resolve subtopics
    sub_path = Path(args.subtopics) if args.subtopics else (base_dir / f"ocv_outlook-agent_{week}_subtopics.csv")
    subtopics = load_subtopics(sub_path if sub_path.exists() else None)

    # auto-resolve prior manifest
    if args.prior_manifest:
        prior_mp: Optional[Path] = Path(args.prior_manifest)
    else:
        wow_basis = manifest.get("wow_basis")
        prior_mp = Path(wow_basis) if wow_basis and Path(wow_basis).exists() else find_prior_manifest(mp, week)
    prior_manifest = load_json(prior_mp) if prior_mp and prior_mp.exists() else None

    prior_sub: List[dict] = []
    if prior_manifest:
        pwk = prior_manifest.get("week", "")
        psub_path = Path(args.prior_subtopics) if args.prior_subtopics else (base_dir / f"ocv_outlook-agent_{pwk}_subtopics.csv")
        prior_sub = load_subtopics(psub_path if psub_path.exists() else None)

    # auto-resolve report.md
    md_path = Path(args.report_md) if args.report_md else (base_dir / f"ocv_outlook-agent_{week}_report.md")
    md_sections = load_markdown_sections(md_path if md_path.exists() else None)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_str = build_html(manifest, prior_manifest, subtopics, prior_sub, md_sections, preview=not args.no_preview_tag)
    out_path.write_text(html_str, encoding="utf-8")
    print(f"[publish_ocv_report_v2] wrote {out_path} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

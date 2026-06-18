#!/usr/bin/env python3
"""draft_ocv_email.py — Build an Outlook-safe HTML email for the weekly OCV report
and save it as a DRAFT in the user's local Classic Outlook (Drafts folder).

Recipients (To/Cc/Bcc) are deliberately left blank — the user adds them in Outlook
before sending. This script never sends mail.

Doctrine: see .claude/skills/ocv-draft-email/SKILL.md
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Canonical taxonomy — kept in lockstep with scripts/publish_ocv_report.py
TOPIC_NAMES: Dict[int, str] = {
    1:  "Action not executed",
    2:  "HitL violation",
    3:  "Output doesn't match intent",
    4:  "Constraints ignored",
    5:  "Unnecessary clarifying question",
    6:  "Reliability failure",
    7:  "Inaccurate or fabricated content",
    8:  "Wrong context / grounding",
    9:  "Tone / language / format",
    10: "File I/O failure",
    11: "Calendar correctness",
    12: "Capability refusal",
    13: "Intrusive Outlook Agent UI",
}

# Defaults — overrideable via CLI
DEFAULT_DASHBOARD_URL = "https://gim-home.github.io/OCV-Weekly/"
DEFAULT_MIRROR_URL    = "https://yohncf.github.io/OCV-Weekly_temp/"
DEFAULT_REPORT_URL_FMT = "https://gim-home.github.io/OCV-Weekly/reports/{week}.html"
DEFAULT_METRICS_PATH   = Path("_ocv_weekly_repo/metrics_v2.json")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def count_subtopic_rows(subtopics_csv: Path) -> Tuple[int, Dict[str, int], Dict[str, int]]:
    """Return (total_rows, priority_breakdown, ticket_worthy_breakdown)."""
    priority: Dict[str, int] = {}
    worthy:   Dict[str, int] = {}
    total = 0
    with subtopics_csv.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            p = (row.get("Priority") or "").strip()
            w = (row.get("Ticket Worthy") or "").strip()
            if p:
                priority[p] = priority.get(p, 0) + 1
            if w:
                worthy[w] = worthy.get(w, 0) + 1
    return total, priority, worthy


def extract_tldr_bullets(report_md: Optional[Path]) -> List[Tuple[str, str]]:
    """Return list of (bold-prefix, rest) from the markdown report's `## TL;DR` list."""
    if not report_md or not report_md.exists():
        return []
    text = report_md.read_text(encoding="utf-8")
    m = re.search(r"##\s+TL;DR\s*\n+(.+?)(?=\n##\s|\Z)", text, flags=re.DOTALL)
    if not m:
        return []
    bullets: List[Tuple[str, str]] = []
    for raw in m.group(1).splitlines():
        raw = raw.strip()
        if not raw.startswith("- "):
            continue
        body = raw[2:].strip()
        bm = re.match(r"\*\*(.+?)\*\*\s+(.*)$", body)
        if bm:
            bullets.append((bm.group(1), bm.group(2)))
        else:
            bullets.append(("", body))
    return bullets


# ---------------------------------------------------------------------------
# HTML helpers (inline styles only — Outlook strips <style>/<link>)
# ---------------------------------------------------------------------------
FONT = "Segoe UI,Arial,sans-serif"

def kpi_cell(label: str, value: str, delta: str, delta_color: str, arrow: str, caption: str, value_size: str = "32px") -> str:
    return f'''
<td valign="top" width="25%" style="padding:6px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#ffffff; border:1px solid #dadce0; border-radius:8px;">
    <tr><td style="padding:14px 16px;">
      <div style="font-family:{FONT}; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#5f6368;">{label}</div>
      <div style="font-family:{FONT}; font-size:{value_size}; font-weight:600; color:#202124; margin:6px 0 4px 0; line-height:1.2;">{value}</div>
      <div style="font-family:{FONT}; font-size:12px; font-weight:600; color:{delta_color};">{arrow} {delta}</div>
      <div style="font-family:{FONT}; font-size:11px; color:#5f6368; margin-top:4px;">{caption}</div>
    </td></tr>
  </table>
</td>'''


def wow_chip(delta_text: str, direction: str) -> str:
    if direction == "up":
        color, bg, arrow = "#b3261e", "#fce8e6", "&#9650;"
    elif direction == "down":
        color, bg, arrow = "#1e8e3e", "#e6f4ea", "&#9660;"
    else:
        color, bg, arrow = "#5f6368", "#f1f3f4", "&mdash;"
    return (
        f'<span style="display:inline-block; padding:2px 8px; border-radius:10px; '
        f'background:{bg}; color:{color}; font-size:12px; font-weight:600; '
        f'font-family:{FONT};">{arrow}&nbsp;{delta_text}</span>'
    )


def section_eyebrow(eyebrow: str, title: str) -> str:
    return (
        f'<div style="font-family:{FONT}; font-size:11px; letter-spacing:.08em; '
        f'text-transform:uppercase; color:#5f6368; margin-bottom:6px;">{eyebrow}</div>'
        f'<div style="font-family:{FONT}; font-size:18px; font-weight:600; color:#202124; '
        f'margin-bottom:12px;">{title}</div>'
    )


# ---------------------------------------------------------------------------
# Progress-at-a-glance chart (inline SVG, dual-axis line)
# ---------------------------------------------------------------------------
# NOTE on email-client compatibility:
#   Inline SVG renders correctly in OWA, Outlook on macOS, Apple Mail, Gmail,
#   and the Outlook mobile apps. Classic Outlook on Windows (Word renderer)
#   does NOT render <svg> — recipients there will see an empty space with the
#   caption text instead. This is a known and accepted tradeoff (decided
#   2026-06-08); the caption + dashboard link gives those readers a graceful
#   fallback path.
def _fmt_short_week(s: str) -> str:
    """ISO date 'YYYY-MM-DD' -> 'MMM D' (e.g. '2026-06-01' -> 'Jun 1')."""
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return s
    return dt.strftime("%b %#d") if os.name == "nt" else dt.strftime("%b %-d")


def build_progress_svg(metrics_path: Optional[Path]) -> str:
    """Build a dual-axis line chart (negatives + rating %) from metrics_v2.json.

    Returns the <svg>...</svg> string, or "" if the metrics file is missing,
    unparseable, or contains no usable week entries.
    """
    if not metrics_path or not metrics_path.exists():
        return ""
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    weeks = data.get("weeks") or []
    if not weeks:
        return ""
    weeks = sorted(weeks, key=lambda w: (w.get("week_of") or ""))

    pts: List[Dict[str, Any]] = []
    for w in weeks:
        wk = w.get("week_of") or ""
        neg = w.get("negative_items")
        rating = (w.get("rating") or {})
        rate = rating.get("positive_pct")
        if wk and isinstance(neg, (int, float)):
            pts.append({"wk": wk, "neg": neg, "rate": rate})
    if not pts:
        return ""

    # Canvas + plot area
    W, H = 720, 320
    PT, PB, PL, PR = 70, 56, 56, 56
    PW = W - PL - PR
    PH = H - PT - PB

    # Axis scales (nearest "nice" cap above max, with floors)
    max_neg = max((p["neg"] for p in pts), default=0)
    neg_max = max(100, (int(max_neg) // 50 + 1) * 50 + 50)
    rates = [p["rate"] for p in pts if isinstance(p["rate"], (int, float))]
    max_rate = max(rates) if rates else 0
    rate_max = max(20, (int(max_rate) // 10 + 1) * 10 + 10)

    n = len(pts)
    xs = [PL + PW / 2] if n == 1 else [PL + i * PW / (n - 1) for i in range(n)]
    y_neg  = lambda v: PT + PH - (v / neg_max)  * PH
    y_rate = lambda v: PT + PH - (v / rate_max) * PH

    NEG, POS, GRID, TEXT, DARK = "#e07a5f", "#588157", "#e8eaed", "#5f6368", "#202124"

    # Horizontal gridlines + axis tick labels (6 ticks → 5 intervals)
    # NOTE: OWA's HTML sanitizer strips bare <line> elements, so we render
    # grid rules as 1-px-tall <rect> (preserved) instead. Same visual.
    grid: List[str] = []
    for i in range(6):
        y = PT + i * PH / 5
        grid.append(
            f'<rect x="{PL}" y="{y:.1f}" width="{PW}" height="1" '
            f'fill="{GRID}"/>'
        )
        nv = neg_max  * (1 - i / 5)
        rv = rate_max * (1 - i / 5)
        grid.append(
            f'<text x="{PL - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="{FONT}" font-size="10" fill="{NEG}">{int(round(nv))}</text>'
        )
        grid.append(
            f'<text x="{PL + PW + 8}" y="{y + 4:.1f}" text-anchor="start" '
            f'font-family="{FONT}" font-size="10" fill="{POS}">{int(round(rv))}%</text>'
        )

    # X-axis labels
    x_labels = [
        f'<text x="{xs[i]:.1f}" y="{PT + PH + 18:.1f}" text-anchor="middle" '
        f'font-family="{FONT}" font-size="11" fill="{DARK}">{_fmt_short_week(p["wk"])}</text>'
        for i, p in enumerate(pts)
    ]

    # Negatives line — rendered as <path> instead of <polyline>; OWA strips
    # polylines as part of its XSS sanitization but keeps <path>.
    neg_path_d = "M " + " L ".join(f"{xs[i]:.1f},{y_neg(p['neg']):.1f}" for i, p in enumerate(pts))
    neg_poly = (
        f'<path d="{neg_path_d}" fill="none" stroke="{NEG}" '
        f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    neg_markers: List[str] = []
    for i, p in enumerate(pts):
        cy = y_neg(p["neg"])
        # Flip label below the marker if it would overlap the top edge / title row.
        ly = cy + 16 if cy < PT + 18 else cy - 9
        neg_markers.append(
            f'<circle cx="{xs[i]:.1f}" cy="{cy:.1f}" r="4" fill="{NEG}" '
            f'stroke="#ffffff" stroke-width="1.5"/>'
            f'<text x="{xs[i]:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="11" font-weight="600" fill="{NEG}">'
            f'{int(p["neg"])}</text>'
        )

    # Rating line + markers + datalabels — same <path> substitution for OWA.
    rate_pts = [(xs[i], p["rate"]) for i, p in enumerate(pts) if isinstance(p["rate"], (int, float))]
    if rate_pts:
        rate_path_d = "M " + " L ".join(f"{x:.1f},{y_rate(v):.1f}" for x, v in rate_pts)
        rate_poly = (
            f'<path d="{rate_path_d}" fill="none" stroke="{POS}" '
            f'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    else:
        rate_poly = ""
    rate_markers: List[str] = []
    for x, v in rate_pts:
        cy = y_rate(v)
        # Flip label above the marker if it would overlap the bottom edge / x labels.
        ly = cy - 9 if cy > PT + PH - 18 else cy + 16
        rate_markers.append(
            f'<circle cx="{x:.1f}" cy="{cy:.1f}" r="4" fill="{POS}" '
            f'stroke="#ffffff" stroke-width="1.5"/>'
            f'<text x="{x:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="11" font-weight="600" fill="{POS}">'
            f'{int(round(v))}%</text>'
        )

    # Title (above plot, left-aligned)
    title = (
        f'<text x="{PL}" y="20" font-family="{FONT}" font-size="14" font-weight="600" fill="{DARK}">'
        f'<tspan fill="{POS}">User Rating</tspan>'
        f'<tspan fill="{DARK}"> vs </tspan>'
        f'<tspan fill="{NEG}">Negative comments</tspan>'
        f'</text>'
    )

    # Inline legend (just right of the title block)
    legend = (
        f'<g>'
        f'<circle cx="{PL + 4}" cy="44" r="5" fill="{POS}"/>'
        f'<text x="{PL + 14}" y="48" font-family="{FONT}" font-size="11" fill="{DARK}">User rating</text>'
        f'<circle cx="{PL + 110}" cy="44" r="5" fill="{NEG}"/>'
        f'<text x="{PL + 120}" y="48" font-family="{FONT}" font-size="11" fill="{DARK}">Negative comments received</text>'
        f'</g>'
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Weekly trend of user rating percentage and negative-comment volume">'
        f'{title}{legend}'
        f'{"".join(grid)}'
        f'{"".join(x_labels)}'
        f'{neg_poly}{rate_poly}'
        f'{"".join(neg_markers)}{"".join(rate_markers)}'
        f'</svg>'
    )
    return svg


PROGRESS_CHART_CID    = "ocv_progress_chart"
PROGRESS_CHART_WIDTH  = 720
PROGRESS_CHART_HEIGHT = 320


def build_progress_section(chart_html: str, dashboard_url: str) -> str:
    """Wrap a chart payload (inline SVG or <img>) in the styled progress card.

    The card layout/labels are identical regardless of transport — callers
    decide whether to embed an inline <svg> or an <img src="cid:..."> /
    relative-PNG reference and pass it as ``chart_html``.
    """
    if not chart_html:
        return ""
    return (
        f'<div style="margin:0 0 24px 0; padding:18px 20px 14px; '
        f'background:#ffffff; border:1px solid #e8eaed; border-radius:10px;">'
        f'  <div style="font-family:{FONT}; font-size:11px; letter-spacing:.08em; '
        f'text-transform:uppercase; color:#5f6368; margin-bottom:4px;">Progress at a glance</div>'
        f'  <div style="font-family:{FONT}; font-size:13px; font-weight:600; color:#202124; '
        f'margin:0 0 4px 0;">'
        f'<span style="color:#588157;">User Rating</span> vs '
        f'<span style="color:#e07a5f;">Negative comments</span>'
        f'  </div>'
        f'  <div style="font-family:{FONT}; font-size:12px; color:#5f6368; '
        f'margin:0 0 10px 0; line-height:1.45;">'
        f'Trend across every published week &mdash; total negative feedback received '
        f'and the share of users rating the agent positively.'
        f'  </div>'
        f'  <div style="text-align:center;">{chart_html}</div>'
        f'  <div style="font-family:{FONT}; font-size:11px; color:#5f6368; '
        f'margin-top:8px; text-align:right;">'
        f'<em>Interactive version on the <a href="{dashboard_url}" '
        f'style="color:#1a73e8;">dashboard</a>.</em>'
        f'  </div>'
        f'</div>'
    )


def render_progress_png(metrics_path: Optional[Path], out_png: Path, scale: int = 2) -> Optional[Path]:
    """Render the progress chart SVG to a PNG via Playwright.

    Returns the PNG path on success, or ``None`` if the SVG cannot be built
    or Playwright is unavailable. Used by the CID-embedding flow so the
    chart renders in OWA (which strips inline SVG entirely).
    """
    svg = build_progress_svg(metrics_path)
    if not svg:
        return None
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        sys.stderr.write(
            "[draft] Playwright not installed; cannot render chart PNG. "
            "Run `pip install playwright && playwright install chromium`, "
            "or pass --chart-mode svg (renders inline SVG, blank in OWA) "
            "or --chart-mode none.\n"
        )
        return None

    W, H = PROGRESS_CHART_WIDTH, PROGRESS_CHART_HEIGHT
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = out_png.with_suffix(".render.html")
    tmp_html.write_text(
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>html,body{{margin:0;padding:0;background:#fff;}}"
        f"svg{{display:block;}}</style></head><body>{svg}</body></html>",
        encoding="utf-8",
    )
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": W, "height": H},
                device_scale_factor=scale,
            )
            page.goto(f"file:///{tmp_html.resolve().as_posix()}")
            page.wait_for_load_state("networkidle")
            page.locator("svg").screenshot(path=str(out_png))
            browser.close()
    finally:
        try:
            tmp_html.unlink()
        except OSError:
            pass
    return out_png if out_png.exists() else None


# ---------------------------------------------------------------------------
# Body builders
# ---------------------------------------------------------------------------
def build_kpi_row(manifest: dict, prior: Optional[dict]) -> str:
    rating = manifest.get("rating", {})
    up = rating.get("ThumbsUp", 0)
    down = rating.get("ThumbsDown", 0)
    total = up + down
    pct_up = (100.0 * up / total) if total else 0.0

    prior_pct_up = None
    if prior:
        pr = prior.get("rating", {})
        pu, pd = pr.get("ThumbsUp", 0), pr.get("ThumbsDown", 0)
        if pu + pd:
            prior_pct_up = 100.0 * pu / (pu + pd)
    if prior_pct_up is None:
        rating_delta = "no prior"
        rating_arrow = "&mdash;"
        rating_color = "#5f6368"
    else:
        delta_pp = pct_up - prior_pct_up
        if abs(delta_pp) < 0.05:
            rating_delta, rating_arrow, rating_color = "flat vs last week", "&mdash;", "#5f6368"
        elif delta_pp > 0:
            rating_delta, rating_arrow, rating_color = f"+{delta_pp:.1f} pp vs last week", "&#9650;", "#1e8e3e"
        else:
            rating_delta, rating_arrow, rating_color = f"{delta_pp:.1f} pp vs last week", "&#9660;", "#b3261e"

    # Verbatim negatives KPI
    neg = manifest.get("negative_items", 0)
    verbatim = manifest.get("verbatim_items", 0)
    total_items = manifest.get("total_items", 0)
    prior_neg = prior.get("negative_items", 0) if prior else 0
    if prior_neg:
        delta_pct = 100.0 * (neg - prior_neg) / prior_neg
        if abs(delta_pct) < 0.5:
            neg_delta, neg_arrow, neg_color = "flat vs last week", "&mdash;", "#5f6368"
        elif delta_pct > 0:
            neg_delta, neg_arrow, neg_color = f"+{delta_pct:.1f}% vs last week", "&#9650;", "#b3261e"
        else:
            neg_delta, neg_arrow, neg_color = f"{delta_pct:.1f}% vs last week", "&#9660;", "#1e8e3e"
    else:
        neg_delta, neg_arrow, neg_color = "no prior", "&mdash;", "#5f6368"

    # Top pain topic
    topic_counts = {int(k): v for k, v in manifest.get("topic_counts", {}).items()}
    prior_counts = {int(k): v for k, v in (prior.get("topic_counts", {}) if prior else {}).items()}
    top_id, top_n = (max(topic_counts.items(), key=lambda kv: kv[1]) if topic_counts else (None, 0))
    top_name = TOPIC_NAMES.get(top_id, "—") if top_id else "—"
    if top_id and prior_counts.get(top_id, 0):
        d = top_n - prior_counts[top_id]
        if d > 0:
            top_delta, top_arrow, top_color = f"+{d} items vs last week", "&#9650;", "#b3261e"
        elif d < 0:
            top_delta, top_arrow, top_color = f"{d} items vs last week", "&#9660;", "#1e8e3e"
        else:
            top_delta, top_arrow, top_color = "flat vs last week", "&mdash;", "#5f6368"
    else:
        top_delta, top_arrow, top_color = "no prior comparison", "&mdash;", "#5f6368"

    # P0+P1 queue from subtopics breakdown (caller injects this)
    return ""  # placeholder; we build below for clarity


def build_full_body(
    manifest: dict,
    prior: Optional[dict],
    subtopics_csv: Path,
    report_md: Optional[Path],
    dashboard_url: str,
    mirror_url: Optional[str],
    ado_query_url: Optional[str],
    highlights: Optional[str],
    intro_paragraph: Optional[str],
    progress_paragraph: Optional[str],
    closing_paragraphs: List[str],
    signoff: Optional[str],
    ado_writes: Optional[int] = None,
    chart_html: str = "",
) -> str:
    week = manifest.get("week", "")
    date_range = manifest.get("date_range", week)
    rating = manifest.get("rating", {})
    up, down = rating.get("ThumbsUp", 0), rating.get("ThumbsDown", 0)
    total = up + down
    pct_up = (100.0 * up / total) if total else 0.0

    neg = manifest.get("negative_items", 0)
    verbatim = manifest.get("verbatim_items", 0)
    total_items = manifest.get("total_items", 0)

    topic_counts = {int(k): v for k, v in manifest.get("topic_counts", {}).items()}
    topic_pcts   = {int(k): v for k, v in manifest.get("topic_percentages", {}).items()}
    prior_counts = {int(k): v for k, v in (prior.get("topic_counts", {}) if prior else {}).items()}
    prior_pcts   = {int(k): v for k, v in (prior.get("topic_percentages", {}) if prior else {}).items()}
    prior_week   = (prior or {}).get("week", "")
    prior_total_neg = (prior or {}).get("negative_items", 0)

    # ---- KPI deltas
    # Rating
    prior_pct_up = None
    if prior:
        pr = prior.get("rating", {})
        pu, pd = pr.get("ThumbsUp", 0), pr.get("ThumbsDown", 0)
        if pu + pd:
            prior_pct_up = 100.0 * pu / (pu + pd)
    if prior_pct_up is None:
        rating_delta, rating_arrow, rating_color = "no prior", "&mdash;", "#5f6368"
    else:
        delta_pp = pct_up - prior_pct_up
        if abs(delta_pp) < 0.05:
            rating_delta, rating_arrow, rating_color = "flat vs last week", "&mdash;", "#5f6368"
        elif delta_pp > 0:
            rating_delta, rating_arrow, rating_color = f"+{delta_pp:.1f} pp vs last week", "&#9650;", "#1e8e3e"
        else:
            rating_delta, rating_arrow, rating_color = f"{delta_pp:.1f} pp vs last week", "&#9660;", "#b3261e"

    # Negatives
    if prior_total_neg:
        d_pct = 100.0 * (neg - prior_total_neg) / prior_total_neg
        if abs(d_pct) < 0.5:
            neg_delta, neg_arrow, neg_color = "flat vs last week", "&mdash;", "#5f6368"
        elif d_pct > 0:
            neg_delta, neg_arrow, neg_color = f"+{d_pct:.1f}% vs last week", "&#9650;", "#b3261e"
        else:
            neg_delta, neg_arrow, neg_color = f"{d_pct:.1f}% vs last week", "&#9660;", "#1e8e3e"
    else:
        neg_delta, neg_arrow, neg_color = "no prior", "&mdash;", "#5f6368"

    # Top topic
    top_id, top_n = (max(topic_counts.items(), key=lambda kv: kv[1]) if topic_counts else (None, 0))
    top_name = TOPIC_NAMES.get(top_id, "—") if top_id else "—"
    if top_id and prior_counts.get(top_id, 0):
        d = top_n - prior_counts[top_id]
        if d > 0:
            top_delta, top_arrow, top_color = f"+{d} items vs last week", "&#9650;", "#b3261e"
        elif d < 0:
            top_delta, top_arrow, top_color = f"{d} items vs last week", "&#9660;", "#1e8e3e"
        else:
            top_delta, top_arrow, top_color = "flat vs last week", "&mdash;", "#5f6368"
    else:
        top_delta, top_arrow, top_color = "no prior", "&mdash;", "#5f6368"

    # P0+P1 queue
    sub_total, prio_breakdown, _ = count_subtopic_rows(subtopics_csv)
    p0 = prio_breakdown.get("P0", 0)
    p1 = prio_breakdown.get("P1", 0)
    p0p1 = p0 + p1
    if ado_writes is not None:
        p_caption = f"{p0} P0 &middot; {p1} P1 &middot; {ado_writes} synced to ADO"
    else:
        p_caption = f"{p0} P0 &middot; {p1} P1 &middot; {sub_total} subtopic rows"
    p_delta, p_arrow, p_color = "flat vs last week", "&mdash;", "#5f6368"

    kpis = [
        kpi_cell("Rating", f"{pct_up:.1f}%", rating_delta, rating_color, rating_arrow,
                 f"{up:,} up &middot; {down:,} down"),
        kpi_cell("Verbatim negatives", f"{neg:,}", neg_delta, neg_color, neg_arrow,
                 f"of {verbatim:,} verbatim, {total_items:,} total submissions"),
        kpi_cell("Top pain topic", top_name, top_delta, top_color, top_arrow,
                 f"{top_n} negatives this week", value_size="18px"),
        kpi_cell("P0 + P1 queue", str(p0p1), p_delta, p_color, p_arrow, p_caption),
    ]
    kpi_row = "".join(kpis)

    # ---- TL;DR bullets
    bullets = extract_tldr_bullets(report_md)
    if bullets:
        tldr_html = "".join(
            f'<li style="margin:0 0 8px 0;"><strong>{label}</strong> {text}</li>' if label
            else f'<li style="margin:0 0 8px 0;">{text}</li>'
            for label, text in bullets
        )
    else:
        tldr_html = (
            f'<li style="margin:0 0 8px 0;"><strong>Volume.</strong> '
            f'{total_items:,} OCV submissions ingested; {neg} classified as Negative.</li>'
        )

    # ---- WoW topic shifts
    sorted_topics = sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    wow_rows_html = ""
    for tid, tw_n in sorted_topics:
        tw_p = topic_pcts.get(tid, 0.0)
        lw_n = prior_counts.get(tid, 0)
        lw_p = prior_pcts.get(tid, 0.0)
        if prior:
            d_pp = tw_p - lw_p
            if abs(d_pp) < 0.05:
                chip = wow_chip("flat", "flat")
            elif d_pp > 0:
                chip = wow_chip(f"+{d_pp:.1f} pp", "up")
            else:
                chip = wow_chip(f"{d_pp:.1f} pp", "down")
        else:
            chip = wow_chip("&mdash;", "flat")
        name = TOPIC_NAMES.get(tid, f"Topic {tid}")
        wow_rows_html += f'''
<tr>
  <td style="padding:8px 10px; border-bottom:1px solid #e8eaed; font-family:{FONT}; font-size:13px; color:#202124;">
    <span style="display:inline-block; width:22px; height:22px; line-height:22px; text-align:center; border-radius:50%; background:#f1f3f4; color:#5f6368; font-size:11px; font-weight:600; margin-right:8px;">{tid}</span>
    {name}
  </td>
  <td align="right" style="padding:8px 10px; border-bottom:1px solid #e8eaed; font-family:{FONT}; font-size:13px; color:#202124; white-space:nowrap;">{tw_n} <span style="color:#5f6368; font-size:11px;">({tw_p:.1f}%)</span></td>
  <td align="right" style="padding:8px 10px; border-bottom:1px solid #e8eaed; font-family:{FONT}; font-size:13px; color:#5f6368; white-space:nowrap;">{lw_n} <span style="font-size:11px;">({lw_p:.1f}%)</span></td>
  <td align="right" style="padding:8px 10px; border-bottom:1px solid #e8eaed; white-space:nowrap;">{chip}</td>
</tr>'''

    wow_title = (
        f"WoW shifts vs week of {prior_week}" if prior_week
        else "Topic distribution this week"
    )

    # ---- Intro paragraphs
    intro = intro_paragraph or (
        "Sharing this week&rsquo;s OCV feedback breakdown for the Outlook Agent in Frontier."
    )
    progress = progress_paragraph or (
        "I&rsquo;ve created a new <strong>&ldquo;Progress at a glance&rdquo;</strong> section at the top of the index "
        "to provide a clear, immediate snapshot of user sentiment and reactions (&#128077;&#128078;) to the Outlook Agent."
    )
    head = highlights or ""

    mirror_html = ""
    if mirror_url:
        mirror_html = (
            f'<br>And here is the mirror repo, in case you don&rsquo;t have access to GitHub EMU: '
            f'<a href="{mirror_url}" style="color:#1a73e8;">{mirror_url}</a>'
        )

    # ---- Closing paragraphs
    closing_html = ""
    if closing_paragraphs or ado_query_url or signoff:
        parts: List[str] = []
        if ado_query_url:
            parts.append(
                f'<p style="margin:0 0 12px 0;">'
                f'New OCV tickets have been added and assigned here: '
                f'<a href="{ado_query_url}" style="color:#1a73e8;">ADO query &mdash; new items this week</a>. '
                f'Links to those new items can be found in the dashboard, now they will include the Dash links too.'
                f'</p>'
            )
        for para in (closing_paragraphs or []):
            parts.append(f'<p style="margin:0 0 12px 0;">{para}</p>')
        if signoff:
            parts.append(f'<p style="margin:0;">{signoff}</p>')
        closing_html = (
            f'<div style="margin-top:28px; border-top:1px solid #e8eaed; padding-top:18px; '
            f'font-family:{FONT}; font-size:14px; color:#202124; line-height:1.55;">'
            + "".join(parts) + "</div>"
        )

    head_html = f'<p style="margin:0 0 20px 0;">{head}</p>' if head else ""

    # ---- Progress-at-a-glance chart (caller passes the rendered chart payload
    # — inline SVG, <img cid:...>, or relative-path PNG <img>. The card wrapper
    # is the same regardless of transport; see render_progress_png + the COM
    # helper's create action for the CID-attached PNG path that survives OWA.)
    progress_chart_html = build_progress_section(chart_html, dashboard_url) if chart_html else ""

    return f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center">
<table role="presentation" width="720" cellpadding="0" cellspacing="0" border="0" style="max-width:720px;">
<tr><td style="padding:16px 8px; font-family:{FONT}; font-size:14px; color:#202124; line-height:1.55;">

<p style="margin:0 0 12px 0;">Hello there,</p>

<p style="margin:0 0 12px 0;">{intro}</p>

<p style="margin:0 0 12px 0;">
  As a reminder, the dashboard with more details on each category and ADO items to follow can be found here:
  <a href="{dashboard_url}" style="color:#1a73e8;">{dashboard_url}</a>{mirror_html}
</p>

<p style="margin:0 0 12px 0;">{progress}</p>

{head_html}

{progress_chart_html}

<!-- Headline numbers -->
{section_eyebrow("Headline numbers", f"Week of {date_range}")}

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:24px;">
  <tr>{kpi_row}</tr>
</table>

<!-- TL;DR -->
{section_eyebrow("TL;DR", "What you need to know")}
<ul style="margin:0 0 24px 0; padding-left:22px; font-family:{FONT}; font-size:13px; color:#202124; line-height:1.55;">
  {tldr_html}
</ul>

<!-- Topic shifts -->
{section_eyebrow("Topic shifts (week over week)", wow_title)}

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #e8eaed; border-radius:8px; border-collapse:separate;">
  <thead>
    <tr style="background:#f8f9fa;">
      <th align="left"  style="padding:10px; font-family:{FONT}; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:#5f6368; border-bottom:1px solid #e8eaed;">Topic</th>
      <th align="right" style="padding:10px; font-family:{FONT}; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:#5f6368; border-bottom:1px solid #e8eaed;">This week</th>
      <th align="right" style="padding:10px; font-family:{FONT}; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:#5f6368; border-bottom:1px solid #e8eaed;">Last week</th>
      <th align="right" style="padding:10px; font-family:{FONT}; font-size:11px; letter-spacing:.06em; text-transform:uppercase; color:#5f6368; border-bottom:1px solid #e8eaed;">&Delta; vs last week</th>
    </tr>
  </thead>
  <tbody>{wow_rows_html}
  </tbody>
</table>

{closing_html}

</td></tr></table>
</td></tr></table>
</body></html>'''


# ---------------------------------------------------------------------------
# COM driver
# ---------------------------------------------------------------------------
def call_com_create(
    subject: str,
    html_path: Path,
    helper_script: Path,
    chart_image: Optional[Path] = None,
    chart_cid: Optional[str] = None,
) -> dict:
    """Invoke the COM helper PowerShell script; return parsed result."""
    args = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(helper_script),
        "-Action", "create",
        "-Subject", subject,
        "-HtmlPath", str(html_path),
    ]
    if chart_image and chart_image.exists():
        args += ["-ChartImage", str(chart_image)]
        if chart_cid:
            args += ["-ChartCid", chart_cid]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"COM create failed (exit {proc.returncode})")
    # Helper emits a final JSON line tagged "RESULT_JSON:"
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("RESULT_JSON:"):
            return json.loads(line[len("RESULT_JSON:"):].strip())
    raise SystemExit("COM helper did not emit RESULT_JSON")


def call_com_verify(entry_id: str, helper_script: Path) -> dict:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(helper_script),
         "-Action", "verify",
         "-EntryID", entry_id],
        capture_output=True, text=True, encoding="utf-8",
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return {"found": False, "error": proc.stderr.strip()}
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("RESULT_JSON:"):
            return json.loads(line[len("RESULT_JSON:"):].strip())
    return {"found": False}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Create an Outlook draft for the weekly OCV report.")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--subtopics", required=True, type=Path)
    ap.add_argument("--prior-manifest", type=Path, default=None,
                    help="WoW basis (defaults to manifest['wow_basis'] if present).")
    ap.add_argument("--report-md", type=Path, default=None,
                    help="Markdown report to harvest TL;DR bullets from.")
    ap.add_argument("--subject", default=None,
                    help="Email subject (default auto-generated from manifest).")
    ap.add_argument("--highlights", default=None,
                    help="Optional one-line headline rendered above the KPI cards.")
    ap.add_argument("--intro", default=None,
                    help="Override the intro paragraph (under 'Hello there,').")
    ap.add_argument("--progress-blurb", default=None,
                    help="Override the 'Progress at a glance' paragraph.")
    ap.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD_URL)
    ap.add_argument("--mirror-url", default=DEFAULT_MIRROR_URL,
                    help="Mirror repo (set empty string '' to omit).")
    ap.add_argument("--ado-query-url", default=None,
                    help="ADO query URL surfaced in the closing block.")
    ap.add_argument("--ado-writes", type=int, default=None,
                    help="If set, KPI caption reads 'X synced to ADO' instead of 'X subtopic rows'.")
    ap.add_argument("--metrics", type=Path, default=DEFAULT_METRICS_PATH,
                    help=f"Path to metrics_v2.json (defaults to {DEFAULT_METRICS_PATH}). "
                         "Used to render the 'User Rating vs Negative comments' chart. "
                         "Pass an empty path or a missing file to skip the chart.")
    ap.add_argument("--chart-mode", choices=["cid", "svg", "none"], default="cid",
                    help="How to embed the Progress-at-a-glance chart: "
                         "'cid' (default) renders a PNG via Playwright and attaches it "
                         "inline via Content-ID — works in EVERY client including OWA, "
                         "Classic Outlook for Windows, Mac, mobile, and Gmail. "
                         "'svg' uses inline SVG (renders in OWA/Mac/mobile but OWA strips "
                         "the <svg> entirely on sync — kept for debugging only). "
                         "'none' omits the chart entirely.")
    ap.add_argument("--chart-png-out", type=Path, default=None,
                    help="Path to write the rendered chart PNG (cid mode). "
                         "Defaults to output/ocv/email-drafts/ocv_progress_chart_<week>.png.")
    ap.add_argument("--closing-paragraph", action="append", default=None,
                    help="Extra paragraph(s) to include after the ADO line. Can be repeated.")
    ap.add_argument("--signoff", default="Cheers,<br><em>_Yohn</em>",
                    help="Sign-off HTML (set '' to omit).")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output preview HTML path (default: output/ocv/email-drafts/<base>_email.html).")
    ap.add_argument("--helper", type=Path,
                    default=Path("scripts/draft_ocv_email_com.ps1"),
                    help="Path to the PowerShell COM helper.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the HTML preview only; do not touch Outlook.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the script-level confirmation (Gate). Agent must have cleared with the user.")
    ap.add_argument("--verify", action="store_true",
                    help="After creating, re-read the draft via COM and print metadata.")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    week = manifest.get("week", "")
    date_range = manifest.get("date_range", week)

    # Resolve prior manifest
    prior_path: Optional[Path] = args.prior_manifest
    if prior_path is None:
        wow = manifest.get("wow_basis")
        if wow and Path(wow).exists():
            prior_path = Path(wow)
    prior: Optional[dict] = load_manifest(prior_path) if (prior_path and prior_path.exists()) else None

    # Default subject
    if args.subject:
        subject = args.subject
    else:
        try:
            start, end = [s.strip() for s in date_range.split("to")]
            m_d = lambda s: "/".join(s.split("-")[1:])  # YYYY-MM-DD -> MM/DD
            subject = f"Weekly OCV Feedback ({m_d(start)} - {m_d(end)}) + CopilotDash"
        except Exception:
            subject = f"Weekly OCV Feedback ({date_range}) + CopilotDash"

    # Default closing paragraphs (only added when no explicit ones supplied)
    closing_paragraphs = args.closing_paragraph
    if closing_paragraphs is None:
        closing_paragraphs = [
            "If you need any specific bug in another ADO project let me know.",
            "Ping me directly if you want to be dropped out of this or any upcoming weekly updates.",
            "Happy to walk through the details &mdash; feel free to reach out if you want to discuss any of this live or need a deeper cut on specific issues.",
        ]

    mirror_url = args.mirror_url if args.mirror_url else None

    # ---- Resolve the Progress-at-a-glance chart payload --------------------
    # cid (default): render PNG via Playwright; preview HTML references it as
    #   a relative path (browser-friendly); COM helper rewrites that <img src>
    #   to "cid:<id>" and attaches the PNG with PR_ATTACH_CONTENT_ID +
    #   PR_ATTACHMENT_HIDDEN so OWA renders it. PNG bytes never pass through
    #   the OWA HTML sanitizer (which strips inline <svg> entirely).
    # svg : legacy inline-SVG path (renders in browser preview but is stripped
    #   by OWA on the round-trip — see SKILL.md). Kept for debugging.
    # none: omit the chart entirely.
    chart_html: str = ""
    chart_png: Optional[Path] = None
    out_dir = (args.out.parent if args.out else Path("output/ocv/email-drafts"))
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics_path: Optional[Path] = (
        args.metrics if (args.metrics and str(args.metrics) and args.metrics.exists()) else None
    )

    if args.chart_mode == "svg":
        svg = build_progress_svg(metrics_path) if metrics_path else ""
        chart_html = svg
    elif args.chart_mode == "cid" and metrics_path:
        chart_png = args.chart_png_out or (out_dir / f"ocv_progress_chart_{week}.png")
        rendered = render_progress_png(metrics_path, chart_png)
        if rendered:
            # Preview HTML refers to the PNG by relative filename so the file
            # opens cleanly in a browser. The COM helper rewrites this src to
            # cid:<id> at draft-creation time.
            rel = chart_png.name
            chart_html = (
                f'<img src="{rel}" alt="User Rating vs Negative comments &mdash; weekly trend" '
                f'width="{PROGRESS_CHART_WIDTH}" height="{PROGRESS_CHART_HEIGHT}" '
                f'style="max-width:100%; height:auto; display:block; margin:0 auto; border:0;">'
            )
        else:
            # Playwright missing or render failed; fall back to inline SVG so
            # the preview still shows something. Warn loudly.
            sys.stderr.write(
                "[draft] PNG render failed; falling back to inline SVG. "
                "Chart will be blank in OWA.\n"
            )
            chart_html = build_progress_svg(metrics_path)
            chart_png = None
    # else: chart_mode == "none" or metrics_path missing -> empty chart_html

    body = build_full_body(
        manifest=manifest,
        prior=prior,
        subtopics_csv=args.subtopics,
        report_md=args.report_md,
        dashboard_url=args.dashboard_url,
        mirror_url=mirror_url,
        ado_query_url=args.ado_query_url,
        highlights=args.highlights,
        intro_paragraph=args.intro,
        progress_paragraph=args.progress_blurb,
        closing_paragraphs=closing_paragraphs,
        signoff=args.signoff or None,
        ado_writes=args.ado_writes,
        chart_html=chart_html,
    )

    # Write preview HTML
    out = args.out or Path("output/ocv/email-drafts") / f"ocv_email_{week}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"[draft] wrote preview HTML: {out}  ({len(body):,} bytes)")
    print(f"[draft] subject: {subject!r}")
    print(f"[draft] prior manifest: {prior_path if prior else '(none)'}")
    if chart_png:
        print(f"[draft] chart PNG    : {chart_png}  ({chart_png.stat().st_size:,} bytes)  cid={PROGRESS_CHART_CID}")
    elif args.chart_mode == "none":
        print("[draft] chart mode   : none (chart omitted)")
    elif not metrics_path:
        print(f"[draft] chart mode   : {args.chart_mode} (metrics file missing -> chart omitted)")

    if args.dry_run:
        print("[draft] --dry-run set; not touching Outlook.")
        return 0

    if not args.yes:
        print()
        print("=" * 60)
        print("Gate: this will create an Outlook DRAFT (recipients blank).")
        print("=" * 60)
        try:
            ans = input("Type 'yes' to proceed: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "yes":
            print("[draft] not confirmed; aborting (no Outlook write).")
            return 1

    helper = args.helper if args.helper.exists() else Path(__file__).resolve().parent / args.helper.name
    if not helper.exists():
        raise SystemExit(f"COM helper not found at {args.helper} or {helper}")

    result = call_com_create(
        subject,
        out.resolve(),
        helper.resolve(),
        chart_image=(chart_png.resolve() if chart_png else None),
        chart_cid=PROGRESS_CHART_CID,
    )
    entry_id = result.get("entryId")
    print()
    print("[draft] DRAFT CREATED")
    print(f"  Subject : {result.get('subject')}")
    print(f"  Folder  : {result.get('storedIn')}")
    print(f"  Size    : {result.get('size')} bytes")
    print(f"  EntryID : {entry_id}")
    print()
    print("Open Classic Outlook (or wait for OWA to sync) -> Drafts -> add To/Cc/Bcc -> send.")
    print("Reminder: OWA may take 30-60s to show the draft; trigger Send/Receive in Classic Outlook if needed.")

    if args.verify and entry_id:
        print()
        print("[draft] verifying via COM ...")
        v = call_com_verify(entry_id, helper.resolve())
        print(f"  Found={v.get('found')} Parent={v.get('parent')} SavedAt={v.get('savedAt')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

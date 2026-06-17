"""
publish_eval_regression_report.py — Push a freshly-rendered SEVAL
regression report into the gim-home/OCV-Weekly GitHub repo (which has
dual-push configured on `origin` so it mirrors automatically to
yohncf/OCV-Weekly_temp).

What it does:
  1. Strict-validates the manifest (no blank why_failed, reviewed_for_publish=true).
  2. Pulls + rebases the local clone.
  3. Copies the HTML into eval-reports/<slug>.html (default upserts the
     same slug; pass --new-version to write a suffixed -2/-3 copy).
  4. Upserts the entry in eval-reports.json (sorted by date descending).
  5. Re-renders eval.html from a managed template that lists every
     entry in eval-reports.json (dark M3 theme matching the OCV index).
  6. Idempotently injects a discrete dropdown into index.html via
     marker comments. On first run only.
  7. Commits + git push origin main.

Two-gate doctrine (mirrors publish_to_github.py + ado_sync.py):
  - Always prints the publish plan (Gate A clearance is expected to
    have happened in chat before invocation).
  - Blocks on stdin for `yes` before any write, unless --yes.
  - --dry-run skips all writes and the push.

Usage:
  python scripts/publish_eval_regression_report.py \
      --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
      --html     output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html \
      --highlights "<one-line summary>"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Force UTF-8 console output on Windows. The script's banners contain
# en/em dashes, arrows, ellipsis, and the warning sign, which crash under
# the default cp1252 console encoding mid-publish (leaving the working
# tree dirty). Idempotent and safe on Linux/macOS where stdout is already
# utf-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


REPO_ROOT = Path(__file__).resolve().parents[2]   # OLAgentWork/ (monorepo root)
DEFAULT_REPO = REPO_ROOT / "_ocv_weekly_repo"
DEFAULT_GITHUB_URL = "https://github.com/gim-home/OCV-Weekly.git"


def _resolve_input(path: Path) -> Path:
    """Resolve a relative input path robustly regardless of the working
    directory: try it as given (cwd-relative) first, then against the
    monorepo root. Returns the first existing candidate, else the
    monorepo-root candidate so error messages point at the canonical
    location. Absolute paths are returned unchanged."""
    if path.is_absolute():
        return path
    cwd_candidate = Path.cwd() / path
    if cwd_candidate.exists():
        return cwd_candidate
    root_candidate = REPO_ROOT / path
    if root_candidate.exists():
        return root_candidate
    return root_candidate


def _default_owner() -> str:
    """Best-effort owner name for a freshly seeded eval-reports.json.
    Reads `git config user.name`; falls back to "Owner" if unavailable.
    Only used the very first time the listing manifest is created — once
    a real value lands in the JSON file the script preserves it."""
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
DEFAULT_LIVE_BASE = "https://gim-home.github.io/OCV-Weekly"
PERSONAL_LIVE_BASE = "https://yohncf.github.io/OCV-Weekly_temp"

EVAL_REPORTS_DIR_NAME = "eval-reports"
EVAL_REPORTS_JSON_NAME = "eval-reports.json"
EVAL_HTML_NAME = "eval.html"

# Marker tokens used to inject (and on every re-run, replace) the
# dropdown nav + its CSS into the existing index.html. Markers are
# intentionally visible HTML comments so a human can spot them.
NAV_MARKER_START = "<!-- EVAL-NAV-START -->"
NAV_MARKER_END = "<!-- EVAL-NAV-END -->"
NAV_CSS_MARKER_START = "<!-- EVAL-NAV-CSS-START -->"
NAV_CSS_MARKER_END = "<!-- EVAL-NAV-CSS-END -->"


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

def _run(cmd: list, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          check=check, text=True, capture_output=True)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def validate_manifest_for_publish(manifest: Dict[str, Any], html_path: Path,
                                  manifest_path: Path) -> List[str]:
    problems: List[str] = []

    if not manifest.get("publish_safety", {}).get("reviewed_for_publish"):
        problems.append("publish_safety.reviewed_for_publish must be true before publish.")

    regs = manifest.get("regressions", []) or []
    blank_ids = [r["id"] for r in regs if not (r.get("why_failed") or "").strip()]
    if blank_ids:
        sample = ", ".join(blank_ids[:5])
        more = f", … {len(blank_ids) - 5} more" if len(blank_ids) > 5 else ""
        problems.append(f"{len(blank_ids)} regression rows have blank why_failed: {sample}{more}")

    if html_path.stat().st_mtime < manifest_path.stat().st_mtime - 1:
        problems.append(
            f"HTML ({html_path.name}) is older than manifest ({manifest_path.name}); "
            "re-render before publishing."
        )

    for required in ("control", "experiment", "summary", "feature_flags"):
        if required not in manifest:
            problems.append(f"Manifest missing top-level field: {required}")

    return problems


def build_slug(manifest: Dict[str, Any]) -> str:
    c = manifest["control"]
    e = manifest["experiment"]
    return f"{e['run_date']}_{c['id']}_vs_{e['id']}"


def build_label(manifest: Dict[str, Any]) -> str:
    c = manifest["control"]
    e = manifest["experiment"]
    return f"{c['name']} {c['id']} vs {e['name']} {e['id']} ({e['run_date']})"


def build_entry(manifest: Dict[str, Any], slug: str, filename: str, highlights: str) -> Dict[str, Any]:
    c = manifest["control"]
    e = manifest["experiment"]
    summary = manifest["summary"]
    counts = summary["regression_counts"]
    cc = counts["control_vs_control"]["regressions"]
    ee = counts["experiment_vs_experiment"]["regressions"]
    pass_delta = round(
        (e.get("experiment_side_pass_rate", 0.0) - c.get("experiment_side_pass_rate", 0.0)) * 100, 2
    )
    pass_delta_ctrl = round(
        (e.get("control_side_pass_rate", 0.0) - c.get("control_side_pass_rate", 0.0)) * 100, 2
    )
    return {
        "slug": slug,
        "label": build_label(manifest),
        "date": e["run_date"],
        "file": f"{EVAL_REPORTS_DIR_NAME}/{filename}",
        "control": {"id": c["id"], "name": c["name"], "run_date": c["run_date"]},
        "experiment": {"id": e["id"], "name": e["name"], "run_date": e["run_date"]},
        "regressions": {
            "control_vs_control": cc,
            "experiment_vs_experiment": ee,
            "total": cc + ee,
            "comparable": summary["matched_pairs"],
        },
        "pass_rate_delta_pp": pass_delta,
        "pass_rate_delta_pp_control": pass_delta_ctrl,
        "pass_rates": {
            "latest_control_side": e.get("control_side_pass_rate"),
            "latest_experiment_side": e.get("experiment_side_pass_rate"),
            "base_control_side": c.get("control_side_pass_rate"),
            "base_experiment_side": c.get("experiment_side_pass_rate"),
        },
        "highlights": highlights,
        "published": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


def upsert_entry(reports: List[Dict[str, Any]], entry: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """Upsert by slug. Returns (new_list, action) where action ∈ {'added', 'replaced'}."""
    out = []
    action = "added"
    for r in reports:
        if r.get("slug") == entry["slug"]:
            action = "replaced"
            continue
        out.append(r)
    out.append(entry)
    out.sort(key=lambda r: (r.get("date", ""), r.get("slug", "")), reverse=True)
    return out, action


def resolve_destination(repo: Path, slug: str, new_version: bool) -> Tuple[Path, str, str]:
    """Return (dest_path, final_filename, collision_status)."""
    eval_dir = repo / EVAL_REPORTS_DIR_NAME
    base = f"{slug}.html"
    candidate = eval_dir / base
    if not candidate.exists():
        return candidate, base, "new"
    if not new_version:
        return candidate, base, "replace"
    # --new-version: find next free -N suffix.
    n = 2
    while True:
        suffixed = f"{slug}-{n}.html"
        candidate = eval_dir / suffixed
        if not candidate.exists():
            return candidate, suffixed, f"new-suffixed (-{n})"
        n += 1


# ---------------------------------------------------------------------------
# eval.html (the EVAL Analysis listing page) — managed template
# ---------------------------------------------------------------------------

EVAL_HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- EVAL-HTML-GENERATED: do not hand-edit. Regenerated by scripts/publish_eval_regression_report.py on every publish. -->
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TITLE}}</title>
<meta name="description" content="{{SUBTITLE}}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans+Display:wght@400;500;700&family=Google+Sans+Text:wght@400;500;600&family=Roboto+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #101418;
  --surface: #161a1f;
  --surface-1: #1a1e23;
  --surface-2: #22272d;
  --surface-3: #2a2f36;
  --text: #e3e2e6;
  --text-muted: #c4c6cf;
  --text-faint: #8a8d94;
  --outline: #43474e;
  --primary: #a8c7fa;
  --primary-container: rgba(168, 199, 250, 0.12);
  --tertiary: #a4ccaf;
  --error: #ffb4ab;
  --ctrl: #d29922;
  --expr: #a371f7;
  --font-display: 'Google Sans Display','Google Sans',system-ui,sans-serif;
  --font-body: 'Google Sans Text','Google Sans',system-ui,sans-serif;
  --font-mono: 'Roboto Mono',ui-monospace,monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: var(--font-body); font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }

.page { max-width: 1100px; margin: 0 auto; padding: 28px 28px 80px; }

/* Reused nav-dropdown style (also injected into index.html via markers) */
.eval-nav { display: flex; justify-content: flex-end; margin-bottom: 12px; }
.eval-nav-dropdown { position: relative; }
.eval-nav-dropdown > summary {
  list-style: none; cursor: pointer;
  padding: 6px 12px; border-radius: 8px;
  background: var(--surface-2); color: var(--text-muted);
  border: 1px solid var(--outline);
  font-size: 12px; font-weight: 500;
}
.eval-nav-dropdown > summary::-webkit-details-marker { display: none; }
.eval-nav-dropdown > summary:hover { background: var(--surface-3); color: var(--text); }
.eval-nav-dropdown ul {
  position: absolute; right: 0; top: 100%; margin: 4px 0 0; padding: 4px;
  list-style: none; min-width: 180px;
  background: var(--surface-1); border: 1px solid var(--outline); border-radius: 8px;
  box-shadow: 0 6px 16px rgba(0,0,0,0.45); z-index: 100;
}
.eval-nav-dropdown li a {
  display: block; padding: 8px 12px; border-radius: 6px;
  font-size: 13px; color: var(--text-muted);
}
.eval-nav-dropdown li a:hover { background: var(--surface-2); color: var(--text); text-decoration: none; }
.eval-nav-dropdown li a[aria-current="page"] { color: var(--primary); background: var(--primary-container); }

.eyebrow {
  display: inline-block; padding: 4px 10px;
  background: var(--primary-container); color: var(--primary);
  border-radius: 999px; font-size: 12px; font-weight: 500;
  letter-spacing: 0.4px; text-transform: uppercase; margin-bottom: 12px;
}
h1.title { font-family: var(--font-display); font-weight: 500; font-size: 32px; line-height: 40px; margin: 0 0 6px; }
.subtitle { color: var(--text-muted); }

.list { display: grid; gap: 12px; margin-top: 28px; }
.card {
  background: var(--surface); border: 1px solid var(--outline); border-radius: 14px;
  padding: 16px 18px; display: block; color: inherit;
  transition: border-color 0.15s, transform 0.15s;
}
.card:hover { border-color: var(--primary); transform: translateY(-1px); text-decoration: none; }
.card-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.card-label { font-family: var(--font-display); font-weight: 500; font-size: 18px; color: var(--text); }
.card-date { font-size: 12px; color: var(--text-faint); font-family: var(--font-mono); }
.card-row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 10px; align-items: center; font-size: 13px; color: var(--text-muted); }
.metric { display: inline-flex; align-items: baseline; gap: 4px; }
.metric .n { font-family: var(--font-mono); font-size: 15px; color: var(--text); font-weight: 500; }
.delta { font-family: var(--font-mono); font-size: 13px; padding: 2px 8px; border-radius: 6px; border: 1px solid var(--outline); }
.delta.down { color: var(--error); border-color: var(--error); background: rgba(255,180,171,0.10); }
.delta.up { color: var(--tertiary); border-color: var(--tertiary); background: rgba(164,204,175,0.10); }
.delta.flat { color: var(--text-faint); }
.highlights { margin-top: 8px; color: var(--text-muted); font-size: 13px; }

.empty {
  margin-top: 24px; padding: 40px; text-align: center;
  background: var(--surface); border: 1px dashed var(--outline); border-radius: 14px;
  color: var(--text-faint);
}

footer { margin-top: 60px; color: var(--text-faint); font-size: 12px; }

/* Performance trend panel */
.trend { margin-top: 28px; }
.trend-card {
  background: var(--surface); border: 1px solid var(--outline); border-radius: 14px;
  padding: 18px 18px 14px;
}
.trend-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
.trend-title { font-family: var(--font-display); font-weight: 500; font-size: 18px; color: var(--text); }
.trend-sub { font-size: 12px; color: var(--text-faint); }
.trend-stats { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 18px; }
.stat {
  flex: 1 1 150px; background: var(--surface-1); border: 1px solid var(--outline);
  border-radius: 10px; padding: 10px 12px;
}
.stat .lbl { font-size: 11px; color: var(--text-faint); text-transform: uppercase; letter-spacing: 0.4px; }
.stat .val { font-family: var(--font-mono); font-size: 22px; font-weight: 500; color: var(--text); margin-top: 2px; }
.stat .val.down { color: var(--error); }
.stat .val.up { color: var(--tertiary); }
.stat .sfx { font-size: 12px; color: var(--text-faint); margin-left: 4px; }
.chart-block { margin-top: 10px; }
.chart-block + .chart-block { margin-top: 22px; }
.chart-cap { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
.chart-name { font-size: 13px; font-weight: 600; color: var(--text-muted); }
.legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px; color: var(--text-faint); }
.legend .key { display: inline-flex; align-items: center; gap: 5px; }
.legend .swatch { width: 18px; height: 3px; border-radius: 2px; display: inline-block; }
.chart-svg { width: 100%; height: auto; display: block; }
</style>
</head>
<body>
<div class="page">

<nav class="eval-nav" aria-label="Site sections">
  <details class="eval-nav-dropdown">
    <summary>Dashboards ▾</summary>
    <ul>
      <li><a href="./index.html">OCV Weekly</a></li>
      <li><a href="./eval.html" aria-current="page">EVAL Analysis</a></li>
    </ul>
  </details>
</nav>

<header>
  <span class="eyebrow">EVAL Analysis</span>
  <h1 class="title">{{TITLE}}</h1>
  <p class="subtitle">{{SUBTITLE}}</p>
  <p class="subtitle" style="font-size:12px">Owner: {{OWNER}}</p>
</header>

{{TREND}}

<section class="list">
{{CARDS}}
</section>

<footer>Generated {{GENERATED_AT}}. {{REPORT_COUNT}} report(s).</footer>

</div>
</body>
</html>
"""


def _nice_ceil(v: float) -> float:
    """Round a positive number up to a clean axis bound (1/2/5 x 10^n)."""
    if v <= 0:
        return 1.0
    import math
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def render_trend(reports: List[Dict[str, Any]]) -> str:
    """Inline-SVG performance-trend panel for the top of eval.html.

    Plots, across every published run (oldest -> newest): regressions per run
    (total / Mainline-side / CodeGen-side) and the CodeGen-side pass-rate change
    vs the base run (pp). Self-contained (no JS / no external chart lib) and
    dark-themed to match the listing page.
    """
    if not reports:
        return ""

    # Theme palette (kept in sync with the template :root vars).
    C_TOTAL, C_CTRL, C_EXPR = "#a8c7fa", "#d29922", "#a371f7"
    C_GREEN, C_RED = "#a4ccaf", "#ffb4ab"
    C_GRID, C_AXIS, C_TXT, C_FAINT = "#2a2f36", "#43474e", "#c4c6cf", "#8a8d94"

    pts = []
    for r in reports:
        reg = r.get("regressions", {}) or {}
        cc = reg.get("control_vs_control", 0) or 0
        ee = reg.get("experiment_vs_experiment", 0) or 0
        pts.append({
            "date": r.get("date", ""),
            "total": reg.get("total", cc + ee),
            "cc": cc,
            "ee": ee,
            "comparable": reg.get("comparable", 0) or 0,
            "delta": float(r.get("pass_rate_delta_pp", 0.0) or 0.0),
        })
    pts.sort(key=lambda p: p["date"])
    n = len(pts)

    # ---- Stat callouts (latest run + run-over-run direction) ----
    latest = pts[-1]
    prev = pts[-2] if n >= 2 else None
    if prev is not None:
        dt = latest["total"] - prev["total"]
        # Fewer regressions run-over-run is an improvement.
        dir_cls = "up" if dt < 0 else ("down" if dt > 0 else "")
        dir_txt = f"{'+' if dt > 0 else ''}{dt} vs prev run"
    else:
        dir_cls, dir_txt = "", "first run"
    d = latest["delta"]
    d_cls = "down" if d < 0 else ("up" if d > 0 else "")
    d_str = f"{'+' if d >= 0 else ''}{d:g}"
    stats = (
        '<div class="trend-stats">'
        f'<div class="stat"><div class="lbl">Latest regressions</div>'
        f'<div class="val">{latest["total"]}<span class="sfx">/ {latest["comparable"]} comparable</span></div></div>'
        f'<div class="stat"><div class="lbl">Run-over-run</div>'
        f'<div class="val {dir_cls}">{html.escape(dir_txt)}</div></div>'
        f'<div class="stat"><div class="lbl">Pass-rate &Delta; (CodeGen)</div>'
        f'<div class="val {d_cls}">{html.escape(d_str)}<span class="sfx">pp</span></div></div>'
        f'<div class="stat"><div class="lbl">Runs tracked</div>'
        f'<div class="val">{n}</div></div>'
        '</div>'
    )

    def xcoord(i: int, left: float, plot_w: float) -> float:
        return left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    # ---- Chart A: regressions per run ----
    W, H = 1000.0, 300.0
    L, R, T, B = 48.0, 16.0, 16.0, 36.0
    pw, ph = W - L - R, H - T - B
    y0, y1 = T, T + ph
    ymax = _nice_ceil(max((p["total"] for p in pts), default=1))

    def ay(v: float) -> float:
        return y1 - (v / ymax) * ph if ymax else y1

    grid, ylabels = [], []
    for k in range(5):
        gv = ymax * k / 4
        gy = ay(gv)
        grid.append(f'<line x1="{L}" y1="{gy:.1f}" x2="{W - R}" y2="{gy:.1f}" '
                    f'stroke="{C_GRID}" stroke-width="1"/>')
        ylabels.append(f'<text x="{L - 8}" y="{gy + 4:.1f}" text-anchor="end" '
                       f'font-size="11" fill="{C_FAINT}" font-family="monospace">{gv:g}</text>')

    def series(key: str, color: str, width: float, dots: bool = True) -> str:
        ptsxy = [(xcoord(i, L, pw), ay(p[key])) for i, p in enumerate(pts)]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in ptsxy)
        out = [f'<polyline points="{poly}" fill="none" stroke="{color}" '
               f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>']
        if dots:
            for (x, y), p in zip(ptsxy, pts):
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}">'
                           f'<title>{html.escape(p["date"])}: {p[key]} ({key})</title></circle>')
        return "".join(out)

    xlabels = []
    for i, p in enumerate(pts):
        x = xcoord(i, L, pw)
        xlabels.append(f'<text x="{x:.1f}" y="{y1 + 20:.1f}" text-anchor="middle" '
                       f'font-size="10" fill="{C_FAINT}" font-family="monospace">'
                       f'{html.escape(p["date"][5:])}</text>')
    # Value labels on the total line.
    tvals = []
    for i, p in enumerate(pts):
        x, y = xcoord(i, L, pw), ay(p["total"])
        tvals.append(f'<text x="{x:.1f}" y="{y - 8:.1f}" text-anchor="middle" '
                     f'font-size="11" fill="{C_TXT}" font-family="monospace" font-weight="600">{p["total"]}</text>')

    chart_a = (
        f'<svg class="chart-svg" viewBox="0 0 {W:g} {H:g}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Regressions per run over time">'
        f'{"".join(grid)}{"".join(ylabels)}'
        f'{series("ee", C_EXPR, 1.6)}{series("cc", C_CTRL, 1.6)}{series("total", C_TOTAL, 2.6)}'
        f'{"".join(tvals)}{"".join(xlabels)}'
        f'</svg>'
    )

    # ---- Chart B: pass-rate delta (pp) vs base, zero baseline ----
    W2, H2 = 1000.0, 170.0
    L2, R2, T2, B2 = 48.0, 16.0, 16.0, 32.0
    pw2, ph2 = W2 - L2 - R2, H2 - T2 - B2
    amax = _nice_ceil(max((abs(p["delta"]) for p in pts), default=1))
    zy = T2 + ph2 / 2

    def by(v: float) -> float:
        return zy - (v / amax) * (ph2 / 2) if amax else zy

    grid_b = [f'<line x1="{L2}" y1="{zy:.1f}" x2="{W2 - R2}" y2="{zy:.1f}" '
              f'stroke="{C_AXIS}" stroke-width="1" stroke-dasharray="4 3"/>',
              f'<text x="{L2 - 8}" y="{zy + 4:.1f}" text-anchor="end" font-size="11" '
              f'fill="{C_FAINT}" font-family="monospace">0</text>',
              f'<text x="{L2 - 8}" y="{T2 + 10:.1f}" text-anchor="end" font-size="10" '
              f'fill="{C_FAINT}" font-family="monospace">+{amax:g}</text>',
              f'<text x="{L2 - 8}" y="{T2 + ph2 + 2:.1f}" text-anchor="end" font-size="10" '
              f'fill="{C_FAINT}" font-family="monospace">-{amax:g}</text>']
    dxy = [(xcoord(i, L2, pw2), by(p["delta"])) for i, p in enumerate(pts)]
    dpoly = " ".join(f"{x:.1f},{y:.1f}" for x, y in dxy)
    dmarks = []
    for (x, y), p in zip(dxy, pts):
        col = C_GREEN if p["delta"] >= 0 else C_RED
        dmarks.append(f'<line x1="{x:.1f}" y1="{zy:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
                      f'stroke="{col}" stroke-width="1" opacity="0.4"/>')
        dmarks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="{col}">'
                      f'<title>{html.escape(p["date"])}: {p["delta"]:g}pp</title></circle>')
        ly = y - 8 if p["delta"] >= 0 else y + 16
        dmarks.append(f'<text x="{x:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="10" '
                      f'fill="{col}" font-family="monospace">{p["delta"]:g}</text>')
    xlabels_b = []
    for i, p in enumerate(pts):
        x = xcoord(i, L2, pw2)
        xlabels_b.append(f'<text x="{x:.1f}" y="{H2 - 8:.1f}" text-anchor="middle" '
                         f'font-size="10" fill="{C_FAINT}" font-family="monospace">'
                         f'{html.escape(p["date"][5:])}</text>')
    chart_b = (
        f'<svg class="chart-svg" viewBox="0 0 {W2:g} {H2:g}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Pass-rate delta per run over time">'
        f'{"".join(grid_b)}'
        f'<polyline points="{dpoly}" fill="none" stroke="{C_FAINT}" stroke-width="1.4" '
        f'stroke-linejoin="round"/>'
        f'{"".join(dmarks)}{"".join(xlabels_b)}'
        f'</svg>'
    )

    legend_a = (
        '<div class="legend">'
        f'<span class="key"><span class="swatch" style="background:{C_TOTAL}"></span>Total</span>'
        f'<span class="key"><span class="swatch" style="background:{C_CTRL}"></span>Mainline-side</span>'
        f'<span class="key"><span class="swatch" style="background:{C_EXPR}"></span>CodeGen-side</span>'
        '</div>'
    )

    return (
        '<section class="trend">'
        '<div class="trend-card">'
        '<div class="trend-head">'
        '<div class="trend-title">Performance trend</div>'
        '<div class="trend-sub">Per-run regressions &amp; pass-rate movement &middot; oldest &rarr; newest</div>'
        '</div>'
        f'{stats}'
        '<div class="chart-block">'
        f'<div class="chart-cap"><span class="chart-name">Regressions per run</span>{legend_a}</div>'
        f'{chart_a}'
        '</div>'
        '<div class="chart-block">'
        '<div class="chart-cap"><span class="chart-name">Pass-rate change vs base run (pp) &middot; CodeGen side</span></div>'
        f'{chart_b}'
        '</div>'
        '</div>'
        '</section>'
    )


def render_eval_html(site_manifest: Dict[str, Any]) -> str:
    title = site_manifest.get("title", "OL Agent EVAL Analysis")
    subtitle = site_manifest.get("subtitle", "Regression reports for SEVAL HeroEval runs")
    owner = site_manifest.get("owner", "")
    reports = site_manifest.get("reports", []) or []

    if not reports:
        cards_html = '<div class="empty">No reports yet. Publish one with <code>seval-regression-publish</code>.</div>'
    else:
        parts = []
        for r in reports:
            cc = r.get("regressions", {}).get("control_vs_control", 0)
            ee = r.get("regressions", {}).get("experiment_vs_experiment", 0)
            total = r.get("regressions", {}).get("total", cc + ee)
            comparable = r.get("regressions", {}).get("comparable", 0)
            delta = r.get("pass_rate_delta_pp", 0.0)
            delta_cls = "down" if delta < 0 else ("up" if delta > 0 else "flat")
            delta_str = f"{'+' if delta >= 0 else ''}{delta}pp"
            highlights = r.get("highlights", "")
            parts.append(
                f'<a class="card" href="./{html.escape(r["file"])}">'
                f'  <div class="card-head">'
                f'    <div class="card-label">{html.escape(r["label"])}</div>'
                f'    <div class="card-date">{html.escape(r["date"])}</div>'
                f'  </div>'
                f'  <div class="card-row">'
                f'    <span class="metric"><span class="n">{total}</span> regressions out of <span class="n">{comparable}</span> comparable</span>'
                f'    <span class="metric" style="color:var(--ctrl)"><span class="n">{cc}</span> {html.escape(r["control"]["name"])} vs {html.escape(r["control"]["name"])}</span>'
                f'    <span class="metric" style="color:var(--expr)"><span class="n">{ee}</span> {html.escape(r["experiment"]["name"])} vs {html.escape(r["experiment"]["name"])}</span>'
                f'    <span class="delta {delta_cls}">{html.escape(delta_str)}</span>'
                f'  </div>'
                + (f'  <div class="highlights">{html.escape(highlights)}</div>' if highlights else "")
                + f'</a>'
            )
        cards_html = "\n".join(parts)

    subs = {
        "{{TITLE}}": html.escape(title),
        "{{SUBTITLE}}": html.escape(subtitle),
        "{{OWNER}}": html.escape(owner),
        "{{TREND}}": render_trend(reports),
        "{{CARDS}}": cards_html,
        "{{GENERATED_AT}}": html.escape(_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
        "{{REPORT_COUNT}}": str(len(reports)),
    }
    out = EVAL_HTML_TEMPLATE
    for k, v in subs.items():
        out = out.replace(k, v)
    return out


# ---------------------------------------------------------------------------
# index.html dropdown injection
# ---------------------------------------------------------------------------

NAV_BLOCK = (
    NAV_MARKER_START + "\n"
    '    <details class="eval-nav-dropdown" aria-label="Site sections">\n'
    '      <summary>\n'
    '        <span class="material-symbols-rounded eval-nav-icon">dashboard</span>\n'
    '        <span class="eval-nav-text">Dashboards</span>\n'
    '        <span class="material-symbols-rounded eval-nav-caret">arrow_drop_down</span>\n'
    '      </summary>\n'
    '      <ul>\n'
    '        <li><a href="./index.html" aria-current="page">OCV Weekly</a></li>\n'
    '        <li><a href="./eval.html">EVAL Analysis</a></li>\n'
    '      </ul>\n'
    '    </details>\n'
    '    ' + NAV_MARKER_END
)

_NAV_CSS_BODY = """\
<style>
/* Dropdown lives INSIDE .top-bar (injected before .top-bar__chip). */
.eval-nav-dropdown { position: relative; }
.eval-nav-dropdown > summary {
  list-style: none; cursor: pointer; user-select: none;
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 8px 6px 12px;
  border-radius: 999px;
  background: var(--md-sys-color-surface-container-high, #22272d);
  color: var(--md-sys-color-on-surface-variant, #c4c6cf);
  border: 1px solid var(--md-sys-color-outline-variant, #43474e);
  font: 500 13px/1 'Google Sans Text','Google Sans',system-ui,sans-serif;
  transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
}
.eval-nav-dropdown > summary::-webkit-details-marker { display: none; }
.eval-nav-dropdown > summary:hover {
  background: var(--md-sys-color-surface-container-highest, #2a2f36);
  color: var(--md-sys-color-on-surface, #e3e2e6);
  border-color: var(--md-sys-color-on-surface-variant, #c4c6cf);
}
.eval-nav-dropdown > summary .eval-nav-icon {
  font-size: 18px;
  color: var(--md-sys-color-primary, #a8c7fa);
}
.eval-nav-dropdown > summary .eval-nav-caret {
  font-size: 18px;
  color: var(--md-sys-color-on-surface-variant, #c4c6cf);
  transition: transform 150ms ease;
}
.eval-nav-dropdown[open] > summary .eval-nav-caret { transform: rotate(180deg); }
.eval-nav-dropdown[open] > summary {
  background: var(--md-sys-color-surface-container-highest, #2a2f36);
  color: var(--md-sys-color-on-surface, #e3e2e6);
}
.eval-nav-dropdown ul {
  position: absolute; right: 0; top: calc(100% + 6px);
  margin: 0; padding: 6px;
  list-style: none; min-width: 200px;
  background: var(--md-sys-color-surface-container-highest, #2a2f36);
  border: 1px solid var(--md-sys-color-outline-variant, #43474e);
  border-radius: var(--md-shape-corner-medium, 12px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.40);
  z-index: 100;
}
.eval-nav-dropdown li a {
  display: block; padding: 8px 12px; border-radius: 8px;
  font: 500 13px/1.4 'Google Sans Text','Google Sans',system-ui,sans-serif;
  color: var(--md-sys-color-on-surface-variant, #c4c6cf); text-decoration: none;
}
.eval-nav-dropdown li a:hover {
  background: var(--md-sys-color-surface-container-high, #22272d);
  color: var(--md-sys-color-on-surface, #e3e2e6);
}
.eval-nav-dropdown li a[aria-current="page"] {
  color: var(--md-sys-color-primary, #a8c7fa);
  background: var(--md-sys-color-primary-container, rgba(168,199,250,0.12));
}
@media (max-width: 600px) {
  .eval-nav-dropdown > summary .eval-nav-text { display: none; }
  .eval-nav-dropdown > summary { padding: 6px 8px; }
}
</style>
"""

NAV_CSS_BLOCK = NAV_CSS_MARKER_START + "\n" + _NAV_CSS_BODY + NAV_CSS_MARKER_END


# Pattern for the legacy standalone <nav class="eval-nav"> ... </nav> block
# (with optional surrounding whitespace) that we used to inject above the
# top bar. We strip it on first migration so it doesn't double-render.
_LEGACY_STANDALONE_NAV_RE = re.compile(
    r"\s*<nav\b[^>]*class=\"eval-nav\"[^>]*>.*?</nav>\s*",
    re.DOTALL,
)


def inject_nav_into_index(index_path: Path) -> Tuple[bool, str]:
    """Idempotently inject the dropdown (inside `.top-bar`) and its CSS
    (inside `<head>`) into index.html. Migrates from the legacy floating
    standalone <nav class="eval-nav"> block if found. Returns
    (changed?, status_message)."""
    text = index_path.read_text(encoding="utf-8")
    original = text
    migrated_legacy = False

    # ---- 1. CSS injection (inside <head>). Replace block if markers exist,
    # otherwise insert just before </head>.
    if NAV_CSS_MARKER_START in text and NAV_CSS_MARKER_END in text:
        text = re.sub(
            re.escape(NAV_CSS_MARKER_START) + r".*?" + re.escape(NAV_CSS_MARKER_END),
            NAV_CSS_BLOCK.strip(),
            text, count=1, flags=re.DOTALL,
        )
    elif NAV_CSS_MARKER_START in text or NAV_CSS_MARKER_END in text:
        return False, "ABORT: index.html has only one of the two CSS markers — manual cleanup required."
    else:
        if "</head>" not in text:
            return False, "ABORT: index.html has no </head> tag — cannot inject CSS."
        text = text.replace("</head>", NAV_CSS_BLOCK + "\n</head>", 1)

    # ---- 2. Strip legacy markers AND legacy standalone <nav> wherever they
    # currently sit. (If they're already inside .top-bar, the strip is a
    # no-op for layout because we'll re-inject in the same place.)
    if NAV_MARKER_START in text and NAV_MARKER_END in text:
        before = text
        text = re.sub(
            r"\s*" + re.escape(NAV_MARKER_START) + r".*?" + re.escape(NAV_MARKER_END) + r"\s*",
            "",
            text, count=1, flags=re.DOTALL,
        )
        if text != before:
            migrated_legacy = True
    elif NAV_MARKER_START in text or NAV_MARKER_END in text:
        return False, "ABORT: index.html has only one of the two NAV markers — manual cleanup required."

    # Strip any orphaned legacy standalone <nav class="eval-nav"> block.
    new_text, n = _LEGACY_STANDALONE_NAV_RE.subn("", text, count=1)
    if n > 0:
        text = new_text
        migrated_legacy = True
    if 'class="eval-nav"' in text:
        return False, ('ABORT: index.html still contains a .eval-nav element after stripping known forms. '
                       'Manual cleanup required.')

    # ---- 3. Inject the new block INSIDE .top-bar, right before
    # .top-bar__chip (if present) so it appears between the spacer and the
    # live chip. If no chip is found, insert just before </div> of .top-bar.
    chip_re = re.compile(r'(\n?[ \t]*)(<div[^>]*class="top-bar__chip[^"]*"[^>]*>)', re.IGNORECASE)
    m = chip_re.search(text)
    if m:
        indent = m.group(1) or "\n    "
        insertion = indent + NAV_BLOCK.strip() + indent + m.group(2)
        text = text[:m.start()] + insertion + text[m.end():]
    else:
        # Fall back: find the .top-bar opening div and append before its
        # closing </div>. This is a best-effort heuristic.
        tb_open = re.search(r'<div[^>]*class="top-bar(?:\s[^"]*)?"[^>]*>', text)
        if not tb_open:
            return False, ("ABORT: index.html has no .top-bar container — cannot inject in-bar "
                           "dropdown. (Did the top-bar markup change?)")
        # Walk forward to find the matching </div> by counting nesting.
        i = tb_open.end()
        depth = 1
        while depth > 0 and i < len(text):
            nxt = re.search(r"<div\b|</div>", text[i:], re.IGNORECASE)
            if not nxt:
                return False, "ABORT: could not find closing </div> for .top-bar."
            tok_start = i + nxt.start()
            tok_end = i + nxt.end()
            if text[tok_start:tok_end].lower().startswith("</"):
                depth -= 1
                if depth == 0:
                    text = text[:tok_start] + NAV_BLOCK + "\n  " + text[tok_start:]
                    break
            else:
                depth += 1
            i = tok_end

    if text == original:
        return False, "unchanged"
    index_path.write_text(text, encoding="utf-8")
    return True, "migrated-into-top-bar" if migrated_legacy else "updated"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def ensure_clone(repo: Path, remote_url: str, dry_run: bool) -> None:
    if repo.exists() and (repo / ".git").exists():
        return
    print(f"[publish-eval] No clone at {repo}. Cloning {remote_url} ...")
    if dry_run:
        print("[publish-eval] --dry-run set; skipping clone.")
        return
    repo.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", remote_url, str(repo)])


def git_pull_rebase(repo: Path) -> None:
    """Pull --rebase, auto-stashing any uncommitted changes left over from
    a previous interrupted run. The stash is restored after rebase, so a
    real conflict would surface here just like a plain pull would."""
    if not git_status_clean(repo):
        print("[publish-eval] Working tree dirty (likely a leftover from a "
              "previous interrupted publish). Using `git pull --rebase "
              "--autostash` to recover.")
        _run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=repo)
    else:
        _run(["git", "pull", "--rebase", "origin", "main"], cwd=repo)


def git_status_clean(repo: Path) -> bool:
    r = _run(["git", "status", "--porcelain"], cwd=repo, check=False)
    return r.returncode == 0 and not r.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--html", required=True, type=Path)
    p.add_argument("--highlights", default="",
                   help="One-line summary for the eval-reports.json card (recommended).")
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO,
                   help=f"Local clone path (default: {DEFAULT_REPO}).")
    p.add_argument("--remote-url", default=DEFAULT_GITHUB_URL,
                   help="GitHub URL to clone from if --repo doesn't exist yet.")
    p.add_argument("--branch", default="main")
    p.add_argument("--commit-message", default=None)
    p.add_argument("--new-version", action="store_true",
                   help="Write a suffixed copy (-2, -3, …) instead of replacing the same-slug file.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the publish plan and exit; touch nothing.")
    p.add_argument("--yes", action="store_true",
                   help="Skip stdin confirmation. ONLY pass this after Gate A clearance.")
    p.add_argument("--skip-strict", action="store_true",
                   help="(Discouraged) skip strict validation. Will refuse to push if used.")
    p.add_argument("--skip-mirror", action="store_true",
                   help="Do not run mirror_to_personal_v2.ps1 after the push. "
                        "By default the mirror runs automatically so the public "
                        "yohncf/OCV-Weekly_temp repo doesn't drift behind gim-home.")
    p.add_argument("--mirror-script", type=Path, default=None,
                   help="Override path to the personal-mirror script. Default: "
                        "<ocv-extraction-root>/mirror_to_personal_v2.ps1.")
    args = p.parse_args()

    args.manifest = _resolve_input(args.manifest)
    args.html = _resolve_input(args.html)

    if not args.manifest.exists():
        sys.exit(f"Manifest not found: {args.manifest}")
    if not args.html.exists():
        sys.exit(f"HTML not found: {args.html}")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    # ---- Validation ------------------------------------------------------
    if not args.skip_strict:
        problems = validate_manifest_for_publish(manifest, args.html, args.manifest)
        if problems:
            print("[publish-eval] Strict validation failed:")
            for pr in problems:
                print(f"  - {pr}")
            return 2
    else:
        print("[publish-eval] WARNING: --skip-strict set. Will refuse to push.")

    # ---- Resolve destination --------------------------------------------
    slug = build_slug(manifest)
    repo = args.repo.resolve()
    ensure_clone(repo, args.remote_url, args.dry_run)

    eval_dir = repo / EVAL_REPORTS_DIR_NAME
    site_manifest_path = repo / EVAL_REPORTS_JSON_NAME
    eval_html_path = repo / EVAL_HTML_NAME
    index_html_path = repo / "index.html"

    dest_html, dest_filename, collision_status = resolve_destination(repo, slug, args.new_version)

    # Load or seed the listing manifest.
    if site_manifest_path.exists():
        site_manifest = json.loads(site_manifest_path.read_text(encoding="utf-8"))
    else:
        site_manifest = {
            "title": "OL Agent EVAL Analysis",
            "subtitle": "Regression reports for SEVAL HeroEval runs",
            "owner": manifest.get("control", {}).get("owner", _default_owner()),
            "reports": [],
        }
    existing_reports = list(site_manifest.get("reports", []))

    # On --new-version, force slug change so upsert doesn't replace original.
    entry_slug = slug
    if args.new_version and collision_status.startswith("new-suffixed"):
        suffix_match = re.search(r"-(\d+)$", dest_filename.replace(".html", ""))
        if suffix_match:
            entry_slug = f"{slug}-{suffix_match.group(1)}"

    new_entry = build_entry(manifest, entry_slug, dest_filename, args.highlights.strip())
    new_reports, upsert_action = upsert_entry(existing_reports, new_entry)

    # Index injection: dry-detect whether index.html would change.
    index_change_msg = ""
    if index_html_path.exists():
        text = index_html_path.read_text(encoding="utf-8")
        has_markers = NAV_MARKER_START in text and NAV_MARKER_END in text
        has_legacy_standalone = bool(_LEGACY_STANDALONE_NAV_RE.search(text))
        has_in_top_bar = bool(
            re.search(
                re.escape(NAV_MARKER_START) + r".*?eval-nav-dropdown.*?" + re.escape(NAV_MARKER_END),
                text, flags=re.DOTALL,
            )
        ) and '<div class="top-bar' in text and 'eval-nav-dropdown' in text
        if NAV_MARKER_START in text and NAV_MARKER_END not in text or (
            NAV_MARKER_END in text and NAV_MARKER_START not in text
        ):
            index_change_msg = "ABORT later: only one nav marker present"
        elif has_legacy_standalone:
            index_change_msg = "migrate legacy floating dropdown → into top-bar"
        elif has_markers and has_in_top_bar:
            index_change_msg = "refresh dropdown in top-bar (markers found, idempotent)"
        elif has_markers:
            index_change_msg = "migrate dropdown markers → into top-bar"
        elif 'class="eval-nav"' in text:
            index_change_msg = "ABORT later: .eval-nav present without markers"
        else:
            index_change_msg = "first-run: inject dropdown into top-bar + CSS"
    else:
        index_change_msg = "no index.html present (will skip injection)"

    commit_msg = args.commit_message or (
        f"eval: publish {entry_slug} regression report\n\n"
        f"- {EVAL_REPORTS_DIR_NAME}/{dest_filename} ({args.html.stat().st_size // 1024} KB)\n"
        f"- {EVAL_REPORTS_JSON_NAME}: {upsert_action} entry for slug={entry_slug}\n"
        f"- eval.html: regenerated ({len(new_reports)} report(s))\n"
        + (f"- index.html: {index_change_msg}\n" if "ABORT" not in index_change_msg and "no index" not in index_change_msg else "")
        + (f"- Highlights: {new_entry['highlights']}\n" if new_entry['highlights'] else "")
    )

    # ---- Plan output -----------------------------------------------------
    print()
    print("=" * 72)
    print("PUBLISH PLAN — SEVAL Regression Report")
    print("=" * 72)
    print(f"  Repo                : {repo}")
    print(f"  Source HTML         : {args.html}  ({args.html.stat().st_size // 1024} KB)")
    print(f"  Source manifest     : {args.manifest}")
    print(f"  Slug                : {entry_slug}")
    print(f"  Collision status    : {collision_status}")
    print(f"  Target HTML         : {EVAL_REPORTS_DIR_NAME}/{dest_filename}")
    print(f"  Site manifest       : {EVAL_REPORTS_JSON_NAME} — action: {upsert_action}")
    print(f"  Listing page        : {EVAL_HTML_NAME} — regenerate ({len(new_reports)} card(s))")
    print(f"  index.html dropdown : {index_change_msg}")
    print(f"  Personal mirror     : "
          + ("skipped (--skip-mirror)" if args.skip_mirror
             else "run mirror_to_personal_v2.ps1 after push"))
    print(f"  Branch              : {args.branch}")
    print(f"  Commit message      :")
    for line in commit_msg.splitlines():
        print(f"    | {line}")
    print()
    print("  Live URLs after push:")
    print(f"    {DEFAULT_LIVE_BASE}/{EVAL_REPORTS_DIR_NAME}/{dest_filename}")
    print(f"    {DEFAULT_LIVE_BASE}/{EVAL_HTML_NAME}")
    print(f"    {PERSONAL_LIVE_BASE}/{EVAL_REPORTS_DIR_NAME}/{dest_filename}  (personal mirror)")
    print("=" * 72)
    print()

    if args.dry_run:
        print("[publish-eval] --dry-run set; no filesystem or git writes performed.")
        return 0

    if args.skip_strict:
        sys.exit("[publish-eval] Refusing to push because --skip-strict is set. Remove the flag and try again.")

    if not args.yes:
        try:
            answer = input("Type 'yes' to apply this plan, anything else to cancel: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("[publish-eval] Cancelled — no changes made.")
            return 1

    # ---- Apply -----------------------------------------------------------
    git_pull_rebase(repo)

    # Re-resolve destination after pull (in case a same-slug file was added remotely).
    dest_html, dest_filename, collision_status = resolve_destination(repo, slug, args.new_version)

    eval_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.html, dest_html)
    print(f"[publish-eval] Copied → {dest_html}")

    # Re-load + upsert in case the file changed during pull.
    if site_manifest_path.exists():
        site_manifest = json.loads(site_manifest_path.read_text(encoding="utf-8"))
    existing_reports = list(site_manifest.get("reports", []))
    new_reports, upsert_action = upsert_entry(existing_reports, new_entry)
    site_manifest["reports"] = new_reports
    site_manifest_path.write_text(
        json.dumps(site_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[publish-eval] {upsert_action} entry in {site_manifest_path.name}")

    eval_html_path.write_text(render_eval_html(site_manifest), encoding="utf-8")
    print(f"[publish-eval] Regenerated {eval_html_path.name}")

    if index_html_path.exists():
        changed, msg = inject_nav_into_index(index_html_path)
        if msg.startswith("ABORT"):
            sys.exit(f"[publish-eval] {msg}")
        print(f"[publish-eval] index.html: {msg}")

    # ---- Stage + commit + push ------------------------------------------
    to_add = [
        EVAL_REPORTS_DIR_NAME + "/" + dest_filename,
        EVAL_REPORTS_JSON_NAME,
        EVAL_HTML_NAME,
    ]
    if index_html_path.exists():
        to_add.append("index.html")
    _run(["git", "add"] + to_add, cwd=repo)

    if git_status_clean(repo):
        print("[publish-eval] Nothing staged after add — already up to date.")
        return 0

    _run(["git", "commit", "-m", commit_msg], cwd=repo)
    _run(["git", "push", "origin", args.branch], cwd=repo)

    sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo).stdout.strip()

    # ---- Mirror to personal repo ---------------------------------------
    mirror_status = "skipped (--skip-mirror)"
    if not args.skip_mirror:
        # Default mirror script lives in the ocv-extraction repo root,
        # which is the parent of `_ocv_weekly_repo` in the standard layout.
        default_script = repo.parent / "mirror_to_personal_v2.ps1"
        mirror_script = args.mirror_script or default_script
        if not mirror_script.exists():
            mirror_status = f"skipped (mirror script not found at {mirror_script})"
            print(f"[publish-eval] WARNING: {mirror_status}")
        else:
            print(f"[publish-eval] Mirroring to personal repo via {mirror_script.name} ...")
            try:
                _run(
                    [
                        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(mirror_script),
                    ],
                    cwd=mirror_script.parent,
                )
                mirror_status = "synced"
            except subprocess.CalledProcessError as err:
                mirror_status = f"FAILED (exit {err.returncode}) — run {mirror_script.name} manually"
                print(f"[publish-eval] WARNING: mirror step failed; "
                      f"gim-home is up to date but personal mirror needs a manual "
                      f"`./{mirror_script.name}` run.")

    print()
    print("=" * 72)
    print("PUBLISHED")
    print("=" * 72)
    print(f"  Commit              : {sha}")
    print(f"  Live report         : {DEFAULT_LIVE_BASE}/{EVAL_REPORTS_DIR_NAME}/{dest_filename}")
    print(f"  Listing page        : {DEFAULT_LIVE_BASE}/{EVAL_HTML_NAME}")
    print(f"  Personal mirror     : {PERSONAL_LIVE_BASE}/{EVAL_REPORTS_DIR_NAME}/{dest_filename}")
    print(f"  Mirror sync         : {mirror_status}")
    print()
    print("  Note: GitHub Pages may take ~1–2 minutes to publish the new file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
eval_regression_render.py — Render a SEVAL regression manifest into a single
self-contained dark-themed HTML report.

Reads a manifest produced by `eval_regression_extract.py` (plus any
agent-authored `why_failed` sentences) and writes one HTML file that can
be opened in any browser without local dependencies (only the marked.js
and DOMPurify CDN scripts are fetched at view-time).

Modes:
  default     Lenient. Rows with empty why_failed render with a "Pending
              analysis" placeholder so you can preview the layout while
              you finish the prose.
  --strict    Fail (exit code 2) if any regression has an empty why_failed
              or if publish_safety.reviewed_for_publish is not true.
              Required by publish_eval_regression_report.py.

Usage:
  python scripts/eval_regression_render.py \
      --manifest data/eval-manifests/<date>_<cid>_vs_<eid>_manifest.json \
      --out      output/eval-regression_<date>_<cid>_vs_<eid>.html

The script is deterministic. It never invents prose. All text comes from
the manifest or is fixed UI chrome.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


# Force UTF-8 console output on Windows (cp1252 default crashes on
# non-ASCII characters in status lines). No-op where stdout is already
# utf-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def safe_json_for_html(obj: Any) -> str:
    """JSON-encode for embedding inside <script>. Escape </ to break out of
    </script>, and escape <! and <script tokens defensively. ensure_ascii=False
    is fine because we're embedding in UTF-8 HTML and breaking on the literal
    angle-bracket sequences GitHub Pages might otherwise misinterpret."""
    s = json.dumps(obj, ensure_ascii=False)
    return (s
            .replace("</", "<\\/")
            .replace("<!--", "<\\!--")
            .replace("<script", "<\\script"))


def pct(x: float) -> str:
    return f"{round(x * 100, 1)}%"


def pp(delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{round(delta * 100, 1)}pp"


# ---------------------------------------------------------------------------
# HTML template — M3 dark palette aligned with OCV-Weekly
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{TITLE}}</title>
<meta name="description" content="{{META_DESC}}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans+Display:wght@400;500;700&family=Google+Sans+Text:wght@400;500;600&family=Roboto+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.6/dist/purify.min.js"></script>
<style>
:root {
  --bg: #101418;
  --bg-dim: #0a0d10;
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
  --tertiary-container: rgba(164, 204, 175, 0.14);
  --error: #ffb4ab;
  --error-container: rgba(255, 180, 171, 0.14);
  --warn: #ffd479;
  --warn-container: rgba(255, 212, 121, 0.12);
  --ctrl: #d29922;
  --ctrl-container: rgba(210, 153, 34, 0.15);
  --expr: #a371f7;
  --expr-container: rgba(163, 113, 247, 0.15);

  --font-display: 'Google Sans Display', 'Google Sans', system-ui, sans-serif;
  --font-body:    'Google Sans Text', 'Google Sans', system-ui, sans-serif;
  --font-mono:    'Roboto Mono', ui-monospace, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre { font-family: var(--font-mono); }

.page { max-width: 1280px; margin: 0 auto; padding: 32px 28px 80px; }
header.page-head { margin-bottom: 24px; }
.eyebrow {
  display: inline-block;
  padding: 4px 10px;
  background: var(--primary-container);
  color: var(--primary);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  margin-bottom: 12px;
}
h1.title {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 32px;
  line-height: 40px;
  margin: 0 0 8px;
}
.subtitle { color: var(--text-muted); font-size: 14px; }

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin: 24px 0 8px;
}
.kpi {
  background: var(--surface-1);
  border: 1px solid var(--outline);
  border-radius: 12px;
  padding: 14px 16px;
}
.kpi-label {
  font-size: 11px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: var(--text-faint);
  margin-bottom: 4px;
}
.kpi-value {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 22px;
  color: var(--text);
}
.kpi-value a.run-link {
  color: inherit;
  text-decoration: none;
  border-bottom: 1px dotted var(--text-faint);
  padding-bottom: 1px;
  transition: color 120ms ease, border-color 120ms ease;
}
.kpi-value a.run-link:hover,
.kpi-value a.run-link:focus-visible {
  color: var(--primary);
  border-bottom-color: var(--primary);
  outline: none;
}
.kpi-delta {
  font-size: 12px;
  margin-top: 2px;
}
.kpi-delta.up { color: var(--tertiary); }
.kpi-delta.down { color: var(--error); }
.kpi-delta.flat { color: var(--text-faint); }

.section {
  margin: 32px 0 0;
  background: var(--surface);
  border: 1px solid var(--outline);
  border-radius: 16px;
  padding: 20px 22px;
}
.section h2 {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 22px;
  margin: 0 0 14px;
}
.section .section-sub { color: var(--text-faint); margin-bottom: 14px; font-size: 13px; }

.flag-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.flag-col {
  background: var(--surface-1);
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid var(--outline);
}
.flag-col h3 {
  font-size: 13px;
  margin: 0 0 8px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.flag-col h3.added::before { content: "+"; color: var(--tertiary); margin-right: 4px; font-weight: 700; }
.flag-col h3.removed::before { content: "−"; color: var(--error); margin-right: 4px; font-weight: 700; }
.flag-list { display: flex; flex-wrap: wrap; gap: 6px; max-height: 320px; overflow-y: auto; }

.flag-diff-card {
  background: var(--surface-1);
  border: 1px solid var(--outline);
  border-radius: 12px;
  padding: 16px 18px;
  margin-bottom: 16px;
}
.flag-diff-card:last-child { margin-bottom: 0; }
.flag-diff-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.flag-diff-card__side {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 18px;
  color: var(--text);
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.flag-diff-card__side .side-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--primary);
  display: inline-block;
}
.flag-diff-card__meta {
  color: var(--text-faint);
  font-size: 12px;
  font-family: var(--font-mono);
}
.flag-diff-card .flag-grid { margin-top: 4px; }
.flag-diff-card details.flag-browse { margin-top: 14px; }
.flag-diff-card__nochange {
  color: var(--text-muted);
  font-style: italic;
  font-size: 13px;
  padding: 8px 0 4px;
}
.flag-diff-card__error {
  color: var(--error);
  font-size: 13px;
  padding: 8px 12px;
  border: 1px dashed var(--error);
  border-radius: 8px;
  background: var(--error-container);
}
.flag-pill {
  font-family: var(--font-mono);
  font-size: 11.5px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--surface-3);
  color: var(--text);
  border: 1px solid var(--outline);
}
.flag-pill.added { color: var(--tertiary); border-color: var(--tertiary); background: var(--tertiary-container); }
.flag-pill.removed { color: var(--error); border-color: var(--error); background: var(--error-container); }
.flag-pill.shared { color: var(--text-muted); border-color: var(--outline); background: var(--surface-2); }
.flag-empty { color: var(--text-faint); font-style: italic; font-size: 13px; }

details.flag-browse {
  margin-top: 18px;
  border: 1px solid var(--outline);
  border-radius: 10px;
  background: var(--surface-1);
  padding: 0;
  overflow: hidden;
}
details.flag-browse > summary {
  cursor: pointer;
  padding: 12px 16px;
  font-weight: 500;
  font-size: 14px;
  color: var(--text);
  background: var(--surface-2);
  list-style: none;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
details.flag-browse > summary::-webkit-details-marker { display: none; }
details.flag-browse > summary::before {
  content: '▸';
  color: var(--text-faint);
  display: inline-block;
  transition: transform 0.15s;
  margin-right: 8px;
}
details.flag-browse[open] > summary::before { transform: rotate(90deg); }
details.flag-browse > summary:hover { background: var(--surface-3); }
.flag-browse-legend {
  display: flex; gap: 14px; padding: 12px 16px 4px;
  font-size: 12px; color: var(--text-muted);
  flex-wrap: wrap;
}
.flag-browse-legend span { display: inline-flex; align-items: center; gap: 6px; }
.flag-browse-legend i {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  border: 1px solid currentColor;
}
.flag-browse-search {
  width: 100%;
  background: var(--surface);
  border: 1px solid var(--outline);
  border-radius: 8px;
  padding: 8px 12px;
  color: var(--text);
  font-family: inherit;
  font-size: 13px;
  margin: 8px 16px 0;
  max-width: calc(100% - 32px);
  box-sizing: border-box;
}
.flag-browse-search:focus { outline: 2px solid var(--primary); }
.flag-browse-body {
  padding: 12px 16px 16px;
  display: flex; flex-wrap: wrap; gap: 6px;
  max-height: 420px; overflow-y: auto;
}

.warn-banner {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--warn-container);
  color: var(--warn);
  border: 1px solid var(--warn);
  font-size: 13px;
}

.controls {
  position: sticky; top: 0;
  background: var(--bg);
  border-bottom: 1px solid var(--outline);
  padding: 12px 0;
  margin: 24px 0 12px;
  z-index: 10;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}
.search-box {
  flex: 1;
  min-width: 260px;
  background: var(--surface-1);
  border: 1px solid var(--outline);
  border-radius: 8px;
  padding: 8px 12px;
  color: var(--text);
  font-family: inherit;
  font-size: 14px;
}
.search-box:focus { outline: 2px solid var(--primary); }
.filter-pill {
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  cursor: pointer;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--outline);
  user-select: none;
}
.filter-pill.active { outline: 2px solid var(--primary); }
.pill-cc { background: var(--ctrl-container); color: var(--ctrl); border-color: var(--ctrl); }
.pill-ee { background: var(--expr-container); color: var(--expr); border-color: var(--expr); }
.btn-action {
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--outline);
  cursor: pointer;
  font-size: 12px;
}
.btn-action:hover { background: var(--surface-3); }

.no-results {
  padding: 40px;
  text-align: center;
  color: var(--text-faint);
  font-style: italic;
}

details.query {
  background: var(--surface);
  border: 1px solid var(--outline);
  border-radius: 12px;
  margin: 10px 0;
  overflow: hidden;
}
details.query[open] { border-color: var(--primary); }
summary.query-header {
  padding: 14px 16px;
  cursor: pointer;
  display: flex;
  gap: 10px;
  align-items: center;
  list-style: none;
}
summary.query-header::-webkit-details-marker { display: none; }
summary.query-header::before {
  content: "▸";
  color: var(--text-muted);
  transition: transform 0.15s;
  font-size: 11px;
}
details.query[open] > summary.query-header::before { transform: rotate(90deg); }
.query-text { flex: 1; font-weight: 500; }
.badge {
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 11px;
  border: 1px solid;
  font-weight: 500;
}
.badge-cc { background: var(--ctrl-container); color: var(--ctrl); border-color: var(--ctrl); }
.badge-ee { background: var(--expr-container); color: var(--expr); border-color: var(--expr); }
.badge-count { background: var(--surface-3); color: var(--text); border-color: var(--outline); font-weight: 600; }

.query-body { padding: 4px 18px 18px; }
.assertion {
  background: var(--bg-dim);
  border-radius: 10px;
  padding: 14px;
  margin: 10px 0;
  border: 1px solid var(--outline);
}
.assertion-header { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px; align-items: flex-start; }
.assertion-text { color: var(--text); font-weight: 500; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
.col {
  background: var(--surface-1);
  border-radius: 8px;
  border: 1px solid var(--outline);
  overflow: hidden;
}
.col-header {
  padding: 8px 12px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  font-weight: 600;
}
.col-header.pass { background: rgba(164,204,175,0.12); color: var(--tertiary); }
.col-header.fail { background: rgba(255,180,171,0.12); color: var(--error); }
.col-body { padding: 12px 14px; font-size: 13px; color: var(--text-muted); max-height: 380px; overflow-y: auto; }
.col-body :first-child { margin-top: 0; }
.col-body :last-child { margin-bottom: 0; }
.col-body p, .col-body li { margin: 6px 0; }
.col-body code { background: var(--surface-3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.col-body pre { background: var(--surface-3); padding: 10px; border-radius: 6px; overflow-x: auto; }
.col-body table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px; }
.col-body th, .col-body td { border: 1px solid var(--outline); padding: 6px 8px; text-align: left; }
.col-body th { background: var(--surface-2); }
.col-body a { word-break: break-all; }

.why {
  background: var(--primary-container);
  border-left: 3px solid var(--primary);
  padding: 10px 12px;
  margin-top: 10px;
  border-radius: 6px;
  font-size: 13px;
}
.why.placeholder { background: var(--warn-container); border-left-color: var(--warn); color: var(--warn); font-style: italic; }
.why-label {
  font-size: 11px;
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--primary);
  margin-bottom: 4px;
}
.why.placeholder .why-label { color: var(--warn); }

details.rationale { margin-top: 8px; font-size: 12px; }
details.rationale > summary {
  cursor: pointer;
  color: var(--text-faint);
  padding: 4px 0;
  list-style: none;
}
details.rationale > summary::-webkit-details-marker { display: none; }
details.rationale > summary::before { content: "▸ "; }
details.rationale[open] > summary::before { content: "▾ "; }
.rationale-body {
  background: var(--surface-2);
  border-radius: 6px;
  padding: 10px 12px;
  margin-top: 6px;
}
.rationale-side { margin-bottom: 6px; }
.rationale-side strong { color: var(--text); }

.footer-meta {
  margin-top: 40px;
  padding-top: 16px;
  border-top: 1px solid var(--outline);
  color: var(--text-faint);
  font-size: 12px;
}
.footer-meta a { color: var(--primary); }

@media (max-width: 720px) {
  .grid { grid-template-columns: 1fr; }
  .flag-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="page">
<header class="page-head">
  <span class="eyebrow">SEVAL Regression Report</span>
  <h1 class="title">{{H1_TITLE}}</h1>
  <p class="subtitle">{{SUBTITLE}}</p>
</header>

<section class="kpi-grid">
{{KPI_CARDS}}
</section>

<section class="section">
  <h2>Feature flag diff</h2>
  <p class="section-sub">{{FLAG_DESCRIPTION}}</p>
  {{FLAG_DIFFS_HTML}}
</section>

<section class="section">
  <h2>Regressions</h2>
  <p class="section-sub">{{REGRESSION_DENOMINATOR}}</p>
  <div class="controls">
    <input type="search" class="search-box" id="search" placeholder="Search queries, assertions, replies …">
    <button class="filter-pill pill-all active" data-filter="all">All ({{TOTAL_REG}})</button>
    <button class="filter-pill pill-cc" data-filter="control_vs_control">{{CONTROL_NAME}} vs {{CONTROL_NAME}} ({{CC_COUNT}})</button>
    <button class="filter-pill pill-ee" data-filter="experiment_vs_experiment">{{EXPERIMENT_NAME}} vs {{EXPERIMENT_NAME}} ({{EE_COUNT}})</button>
    <button class="btn-action" id="expand-all">Expand all</button>
    <button class="btn-action" id="collapse-all">Collapse all</button>
  </div>
  <div id="queries">
{{QUERIES_HTML}}
  </div>
  <div class="no-results" id="no-results" style="display:none">No regressions match the current filter.</div>
</section>

<footer class="footer-meta">
  <div>Generated {{GENERATED_AT}} from <code>{{MANIFEST_PATH}}</code>.</div>
  <div>Schema version {{SCHEMA_VERSION}}. SEVAL replies are model outputs (not customer content).</div>
</footer>

</div>
<script id="manifest-data" type="application/json">{{MANIFEST_JSON}}</script>
<script>
(function () {
  const cfg = { GFM: true, breaks: true, mangle: false, headerIds: false };
  marked.setOptions(cfg);

  function renderMarkdown(text) {
    if (!text) return '<em style="color:var(--text-faint)">(empty)</em>';
    return DOMPurify.sanitize(marked.parse(String(text)));
  }

  // Render markdown into every .md-target element.
  document.querySelectorAll('.md-target').forEach(el => {
    const src = el.getAttribute('data-md') || '';
    el.innerHTML = renderMarkdown(src);
  });

  // Search + filter wiring.
  const search = document.getElementById('search');
  const pills = document.querySelectorAll('.filter-pill');
  const queriesRoot = document.getElementById('queries');
  const noResults = document.getElementById('no-results');
  let activeFilter = 'all';

  function applyFilter() {
    const q = (search.value || '').toLowerCase().trim();
    let anyVisible = false;
    queriesRoot.querySelectorAll('details.query').forEach(qNode => {
      let anyAssertionVisible = false;
      const queryText = qNode.querySelector('.query-text').textContent.toLowerCase();
      qNode.querySelectorAll('.assertion').forEach(aNode => {
        const cmp = aNode.getAttribute('data-comparison');
        const filterOk = (activeFilter === 'all' || cmp === activeFilter);
        const text = (queryText + ' ' + aNode.textContent.toLowerCase());
        const searchOk = !q || text.includes(q);
        const visible = filterOk && searchOk;
        aNode.style.display = visible ? '' : 'none';
        if (visible) anyAssertionVisible = true;
      });
      qNode.style.display = anyAssertionVisible ? '' : 'none';
      if (anyAssertionVisible) anyVisible = true;
    });
    noResults.style.display = anyVisible ? 'none' : '';
  }

  search.addEventListener('input', applyFilter);
  pills.forEach(p => p.addEventListener('click', () => {
    pills.forEach(x => x.classList.remove('active'));
    p.classList.add('active');
    activeFilter = p.getAttribute('data-filter');
    applyFilter();
  }));

  document.getElementById('expand-all').addEventListener('click', () =>
    queriesRoot.querySelectorAll('details.query').forEach(d => d.open = true));
  document.getElementById('collapse-all').addEventListener('click', () =>
    queriesRoot.querySelectorAll('details.query').forEach(d => d.open = false));

  // Flag-browse filter — per-card. Each `details.flag-browse` has its own
  // search input and body, scoped by data-scope so multiple cards on the
  // page filter independently.
  document.querySelectorAll('details.flag-browse').forEach(card => {
    const input = card.querySelector('.flag-browse-search');
    const body = card.querySelector('.flag-browse-body');
    if (!input || !body) return;
    input.addEventListener('input', () => {
      const q = (input.value || '').toLowerCase().trim();
      body.querySelectorAll('.flag-pill').forEach(p => {
        const t = p.getAttribute('data-flag') || '';
        p.style.display = (!q || t.indexOf(q) !== -1) ? '' : 'none';
      });
    });
  });
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_kpi_cards(manifest: Dict[str, Any]) -> str:
    ctrl = manifest["control"]
    exp = manifest["experiment"]
    summary = manifest["summary"]
    counts = summary["regression_counts"]
    total_reg = counts["control_vs_control"]["regressions"] + counts["experiment_vs_experiment"]["regressions"]
    total_imp = counts["control_vs_control"]["improvements"] + counts["experiment_vs_experiment"]["improvements"]
    matched = summary["matched_pairs"]

    cards = []

    def card(label: str, value: str, sub: str = "", delta_class: str = "", value_html: str = "") -> str:
        sub_html = f'<div class="kpi-delta {delta_class}">{html.escape(sub)}</div>' if sub else ""
        value_inner = value_html if value_html else html.escape(value)
        return (
            f'<div class="kpi">'
            f'<div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value">{value_inner}</div>'
            f'{sub_html}'
            f'</div>'
        )

    ctrl_id = str(ctrl["id"])
    exp_id = str(exp["id"])
    runs_value_html = (
        f'<a class="run-link" href="https://seval.microsoft.com/job/{html.escape(ctrl_id, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'title="Open SEVAL run {html.escape(ctrl_id)}">#{html.escape(ctrl_id)}</a>'
        f' &rarr; '
        f'<a class="run-link" href="https://seval.microsoft.com/job/{html.escape(exp_id, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'title="Open SEVAL run {html.escape(exp_id)}">#{html.escape(exp_id)}</a>'
    )
    cards.append(card(
        "Runs compared",
        "",
        f"{ctrl['run_date']} · {ctrl['queries']} vs {exp['queries']} queries · {matched} comparable",
        value_html=runs_value_html,
    ))

    # Pass-rate KPI per side.
    cc_a = ctrl["control_side_pass_rate"]; cc_b = exp["control_side_pass_rate"]
    ee_a = ctrl["experiment_side_pass_rate"]; ee_b = exp["experiment_side_pass_rate"]
    cc_delta = cc_b - cc_a
    ee_delta = ee_b - ee_a
    cards.append(card(f"{ctrl['name']} pass rate",
                      f"{pct(cc_a)} → {pct(cc_b)}", pp(cc_delta),
                      "up" if cc_delta > 0 else ("down" if cc_delta < 0 else "flat")))
    cards.append(card(f"{exp['name']} pass rate",
                      f"{pct(ee_a)} → {pct(ee_b)}", pp(ee_delta),
                      "up" if ee_delta > 0 else ("down" if ee_delta < 0 else "flat")))

    cards.append(card("Regressions",
                      str(total_reg),
                      f"out of {matched} comparable"))
    cards.append(card("Improvements",
                      str(total_imp),
                      f"out of {matched} comparable"))

    return "\n".join(cards)


def render_flag_browse(flags_diff: Dict[str, Any], scope_id: str, side_label: str) -> str:
    """Collapsible that shows every flag in this slot, color-coded as
    added / removed / shared. `scope_id` is a unique suffix used to scope
    the search input id so each card on the page can filter independently."""
    added = list(flags_diff.get("added", []))
    removed = list(flags_diff.get("removed", []))
    shared = list(flags_diff.get("shared", []))
    all_pairs = [(t, "added") for t in added] + [(t, "removed") for t in removed] + [(t, "shared") for t in shared]
    if not all_pairs:
        return ""
    all_pairs.sort(key=lambda p: p[0].lower())
    total = len(all_pairs)
    pills = "\n".join(
        f'<span class="flag-pill {cls}" data-flag="{html.escape(t.lower())}">{html.escape(t)}</span>'
        for t, cls in all_pairs
    )
    side_label_esc = html.escape(side_label)
    legend = (
        '<div class="flag-browse-legend">'
        f'<span style="color:var(--tertiary)"><i></i>Added in run #{{{{EID}}}} ({side_label_esc})</span>'
        f'<span style="color:var(--error)"><i></i>Removed from run #{{{{CID}}}} ({side_label_esc})</span>'
        '<span style="color:var(--text-muted)"><i></i>Shared (unchanged)</span>'
        '</div>'
    )
    search_id = f"flag-browse-search-{scope_id}"
    body_id = f"flag-browse-body-{scope_id}"
    return (
        f'<details class="flag-browse" data-scope="{html.escape(scope_id)}">'
        f'<summary>Browse all {total} {side_label_esc} flag(s)</summary>'
        f'{legend}'
        f'<input type="search" class="flag-browse-search" placeholder="Filter flags …" id="{search_id}">'
        f'<div class="flag-browse-body" id="{body_id}">{pills}</div>'
        f'</details>'
    )


def render_flag_pills(items: List[str], cls: str) -> str:
    if not items:
        return '<span class="flag-empty">(none)</span>'
    return "\n".join(
        f'<span class="flag-pill {cls}">{html.escape(t)}</span>' for t in items
    )


def render_flag_diff_card(diff: Dict[str, Any],
                          control_id: str, experiment_id: str,
                          scope_id: str) -> str:
    """Render a single side's flag diff as a self-contained card."""
    side_label = diff.get("side_label") or "Side"
    added = list(diff.get("added", []))
    removed = list(diff.get("removed", []))
    unchanged = int(diff.get("unchanged_count", 0))
    total_ctrl = int(diff.get("total_in_control_run", len(added) + unchanged))
    total_exp = int(diff.get("total_in_experiment_run", len(removed) + unchanged))
    slot_index = diff.get("slot_index")
    ctrl_slot_name = diff.get("control_run_slot_exp_name", "?")
    exp_slot_name = diff.get("experiment_run_slot_exp_name", "?")
    meta = (
        f"slot {slot_index} · "
        f"#{html.escape(str(control_id))}: {total_ctrl} flags ({html.escape(ctrl_slot_name)}) → "
        f"#{html.escape(str(experiment_id))}: {total_exp} flags ({html.escape(exp_slot_name)})"
    )
    mismatch = ""
    if diff.get("slot_name_mismatch"):
        mismatch = (
            f'<div class="flag-diff-card__error">⚠ Slot name mismatch: '
            f'{html.escape(ctrl_slot_name)} (control run) vs '
            f'{html.escape(exp_slot_name)} (experiment run). '
            f'The side label may not apply cleanly.</div>'
        )

    if not added and not removed:
        body = (
            '<div class="flag-diff-card__nochange">No flag changes on this side — '
            f'{unchanged} flags identical between the two runs.</div>'
        )
    else:
        body = (
            '<div class="flag-grid">'
            '<div class="flag-col">'
            f'<h3 class="added">Added in run #{html.escape(str(experiment_id))} ({len(added)})</h3>'
            f'<div class="flag-list">{render_flag_pills(added, "added")}</div>'
            '</div>'
            '<div class="flag-col">'
            f'<h3 class="removed">Removed from run #{html.escape(str(control_id))} ({len(removed)})</h3>'
            f'<div class="flag-list">{render_flag_pills(removed, "removed")}</div>'
            '</div>'
            '</div>'
        )

    browse = render_flag_browse(diff, scope_id, side_label)
    # Wire the {{CID}}/{{EID}} placeholders that render_flag_browse left in
    # the legend so the labels reference the actual run IDs.
    browse = browse.replace("{{CID}}", html.escape(str(control_id))) \
                   .replace("{{EID}}", html.escape(str(experiment_id)))

    return (
        '<div class="flag-diff-card">'
        '<div class="flag-diff-card__head">'
        f'<h3 class="flag-diff-card__side"><span class="side-dot"></span>{html.escape(side_label)}</h3>'
        f'<div class="flag-diff-card__meta">{meta}</div>'
        '</div>'
        f'{mismatch}'
        f'{body}'
        f'{browse}'
        '</div>'
    )


def _legacy_to_diffs(fd: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Adapt a 1.0-shape feature_flags block to the v2 diffs[] list so the
    renderer can show old manifests without re-extraction."""
    primary = {
        "slot_index": 1,
        "side_label": derive_side_label(fd.get("experiment_run_exp_name", "experiment")),
        "control_run_slot_exp_name": fd.get("control_run_exp_name", "experiment"),
        "experiment_run_slot_exp_name": fd.get("experiment_run_exp_name", "experiment"),
        "added": fd.get("added", []),
        "removed": fd.get("removed", []),
        "shared": fd.get("shared", []),
        "added_count": fd.get("added_count", 0),
        "removed_count": fd.get("removed_count", 0),
        "unchanged_count": fd.get("unchanged_count", 0),
        "total_in_control_run": fd.get("total_control_side", 0),
        "total_in_experiment_run": fd.get("total_experiment_side", 0),
    }
    diffs = [primary]
    drift = fd.get("control_side_drift") or {}
    if drift:
        diffs.insert(0, {
            "slot_index": 0,
            "side_label": "Mainline",
            "control_run_slot_exp_name": "control",
            "experiment_run_slot_exp_name": "control",
            "added": drift.get("added", []),
            "removed": drift.get("removed", []),
            "shared": drift.get("shared", []),
            "added_count": drift.get("added_count", 0),
            "removed_count": drift.get("removed_count", 0),
            "unchanged_count": drift.get("unchanged_count", 0),
            "total_in_control_run": drift.get("total_control_side", 0),
            "total_in_experiment_run": drift.get("total_experiment_side", 0),
        })
    return diffs


def derive_side_label(exp_name: str) -> str:
    """Mirror of the extract-side helper. Kept here so renderer can label
    cards when adapting legacy manifests without re-running extract."""
    key = (exp_name or "").strip().lower()
    overrides = {"control": "Mainline", "experiment": "CodeGen"}
    if key in overrides:
        return overrides[key]
    return (exp_name or "Unknown").strip().title() or "Unknown"


def render_flag_diffs_section(fd: Dict[str, Any],
                              control_id: str, experiment_id: str) -> str:
    """Return the inner HTML for the Feature-flag-diff section."""
    diffs = list(fd.get("diffs") or [])
    if not diffs:
        diffs = _legacy_to_diffs(fd)
    if not diffs:
        return '<div class="flag-diff-card__nochange">No flag data available.</div>'
    # Stable order: by slot index when present, else input order.
    diffs.sort(key=lambda d: (d.get("slot_index") if isinstance(d.get("slot_index"), int) else 99))
    parts = [
        render_flag_diff_card(d, control_id, experiment_id, scope_id=f"slot{d.get('slot_index', i)}")
        for i, d in enumerate(diffs)
    ]
    errors = fd.get("errors") or []
    if errors:
        parts.append(
            '<div class="flag-diff-card__error">⚠ Flag extraction errors: '
            + html.escape("; ".join(errors))
            + '</div>'
        )
    return "\n".join(parts)


def render_query_blocks(regressions: List[Dict[str, Any]], ctrl_name: str, exp_name: str) -> str:
    if not regressions:
        return '<div class="no-results">No regressions to display.</div>'

    # Group by query, preserving original ordering.
    by_query: Dict[str, List[Dict[str, Any]]] = {}
    for r in regressions:
        by_query.setdefault(r["query"], []).append(r)

    parts = []
    for query, rows in by_query.items():
        # Per-query badge counts.
        cc_n = sum(1 for r in rows if r["comparison"] == "control_vs_control")
        ee_n = sum(1 for r in rows if r["comparison"] == "experiment_vs_experiment")
        badges = []
        if cc_n:
            badges.append(f'<span class="badge badge-cc">{ctrl_name}: {cc_n}</span>')
        if ee_n:
            badges.append(f'<span class="badge badge-ee">{exp_name}: {ee_n}</span>')
        badges.append(f'<span class="badge badge-count">{len(rows)} total</span>')

        assertions_html = "\n".join(render_assertion(r, ctrl_name, exp_name) for r in rows)
        parts.append(
            f'<details class="query">'
            f'<summary class="query-header">'
            f'<span class="query-text">{html.escape(query)}</span>'
            f'{" ".join(badges)}'
            f'</summary>'
            f'<div class="query-body">{assertions_html}</div>'
            f'</details>'
        )
    return "\n".join(parts)


def render_assertion(r: Dict[str, Any], ctrl_name: str, exp_name: str) -> str:
    cmp = r["comparison"]
    cmp_label = (
        f'{ctrl_name} vs {ctrl_name}' if cmp == "control_vs_control"
        else f'{exp_name} vs {exp_name}'
    )
    cmp_badge_cls = "badge-cc" if cmp == "control_vs_control" else "badge-ee"
    level = r.get("level", "")
    segment = r.get("segment", "")
    meta_bits = []
    if level: meta_bits.append(html.escape(level))
    if segment: meta_bits.append(html.escape(segment))
    meta_html = f' · <span style="color:var(--text-faint);font-size:11px">{" · ".join(meta_bits)}</span>' if meta_bits else ""

    why = (r.get("why_failed") or "").strip()
    if why:
        why_html = (
            f'<div class="why">'
            f'<div class="why-label">Why it failed</div>'
            f'{html.escape(why)}'
            f'</div>'
        )
    else:
        why_html = (
            f'<div class="why placeholder">'
            f'<div class="why-label">Why it failed</div>'
            f'Pending analysis (the agent has not yet authored a one-sentence explanation for this row).'
            f'</div>'
        )

    rationale_passed = r.get("rationale_passed", "")
    rationale_failed = r.get("rationale_failed", "")
    rationale_html = ""
    if rationale_passed or rationale_failed:
        rationale_html = (
            f'<details class="rationale">'
            f'<summary>Judge rationales</summary>'
            f'<div class="rationale-body">'
            f'<div class="rationale-side"><strong>Passed (run that scored 1):</strong> {html.escape(rationale_passed) or "(empty)"}</div>'
            f'<div class="rationale-side"><strong>Failed (run that scored 0):</strong> {html.escape(rationale_failed) or "(empty)"}</div>'
            f'</div>'
            f'</details>'
        )

    # Markdown content lives in data-md attributes; JS turns it into HTML at load.
    reply_passed = r.get("reply_passed", "")
    reply_failed = r.get("reply_failed", "")

    return (
        f'<div class="assertion" data-comparison="{html.escape(cmp)}" data-id="{html.escape(r["id"])}">'
        f'  <div class="assertion-header">'
        f'    <div class="assertion-text">{html.escape(r["assertion"])}{meta_html}</div>'
        f'    <span class="badge {cmp_badge_cls}">{html.escape(cmp_label)}</span>'
        f'  </div>'
        f'  <div class="grid">'
        f'    <div class="col">'
        f'      <div class="col-header pass">✓ Passed in the other run</div>'
        f'      <div class="col-body md-target" data-md="{html.escape(reply_passed, quote=True)}"></div>'
        f'    </div>'
        f'    <div class="col">'
        f'      <div class="col-header fail">✗ Failed here</div>'
        f'      <div class="col-body md-target" data-md="{html.escape(reply_failed, quote=True)}"></div>'
        f'    </div>'
        f'  </div>'
        f'  {why_html}'
        f'  {rationale_html}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(manifest: Dict[str, Any], manifest_path: Path) -> str:
    ctrl = manifest["control"]
    exp = manifest["experiment"]
    summary = manifest["summary"]
    counts = summary["regression_counts"]
    cc_count = counts["control_vs_control"]["regressions"]
    ee_count = counts["experiment_vs_experiment"]["regressions"]
    total_reg = cc_count + ee_count
    matched = summary["matched_pairs"]

    fd = manifest["feature_flags"]

    title = f"SEVAL Regression: Run #{ctrl['id']} vs Run #{exp['id']}"
    subtitle = (
        f"Comparing run #{ctrl['id']} ({ctrl['run_date']}) against "
        f"run #{exp['id']} ({exp['run_date']}). Each run contains both a "
        f"{ctrl['name']}-side and a {exp['name']}-side reply per query; "
        f"this report flags assertions that passed in one run and failed in the other, on the SAME side. "
        f"{total_reg} regressions identified across {matched} comparable (query, assertion) pairs."
    )
    if manifest.get("highlights"):
        subtitle += f" — {manifest['highlights']}"

    # Build a one-line summary of all per-side diffs for the section sub.
    diffs = list(fd.get("diffs") or _legacy_to_diffs(fd))
    if diffs:
        per_side_blurb = "; ".join(
            f"{d.get('side_label', 'side')}: +{d.get('added_count', 0)} / -{d.get('removed_count', 0)} "
            f"({d.get('unchanged_count', 0)} unchanged)"
            for d in diffs
        )
        flag_desc = (
            f"Run #{ctrl['id']} vs Run #{exp['id']}, compared per side. {per_side_blurb}."
        )
    else:
        flag_desc = "No flag data available."

    regression_denominator = (
        f"{total_reg} regressions out of {matched} comparable assertions "
        f"({cc_count} in {ctrl['name']} vs {ctrl['name']}, "
        f"{ee_count} in {exp['name']} vs {exp['name']})."
    )

    queries_html = render_query_blocks(manifest.get("regressions", []), ctrl["name"], exp["name"])

    substitutions = {
        "{{TITLE}}": html.escape(title),
        "{{META_DESC}}": html.escape(subtitle),
        "{{H1_TITLE}}": html.escape(title),
        "{{SUBTITLE}}": html.escape(subtitle),
        "{{KPI_CARDS}}": render_kpi_cards(manifest),
        "{{FLAG_DESCRIPTION}}": html.escape(flag_desc),
        "{{FLAG_DIFFS_HTML}}": render_flag_diffs_section(fd, ctrl["id"], exp["id"]),
        "{{CONTROL_NAME}}": html.escape(ctrl["name"]),
        "{{EXPERIMENT_NAME}}": html.escape(exp["name"]),
        "{{REGRESSION_DENOMINATOR}}": html.escape(regression_denominator),
        "{{TOTAL_REG}}": str(total_reg),
        "{{CC_COUNT}}": str(cc_count),
        "{{EE_COUNT}}": str(ee_count),
        "{{QUERIES_HTML}}": queries_html,
        "{{GENERATED_AT}}": html.escape(_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
        "{{MANIFEST_PATH}}": html.escape(str(manifest_path)),
        "{{SCHEMA_VERSION}}": html.escape(manifest.get("schema_version", "?")),
        "{{MANIFEST_JSON}}": safe_json_for_html(manifest),
    }

    out = HTML_TEMPLATE
    for k, v in substitutions.items():
        out = out.replace(k, v)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--strict", action="store_true",
                   help="Fail (exit 2) if any why_failed is blank or publish_safety.reviewed_for_publish is false.")
    args = p.parse_args()

    if not args.manifest.exists():
        sys.exit(f"[render] Manifest not found: {args.manifest}")

    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Strict validation.
    if args.strict:
        problems = []
        blanks = [r["id"] for r in manifest.get("regressions", []) if not (r.get("why_failed") or "").strip()]
        if blanks:
            problems.append(f"{len(blanks)} regression rows have empty why_failed: {blanks[:5]}{' …' if len(blanks) > 5 else ''}")
        if not manifest.get("publish_safety", {}).get("reviewed_for_publish"):
            problems.append("publish_safety.reviewed_for_publish is not true")
        if problems:
            print("[render] --strict checks failed:")
            for p_ in problems:
                print(f"  - {p_}")
            return 2

    html_text = render(manifest, args.manifest)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_text, encoding="utf-8")

    n_reg = len(manifest.get("regressions", []))
    blanks = sum(1 for r in manifest.get("regressions", []) if not (r.get("why_failed") or "").strip())
    print(f"[render] Wrote {args.out} ({args.out.stat().st_size // 1024} KB)")
    print(f"[render] {n_reg} regressions rendered; {blanks} still have placeholder why_failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
      --out      output/seval/regression/eval-regression_<date>_<cid>_vs_<eid>.html

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


# Monorepo root (OLAgentWork/) = <repo>/seval-analysis/scripts/ -> parents[2].
# Relative output paths are resolved against this so reports always land in
# OLAgentWork/output/... regardless of the current working directory.
REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_output(path: Path) -> Path:
    """Resolve a relative output path against the monorepo root so generated
    files land under OLAgentWork/output/ no matter where the script is run
    from. Absolute paths are returned unchanged."""
    return path if path.is_absolute() else (REPO_ROOT / path)


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
<meta name="seval:manifest-slug" content="{{MANIFEST_SLUG}}">
<meta name="seval:total-regressions" content="{{TOTAL_REG}}">
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
.btn-action.btn-primary {
  background: var(--primary-container);
  color: var(--primary);
  border-color: var(--primary);
  font-weight: 600;
}
.btn-action.btn-primary:hover { background: rgba(168, 199, 250, 0.22); }
.sort-select {
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--outline);
  cursor: pointer;
  font-size: 12px;
  font-family: inherit;
}
.sort-select:focus { outline: 2px solid var(--primary); }

/* ---- Regression themes chart ---- */
.theme-controls {
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  margin: 4px 0 14px;
}
.theme-hint { font-size: 12px; color: var(--text-muted); }
.theme-chart { display: flex; flex-direction: column; gap: 8px; }
.theme-row {
  display: grid;
  grid-template-columns: 130px 1fr auto;
  gap: 10px; align-items: center;
}
.theme-bar-btn {
  display: grid;
  grid-template-columns: 1fr; gap: 4px;
  text-align: left; cursor: pointer;
  background: var(--surface-1);
  border: 1px solid var(--outline);
  border-radius: 10px;
  padding: 8px 12px;
  color: var(--text);
  width: 100%;
}
.theme-bar-btn:hover { background: var(--surface-2); }
.theme-row.theme-active .theme-bar-btn { outline: 2px solid var(--primary); background: var(--surface-2); }
.theme-meta { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
.theme-label { font-size: 13px; font-weight: 600; }
.theme-count { font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.theme-crit { color: var(--error); }
.theme-track { height: 8px; border-radius: 999px; background: var(--surface-3); overflow: hidden; }
.theme-fill { display: block; height: 100%; border-radius: 999px; background: var(--primary); }
.theme-desc { font-size: 11px; color: var(--text-faint); }
.theme-ado {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 8px; border-radius: 6px; font-size: 11px;
  color: var(--text-faint); background: var(--surface-2);
  border: 1px solid var(--outline); cursor: pointer; user-select: none;
  white-space: nowrap; justify-self: start;
}
.theme-ado.is-on { color: var(--tertiary); border-color: var(--tertiary); background: var(--tertiary-container); }
.theme-ado-count { font-variant-numeric: tabular-nums; opacity: 0.85; }
.theme-example {
  font-size: 11px; color: var(--primary); background: transparent;
  border: 1px dashed var(--outline); border-radius: 6px; padding: 4px 8px;
  cursor: pointer; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.theme-example:hover { background: var(--surface-2); border-style: solid; }
.assertion.row-flash { outline: 2px solid var(--primary); outline-offset: 2px; transition: outline-color 0.2s; }
@media (max-width: 720px) {
  .theme-row { grid-template-columns: 1fr; }
}

/* ---- Tool sub-category (owner routing) ---- */
.tool-toggle {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--text-muted); cursor: pointer; user-select: none;
}
.tool-toggle input { cursor: pointer; }
.tool-chips { display: none; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
body.tools-on .tool-chips { display: flex; }
.tool-chip {
  font-size: 10px; font-variant-numeric: tabular-nums;
  padding: 1px 7px; border-radius: 999px;
  background: var(--surface-3); color: var(--text-muted);
  border: 1px solid var(--outline); white-space: nowrap;
}
.tool-chip .tool-chip-crit { color: var(--error); }
.tool-badge {
  display: none;
  font-size: 10px; font-weight: 600; letter-spacing: .02em;
  padding: 1px 7px; border-radius: 999px; white-space: nowrap;
  background: var(--tertiary-container); color: var(--tertiary);
  border: 1px solid var(--tertiary);
}
body.tools-on .tool-badge { display: inline-block; }

.controls-group {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.controls-group.controls-ado {
  margin-left: auto;
  padding-left: 12px;
  border-left: 1px solid var(--outline);
}
.selection-counter {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.selection-counter .sel-on  { color: var(--tertiary); font-weight: 600; }
.selection-counter .sel-off { color: var(--text-faint); }

.assertion-select {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 11px;
  color: var(--text-faint);
  background: var(--surface-2);
  border: 1px solid var(--outline);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}
.assertion-select input { margin: 0; cursor: pointer; accent-color: var(--tertiary); }
.assertion-select:hover { background: var(--surface-3); color: var(--text-muted); }
.assertion-select.is-on { color: var(--tertiary); border-color: var(--tertiary); background: var(--tertiary-container); }

.assertion.is-excluded {
  opacity: 0.55;
  border-style: dashed;
}
.assertion.is-excluded .assertion-text::before {
  content: "EXCLUDED · ";
  color: var(--text-faint);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

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
.assertion-text { color: var(--text); font-weight: 500; flex: 1 1 auto; min-width: 0; }
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
  <h2>Regression themes</h2>
  <p class="section-sub">{{THEME_SUBTITLE}}</p>
  {{THEME_CHART_HTML}}
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
    <select class="sort-select" id="sort-mode" title="Sort the regressions list by assertion level (rows stay grouped by query)">
      <option value="default">Sort: grouped by query</option>
      <option value="critical">Sort: level — critical first</option>
      <option value="aspirational">Sort: level — aspirational first</option>
    </select>
    <div class="controls-group controls-ado" title="Mark which assertions to include when filing ADO bugs. Selection is stored in your browser per report slug; download the JSON when you're done and feed it to seval_regression_ado_sync.py --selection.">
      <span class="selection-counter" id="ado-counter"><span class="sel-on" id="ado-on">{{TOTAL_REG}}</span> / {{TOTAL_REG}} for ADO</span>
      <button class="btn-action" id="ado-select-visible" title="Mark all currently-visible rows as Include">Select visible</button>
      <button class="btn-action" id="ado-clear-visible" title="Mark all currently-visible rows as Exclude">Clear visible</button>
      <button class="btn-action btn-primary" id="ado-download" title="Download a selection JSON file. Pass it to seval_regression_ado_sync.py --selection.">Download ADO selection</button>
    </div>
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
  let activeTheme = null;   // null = all themes; otherwise a theme key.

  function applyFilter() {
    const q = (search.value || '').toLowerCase().trim();
    let anyVisible = false;
    queriesRoot.querySelectorAll('details.query').forEach(qNode => {
      let anyAssertionVisible = false;
      const queryText = qNode.querySelector('.query-text').textContent.toLowerCase();
      qNode.querySelectorAll('.assertion').forEach(aNode => {
        const cmp = aNode.getAttribute('data-comparison');
        const filterOk = (activeFilter === 'all' || cmp === activeFilter);
        const themeOk = (!activeTheme || aNode.getAttribute('data-theme') === activeTheme);
        const text = (queryText + ' ' + aNode.textContent.toLowerCase());
        const searchOk = !q || text.includes(q);
        const visible = filterOk && themeOk && searchOk;
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

  // Sort-by-level wiring. Rows stay grouped by query; the sort reorders the
  // assertion rows within each query group by level severity and reorders the
  // query groups themselves by their most-severe (critical) or least-severe
  // (aspirational) row. "default" restores the original manifest order, which
  // is stamped onto each node at load via data-orig.
  const LEVEL_RANK = { critical: 0, expected: 1, aspirational: 2 };
  function levelRank(node) {
    const l = (node.getAttribute('data-level') || '').toLowerCase();
    return (l in LEVEL_RANK) ? LEVEL_RANK[l] : 99;
  }
  Array.from(queriesRoot.querySelectorAll('details.query')).forEach((q, qi) => {
    q.dataset.orig = qi;
    Array.from(q.querySelectorAll('.assertion')).forEach((a, ai) => { a.dataset.orig = ai; });
  });
  const sortSelect = document.getElementById('sort-mode');
  function applySort(mode) {
    const queryNodes = Array.from(queriesRoot.querySelectorAll('details.query'));
    queryNodes.forEach(q => {
      const body = q.querySelector('.query-body');
      const rows = Array.from(q.querySelectorAll('.assertion'));
      rows.sort((a, b) => {
        if (mode === 'default') return (+a.dataset.orig) - (+b.dataset.orig);
        const d = (mode === 'aspirational') ? (levelRank(b) - levelRank(a)) : (levelRank(a) - levelRank(b));
        return d !== 0 ? d : (+a.dataset.orig) - (+b.dataset.orig);
      });
      rows.forEach(r => body.appendChild(r));
    });
    queryNodes.sort((a, b) => {
      if (mode === 'default') return (+a.dataset.orig) - (+b.dataset.orig);
      const ranks = n => Array.from(n.querySelectorAll('.assertion')).map(levelRank);
      const key = arr => (mode === 'aspirational') ? Math.max.apply(null, arr) : Math.min.apply(null, arr);
      const d = (mode === 'aspirational') ? (key(ranks(b)) - key(ranks(a))) : (key(ranks(a)) - key(ranks(b)));
      return d !== 0 ? d : (+a.dataset.orig) - (+b.dataset.orig);
    });
    queryNodes.forEach(q => queriesRoot.appendChild(q));
  }
  sortSelect && sortSelect.addEventListener('change', () => applySort(sortSelect.value));

  // Theme chart wiring. Clicking a theme bar filters the regressions list to
  // that theme; clicking the active theme again (or "Clear theme filter")
  // resets it. Example buttons expand the containing query group, scroll the
  // row into view, and briefly highlight it.
  const themeBars = Array.from(document.querySelectorAll('.theme-bar-btn'));
  const themeRows = Array.from(document.querySelectorAll('.theme-row'));
  function setActiveTheme(theme) {
    activeTheme = theme;
    themeRows.forEach(r => r.classList.toggle('theme-active', !!theme && r.getAttribute('data-theme') === theme));
    applyFilter();
  }
  themeBars.forEach(bar => bar.addEventListener('click', () => {
    const theme = bar.getAttribute('data-theme');
    setActiveTheme(activeTheme === theme ? null : theme);
    if (activeTheme) {
      const regSection = document.getElementById('search');
      if (regSection) regSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }));
  const themeClear = document.getElementById('theme-clear');
  themeClear && themeClear.addEventListener('click', () => setActiveTheme(null));

  document.querySelectorAll('.theme-example').forEach(btn => btn.addEventListener('click', () => {
    const targetId = btn.getAttribute('data-target');
    const row = targetId && document.getElementById(targetId);
    if (!row) return;
    const qNode = row.closest('details.query');
    if (qNode) qNode.open = true;
    // Make sure the row is not hidden by an active theme/filter.
    setActiveTheme(null);
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('row-flash');
    setTimeout(() => row.classList.remove('row-flash'), 1600);
  }));

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

  // ADO selection — per-row checkboxes, persisted to localStorage, exported
  // as JSON for `seval_regression_ado_sync.py --selection <file>`. Default
  // (first load, no saved state) is critical-level only: critical rows are
  // pre-checked, every other level is unchecked. Users can toggle any row;
  // their saved selection then takes precedence on later visits.
  (function adoSelection() {
    const slugMeta = document.querySelector('meta[name="seval:manifest-slug"]');
    const slug = slugMeta ? slugMeta.getAttribute('content') : 'unknown';
    const storageKey = 'sevalAdoSelection:' + slug;
    const boxes = Array.from(document.querySelectorAll('input.row-include'));
    const counterOn = document.getElementById('ado-on');
    const downloadBtn = document.getElementById('ado-download');
    const selectVisibleBtn = document.getElementById('ado-select-visible');
    const clearVisibleBtn = document.getElementById('ado-clear-visible');
    const themeBoxes = Array.from(document.querySelectorAll('input.theme-include-box'));
    if (!boxes.length || !counterOn || !downloadBtn) return;

    // "Sub-category by tool" toggle. When on, the body gets `tools-on` (which
    // reveals tool chips + per-row tool badges) and the exported selection asks
    // the sync to split each theme cluster by tool -> "[tool] theme" tickets.
    // When off, the sync files one ticket per theme (maximum grouping).
    const toolToggle = document.getElementById('tool-split-toggle');
    let splitByTool = true;
    const splitStorageKey = 'sevalSplitByTool:' + slug;
    try {
      const s = localStorage.getItem(splitStorageKey);
      if (s !== null) splitByTool = (s === '1');
    } catch (e) {}
    function applyToolMode() {
      document.body.classList.toggle('tools-on', splitByTool);
      if (toolToggle) toolToggle.checked = splitByTool;
    }
    applyToolMode();
    toolToggle && toolToggle.addEventListener('change', () => {
      splitByTool = !!toolToggle.checked;
      try { localStorage.setItem(splitStorageKey, splitByTool ? '1' : '0'); } catch (e) {}
      applyToolMode();
    });

    // Load saved state. Schema: { "<id>": true } means excluded; missing keys
    // default to included. We only persist excludes so re-rendering with new
    // rows defaults them to the level-based default below.
    let excludes = {};
    let hadSaved = false;
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) { excludes = JSON.parse(raw) || {}; hadSaved = true; }
    } catch (e) { excludes = {}; hadSaved = false; }
    // Back-compat: an earlier build stored `false` to mean excluded. Normalize
    // any falsy entries to `true` so the toggle logic below works.
    Object.keys(excludes).forEach(k => { excludes[k] = true; });
    // First load (no saved selection): default to critical-only. Seed every
    // non-critical row into the exclude set so only critical assertions are
    // pre-checked for ADO. Once the user edits, their saved state wins.
    if (!hadSaved) {
      boxes.forEach(b => {
        const lvl = (b.getAttribute('data-level') || '').toLowerCase();
        if (lvl !== 'critical') excludes[b.getAttribute('data-id')] = true;
      });
    }

    function applyToBox(box) {
      const id = box.getAttribute('data-id');
      const isOn = !excludes[id];
      box.checked = isOn;
      const label = box.closest('label.assertion-select');
      if (label) label.classList.toggle('is-on', isOn);
      const card = box.closest('.assertion');
      if (card) card.classList.toggle('is-excluded', !isOn);
    }
    function updateCounter() {
      const on = boxes.reduce((n, b) => n + (b.checked ? 1 : 0), 0);
      counterOn.textContent = String(on);
    }
    function persist() {
      try { localStorage.setItem(storageKey, JSON.stringify(excludes)); } catch (e) {}
    }

    boxes.forEach(box => {
      applyToBox(box);
      box.addEventListener('change', () => {
        const id = box.getAttribute('data-id');
        if (box.checked) delete excludes[id]; else excludes[id] = true;
        applyToBox(box);
        updateCounter();
        persist();
        refreshThemeBoxes();
      });
    });
    updateCounter();

    // Theme-level ADO checkboxes — bulk include/exclude every row of a theme,
    // and reflect aggregate state (checked = all rows in, indeterminate = some).
    // This is the "file ADO by topic" path; per-row boxes remain the "by query"
    // path. Both edit the same underlying selection.
    function rowsForTheme(theme) {
      return boxes.filter(b => (b.getAttribute('data-theme') || '') === theme);
    }
    function refreshThemeBoxes() {
      themeBoxes.forEach(tb => {
        const theme = tb.getAttribute('data-theme');
        const rs = rowsForTheme(theme);
        const on = rs.reduce((n, b) => n + (b.checked ? 1 : 0), 0);
        tb.checked = rs.length > 0 && on === rs.length;
        tb.indeterminate = on > 0 && on < rs.length;
        const lbl = tb.closest('.theme-ado');
        if (lbl) lbl.classList.toggle('is-on', on > 0);
        const cnt = lbl && lbl.querySelector('.theme-ado-count');
        if (cnt) cnt.textContent = on + '/' + rs.length;
      });
    }
    themeBoxes.forEach(tb => {
      tb.addEventListener('change', () => {
        const theme = tb.getAttribute('data-theme');
        const wantOn = tb.checked;   // checkbox value after the click
        rowsForTheme(theme).forEach(b => {
          const id = b.getAttribute('data-id');
          if (wantOn) delete excludes[id]; else excludes[id] = true;
          applyToBox(b);
        });
        updateCounter();
        persist();
        refreshThemeBoxes();
      });
    });
    refreshThemeBoxes();

    function visibleBoxes() {
      return boxes.filter(b => {
        const card = b.closest('.assertion');
        if (!card || card.style.display === 'none') return false;
        const qNode = card.closest('details.query');
        if (qNode && qNode.style.display === 'none') return false;
        return true;
      });
    }
    selectVisibleBtn && selectVisibleBtn.addEventListener('click', () => {
      visibleBoxes().forEach(b => {
        const id = b.getAttribute('data-id');
        delete excludes[id];
        applyToBox(b);
      });
      updateCounter(); persist(); refreshThemeBoxes();
    });
    clearVisibleBtn && clearVisibleBtn.addEventListener('click', () => {
      visibleBoxes().forEach(b => {
        const id = b.getAttribute('data-id');
        excludes[id] = true;
        applyToBox(b);
      });
      updateCounter(); persist(); refreshThemeBoxes();
    });

    downloadBtn.addEventListener('click', () => {
      const payload = {
        manifest_slug: slug,
        exported_at: new Date().toISOString(),
        format: 'seval-ado-selection/v1',
        notes: 'Pass to seval_regression_ado_sync.py via --selection <file>. Rows with include=false are dropped. With --cluster-by theme the sync groups included rows by theme; options.split_by_tool controls whether each theme is further split by tool -> "[tool] theme" tickets.',
        options: {
          cluster_by: 'theme',
          split_by_tool: splitByTool
        },
        selections: boxes.map(b => ({
          id: b.getAttribute('data-id'),
          theme: b.getAttribute('data-theme') || null,
          tool: b.getAttribute('data-tool') || null,
          include: b.checked
        }))
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = slug + '_ado_selection.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    });
  })();
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


def render_theme_section(manifest: Dict[str, Any]) -> str:
    """Render the clickable theme chart shown before the Regressions list.

    Each theme is a horizontal bar (count-scaled) that filters the list when
    clicked, plus a theme-level "ADO" checkbox that bulk-toggles every row of
    that theme in the selection, and an example button that jumps to a
    representative regression row.
    """
    themes = manifest.get("themes") or []
    regs = manifest.get("regressions", [])
    if not themes:
        return '<div class="no-results">No theme classification available.</div>'

    id_to_query = {r["id"]: r.get("query", "") for r in regs}
    max_count = max((int(t.get("count", 0)) for t in themes), default=0) or 1

    # Tool sub-category breakdown per theme (for owner routing). Each theme bar
    # shows small "<tool>·<n>" chips that reveal which surfaces drive it.
    tool_labels = {str(t.get("key", "")): str(t.get("label", t.get("key", "")))
                   for t in (manifest.get("tools") or [])}
    theme_tool_counts: Dict[str, Dict[str, Dict[str, int]]] = {}
    for r in regs:
        tk = (r.get("theme") or "").strip()
        tl = (r.get("tool") or "").strip()
        if not tk or not tl:
            continue
        bucket = theme_tool_counts.setdefault(tk, {})
        cell = bucket.setdefault(tl, {"count": 0, "crit": 0})
        cell["count"] += 1
        if (r.get("level", "") or "").lower() == "critical":
            cell["crit"] += 1

    rows_html: List[str] = []
    for t in themes:
        key = str(t.get("key", ""))
        label = str(t.get("label", key))
        desc = str(t.get("description", ""))
        count = int(t.get("count", 0))
        crit = int(t.get("critical_count", 0))
        pct = max(2, round(100 * count / max_count)) if count else 0

        crit_html = f'<span class="theme-crit"> · {crit} critical</span>' if crit else ""

        # Tool chips (hidden unless the "Sub-category by tool" toggle is on).
        chips = []
        for tl, cell in sorted(theme_tool_counts.get(key, {}).items(),
                               key=lambda kv: (-kv[1]["count"], kv[0])):
            crit_chip = (f'<span class="tool-chip-crit"> ({cell["crit"]} crit)</span>'
                         if cell["crit"] else "")
            chips.append(
                f'<span class="tool-chip" title="{html.escape(tool_labels.get(tl, tl))}">'
                f'{html.escape(tl)}·{cell["count"]}{crit_chip}</span>'
            )
        chips_html = (f'<span class="tool-chips">{"".join(chips)}</span>'
                      if chips else "")

        example_html = ""
        ex_ids = t.get("example_ids") or []
        if ex_ids:
            ex_id = str(ex_ids[0])
            ex_q = id_to_query.get(ex_id, "")
            ex_label = (ex_q[:58] + "…") if len(ex_q) > 58 else (ex_q or "example")
            example_html = (
                f'<button type="button" class="theme-example" data-target="row-{html.escape(ex_id)}" '
                f'title="Jump to an example: {html.escape(ex_q)}">e.g. {html.escape(ex_label)}</button>'
            )

        rows_html.append(
            f'<div class="theme-row" data-theme="{html.escape(key)}">'
            f'  <label class="theme-ado" title="Include every &quot;{html.escape(label)}&quot; regression when filing ADO bugs.">'
            f'    <input type="checkbox" class="theme-include-box" data-theme="{html.escape(key)}">'
            f'    <span class="theme-ado-text">ADO</span> <span class="theme-ado-count"></span>'
            f'  </label>'
            f'  <button type="button" class="theme-bar-btn" data-theme="{html.escape(key)}" '
            f'title="Filter the regressions list to this theme">'
            f'    <span class="theme-meta">'
            f'      <span class="theme-label">{html.escape(label)}</span>'
            f'      <span class="theme-count">{count}{crit_html}</span>'
            f'    </span>'
            f'    <span class="theme-track"><span class="theme-fill" style="width:{pct}%"></span></span>'
            f'    <span class="theme-desc">{html.escape(desc)}</span>'
            f'    {chips_html}'
            f'  </button>'
            f'  {example_html}'
            f'</div>'
        )

    controls = (
        '<div class="theme-controls">'
        '<span class="theme-hint">Click a theme to filter the regressions below. '
        'Use a theme\u2019s ADO box to bulk-include the whole theme for ticketing '
        '(file ADO bugs by query or by theme).</span>'
        '<label class="tool-toggle" title="When on, each ADO ticket is split by the '
        'failing tool/surface (e.g. &quot;[calendar] Source grounding missing&quot;) so '
        'tickets route to the right owner. When off, you get one ticket per theme '
        '(maximum grouping).">'
        '<input type="checkbox" id="tool-split-toggle" checked> '
        'Sub-category by tool (one ticket per tool within a theme)</label>'
        '<button class="btn-action" id="theme-clear">Clear theme filter</button>'
        '</div>'
    )
    return controls + '<div class="theme-chart">' + "\n".join(rows_html) + '</div>'


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
    level_norm = (level or "").strip().lower()
    theme = (r.get("theme") or "").strip()
    tool = (r.get("tool") or "").strip()
    is_critical = level_norm == "critical"
    checked_attr = " checked" if is_critical else ""
    label_on_cls = " is-on" if is_critical else ""
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

    tool_badge_html = (
        f'<span class="tool-badge" title="Owner surface / tool sub-category">{html.escape(tool)}</span>'
        if tool else ""
    )

    return (
        f'<div class="assertion" id="row-{html.escape(r["id"])}" data-comparison="{html.escape(cmp)}" data-level="{html.escape(level_norm)}" data-theme="{html.escape(theme)}" data-tool="{html.escape(tool)}" data-id="{html.escape(r["id"])}">'
        f'  <div class="assertion-header">'
        f'    <div class="assertion-text">{html.escape(r["assertion"])}{meta_html}</div>'
        f'    {tool_badge_html}'
        f'    <span class="badge {cmp_badge_cls}">{html.escape(cmp_label)}</span>'
        f'    <label class="assertion-select{label_on_cls}" title="Include this assertion when filing ADO bugs (via seval_regression_ado_sync.py --selection). Only critical-level rows are checked by default.">'
        f'      <input type="checkbox" class="row-include" data-id="{html.escape(r["id"])}" data-level="{html.escape(level_norm)}" data-theme="{html.escape(theme)}" data-tool="{html.escape(tool)}"{checked_attr}> Include in ADO'
        f'    </label>'
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

    themes = manifest.get("themes") or []
    theme_chart_html = render_theme_section(manifest)
    n_themes = len(themes)
    theme_subtitle = (
        f"{total_reg} regressions grouped into {n_themes} recurring failure themes "
        f"(each links to at least one example). Counts and critical counts are shown per theme; "
        f"click a theme to filter the list, or use its ADO box to ticket the whole topic."
        if n_themes else "No theme classification available for this manifest."
    )

    manifest_slug = manifest_path.stem
    if manifest_slug.endswith("_manifest"):
        manifest_slug = manifest_slug[: -len("_manifest")]

    substitutions = {
        "{{TITLE}}": html.escape(title),
        "{{META_DESC}}": html.escape(subtitle),
        "{{H1_TITLE}}": html.escape(title),
        "{{SUBTITLE}}": html.escape(subtitle),
        "{{KPI_CARDS}}": render_kpi_cards(manifest),
        "{{FLAG_DESCRIPTION}}": html.escape(flag_desc),
        "{{FLAG_DIFFS_HTML}}": render_flag_diffs_section(fd, ctrl["id"], exp["id"]),
        "{{THEME_SUBTITLE}}": html.escape(theme_subtitle),
        "{{THEME_CHART_HTML}}": theme_chart_html,
        "{{CONTROL_NAME}}": html.escape(ctrl["name"]),
        "{{EXPERIMENT_NAME}}": html.escape(exp["name"]),
        "{{REGRESSION_DENOMINATOR}}": html.escape(regression_denominator),
        "{{TOTAL_REG}}": str(total_reg),
        "{{CC_COUNT}}": str(cc_count),
        "{{EE_COUNT}}": str(ee_count),
        "{{QUERIES_HTML}}": queries_html,
        "{{GENERATED_AT}}": html.escape(_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")),
        "{{MANIFEST_PATH}}": html.escape(str(manifest_path)),
        "{{MANIFEST_SLUG}}": html.escape(manifest_slug),
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

    out_path = resolve_output(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")

    n_reg = len(manifest.get("regressions", []))
    blanks = sum(1 for r in manifest.get("regressions", []) if not (r.get("why_failed") or "").strip())
    print(f"[render] Wrote {out_path} ({out_path.stat().st_size // 1024} KB)")
    print(f"[render] {n_reg} regressions rendered; {blanks} still have placeholder why_failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

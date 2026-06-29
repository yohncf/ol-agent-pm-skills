#!/usr/bin/env python3
"""Render a self-contained dark-themed HTML report from a single SEVAL run
report JSON (the new unified format that bundles summary + per-query results
into one file, e.g. eval_report_outlook_seval_*.json).

Unlike seval-run-triage (which joins 4 separate artifacts), this reads the new
all-in-one report straight from one file. No external dependencies.

Usage:
  python eval_single_run_report.py --in <report.json> [--out <report.html>]
                                   [--run-name "..."] [--run-date YYYY-MM-DD]
"""
import argparse
import datetime as _dt
import html
import json
import os
import re
import sys


# ---------- helpers ----------

def esc(v):
    return html.escape("" if v is None else str(v))


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


def fmt_pct(x):
    return f"{x:.1f}%"


def get(d, *path, default=None):
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def load_rows(report):
    """The per-query results live under results[i][<evaluator>] which is itself
    a list. Flatten across evaluators/result-blocks into one row list."""
    results = report.get("results")
    evaluators = report.get("evaluators") or []
    rows = []

    def _take(v):
        if isinstance(v, list):
            rows.extend([r for r in v if isinstance(r, dict)])
        elif isinstance(v, dict):
            rows.append(v)

    if isinstance(results, dict):
        # New format: results = { "<evaluator>": [ row, ... ] }
        keys = evaluators if evaluators else list(results.keys())
        for k in keys:
            _take(results.get(k))
    elif isinstance(results, list):
        # Alt format: results = [ { "<evaluator>": [...] }, ... ]
        for block in results:
            if isinstance(block, dict):
                keys = evaluators if evaluators else list(block.keys())
                for k in keys:
                    _take(block.get(k))
            elif isinstance(block, list):
                _take(block)
    return rows


# ---------- aggregation ----------

DIMENSION_KEYS = [
    "tool_call_made", "code_generated", "execution_success",
    "has_return_value", "answer_generated", "answer_correct",
]
DIMENSION_LABEL = {
    "tool_call_made": "Tool call made",
    "code_generated": "Code generated",
    "execution_success": "Execution success",
    "has_return_value": "Has return value",
    "answer_generated": "Answer generated",
    "answer_correct": "Answer correct",
}


def query_passed(row):
    mp = get(row, "metrics", "query_passed")
    if mp is not None:
        return bool(mp)
    jr = row.get("judge_result") or {}
    return jr.get("assertions_passed") == jr.get("assertions_total")


def aggregate(rows, summary):
    total_q = len(rows)
    passed_q = sum(1 for r in rows if query_passed(r))
    a_pass = a_total = 0
    dim_counts = {k: 0 for k in DIMENSION_KEYS}
    dim_present = {k: 0 for k in DIMENSION_KEYS}
    cost = 0.0
    lat = []
    toks = 0
    for r in rows:
        jr = r.get("judge_result") or {}
        a_pass += jr.get("assertions_passed") or 0
        a_total += jr.get("assertions_total") or 0
        dims = r.get("dimensions") or {}
        for k in DIMENSION_KEYS:
            if k in dims:
                dim_present[k] += 1
                if dims[k]:
                    dim_counts[k] += 1
        pm = r.get("performance_metrics") or {}
        cost += pm.get("cost_usd") or 0.0
        if pm.get("overall_latency_ms") is not None:
            lat.append(pm["overall_latency_ms"])
        toks += pm.get("total_tokens") or 0
    # prefer authoritative summary block when present
    s = summary or {}
    s0 = next(iter(s.values())) if isinstance(s, dict) and s else {}
    return {
        "total_q": total_q,
        "passed_q": passed_q,
        "a_pass": s0.get("assertions_passed", a_pass),
        "a_total": s0.get("total_assertions", a_total),
        "q_rate": s0.get("query_pass_rate_pct", pct(passed_q, total_q)),
        "a_rate": s0.get("assertion_score_pct", pct(a_pass, a_total)),
        "dim_counts": dim_counts,
        "dim_present": dim_present,
        "cost": cost,
        "avg_lat": (sum(lat) / len(lat)) if lat else 0,
        "max_lat": max(lat) if lat else 0,
        "toks": toks,
        "level_breakdown": s0.get("level_breakdown", {}),
    }


# ---------- HTML rendering ----------

CSS = """
:root{
  --bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--border:#30363d;
  --fg:#e6edf3;--muted:#8b949e;--accent:#58a6ff;
  --pass:#3fb950;--passbg:#13261a;--fail:#f85149;--failbg:#2a1416;
  --warn:#d29922;--warnbg:#2a2113;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1200px;margin:0 auto;padding:28px 22px 80px;}
h1{font-size:24px;margin:0 0 4px;}
h2{font-size:17px;margin:34px 0 12px;border-bottom:1px solid var(--border);padding-bottom:6px;}
.sub{color:var(--muted);font-size:13px;margin-bottom:6px;}
.meta{color:var(--muted);font-size:12px;}
.meta code{background:var(--panel2);padding:1px 6px;border-radius:5px;color:var(--accent);}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:18px;}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}
.card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
.card .v{font-size:26px;font-weight:650;margin-top:4px;}
.card .d{color:var(--muted);font-size:12px;margin-top:2px;}
table{width:100%;border-collapse:collapse;font-size:13px;}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top;}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;}
tr:hover td{background:var(--panel);}
.bar{height:8px;border-radius:5px;background:var(--panel2);overflow:hidden;min-width:90px;}
.bar>span{display:block;height:100%;background:var(--pass);}
.bar.low>span{background:var(--fail);}
.bar.mid>span{background:var(--warn);}
.badge{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600;}
.b-pass{background:var(--passbg);color:var(--pass);border:1px solid #1f6f33;}
.b-fail{background:var(--failbg);color:var(--fail);border:1px solid #8c2a26;}
.b-lvl{background:var(--panel2);color:var(--muted);border:1px solid var(--border);}
details{background:var(--panel);border:1px solid var(--border);border-radius:10px;margin:10px 0;}
details[open]{border-color:#3d4654;}
summary{cursor:pointer;padding:12px 16px;list-style:none;}
summary::-webkit-details-marker{display:none}
summary:hover{background:var(--panel2);}
summary.fail{border-left:3px solid var(--fail);border-radius:10px 10px 0 0;}
summary.pass{border-left:3px solid var(--pass);border-radius:10px 10px 0 0;}
.qrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
.qhead{display:flex;align-items:center;gap:12px;padding:6px 16px 6px 19px;color:var(--muted);
  font-size:12px;text-transform:uppercase;letter-spacing:.03em;font-weight:600;}
.qhead .h-id{margin-left:52px;}
.h-id{flex:0 0 150px;}
.h-prompt{flex:1;min-width:200px;}
.h-assert{flex:0 0 80px;text-align:right;}
.h-score{flex:0 0 56px;text-align:right;color:var(--fg);}
.h-bar{flex:0 0 100px;}
details.sub{margin:8px 0;}
details.sub>summary.sublbl{padding:8px 12px;color:var(--muted);font-size:12px;font-weight:600;
  text-transform:uppercase;letter-spacing:.03em;border-radius:8px;background:var(--panel2);
  border:1px solid var(--border);}
details.sub[open]>summary.sublbl{border-radius:8px 8px 0 0;}
details.sub>pre{margin-top:0;border-radius:0 0 8px 8px;border-top:none;}
.qid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);font-size:12px;}
.qprompt{flex:1;min-width:240px;}
.body{padding:6px 18px 18px;border-top:1px solid var(--border);}
.assert{border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--panel2);}
.assert.fail{border-color:#8c2a26;}
.assert .crit{font-weight:600;margin-bottom:4px;}
.assert .rat{color:var(--muted);font-size:12.5px;white-space:pre-wrap;}
.dimrow{display:flex;flex-wrap:wrap;gap:6px;margin:9px 0 1px;}
.dim{font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--border);}
.dim.y{color:var(--pass);border-color:#1f6f33;}
.dim.n{color:var(--fail);border-color:#8c2a26;}
pre{background:#0a0e14;border:1px solid var(--border);border-radius:8px;padding:12px;
  overflow:auto;font-size:12px;color:#c9d1d9;max-height:340px;}
.kv{display:flex;flex-wrap:wrap;gap:14px;color:var(--muted);font-size:12px;margin-top:8px;}
.kv b{color:var(--fg);font-weight:600;}
.resp{white-space:pre-wrap;background:var(--panel2);border:1px solid var(--border);
  border-radius:8px;padding:12px;font-size:13px;}
.lbl{color:var(--muted);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;margin:14px 0 4px;}
.flt{margin:10px 0 0;color:var(--muted);font-size:12px;}
.flt button{background:var(--panel2);color:var(--fg);border:1px solid var(--border);
  border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px;margin-right:6px;}
.flt button.on{background:var(--accent);color:#04101f;border-color:var(--accent);}
"""

JS = """
function flt(mode,btn){
  document.querySelectorAll('.flt button').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('details.q').forEach(d=>{
    const f=d.dataset.fail==='1';
    d.style.display=(mode==='all'||(mode==='fail'&&f)||(mode==='pass'&&!f))?'':'none';
  });
}
"""


def bar(p):
    cls = "" if p >= 80 else ("mid" if p >= 50 else "low")
    return f'<div class="bar {cls}"><span style="width:{max(0,min(100,p)):.0f}%"></span></div>'


def card(k, v, d=""):
    return f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{v}</div><div class="d">{esc(d)}</div></div>'


def render(report, run_name, run_date, src_name):
    summary = report.get("summary") or {}
    rows = load_rows(report)
    agg = aggregate(rows, summary)

    model = report.get("evaluation_models") or get(report, "evaluation_models")
    dataset = report.get("dataset") or ""
    judge_model = (rows[0].get("evaluation_model") if rows else "") or ""
    agent_model = (rows[0].get("main_agent_model") if rows else "") or ""

    out = []
    out.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    out.append(f"<title>{esc(run_name)} — SEVAL Run Report</title>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append(f"<style>{CSS}</style></head><body><div class='wrap'>")

    out.append(f"<h1>{esc(run_name)}</h1>")
    out.append(f"<div class='sub'>{esc(report.get('evaluation_name',''))} &middot; single-run evaluation report</div>")
    out.append("<div class='meta'>")
    out.append(f"Date <code>{esc(run_date)}</code> &nbsp; Dataset <code>{esc(dataset)}</code><br>")
    out.append(f"Agent model <code>{esc(agent_model)}</code> &nbsp; Judge model <code>{esc(judge_model or model)}</code> &nbsp; Source <code>{esc(src_name)}</code>")
    out.append("</div>")

    # KPI cards
    qr = agg["q_rate"]
    ar = agg["a_rate"]
    out.append("<div class='cards'>")
    out.append(card("Query pass rate", fmt_pct(qr), f"{agg['passed_q']} / {agg['total_q']} queries"))
    out.append(card("Assertion score", fmt_pct(ar), f"{agg['a_pass']} / {agg['a_total']} assertions"))
    out.append(card("Queries failed", str(agg["total_q"] - agg["passed_q"]), "below full pass"))
    out.append(card("Total cost", f"${agg['cost']:.2f}", f"{agg['toks']:,} tokens"))
    out.append(card("Avg latency", f"{agg['avg_lat']/1000:.1f}s", f"max {agg['max_lat']/1000:.1f}s"))
    out.append("</div>")

    # Level breakdown
    lb = agg["level_breakdown"]
    if lb:
        out.append("<h2>Assertion score by level</h2><table><thead><tr>"
                   "<th>Level</th><th>Passed</th><th>Total</th><th>Score</th><th></th></tr></thead><tbody>")
        order = ["critical", "expected", "aspirational"]
        for lvl in sorted(lb.keys(), key=lambda x: order.index(x) if x in order else 9):
            d = lb[lvl]
            p = d.get("assertion_score_pct", pct(d.get("assertions_passed", 0), d.get("assertions_total", 0)))
            out.append(f"<tr><td><span class='badge b-lvl'>{esc(lvl)}</span></td>"
                       f"<td>{d.get('assertions_passed',0)}</td><td>{d.get('assertions_total',0)}</td>"
                       f"<td>{fmt_pct(p)}</td><td>{bar(p)}</td></tr>")
        out.append("</tbody></table>")

    # Dimension breakdown
    dc, dp = agg["dim_counts"], agg["dim_present"]
    if any(dp.values()):
        out.append("<h2>Execution dimensions (across queries)</h2><table><thead><tr>"
                   "<th>Dimension</th><th>True</th><th>Of</th><th>Rate</th><th></th></tr></thead><tbody>")
        for k in DIMENSION_KEYS:
            if dp[k]:
                p = pct(dc[k], dp[k])
                out.append(f"<tr><td>{esc(DIMENSION_LABEL[k])}</td><td>{dc[k]}</td><td>{dp[k]}</td>"
                           f"<td>{fmt_pct(p)}</td><td>{bar(p)}</td></tr>")
        out.append("</tbody></table>")

    # Per-query results (merged table + collapsible detail, sorted by score asc)
    def qscore(r):
        jr = r.get("judge_result") or {}
        return pct(jr.get("assertions_passed", 0), jr.get("assertions_total", 1) or 1)

    out.append("<h2>Per-query results</h2>")
    out.append("<div class='flt'>"
               "<button class='on' onclick=\"flt('all',this)\">All</button>"
               "<button onclick=\"flt('fail',this)\">Failing only</button>"
               "<button onclick=\"flt('pass',this)\">Passing only</button></div>")
    out.append("<div class='qhead'><span class='h-id'>ID</span>"
               "<span class='h-prompt'>Prompt</span>"
               "<span class='h-assert'>Assertions</span>"
               "<span class='h-score'>Score</span><span class='h-bar'></span></div>")
    rows_sorted = sorted(rows, key=lambda r: (qscore(r), r.get("test_id", "")))
    for r in rows_sorted:
        passed = query_passed(r)
        jr = r.get("judge_result") or {}
        ap, at = jr.get("assertions_passed", 0), jr.get("assertions_total", 0)
        p = pct(ap, at)
        tid = r.get("test_id", "")
        prompt = r.get("user_prompt") or ""
        badge = "<span class='badge b-pass'>PASS</span>" if passed else "<span class='badge b-fail'>FAIL</span>"
        # dimension pills shown in the summary so failures are visible without expanding
        dims = r.get("dimensions") or {}
        pills = []
        for k in DIMENSION_KEYS:
            if k in dims:
                yn = "y" if dims[k] else "n"
                mark = "&#10003;" if dims[k] else "&#10007;"
                pills.append(f"<span class='dim {yn}'>{mark} {esc(DIMENSION_LABEL[k])}</span>")
        pills_html = f"<div class='dimrow'>{''.join(pills)}</div>" if pills else ""
        out.append(f"<details class='q' data-fail='{0 if passed else 1}'>")
        out.append(f"<summary class='{'pass' if passed else 'fail'}'>"
                   f"<div class='qrow'>{badge}"
                   f"<span class='qid h-id'>{esc(tid)}</span>"
                   f"<span class='qprompt h-prompt'>{esc(prompt)}</span>"
                   f"<span class='h-assert'>{ap}/{at}</span>"
                   f"<span class='h-score'>{fmt_pct(p)}</span>"
                   f"<span class='h-bar'>{bar(p)}</span></div>"
                   f"{pills_html}</summary>")
        out.append("<div class='body'>")

        # response
        resp = r.get("agent_response_preview")
        if resp:
            out.append("<div class='lbl'>Agent response</div>")
            out.append(f"<div class='resp'>{esc(resp)}</div>")

        # assertions
        out.append("<div class='lbl'>Assertions</div>")
        for a in r.get("assertion_results") or []:
            j = a.get("judge_result") or {}
            sc = j.get("score")
            apass = (sc == 1 or sc is True)
            b = "<span class='badge b-pass'>1</span>" if apass else "<span class='badge b-fail'>0</span>"
            lvl = a.get("level", "")
            out.append(f"<div class='assert {'pass' if apass else 'fail'}'>")
            out.append(f"<div class='crit'>{b} <span class='badge b-lvl'>{esc(lvl)}</span> {esc(j.get('criteria',''))}</div>")
            out.append(f"<div class='rat'>{esc(j.get('rationale',''))}</div></div>")

        # execution trace
        et = r.get("execution_trace") or {}
        code = et.get("code_generated")
        exres = et.get("execution_result")
        if code:
            out.append("<details class='sub'><summary class='sublbl'>Generated code</summary>"
                       f"<pre>{esc(code)}</pre></details>")
        if exres:
            out.append("<details class='sub'><summary class='sublbl'>Execution result</summary>"
                       f"<pre>{esc(exres)}</pre></details>")

        # perf
        pm = r.get("performance_metrics") or {}
        kv = []
        if pm.get("total_tokens") is not None:
            kv.append(f"Tokens <b>{pm['total_tokens']:,}</b>")
        if pm.get("cost_usd") is not None:
            kv.append(f"Cost <b>${pm['cost_usd']:.4f}</b>")
        if pm.get("overall_latency_ms") is not None:
            kv.append(f"Latency <b>{pm['overall_latency_ms']/1000:.1f}s</b>")
        if pm.get("tool_call_count") is not None:
            kv.append(f"Tool calls <b>{pm['tool_call_count']}</b>")
        if pm.get("llm_turns") is not None:
            kv.append(f"LLM turns <b>{pm['llm_turns']}</b>")
        if get(r, "execution_trace", "iteration_count") is not None:
            kv.append(f"Iterations <b>{et.get('iteration_count')}</b>")
        if kv:
            out.append("<div class='kv'>" + " &nbsp;|&nbsp; ".join(kv) + "</div>")

        out.append("</div></details>")

    gen = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append(f"<div class='meta' style='margin-top:40px'>Generated {gen} &middot; "
               f"{agg['total_q']} queries &middot; {agg['a_total']} assertions</div>")
    out.append(f"<script>{JS}</script>")
    out.append("</div></body></html>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", default=None)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--run-date", default=None)
    args = ap.parse_args()

    with open(args.inp, "r", encoding="utf-8") as f:
        report = json.load(f)

    src_name = os.path.basename(args.inp)
    # derive date from filename: ..._DD_MM_YYYY_HHMM.json
    run_date = args.run_date
    if not run_date:
        m = re.search(r"(\d{2})_(\d{2})_(\d{4})", src_name)
        if m:
            run_date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        else:
            run_date = _dt.date.today().isoformat()

    run_name = args.run_name or (report.get("evaluation_name") or "SEVAL Run")
    if args.run_name is None:
        run_name = f"{report.get('evaluation_name','SEVAL Run')} — {run_date}"

    out_path = args.out
    if not out_path:
        base = os.path.splitext(src_name)[0]
        out_dir = os.path.join("data", "eval-reports")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, base + ".html")

    htmltxt = render(report, run_name, run_date, src_name)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(htmltxt)
    print(f"Wrote {out_path} ({len(htmltxt):,} bytes)")
    print(f"Source: {src_name}")


if __name__ == "__main__":
    sys.exit(main())

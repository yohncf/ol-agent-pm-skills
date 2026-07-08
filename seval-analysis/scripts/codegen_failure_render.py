"""
codegen_failure_render.py — Interactive dark-themed HTML for CodeGen failures.

Consumes the manifest produced by `codegen_failure_extract.py` and renders a
single self-contained HTML file. The report is built for two jobs:

  1. Understand the CodeGen failure profile fast — KPI strip, clickable trend
     and tool rollups, family + cross-arm breakdown.
  2. Hand-pick the failures to ticket — every failure card has an "Include"
     checkbox; filter pills (trend / tool / family / cross-arm / level) and a
     search box narrow the set; the "Download selected JSON" button exports a
     `codegen-ado-selection/v1` file carrying the full query + assertion +
     answer for each selected row, ready for ADO item creation later.

Dark theme (M3 dark palette, Google Sans) matching seval-regression-analyze.

Usage:
  python scripts/codegen_failure_render.py \
    --manifest data/eval-manifests/576292_2026-06-24_codegen.json \
    --out      data/eval-reports/576292_2026-06-24_codegen.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream and getattr(_stream, "encoding", "").lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass


CSS = """
:root{
  --bg:#101418;--bg-dim:#0a0d10;--surface:#161a1f;--surface-1:#1a1e23;
  --surface-2:#22272d;--surface-3:#2a2f36;--text:#e3e2e6;--text-muted:#c4c6cf;
  --text-faint:#8a8d94;--outline:#43474e;--primary:#a8c7fa;
  --primary-container:rgba(168,199,250,.12);--tertiary:#a4ccaf;
  --tertiary-container:rgba(164,204,175,.14);--error:#ffb4ab;
  --error-container:rgba(255,180,171,.14);--warn:#ffd479;
  --warn-container:rgba(255,212,121,.12);--expr:#a371f7;
  --expr-container:rgba(163,113,247,.15);
  --font-display:'Google Sans Display','Google Sans',system-ui,sans-serif;
  --font-body:'Google Sans Text','Google Sans',system-ui,sans-serif;
  --font-mono:'Roboto Mono',ui-monospace,monospace;
}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font-body);
  line-height:1.5;font-size:14px}
a{color:var(--primary)}
.wrap{max-width:1200px;margin:0 auto;padding:28px 22px 80px}
h1{font-family:var(--font-display);font-size:26px;margin:0 0 4px}
h2{font-family:var(--font-display);font-size:18px;margin:34px 0 12px;
  border-bottom:1px solid var(--outline);padding-bottom:6px}
.sub{color:var(--text-faint);font-size:13px;margin-bottom:18px}
.sub code{font-family:var(--font-mono);color:var(--text-muted)}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0}
.kpi{background:var(--surface-1);border:1px solid var(--outline);
  border-radius:12px;padding:14px 18px;min-width:140px}
.kpi .v{font-family:var(--font-display);font-size:24px;font-weight:600}
.kpi .l{color:var(--text-faint);font-size:12px;margin-top:2px}
.kpi.bad .v{color:var(--error)}.kpi.good .v{color:var(--tertiary)}
.kpi.warn .v{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--outline)}
th{color:var(--text-faint);font-weight:500;font-size:12px;text-transform:uppercase;
  letter-spacing:.04em}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--surface-1)}
tr.active{background:var(--primary-container)}
.bar{height:8px;background:var(--surface-3);border-radius:4px;overflow:hidden;
  min-width:120px;display:inline-block;vertical-align:middle}
.bar > i{display:block;height:100%;background:var(--primary)}
.fam-Model > i{background:var(--expr)}.fam-Missing > i{background:var(--warn)}
.fam-Assertion > i{background:var(--tertiary)}.fam-Unclear > i{background:var(--text-faint)}
.controls{position:sticky;top:0;z-index:5;background:var(--bg-dim);
  border:1px solid var(--outline);border-radius:12px;padding:14px;margin:16px 0 22px;
  display:flex;flex-direction:column;gap:10px}
.controls .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.pill{border:1px solid var(--outline);background:var(--surface-2);color:var(--text-muted);
  border-radius:999px;padding:4px 11px;font-size:12px;cursor:pointer;user-select:none}
.pill.on{background:var(--primary-container);border-color:var(--primary);color:var(--primary)}
.pill .n{opacity:.6;margin-left:4px}
.grp-label{color:var(--text-faint);font-size:11px;text-transform:uppercase;
  letter-spacing:.05em;margin-right:4px;min-width:74px}
input[type=search]{flex:1;min-width:200px;background:var(--surface-2);
  border:1px solid var(--outline);color:var(--text);border-radius:8px;
  padding:7px 12px;font-family:var(--font-body);font-size:13px}
.btn{border:1px solid var(--outline);background:var(--surface-2);color:var(--text);
  border-radius:8px;padding:7px 13px;font-size:13px;cursor:pointer;font-family:var(--font-body)}
.btn:hover{background:var(--surface-3)}
.btn.primary{background:var(--primary);color:#0a0d10;border-color:var(--primary);font-weight:600}
.counter{font-size:13px;color:var(--text-muted)}
.counter b{color:var(--tertiary)}
.card{background:var(--surface-1);border:1px solid var(--outline);border-radius:12px;
  padding:14px 16px;margin-bottom:10px}
.card.off{opacity:.5}
.card .top{display:flex;gap:10px;align-items:flex-start}
.card input[type=checkbox]{margin-top:3px;width:16px;height:16px;accent-color:var(--primary);cursor:pointer}
.card .q{font-weight:600;font-size:14px}
.card .ev{color:var(--text-muted);font-size:13px;margin:6px 0 8px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.badge{font-size:11px;border-radius:6px;padding:2px 8px;border:1px solid var(--outline);
  background:var(--surface-2);color:var(--text-muted);font-family:var(--font-mono)}
.badge.trend{border-color:var(--primary);color:var(--primary)}
.badge.tool{border-color:var(--expr);color:var(--expr)}
.badge.crit{border-color:var(--error);color:var(--error)}
.badge.regr{border-color:var(--warn);color:var(--warn)}
.badge.both{border-color:var(--text-faint);color:var(--text-faint)}
details{margin-top:8px}summary{cursor:pointer;color:var(--text-faint);font-size:12px}
.det{background:var(--surface);border:1px solid var(--outline);border-radius:8px;
  padding:10px 12px;margin-top:8px;font-size:13px;white-space:pre-wrap}
.det h4{margin:0 0 4px;font-size:11px;text-transform:uppercase;color:var(--text-faint);
  letter-spacing:.05em}.det .blk{margin-bottom:10px}
.hide{display:none!important}
.note{color:var(--text-faint);font-size:12px;margin-top:6px}
"""


def esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def kpi(value, label, cls="") -> str:
    return f'<div class="kpi {cls}"><div class="v">{value}</div><div class="l">{esc(label)}</div></div>'


def rollup_table(rows, key_label, filter_group) -> str:
    if not rows:
        return ""
    mx = max(r["count"] for r in rows) or 1
    body = []
    for r in rows:
        w = round(r["count"] / mx * 100)
        body.append(
            f'<tr data-filter-group="{filter_group}" data-filter-val="{esc(r["key"])}">'
            f'<td>{esc(r["key"])}</td>'
            f'<td>{r["count"]}</td>'
            f'<td>{r["critical"]}</td>'
            f'<td><span class="bar"><i style="width:{w}%"></i></span></td>'
            f'<td style="color:var(--text-faint)">{esc(r["top_segments"])}</td></tr>'
        )
    return (
        f'<table><thead><tr><th>{esc(key_label)}</th><th>Failures</th>'
        f'<th>Critical</th><th></th><th>Top segments</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
    )


def pill_group(label, group, values_counts) -> str:
    pills = [f'<span class="grp-label">{esc(label)}</span>']
    for val, n in values_counts:
        pills.append(
            f'<span class="pill" data-group="{group}" data-val="{esc(val)}">'
            f'{esc(val)}<span class="n">{n}</span></span>'
        )
    return f'<div class="row">{"".join(pills)}</div>'


def card(f) -> str:
    crit = f["level"] == "critical"
    xa = f["cross_arm_status"]
    xa_badge = ('<span class="badge regr">regression</span>' if xa == "other_passed"
                else '<span class="badge both">both failed</span>')
    lvl_badge = f'<span class="badge {"crit" if crit else ""}">{esc(f["level"])}</span>'
    rationale = f["rationale_experiment"] or "(programmatic assertion — no judge rationale)"
    return f'''<div class="card" data-id="{esc(f["id"])}"
  data-trend="{esc(f["trend"])}" data-tool="{esc(f["tool"])}"
  data-family="{esc(f["family"])}" data-cross="{esc(xa)}" data-level="{esc(f["level"])}"
  data-text="{esc((f["query"]+" "+f["assertion"]+" "+f["trend"]+" "+f["tool"]).lower())}">
  <div class="top">
    <input type="checkbox" class="inc" checked>
    <div style="flex:1">
      <div class="q">{esc(f["query"])}</div>
      <div class="badges">
        <span class="badge trend">{esc(f["trend"])}</span>
        <span class="badge tool">{esc(f["tool"])}</span>
        <span class="badge">{esc(f["family"])}</span>
        {lvl_badge}{xa_badge}
        <span class="badge" style="opacity:.6">{esc(f["id"])}</span>
      </div>
      <div class="ev">{esc(f["failure_evidence"])}</div>
      <details>
        <summary>assertion · judge rationale · CodeGen reply</summary>
        <div class="det">
          <div class="blk"><h4>Assertion ({esc(f["failure_label"])})</h4>{esc(f["assertion"])}</div>
          <div class="blk"><h4>Judge rationale (CodeGen)</h4>{esc(rationale)}</div>
          <div class="blk"><h4>CodeGen reply</h4>{esc(f["reply_experiment"])}</div>
        </div>
      </details>
    </div>
  </div>
</div>'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    m = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    run = m["run"]
    s = m["summary"]
    fails = m["failures"]
    exp = s["experiment"]

    # KPI strip
    kpis = "".join([
        kpi(exp["evaluated"], "CodeGen assertions"),
        kpi(f'{exp["pass_rate"]:.0%}', "CodeGen pass rate", "warn" if exp["pass_rate"] < .6 else "good"),
        kpi(exp["failed"], "CodeGen failures", "bad"),
        kpi(s["regressions_control_passed"], "Regressions (Mainline passed)", "bad"),
        kpi(s["both_failed"], "Both failed", "warn"),
        kpi(s["failures_by_level"].get("critical", 0), "Critical failures", "bad"),
    ])

    # Family breakdown bar
    fam = s["failures_by_family"]
    fam_total = sum(fam.values()) or 1
    fam_rows = "".join(
        f'<tr><td>{esc(k)}</td><td>{v}</td><td>'
        f'<span class="bar fam-{esc(k.split()[0])}"><i style="width:{round(v/fam_total*100)}%"></i></span>'
        f' {round(v/fam_total*100)}%</td></tr>'
        for k, v in sorted(fam.items(), key=lambda x: -x[1])
    )

    # Filter pill groups
    from collections import Counter
    trend_c = Counter(f["trend"] for f in fails).most_common()
    tool_c = Counter(f["tool"] for f in fails).most_common()
    fam_c = Counter(f["family"] for f in fails).most_common()
    cross_c = [("regression", s["regressions_control_passed"]), ("both failed", s["both_failed"])]
    level_c = Counter(f["level"] for f in fails).most_common()

    pills = "".join([
        pill_group("Trend", "trend", trend_c),
        pill_group("Tool", "tool", tool_c),
        pill_group("Family", "family", fam_c),
        pill_group("Cross-arm", "cross", [("regression", cross_c[0][1]), ("both failed", cross_c[1][1])]),
        pill_group("Level", "level", level_c),
    ])

    cards = "".join(card(f) for f in fails)

    # Embed run meta + failures for the JSON exporter
    export_meta = {
        "run": {"id": run["id"], "name": run["name"], "date": run["date"],
                "job_url": run["job_url"], "experiment_label": run.get("experiment_label", "CodeGen")},
    }
    failures_json = json.dumps({f["id"]: f for f in fails}, ensure_ascii=False)
    meta_json = json.dumps(export_meta, ensure_ascii=False)
    for_script = lambda x: x.replace("</", "<\\/")

    js = """
const FAILURES = JSON.parse(document.getElementById('failures-data').textContent);
const META = JSON.parse(document.getElementById('meta-data').textContent);
const active = {trend:new Set(),tool:new Set(),family:new Set(),cross:new Set(),level:new Set()};
const CROSS_MAP = {regression:'other_passed','both failed':'both_failed'};
const cards = Array.from(document.querySelectorAll('.card'));
const search = document.getElementById('search');

function cardCross(c){return c.dataset.cross==='other_passed'?'regression':'both failed';}
function matches(c){
  for(const g of ['trend','tool','family','level']){
    if(active[g].size && !active[g].has(c.dataset[g])) return false;
  }
  if(active.cross.size && !active.cross.has(cardCross(c))) return false;
  const q=search.value.trim().toLowerCase();
  if(q && !c.dataset.text.includes(q)) return false;
  return true;
}
function apply(){
  let vis=0;
  cards.forEach(c=>{const ok=matches(c);c.classList.toggle('hide',!ok);if(ok)vis++;});
  document.getElementById('vis-count').textContent=vis;
  updateSel();
}
function updateSel(){
  const sel=cards.filter(c=>!c.classList.contains('hide')&&c.querySelector('.inc').checked).length;
  document.getElementById('sel-count').textContent=sel;
}
document.querySelectorAll('.pill').forEach(p=>{
  p.addEventListener('click',()=>{
    const g=p.dataset.group,v=p.dataset.val;
    if(active[g].has(v)){active[g].delete(v);p.classList.remove('on');}
    else{active[g].add(v);p.classList.add('on');}
    apply();
  });
});
// rollup-table rows act as trend/tool filters too
document.querySelectorAll('tr[data-filter-group]').forEach(tr=>{
  tr.addEventListener('click',()=>{
    const g=tr.dataset.filterGroup,v=tr.dataset.filterVal;
    const pill=document.querySelector(`.pill[data-group="${g}"][data-val="${CSS.escape(v)}"]`);
    if(pill){pill.click();tr.classList.toggle('active',active[g].has(v));}
  });
});
search.addEventListener('input',apply);
document.getElementById('clear').addEventListener('click',()=>{
  Object.values(active).forEach(s=>s.clear());
  document.querySelectorAll('.pill.on').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('tr.active').forEach(t=>t.classList.remove('active'));
  search.value='';apply();
});
cards.forEach(c=>c.querySelector('.inc').addEventListener('change',updateSel));
document.getElementById('sel-all').addEventListener('click',()=>{
  cards.forEach(c=>{if(!c.classList.contains('hide'))c.querySelector('.inc').checked=true;});updateSel();});
document.getElementById('sel-none').addEventListener('click',()=>{
  cards.forEach(c=>{if(!c.classList.contains('hide'))c.querySelector('.inc').checked=false;});updateSel();});

document.getElementById('download').addEventListener('click',()=>{
  const items=[];
  cards.forEach(c=>{
    if(c.classList.contains('hide'))return;
    if(!c.querySelector('.inc').checked)return;
    const f=FAILURES[c.dataset.id];
    items.push({
      id:f.id,query:f.query,segment:f.segment,tool:f.tool,assertion:f.assertion,
      level:f.level,cross_arm_status:f.cross_arm_status,trend:f.trend,family:f.family,
      failure_label:f.failure_label,failure_evidence:f.failure_evidence,
      rationale_experiment:f.rationale_experiment,reply_experiment:f.reply_experiment
    });
  });
  if(!items.length){alert('No failures selected. Adjust filters or check some rows.');return;}
  const filter={};
  for(const g of ['trend','tool','family','cross','level'])
    if(active[g].size)filter[g]=Array.from(active[g]);
  if(search.value.trim())filter.search=search.value.trim();
  const payload={
    format:'codegen-ado-selection/v1',
    notes:'CodeGen-only SEVAL failures selected for ADO item creation. Group by trend (or tool) -> one bug per cluster.',
    run:META.run,exported_at:new Date().toISOString(),
    filter_applied:filter,count:items.length,items:items
  };
  const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`codegen_${META.run.id}_ado_selection.json`;
  document.body.appendChild(a);a.click();a.remove();
});
apply();
"""

    htmldoc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodeGen Failure Triage — {esc(run['id'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>CodeGen Failure Triage</h1>
<div class="sub">Run <a href="{esc(run['job_url'])}">{esc(run['id'])}</a> ·
{esc(run['name'])} · {esc(run['date'])} · focus: <b>CodeGen (experiment) failures only</b><br>
Control = Mainline · Experiment = CodeGen · source <code>{esc(Path(run['csv_path']).name)}</code></div>

<div class="kpis">{kpis}</div>

<h2>Failure trends</h2>
<div class="note">Click a row to filter the failures below. Critical = headline KPI.</div>
{rollup_table(m['trend_rollup'], 'Trend', 'trend')}

<h2>By tool / primitive</h2>
{rollup_table(m['tool_rollup'], 'Tool', 'tool')}

<h2>Root-cause family</h2>
<table><thead><tr><th>Family</th><th>Failures</th><th>Share</th></tr></thead>
<tbody>{fam_rows}</tbody></table>
<div class="note">Cross-arm guardrail applied: a Missing-data / Assertion label is demoted to Model
when Mainline passed the same (query, assertion). {sum(1 for f in fails if f['guardrail_relabel'])} rows relabeled.</div>

<h2>Failures — select &amp; export for ADO</h2>
<div class="controls">
  <div class="row"><input id="search" type="search" placeholder="Search query, assertion, trend, tool…">
    <button class="btn" id="clear">Clear filters</button></div>
  {pills}
  <div class="row" style="margin-top:4px">
    <button class="btn" id="sel-all">Select all visible</button>
    <button class="btn" id="sel-none">Deselect visible</button>
    <span class="counter"><b id="sel-count">0</b> selected · <span id="vis-count">0</span> visible</span>
    <button class="btn primary" id="download" style="margin-left:auto">⬇ Download selected JSON</button>
  </div>
</div>
{cards}
</div>
<script type="application/json" id="failures-data">{for_script(failures_json)}</script>
<script type="application/json" id="meta-data">{for_script(meta_json)}</script>
<script>{js}</script>
</body></html>"""

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(htmldoc, encoding="utf-8")
    print(f"[render] wrote {out}  ({len(fails)} failure cards)")


if __name__ == "__main__":
    main()

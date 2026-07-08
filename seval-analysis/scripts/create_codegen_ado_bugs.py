"""One-off: create 11 per-topic ADO Bugs for CodeGen failure triage (run 576292).

Reads the triage manifest, groups failures by trend, builds an HTML body per
topic (summary listing ALL affected tools, no regression wording, 4 example
query/assertion/judge/reply), and POSTs one Bug per topic to ADO.

All bugs: project "Outlook Web", area Outlook Agent, assigned to Corina,
tags OutlookAgent;SevalRegression, P1 if any critical else P2, with a
hyperlink to the published yohncf report.
"""
import html, json, re, subprocess, sys, urllib.request, urllib.error
from collections import defaultdict
from pathlib import Path

MANIFEST = r"data\eval-manifests\576292_2026-06-24_codegen.json"
REPORT = "https://yohncf.github.io/OCV-Weekly_temp/eval-runs/2026-06-24_576292_codegen-failure-triage.html"
ORG = "outlookweb"
PROJECT = "Outlook Web"
AREA = r"Outlook Web\Outlook Copilot Service\Outlook Agent"
ITERATION = "Outlook Web"
ASSIGNEE = "corina@microsoft.com"
TAGS = "OutlookAgent; SevalRegression"
RES = "499b84ac-1321-427f-aa17-267ca6975798"

def token():
    out = subprocess.run(["az", "account", "get-access-token", "--resource", RES,
                          "--query", "accessToken", "-o", "tsv"],
                         capture_output=True, text=True, shell=True)
    t = out.stdout.strip()
    if not t:
        sys.exit("token failed: " + out.stderr)
    return t

def clean(s):
    return (s or "").replace("\ufffd", "'").strip()

def esc(s):
    return html.escape(clean(s), quote=True)

def pick(rows):
    rows = sorted(rows, key=lambda r: (r["level"] != "critical", r["cross_arm_status"] != "other_passed"))
    out, seen = [], set()
    for r in rows:
        if r["query"] in seen:
            continue
        seen.add(r["query"]); out.append(r)
        if len(out) == 4:
            break
    return out

def body_html(trend, rows):
    fam = rows[0]["family"]
    crit = sum(1 for r in rows if r["level"] == "critical")
    tools = defaultdict(int)
    for r in rows:
        tools[r["tool"]] += 1
    tool_list = ", ".join(f"{esc(k)} ({v})" for k, v in sorted(tools.items(), key=lambda x: -x[1]))
    parts = [
        f"<b>SEVAL run:</b> {esc(RUN['name'])} (id {esc(RUN['id'])}, {esc(RUN['date'])}) &mdash; "
        f'<a href="{esc(RUN["job_url"])}">{esc(RUN["job_url"])}</a><br>',
        "<b>Focus:</b> CodeGen (experiment) arm only.<br><br>",
        "<b>Summary</b><br>",
        f"Root-cause family: {esc(fam)}<br>",
        f"{len(rows)} CodeGen assertion failures in this topic ({crit} critical).<br>",
        f"Affected tools / primitives: {tool_list}<br><br>",
        f'<b>Full interactive report:</b> <a href="{esc(REPORT)}">{esc(REPORT)}</a><br><br>',
        f"<b>Representative failures ({len(pick(rows))} of {len(rows)})</b><ol>",
    ]
    for e in pick(rows):
        judge = clean(e["rationale_experiment"]) or "(programmatic check &mdash; no judge rationale)"
        parts.append(
            "<li style='margin-bottom:10px'>"
            f"<b>[{esc(e['level'])}] {esc(e['tool'])}</b><br>"
            f"<b>Query:</b> {esc(e['query'])}<br>"
            f"<b>Assertion:</b> {esc(e['assertion'])}<br>"
            f"<b>Judge rationale:</b> {esc(judge)}<br>"
            f"<b>CodeGen reply:</b> {esc(e['reply_experiment'])}"
            "</li>"
        )
    parts.append("</ol>")
    return "".join(parts), crit

def create_bug(tok, title, desc_html, priority):
    patches = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.Description", "value": desc_html},
        {"op": "add", "path": "/fields/System.AssignedTo", "value": ASSIGNEE},
        {"op": "add", "path": "/fields/System.AreaPath", "value": AREA},
        {"op": "add", "path": "/fields/System.IterationPath", "value": ITERATION},
        {"op": "add", "path": "/fields/System.Tags", "value": TAGS},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority},
        {"op": "add", "path": "/relations/-", "value": {
            "rel": "Hyperlink", "url": REPORT,
            "attributes": {"comment": "CodeGen failure triage report"}}},
    ]
    url = (f"https://dev.azure.com/{ORG}/"
           + urllib.parse.quote(PROJECT) + "/_apis/wit/workitems/$Bug?api-version=7.0")
    req = urllib.request.Request(url, data=json.dumps(patches).encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Content-Type", "application/json-patch+json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r), None
    except urllib.error.HTTPError as ex:
        return None, f"HTTP {ex.code}: {ex.read().decode('utf-8', 'replace')[:600]}"

import urllib.parse
m = json.loads(Path(MANIFEST).read_text(encoding="utf-8"))
RUN = m["run"]
g = defaultdict(list)
for f in m["failures"]:
    g[f["trend"]].append(f)
order = [t["key"] for t in m["trend_rollup"]]

tok = token()
created = []
for trend in order:
    rows = g[trend]
    desc, crit = body_html(trend, rows)
    prio = 1 if crit else 2
    title = f"[SEVAL CodeGen {RUN['id']}] {trend} ({len(rows)} failures, {crit} critical)"
    res, err = create_bug(tok, title, desc, prio)
    if err:
        print(f"FAILED: {title}\n  {err}")
        print(f"Created {len(created)} before failure. Aborting.")
        sys.exit(1)
    wid = res["id"]
    link = f"https://{ORG}.visualstudio.com/{urllib.parse.quote(PROJECT)}/_workitems/edit/{wid}"
    created.append((wid, prio, trend, link))
    print(f"  #{wid}  P{prio}  {trend}  -> {link}")

print(f"\nCreated {len(created)} bugs, all assigned to {ASSIGNEE}.")
Path(r"data\eval-ado\576292_created_bugs.json").write_text(
    json.dumps([{"id": w, "priority": p, "trend": t, "url": u} for w, p, t, u in created],
               indent=2), encoding="utf-8")

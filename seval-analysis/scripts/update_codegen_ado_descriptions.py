"""One-off: prepend an engineer-facing 'Main issue / root cause' block to the
11 CodeGen failure-triage bugs (run 576292). Preserves the existing
representative-failure section already in System.Description.
"""
import json, subprocess, sys, urllib.parse, urllib.request, urllib.error

ORG = "outlookweb"
PROJECT = "Outlook Web"
RES = "499b84ac-1321-427f-aa17-267ca6975798"

# id -> (heading stats line, problem paragraph, action)
TICKETS = {
    440709: (
        "202 failures / 202 critical &middot; family: Model &middot; primitives: CreateRule (57), UpdateRule (30), GetRules (8), DeleteRule (7), Flag (6)",
        "CodeGen narrates success in prose but never emits the tool call. The <code>intent_detection.test_tool_invocation</code> check finds no primitive in the execution trace even though the reply claims the action was done (e.g. &ldquo;Marked the email as completed&rdquo;). This is the single largest failure cluster; Mainline passed the same cases.",
        "Fix the CodeGen planner / tool-binding so the model actually invokes the primitive instead of hallucinating completion."),
    440710: (
        "91 failures / 67 critical &middot; family: Model (64) + Missing data (27) &middot; primitives: CreateRule (69), CreateNestedFolder (8), SearchPeople (7)",
        "When a rule or move targets a folder that does not exist in the mailbox (e.g. &ldquo;Important&rdquo;, &ldquo;Updates&rdquo;), CodeGen silently substitutes a different folder (e.g. Archive) or drops the move action entirely, instead of creating the folder or surfacing the gap to the user.",
        "Implement create-folder-on-demand, or explicitly disambiguate / report when the target folder is absent."),
    440711: (
        "55 failures / 0 critical &middot; family: Model &middot; primitives: Complete, FlagWithReminderDate, MarkAsRead, Archive, Flag (broad)",
        "When the user request matches multiple candidate emails, CodeGen acts on one of them directly rather than presenting a disambiguation list for the user to choose from.",
        "Require a disambiguation prompt whenever more than one message matches the request."),
    440712: (
        "49 failures / 49 critical &middot; family: Model &middot; primitives: Flag, Complete, FlagWithReminderDate, MarkAsRead, Archive, Unpin",
        "The <code>email_attachment_test.test_email_in_citation_or_annotation</code> check requires the acted-on email to be cited / annotated as a structured entity in the reply. CodeGen describes the action in plain text only and omits the citation/annotation object. Mainline passed.",
        "Attach the email citation/annotation entity to replies, not just a prose description."),
    440713: (
        "49 failures / 17 critical &middot; family: Model &middot; primitives: UpdateRule (39), CreateRule (6), Flag (2)",
        "Predominantly UpdateRule: CodeGen attempts the write but the change does not persist &mdash; the verification read-back shows the rule unchanged. Replies sometimes admit it directly (e.g. &ldquo;the update did not stick even after two attempts&rdquo;).",
        "Investigate the UpdateRule write path and read-after-write consistency in the CodeGen pipeline."),
    440714: (
        "49 failures / 28 critical &middot; family: Model &middot; primitives: CreateRule (40), CreateFolder (9)",
        "CodeGen returns placeholder / mock tool output &mdash; e.g. the created folder is named &ldquo;[Mock] Folder&rdquo; instead of &ldquo;Personal&rdquo; &mdash; indicating a stub handler executed instead of the real tool.",
        "A mock/stub tool handler is leaking into the CodeGen path; verify real tool wiring vs the mock fallback."),
    440715: (
        "49 failures / 23 critical &middot; family: Model &middot; primitives: CreateRule (14), UpdateRule (9), Unpin (6), UnflagRemoveReminderDate (5), Pin (4)",
        "CodeGen refuses or deflects a supported capability &mdash; e.g. it offers to search the inbox for OneDrive policies when asked about rules, or declines Unpin/Pin/CreateCategory actions that are in fact supported.",
        "Capability routing / guardrails over-trigger; ensure supported primitives are not declined or redirected."),
    440716: (
        "43 failures / 31 critical &middot; family: Model &middot; primitives: CreateRule (19), UpdateRule (12), Flag (2)",
        "The action is only partially applied or applied incorrectly &mdash; e.g. the reply states a category was added to an email, but the verification read-back still shows only the original categories. CodeGen reports success while the actual mailbox state diverges.",
        "Reconcile reported success against the real end state in multi-step execution + verification."),
    440717: (
        "28 failures / 0 critical &middot; family: Model (14) + Assertion (14) &middot; primitives: CreateRule (15), UpdateRule (13)",
        "When creating or updating a rule, CodeGen does not state whether the change applies to future emails only or also affects existing messages. Half of the rows are assertion-quality issues rather than pure model behavior.",
        "Add a scope statement to rule confirmations; eval owners should review the 14 Assertion-family rows for validity."),
    440718: (
        "13 failures / 8 critical &middot; family: Model &middot; primitives: CreateRule (9), UpdateRule (4)",
        "The tool call hard-errors (e.g. a rule cannot be created because an &ldquo;Updates&rdquo; folder is absent) and CodeGen surfaces the failure without recovering or creating the missing prerequisite.",
        "Improve error handling / prerequisite creation so a missing dependency does not hard-fail the action."),
    440719: (
        "4 failures / 0 critical &middot; family: Assertion &middot; primitives: Flag, FlagWithReminderDate, UnflagRemoveDueData, Copy",
        "When multiple items are acted on, the reply does not state the total count of affected items. All four rows are Assertion-family and likely reflect an over-strict / aspirational check rather than a real defect.",
        "Low priority &mdash; eval owners to confirm whether a count statement should be required before treating as a product bug."),
}


def token():
    out = subprocess.run(["az", "account", "get-access-token", "--resource", RES,
                          "--query", "accessToken", "-o", "tsv"],
                         capture_output=True, text=True, shell=True)
    t = out.stdout.strip()
    if not t:
        sys.exit("token failed: " + out.stderr)
    return t


def block_html(stats, problem, action):
    return (
        '<div style="border-left:4px solid #c0392b;padding:6px 12px;margin:0 0 14px 0;background:#f7f1f0">'
        '<b>Main issue / root cause</b><br>'
        f'<i>{stats}</i><br><br>'
        f'{problem}<br><br>'
        f'<b>Suggested action:</b> {action}'
        '</div><hr>'
    )


def get_desc(tok, wid):
    url = (f"https://dev.azure.com/{ORG}/_apis/wit/workitems/{wid}"
           "?fields=System.Description&api-version=7.0")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req) as r:
        data = json.load(r)
    return data["fields"].get("System.Description", "")


def patch_desc(tok, wid, new_desc):
    patches = [{"op": "add", "path": "/fields/System.Description", "value": new_desc}]
    url = (f"https://dev.azure.com/{ORG}/" + urllib.parse.quote(PROJECT)
           + f"/_apis/wit/workitems/{wid}?api-version=7.0")
    req = urllib.request.Request(url, data=json.dumps(patches).encode("utf-8"),
                                 method="PATCH")
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Content-Type", "application/json-patch+json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r), None
    except urllib.error.HTTPError as ex:
        return None, f"HTTP {ex.code}: {ex.read().decode('utf-8', 'replace')[:600]}"


tok = token()
for wid, (stats, problem, action) in TICKETS.items():
    existing = get_desc(tok, wid)
    marker = "Main issue / root cause"
    if marker in (existing or ""):
        print(f"  #{wid}  SKIP (block already present)")
        continue
    new_desc = block_html(stats, problem, action) + (existing or "")
    res, err = patch_desc(tok, wid, new_desc)
    if err:
        print(f"  #{wid}  FAILED: {err}")
        sys.exit(1)
    print(f"  #{wid}  updated (rev {res.get('rev')})")

print("\nDone updating descriptions.")

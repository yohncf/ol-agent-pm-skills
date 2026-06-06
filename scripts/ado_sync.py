"""
ADO Ticket Sync — Match-or-create Azure DevOps work items from the OCV
subtopics CSV, then write the resulting ADO URLs back into that CSV so
ocv-publish-report can render them on the ticket cards.

Two-phase design (review-before-action):

    Phase 1:  python scripts/ado_sync.py propose \
                  --subtopics data/ocv_<area>_<to>_subtopics.csv

              Writes data/ado_proposals_<area>_<to>.json with one
              entry per P0/P1/P2 row: candidate ADO matches + a
              suggested action (link / create / skip).

    Phase 2:  python scripts/ado_sync.py execute \
                  --proposals data/ado_proposals_<area>_<to>.json

              Reads the (possibly user-edited) proposals JSON, calls
              ADO REST APIs to either append a comment with OCV links
              to an existing item or create a new Bug, then writes the
              resulting ADO URL + action back into the subtopics CSV
              under two new columns: 'ADO URL' and 'ADO action'.

Auth: DefaultAzureCredential (uses `az login` — same pattern as
ods_api_extract.py). The Azure DevOps resource ID is hard-coded.

Prerequisites:
    pip install azure-identity
    az login
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Make scripts/lib importable when run as `python scripts/ado_sync.py …`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.model_path import format_models_label, path_slug, diagnose_path  # noqa: E402

from azure.identity import DefaultAzureCredential

# ---------------------------------------------------------------------------
# Constants — edit here if your team uses different defaults.
# ---------------------------------------------------------------------------

ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"  # Azure DevOps
ADO_ORG = "outlookweb"
ADO_PROJECT = "Outlook Web"
ADO_PROJECT_ENC = urllib.parse.quote(ADO_PROJECT)
API_BASE = f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT_ENC}/_apis"
API_VERSION = "7.1"

DEFAULT_AREA_PATH = r"Outlook Web\Outlook Copilot Service\Outlook Agent"
DEFAULT_ITERATION = "Outlook Web"
DEFAULT_TAG = "OutlookAgent"
DEFAULT_WORK_ITEM_TYPE = "Bug"

# Owner-routing config: defaults to configs/ado_owners_<area>.json next to
# the repo's other configs. Override per-run with --owners-config.
DEFAULT_OWNERS_CONFIG = os.path.join("configs", "ado_owners_outlook-agent.json")

# Priority -> ADO Priority field value (1 = highest)
PRIORITY_MAP = {"P0": 1, "P1": 2, "P2": 3, "P3": 4}
PRIORITIES_TO_SYNC = ("P0", "P1", "P2")

# Maximum number of candidate matches to surface per subtopic.
CANDIDATE_LIMIT = 8

# Default similarity threshold for suggesting "link" vs "create" — applied
# to RAW Jaccard (no surface boost). Anything at or above this is considered
# for a link suggestion, but the failure-mode verb check below can still
# downgrade it to "create" when nouns overlap but verbs disagree.
LINK_SUGGEST_THRESHOLD = 0.40

# Override: raw Jaccard at or above this is "obviously the same bug" — auto
# link even when failure-mode verbs disagree. Tunes the trade-off between
# duplicate creation (too low) and wrong links (too high).
STRONG_MATCH_OVERRIDE = 0.65

# Token cache — DefaultAzureCredential caches internally, but we also hold
# the token across the run to avoid re-acquiring on every API call.
_TOKEN_CACHE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Auth + HTTP helpers
# ---------------------------------------------------------------------------

def acquire_token() -> str:
    if "token" in _TOKEN_CACHE:
        return _TOKEN_CACHE["token"]
    cred = DefaultAzureCredential()
    tok = cred.get_token(f"{ADO_RESOURCE_ID}/.default")
    _TOKEN_CACHE["token"] = tok.token
    return tok.token


def ado_request(method: str, url: str, body: Optional[Any] = None,
                content_type: str = "application/json") -> Any:
    """Call ADO REST API and return parsed JSON (or {} on 204)."""
    data: Optional[bytes] = None
    if body is not None:
        if content_type == "application/json-patch+json":
            data = json.dumps(body).encode("utf-8")
        else:
            data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {acquire_token()}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ADO {method} {url} failed: HTTP {e.code}\n{err_body}"
        ) from e


# ---------------------------------------------------------------------------
# Owner routing (maps subtopic rows / proposals to ADO assignees)
# ---------------------------------------------------------------------------

def load_owner_config(path: Optional[str]) -> Dict[str, Any]:
    """Load the owners JSON config. Returns {'rules': []} on missing file
    so callers can still operate (with no assignment)."""
    if not path:
        return {"rules": []}
    if not os.path.exists(path):
        print(f"[ado-sync] owners config not found at {path}; "
              "no auto-assignment will happen.")
        return {"rules": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_topic_num(topic_raw: str) -> str:
    """Parent Topic can be 'T4. Foo' or '4. Foo'. Return '4' or ''."""
    t = (topic_raw or "").strip()
    if not t:
        return ""
    t_clean = t[1:] if t.startswith("T") else t
    if "." in t_clean:
        return t_clean.split(".", 1)[0].strip()
    return t_clean.strip()


def compute_assignee(
    title: str, category: str, topic_raw: str, cfg: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Match the first owner rule against (title, category, topic) and
    return (email, display_name, rule_label) or (default, default_name, 'default').

    Keyword matching is title-only by design — descriptions are PM
    paraphrases that frequently contain accidental surface words
    (e.g., 'handoff', 'codegen') that mis-route the rule.
    """
    t_low = (title or "").lower()
    cat = (category or "").strip()
    topic_num = _extract_topic_num(topic_raw or "")
    for rule in cfg.get("rules", []):
        m = rule.get("match", {})
        if "category" in m and cat not in m["category"]:
            continue
        if "topic" in m and topic_num not in m["topic"]:
            continue
        if "title_keywords" in m:
            if not any(kw.lower() in t_low for kw in m["title_keywords"]):
                continue
        return (rule.get("assignee"), rule.get("owner_name"),
                rule.get("label"))
    return (cfg.get("default_assignee"),
            cfg.get("default_assignee_name"), "default")


def parse_assignee_overrides(items: Optional[List[str]]) -> Dict[str, str]:
    """Parse --override ID=email pairs into a {ado_id_str: email} dict.
    Accepts 'NNNNN=user@microsoft.com' or '#NNNNN=user@microsoft.com'.
    """
    out: Dict[str, str] = {}
    if not items:
        return out
    for raw in items:
        if "=" not in raw:
            raise SystemExit(f"--override must be ID=email, got: {raw!r}")
        k, v = raw.split("=", 1)
        k = k.strip().lstrip("#")
        v = v.strip()
        if not k or not v:
            raise SystemExit(f"--override must be ID=email, got: {raw!r}")
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Subtopics CSV reading + priority derivation (mirrors publish_ocv_report.py)
# ---------------------------------------------------------------------------

CORE_INFO_SURFACES = {"Summarization", "Triage", "Search"}
ACTION_SURFACES = {"Drafting", "Replying", "Scheduling", "Triage"}


def derive_priority(row: Dict[str, str]) -> str:
    if row.get("Priority"):
        return row["Priority"].upper().strip()
    try:
        count = int(row.get("Count") or row.get("Item Count") or "1")
    except ValueError:
        count = 1
    topic_str = row.get("Parent Topic", "") or ""
    m = re.match(r"\s*(\d+)", topic_str)
    topic_num = int(m.group(1)) if m else 0
    category = row.get("Category", "")
    submode = (row.get("SubMode") or "").lower()
    if topic_num == 2:
        return "P0"
    if topic_num == 11 and count >= 2:
        return "P0"
    if count >= 4:
        return "P1"
    if topic_num == 1 and category in ACTION_SURFACES and count >= 2:
        return "P1"
    if topic_num == 6 and "error_string" in submode and category in CORE_INFO_SURFACES and count >= 2:
        return "P1"
    if topic_num == 10 and count >= 2:
        return "P1"
    if count >= 2:
        return "P2"
    if topic_num in (4, 8):
        return "P2"
    return "P3"


def load_subtopics(path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for r in reader:
            rows.append({k: (v or "") for k, v in r.items()})
    return rows, fieldnames


def extract_links(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"[|;,\s]+", raw)
    return [p.strip() for p in parts if p.strip().startswith("http")]


def subtopic_key(row: Dict[str, str]) -> str:
    """Stable identifier for a subtopic row — title + first OCV link if any."""
    title = (row.get("Brief title") or row.get("Brief Title")
             or row.get("Issue description") or "").strip()
    links = extract_links(row.get("OCV links") or row.get("OCV Item Links") or "")
    first = links[0] if links else ""
    return f"{title}|{first}"


def load_dash_index(dash_csv_path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Build {OCV ticket URL -> DashRecord} from the dash_ocv CSV.

    DashRecord shape:
        {
          "dash_url": str,
          "resolved_models": List[str],
          "path_slug": str,             # e.g. "CodeGen-Claude", "Sydney-Tools+WorkBerry"
          "models_label": str,          # "CodeGen-Claude: prod-..., prod-..." (no brackets)
        }
    Falls back gracefully if the optional Resolved Models / Path columns are
    missing (older dash CSVs from before that field was wired up).
    """
    if not dash_csv_path:
        return {}
    if not os.path.exists(dash_csv_path):
        print(f"[ado-sync] WARN: --dash-csv {dash_csv_path} not found; "
              "ADO items will be created without Dash links.")
        return {}
    index: Dict[str, Dict[str, Any]] = {}
    n_with_models = 0
    with open(dash_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ocv = (r.get("OCV ticket") or "").strip()
            dash = (r.get("Dash ticket") or "").strip()
            if not (ocv and dash):
                continue
            raw_models = (r.get("Resolved Models") or "").strip()
            models = [m.strip() for m in raw_models.split(";") if m.strip()] if raw_models else []
            csv_path_slug = (r.get("Path") or "").strip()
            if models:
                # Always (re)compute the label so we stay consistent with the
                # current rule set even if the CSV was extracted with an older
                # reference JSON.
                label = format_models_label(models)
                if not csv_path_slug:
                    branch, _, _, _ = diagnose_path(models)
                    csv_path_slug = path_slug(branch)
                n_with_models += 1
            else:
                label = ""
                csv_path_slug = csv_path_slug or ""
            index[ocv] = {
                "dash_url": dash,
                "resolved_models": models,
                "path_slug": csv_path_slug,
                "models_label": label,
            }
    print(f"[ado-sync] loaded {len(index)} OCV->Dash mappings from "
          f"{dash_csv_path}  ({n_with_models} with Resolved Models)")
    return index


def load_dash_mapping(dash_csv_path: Optional[str]) -> Dict[str, str]:
    """Back-compat shim: returns just {OCV -> Dash URL}."""
    return {ocv: rec["dash_url"] for ocv, rec in load_dash_index(dash_csv_path).items()}


def resolve_dash_links(ocv_links: List[str],
                       ocv_to_dash: Dict[str, Any]) -> List[str]:
    """Return deduped list of Dash URLs for the given OCV links.

    Accepts both the legacy {ocv -> dash_url} mapping and the new
    {ocv -> DashRecord} index so existing call sites keep working.
    """
    out: List[str] = []
    for link in ocv_links:
        val = ocv_to_dash.get(link)
        if not val:
            continue
        dash = val["dash_url"] if isinstance(val, dict) else val
        if dash and dash not in out:
            out.append(dash)
    return out


def resolve_dash_records(ocv_links: List[str],
                         dash_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return deduped list of DashRecord dicts (by dash_url) for these OCV links."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for link in ocv_links:
        rec = dash_index.get(link)
        if not rec:
            continue
        if rec["dash_url"] in seen:
            continue
        seen.add(rec["dash_url"])
        out.append(rec)
    return out


def is_eligible(row: Dict[str, str], dash_links: List[str]) -> bool:
    """Prioritization rule: always include P0/P1; include other priorities
    only if the row has more than one OCV item or any Dash ticket link."""
    pri = derive_priority(row)
    if pri in ("P0", "P1"):
        return True
    try:
        count = int(row.get("Count") or row.get("Item Count") or "1")
    except ValueError:
        count = 1
    return count > 1 or bool(dash_links)


# ---------------------------------------------------------------------------
# Phase 1: PROPOSE — find candidate matches in ADO for each eligible row
# ---------------------------------------------------------------------------

def query_candidate_items(area_path: str, tag: Optional[str]) -> List[int]:
    """WIQL: pull open work items in the target Area Path (optionally tagged)."""
    where = [
        f"[System.TeamProject] = '{ADO_PROJECT}'",
        f"[System.AreaPath] UNDER '{area_path}'",
        "[System.State] NOT IN ('Closed', 'Resolved', 'Removed', 'Done')",
    ]
    if tag:
        where.append(f"[System.Tags] CONTAINS '{tag}'")
    wiql = {
        "query": (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE " + " AND ".join(where) +
            " ORDER BY [System.ChangedDate] DESC"
        )
    }
    url = f"{API_BASE}/wit/wiql?api-version={API_VERSION}"
    result = ado_request("POST", url, wiql)
    items = result.get("workItems", []) or []
    return [int(it["id"]) for it in items]


def fetch_work_items(ids: List[int]) -> List[Dict[str, Any]]:
    """Batch-fetch work item Title + Description + Tags + State."""
    if not ids:
        return []
    out: List[Dict[str, Any]] = []
    fields = ",".join([
        "System.Id", "System.Title", "System.WorkItemType",
        "System.State", "System.Tags", "System.AreaPath",
        "Microsoft.VSTS.Common.Priority",
    ])
    # ADO caps batch fetch at 200 ids per call.
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        url = (f"{API_BASE}/wit/workitems"
               f"?ids={','.join(str(x) for x in chunk)}"
               f"&fields={urllib.parse.quote(fields)}"
               f"&api-version={API_VERSION}")
        result = ado_request("GET", url)
        out.extend(result.get("value", []))
    return out


def web_url(wi_id: int) -> str:
    return (f"https://{ADO_ORG}.visualstudio.com/{ADO_PROJECT_ENC}"
            f"/_workitems/edit/{wi_id}")


def tokenize(text: str) -> set:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return {t for t in text.split() if len(t) >= 3 and t not in STOPWORDS}


STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "have", "has", "are", "was", "were", "but", "you", "your",
    "all", "any", "out", "user", "users", "agent", "copilot",
    "outlook", "email", "emails",
}

# Failure-mode verbs — what the user was trying to do or what went wrong.
# Two bugs on the same surface (e.g., both "Rules") but with different
# verbs ("create" vs "modify", "move" vs "refuse") are almost always
# different bugs and should NOT auto-link to each other.
FAILURE_VERBS = {
    # action verbs (what the user asked Copilot to do)
    "move", "moves", "moved", "moving",
    "modify", "modifies", "modified", "modifying", "modification",
    "create", "creates", "created", "creating", "creation",
    "delete", "deletes", "deleted", "deleting", "deletion",
    "send", "sends", "sent", "sending",
    "schedule", "schedules", "scheduled", "scheduling",
    "archive", "archives", "archived", "archiving",
    "apply", "applies", "applied", "applying",
    "draft", "drafts", "drafted", "drafting",
    "summarize", "summarizes", "summarized", "summarizing", "summary",
    "translate", "translates", "translated", "translating", "translation",
    "cancel", "cancels", "canceled", "cancelled", "canceling", "cancelling",
    "reschedule", "reschedules", "rescheduled", "rescheduling",
    "save", "saves", "saved", "saving",
    "open", "opens", "opened", "opening",
    "read", "reads",
    "reply", "replies", "replied", "replying",
    "remove", "removes", "removed", "removing",
    "forward", "forwards", "forwarded", "forwarding",
    "search", "searches", "searched", "searching",
    "find", "finds", "found", "finding",
    "accept", "accepts", "accepted", "accepting",
    "decline", "declines", "declined", "declining",
    "categorize", "categorizes", "categorized", "categorizing",
    "polish", "polishes", "polished", "polishing",
    # failure verbs (how it failed)
    "refuse", "refuses", "refused", "refusing", "refusal",
    "fail", "fails", "failed", "failing", "failure",
    "block", "blocks", "blocked", "blocking",
    "ignore", "ignores", "ignored", "ignoring",
    "hang", "hangs", "hanged", "hanging",
    # negation contractions (post apostrophe-strip)
    "not", "didn", "doesn", "won", "isn", "wasn", "wouldn", "couldn",
    "shouldn", "hasn", "haven", "hadn", "aren",
}

# Surface keyword bonus: if a candidate's title contains any of these
# tokens for the subtopic's Category, give it a similarity boost so good
# semantic matches outrank low-Jaccard near-misses.
SURFACE_KEYWORDS: Dict[str, set] = {
    "Scheduling": {"calendar", "meeting", "meetings", "scheduling", "schedule",
                   "invite", "appointment", "event"},
    "Drafting":   {"draft", "drafting", "compose", "write", "writing"},
    "Replying":   {"reply", "replying", "respond", "response", "replies"},
    "Search":     {"search", "find", "query", "lookup", "retrieval"},
    "Summarization": {"summary", "summarize", "summarization", "digest", "brief"},
    "Triage":     {"triage", "inbox", "archive", "archived", "categorize", "sort"},
    "Translation":{"translate", "translation", "language", "localization"},
    "File":       {"file", "files", "attachment", "attach", "document",
                   "csv", "excel", "spreadsheet"},
    "Settings":   {"setting", "settings", "config", "configuration", "preference"},
    "Tasks":      {"task", "tasks", "todo"},
    "Reminders":  {"reminder", "reminders", "remind"},
    "Rules":      {"rule", "rules"},
}


def score_candidate(sub_title_tokens: set, sub_category: str,
                    item: Dict[str, Any]) -> Tuple[float, float]:
    """Pre-ranker: returns ``(rank_score, link_score)``.

    - ``rank_score`` = raw Jaccard + surface keyword boost. Used to pick
      the top-N candidates to surface in the proposal — the boost makes
      sure same-surface items rise to the top of the candidate list.
    - ``link_score`` = raw Jaccard only. Used to decide whether to
      auto-suggest ``link`` vs ``create``. Excluded from the boost so
      that a 0.20 raw match doesn't get auto-linked just because the
      candidate happens to share a single surface keyword.

    The model is expected to make the final match decision by reading
    the full candidate list — see the SKILL.md "Interactive review"
    section. These scores only drive default suggestions.
    """
    title = item.get("fields", {}).get("System.Title", "")
    cand_tokens = tokenize(title)
    if not cand_tokens or not sub_title_tokens:
        return (0.0, 0.0)
    inter = len(sub_title_tokens & cand_tokens)
    union = len(sub_title_tokens | cand_tokens)
    jaccard = inter / union if union else 0.0
    boost = 0.0
    keywords = SURFACE_KEYWORDS.get(sub_category, set())
    if keywords and (cand_tokens & keywords):
        boost = 0.20
    return (jaccard + boost, jaccard)


def extract_verbs(tokens: set) -> set:
    """Subset of FAILURE_VERBS present in a token set."""
    return tokens & FAILURE_VERBS


def decide_link_suggestion(sub_tokens: set, top_item: Dict[str, Any],
                           link_score: float) -> Tuple[bool, str]:
    """Decide whether the top candidate should default to ``link``.

    Returns ``(should_link, reason)``. ``reason`` is a short note the
    proposal can surface so the human reviewer understands why the
    default landed where it did.

    Rules:
      * link_score < LINK_SUGGEST_THRESHOLD -> create (low title overlap)
      * link_score >= STRONG_MATCH_OVERRIDE -> link
        (titles are nearly identical; verb mismatch ignored)
      * Otherwise, require at least one shared failure-mode verb (or
        for both titles to contain no verbs at all). Same-surface bugs
        with different verbs almost always indicate different failure
        modes and should default to ``create``.
    """
    if link_score < LINK_SUGGEST_THRESHOLD:
        return (False, "")
    if link_score >= STRONG_MATCH_OVERRIDE:
        return (True, f"strong title overlap ({link_score:.2f})")
    cand_tokens = tokenize((top_item.get("fields", {}) or {}).get("System.Title", ""))
    sub_verbs = extract_verbs(sub_tokens)
    cand_verbs = extract_verbs(cand_tokens)
    if sub_verbs and cand_verbs and not (sub_verbs & cand_verbs):
        return (False,
                f"title overlap is {link_score:.2f} but failure-mode "
                f"verbs differ (subtopic={sorted(sub_verbs)} vs "
                f"candidate={sorted(cand_verbs)}); defaulting to create")
    if not sub_verbs and cand_verbs:
        # Subtopic title doesn't encode a verb; let the human decide.
        return (False,
                f"title overlap is {link_score:.2f} but subtopic title "
                f"has no failure-mode verb to disambiguate against "
                f"candidate verbs {sorted(cand_verbs)}; defaulting to create")
    return (True, f"title overlap {link_score:.2f} with shared verbs "
                  f"{sorted(sub_verbs & cand_verbs) if sub_verbs and cand_verbs else 'none'}")


def propose(args: argparse.Namespace) -> None:
    rows, _ = load_subtopics(args.subtopics)
    if not rows:
        sys.exit("No rows in subtopics CSV.")

    area_path = args.area_path
    tag = args.tag
    dash_index = load_dash_index(args.dash_csv)
    owners_cfg = load_owner_config(args.owners_config)
    owners_n_rules = len(owners_cfg.get("rules", []))
    if owners_n_rules:
        print(f"[ado-sync] loaded owners config: {args.owners_config} "
              f"({owners_n_rules} rules)")

    print(f"[ado-sync] querying ADO for candidates under area path: {area_path}")
    if tag:
        print(f"[ado-sync]   filtered by tag: {tag}")
    ids = query_candidate_items(area_path, tag)
    print(f"[ado-sync] fetched {len(ids)} open work items from project")
    items = fetch_work_items(ids)

    proposals: List[Dict[str, Any]] = []
    eligible_rows = []
    skipped_priority = 0
    for r in rows:
        ocv_links = extract_links(r.get("OCV links") or r.get("OCV Item Links") or "")
        dash_links = resolve_dash_links(ocv_links, dash_index)
        if not is_eligible(r, dash_links):
            skipped_priority += 1
            continue
        eligible_rows.append((r, ocv_links, dash_links))

    print(f"[ado-sync] subtopics CSV has {len(rows)} rows; "
          f"{len(eligible_rows)} eligible (P0/P1 or count>1 or has Dash); "
          f"{skipped_priority} skipped (P2/P3 with count=1 and no Dash)")

    for r, ocv_links, dash_links in eligible_rows:
        title = (r.get("Brief title") or r.get("Brief Title")
                 or r.get("Issue description") or "Untitled").strip()
        desc = (r.get("Issue description") or r.get("Issue Description") or "").strip()
        topic = r.get("Parent Topic", "")
        category = r.get("Category", "")
        submode = r.get("SubMode", "")
        priority = derive_priority(r)
        count = int(r.get("Count") or r.get("Item Count") or "1")

        # If this row was already synced in a prior run, default the action
        # to "skip" so a naive `execute` doesn't re-PATCH the same item.
        # The agent / user can still flip it to "link" or "create" by hand.
        prior_ado_url = (r.get("ADO URL") or r.get("ADO Url") or "").strip()
        prior_ado_action = (r.get("ADO action") or "").strip().lower()

        # Score on TITLE tokens only (description tokens drown out signal).
        # score_candidate now returns (rank_score, link_score):
        #   rank_score = jaccard + surface boost (drives top-N ordering)
        #   link_score = raw jaccard (drives auto-link suggestion)
        sub_title_tokens = tokenize(title)
        scored = [(score_candidate(sub_title_tokens, category, it), it)
                  for it in items]
        scored.sort(key=lambda t: t[0][0], reverse=True)
        # Surface top N candidates regardless of score — the agent decides.
        top = scored[:CANDIDATE_LIMIT]

        candidates = []
        for (rank, link_score), it in top:
            fields = it.get("fields", {})
            candidates.append({
                "id": it.get("id"),
                "title": fields.get("System.Title", ""),
                "type": fields.get("System.WorkItemType", ""),
                "state": fields.get("System.State", ""),
                "tags": fields.get("System.Tags", ""),
                "url": web_url(it.get("id")),
                "score": round(rank, 3),
                "link_score": round(link_score, 3),
                "verbs": sorted(extract_verbs(tokenize(fields.get("System.Title", "")))),
            })

        if prior_ado_url:
            suggested = "skip"
            match_id: Optional[int] = None
            notes = (f"already synced in a prior run "
                     f"(action={prior_ado_action or 'unknown'}, url={prior_ado_url}). "
                     f"Change to 'link'/'create' only if you want to amend or re-file.")
        else:
            suggested = "create"
            match_id = None
            notes = ""
            if top:
                (top_rank, top_link), top_item = top[0]
                should_link, reason = decide_link_suggestion(
                    sub_title_tokens, top_item, top_link
                )
                if should_link:
                    suggested = "link"
                    match_id = top_item.get("id")
                    notes = reason
                elif reason:
                    notes = reason

        # Resolve owner only for rows that will result in a NEW item.
        # 'link' rows already have an existing assignee on the work item;
        # 'skip' rows do nothing.
        assignee_email, assignee_name, assignee_rule = (None, None, None)
        if suggested == "create" and owners_n_rules:
            assignee_email, assignee_name, assignee_rule = compute_assignee(
                title, category, topic, owners_cfg
            )

        link_pairs = []
        for o in ocv_links:
            rec = dash_index.get(o) or {}
            link_pairs.append({
                "ocv": o,
                "dash": rec.get("dash_url", ""),
                "models_label": rec.get("models_label", ""),
                "path_slug": rec.get("path_slug", ""),
                "resolved_models": rec.get("resolved_models", []),
            })

        proposals.append({
            "subtopic_key": subtopic_key(r),
            "title": title,
            "issue_description": desc,
            "priority": priority,
            "topic": topic,
            "category": category,
            "submode": submode,
            "count": count,
            "ocv_links": ocv_links,
            "dash_links": dash_links,
            "link_pairs": link_pairs,
            "verbs": sorted(extract_verbs(sub_title_tokens)),
            "prior_ado": ({"url": prior_ado_url, "action": prior_ado_action}
                          if prior_ado_url else None),
            "candidates": candidates,
            "decision": {
                "action": suggested,
                "match_id": match_id,
                "new_title": title,
                "new_description": desc,
                "notes": notes,
                "assignee": assignee_email,
                "assignee_name": assignee_name,
                "assignee_rule": assignee_rule,
            },
        })

    out_path = args.out or default_proposals_path(args.subtopics)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "subtopics_csv": os.path.abspath(args.subtopics),
            "dash_csv": os.path.abspath(args.dash_csv) if args.dash_csv else None,
            "area_path": area_path,
            "iteration": args.iteration,
            "tag": tag,
            "work_item_type": args.work_item_type,
            "proposals": proposals,
        }, f, indent=2, ensure_ascii=False)
    n_link = sum(1 for p in proposals if p['decision']['action'] == 'link')
    n_create = sum(1 for p in proposals if p['decision']['action'] == 'create')
    n_skip_prior = sum(1 for p in proposals
                       if p['decision']['action'] == 'skip' and p.get('prior_ado'))
    n_with_dash = sum(1 for p in proposals if p['dash_links'])
    print(f"[ado-sync] wrote {out_path}")
    print(f"[ado-sync] {len(proposals)} proposals: "
          f"{n_link} link, {n_create} create, {n_skip_prior} already-synced "
          f"(default=skip); {n_with_dash} have linked Dash tickets")

    # Pre-populate the Dash links / Resolved Models / Path columns on the
    # subtopics CSV so the HTML report can render them even before `execute`
    # runs. This is a read-only update — we never touch the ADO URL / action
    # columns from propose.
    if args.dash_csv and n_with_dash > 0:
        dash_link_map, models_map, path_map = _collect_dash_writeback_maps(proposals)
        try:
            update_subtopics_csv(args.subtopics, results={},
                                 dash_link_map=dash_link_map,
                                 models_map=models_map,
                                 path_map=path_map)
            print(f"[ado-sync] wrote Dash links / Resolved Models / Path columns "
                  f"into {args.subtopics} ({n_with_dash} row(s) populated)")
        except PermissionError as e:
            print(f"[ado-sync] WARN: could not update Dash links column "
                  f"(close the CSV in Excel and re-run): {e}")

    print("[ado-sync] REQUIRED: the calling agent must review each proposal's "
          "`candidates` list semantically before accepting the default action. "
          "The score is a hint, not a verdict — a 0.15 candidate with the right "
          "topic + surface is a better link than a 0.30 candidate that just "
          "shares stopwords.")


def default_proposals_path(subtopics_path: str) -> str:
    base = os.path.basename(subtopics_path).replace("_subtopics.csv", "")
    return os.path.join("data", f"ado_proposals_{base}.json")


# ---------------------------------------------------------------------------
# Phase 2: EXECUTE — apply the decisions to ADO + write back to subtopics CSV
# ---------------------------------------------------------------------------

def build_create_payload(p: Dict[str, Any], area_path: str, iteration: str,
                         tag: Optional[str]) -> List[Dict[str, Any]]:
    decision = p["decision"]
    title = decision.get("new_title") or p["title"]
    desc = decision.get("new_description") or p["issue_description"]
    priority = PRIORITY_MAP.get(p["priority"], 3)
    link_pairs, dash_orphans = _resolve_link_pairs(p)
    body_html = (
        f'<p>{esc(desc)}</p>'
        f'<p><strong>{sync_date_str()}</strong></p>'
        f'{format_link_list(link_pairs, dash_orphans)}'
    )
    ops = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
        {"op": "add", "path": "/fields/System.IterationPath", "value": iteration},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.ReproSteps", "value": body_html},
        {"op": "add", "path": "/fields/System.Description", "value": body_html},
    ]
    if tag:
        ops.append({"op": "add", "path": "/fields/System.Tags", "value": tag})
    assignee = decision.get("assignee")
    if assignee:
        ops.append({"op": "add", "path": "/fields/System.AssignedTo",
                    "value": assignee})
    return ops


def build_append_payload(existing: Dict[str, Any], p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Append OCV + Dash links to an existing item:
    - Patches System.Description so the links are visible in the body
      (the most-glanced field), not buried in Discussion.
    - Also writes a System.History note for an audit trail in Discussion.
    """
    link_pairs, dash_orphans = _resolve_link_pairs(p)
    n_ocv = sum(1 for lp in link_pairs if lp.get("ocv"))
    n_dash = sum(1 for lp in link_pairs if lp.get("dash")) + len(dash_orphans)

    appended_section = (
        f'<p><strong>{sync_date_str()}</strong></p>'
        f'<p>{esc(p["issue_description"])}</p>'
        f'{format_link_list(link_pairs, dash_orphans)}'
    )
    current_desc = (existing.get("fields", {}) or {}).get("System.Description") or ""
    new_desc = current_desc + appended_section

    history_html = (
        f'<p><strong>OCV weekly sync</strong> &mdash; appended {n_ocv} '
        f'OCV item(s)'
        + (f' and {n_dash} Dash ticket(s)' if n_dash else '')
        + f' to the Description.</p>'
    )
    return [
        {"op": "add", "path": "/fields/System.Description", "value": new_desc},
        {"op": "add", "path": "/fields/System.History", "value": history_html},
    ]


def esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def sync_date_str() -> str:
    """Date stamp printed at the top of each batch of links written to ADO."""
    return datetime.now().strftime("%Y-%m-%d")


def _resolve_link_pairs(p: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Return (link_pairs, dash_orphans) for this proposal.

    Modern proposals carry `link_pairs` directly (each pair may include
    `models_label` / `path_slug` / `resolved_models`). Older proposals
    (written before that field existed) only have flat `ocv_links` +
    `dash_links`; in that case we expose every OCV as a pair (no Dash,
    no models), and pass every Dash through as an orphan so no data is
    lost. That keeps `execute` re-runnable against proposal JSONs from
    any prior version.
    """
    pairs = p.get("link_pairs")
    if pairs:
        return pairs, []
    fallback_pairs = [{"ocv": o, "dash": "", "models_label": ""}
                      for o in p.get("ocv_links", []) or []]
    return fallback_pairs, list(p.get("dash_links", []) or [])


def _collect_dash_writeback_maps(
    proposals: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, List[str]]]:
    """Build the three parallel per-row maps used by update_subtopics_csv.

    All three arrays are parallel-aligned by index: arr[i] describes the
    i-th Dash link of the row. After ``" | ".join(...)`` they round-trip
    cleanly into the CSV cells and the HTML report can split them back.

        dash_links[key][i]  = "<dash_url>"
        models_map[key][i]  = "<model>;<model>;..."   (";"-joined for that Dash)
        path_slugs[key][i]  = "<path slug>"           ("" if unknown)

    Modern proposals carry per-pair metadata in `link_pairs`; legacy
    proposals fall back to dash_links only (no models / path info).
    """
    dash_links: Dict[str, List[str]] = {}
    models_map: Dict[str, List[str]] = {}
    path_map: Dict[str, List[str]] = {}
    for p in proposals:
        key = p["subtopic_key"]
        pairs = p.get("link_pairs") or []
        per_dash: List[str] = []
        per_models: List[str] = []
        per_path: List[str] = []
        seen: set = set()
        for pair in pairs:
            d = (pair.get("dash") or "").strip()
            if not d or d in seen:
                continue
            seen.add(d)
            per_dash.append(d)
            per_models.append(";".join(pair.get("resolved_models", []) or []))
            per_path.append((pair.get("path_slug") or "").strip())
        if not per_dash and p.get("dash_links"):
            for d in p["dash_links"]:
                per_dash.append(d)
                per_models.append("")
                per_path.append("")
        if per_dash:
            dash_links[key] = per_dash
            models_map[key] = per_models
            path_map[key] = per_path
    return dash_links, models_map, path_map


def format_link_list(link_pairs: List[Dict[str, Any]],
                     dash_orphans: Optional[List[Any]] = None) -> str:
    """Render link rows for an ADO work item description:
      - <ocv> - <dash> [PathSlug: model, model, ...]    when both are present
      - <ocv>                                            when only OCV is present
      - <dash> [PathSlug: model, ...]                    when only Dash is present (orphan)
    The "[PathSlug: model, ...]" suffix is appended after the Dash URL whenever
    the row carries resolved-model info (from the dash_ocv CSV's Resolved
    Models / Path columns).
    No section headers; the caller is responsible for the date stamp.
    """
    def _models_suffix(label: str) -> str:
        if not label:
            return ""
        return f' <span style="color:#888">[{esc(label)}]</span>'

    items: List[str] = []
    for pair in link_pairs or []:
        ocv = (pair.get("ocv") or "").strip()
        dash = (pair.get("dash") or "").strip()
        suffix = _models_suffix((pair.get("models_label") or "").strip())
        if ocv and dash:
            items.append(
                f'<li><a href="{esc(ocv)}">{esc(ocv)}</a> - '
                f'<a href="{esc(dash)}">{esc(dash)}</a>{suffix}</li>'
            )
        elif ocv:
            items.append(f'<li><a href="{esc(ocv)}">{esc(ocv)}</a></li>')
        elif dash:
            items.append(f'<li><a href="{esc(dash)}">{esc(dash)}</a>{suffix}</li>')
    for orphan in (dash_orphans or []):
        # Orphans can be plain URL strings (legacy proposals) or dicts that
        # carry a pre-formatted models_label (modern proposals).
        if isinstance(orphan, dict):
            d = (orphan.get("dash") or orphan.get("dash_url") or "").strip()
            suffix = _models_suffix((orphan.get("models_label") or "").strip())
        else:
            d = (orphan or "").strip()
            suffix = ""
        if d:
            items.append(f'<li><a href="{esc(d)}">{esc(d)}</a>{suffix}</li>')
    if not items:
        return ""
    return "<ul>" + "".join(items) + "</ul>"


def execute(args: argparse.Namespace) -> None:
    with open(args.proposals, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    subtopics_path = manifest["subtopics_csv"]
    area_path = manifest.get("area_path", DEFAULT_AREA_PATH)
    iteration = manifest.get("iteration", DEFAULT_ITERATION)
    tag = manifest.get("tag", DEFAULT_TAG)
    wi_type = manifest.get("work_item_type", DEFAULT_WORK_ITEM_TYPE)

    proposals = manifest.get("proposals", [])
    if not proposals:
        sys.exit("No proposals in JSON.")

    # Pull any prior results from previous run (resume / retry-csv-update path).
    prior_results = manifest.get("execute_results", {}) or {}

    # ------------------------------------------------------------------
    # GATE B — final write-confirmation summary.
    #
    # Before touching ADO, print a precise count of what WILL happen and
    # require explicit confirmation. The agent must surface this same
    # information to the user via ask_user; --yes is reserved for
    # automated re-runs after Gate B has already been cleared by a human.
    # ------------------------------------------------------------------
    pending_create = []
    pending_link = []
    pending_skip = []
    for p in proposals:
        key = p["subtopic_key"]
        if key in prior_results:
            continue  # already done — execute() will short-circuit below
        action = (p.get("decision", {}).get("action") or "skip").lower()
        if action == "create":
            pending_create.append(p)
        elif action == "link":
            pending_link.append(p)
        else:
            pending_skip.append(p)

    n_writes = len(pending_create) + len(pending_link)

    print()
    print("=" * 62)
    print("ADO EXECUTE PLAN  (Gate B - final confirmation)")
    print("=" * 62)
    print(f"  CREATE new {wi_type:<8s} : {len(pending_create):3d}")
    print(f"  LINK to existing item  : {len(pending_link):3d}")
    print(f"  SKIP (no-op)           : {len(pending_skip):3d}")
    if prior_results:
        print(f"  ALREADY DONE (prior)   : {len(prior_results):3d}  (will not re-call ADO)")
    print("-" * 62)
    print(f"  TOTAL ADO writes       : {n_writes}")
    print(f"  Area Path  : {area_path}")
    print(f"  Iteration  : {iteration}")
    print(f"  Tag        : {tag!r}")
    print("=" * 62)

    if pending_create:
        print()
        print(f"Tickets that will be CREATED ({len(pending_create)}):")
        for p in pending_create:
            assn = (p.get("decision") or {}).get("assignee") or ""
            assn_label = f"  -> {assn}" if assn else "  -> (unassigned)"
            print(f"  [{p.get('priority','?'):3s}] count={p.get('count',0):2d}  "
                  f"{p['title'][:60]}{assn_label}")
    if pending_link:
        print()
        print(f"Existing items that will be APPENDED to ({len(pending_link)}):")
        for p in pending_link:
            mid = p.get("decision", {}).get("match_id")
            cand = next((c for c in p.get("candidates", []) if c.get("id") == mid), None)
            cand_title = (cand or {}).get("title", "?")[:55]
            print(f"  [{p.get('priority','?'):3s}] #{mid}  ({cand_title})  <- {p['title'][:50]}")

    if n_writes == 0 and not args.retry_csv_update:
        print()
        print("[execute] Nothing to write. Exiting.")
        return

    if args.dry_run:
        print()
        print("[execute] --dry-run set; NOT calling ADO. Plan above is what would happen.")
        return

    if not args.yes:
        print()
        try:
            answer = input(
                f"Type 'yes' to proceed with {n_writes} ADO write(s), anything else to abort: "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("[execute] aborted by user (Gate B not cleared).")
            return
        print("[execute] Gate B cleared. Proceeding with ADO writes.")
    else:
        print("[execute] --yes set; bypassing interactive Gate B "
              "(the agent must have shown the plan to the user already).")

    results: Dict[str, Dict[str, str]] = dict(prior_results)
    created = 0
    linked = 0
    skipped = 0
    already_done = 0

    def persist_results() -> None:
        """Always write execute_results back to the proposals JSON so an
        interrupted run can be resumed without re-hitting ADO."""
        manifest["execute_results"] = results
        with open(args.proposals, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    for p in proposals:
        decision = p.get("decision", {})
        action = (decision.get("action") or "skip").lower()
        key = p["subtopic_key"]

        # Idempotency: if this row already has a recorded result, skip ADO call.
        if key in prior_results and not args.retry_csv_update:
            already_done += 1
            print(f"[done] {prior_results[key].get('action','?'):7} {prior_results[key].get('url','')}")
            continue

        if args.retry_csv_update:
            # In retry mode, do not touch ADO; just rebuild the CSV from JSON.
            continue

        if action == "skip":
            print(f"[skip] {p['title'][:70]}")
            skipped += 1
            continue

        try:
            if action == "link":
                match_id = decision.get("match_id")
                if not match_id:
                    print(f"[warn] link requested but no match_id for: {p['title']}")
                    skipped += 1
                    continue
                get_url = f"{API_BASE}/wit/workitems/{match_id}?api-version={API_VERSION}"
                existing = ado_request("GET", get_url)
                patch = build_append_payload(existing, p)
                patch_url = f"{API_BASE}/wit/workitems/{match_id}?api-version={API_VERSION}"
                ado_request("PATCH", patch_url, patch,
                            content_type="application/json-patch+json")
                url = web_url(match_id)
                results[key] = {"url": url, "action": "linked"}
                linked += 1
                print(f"[link] #{match_id}  ←  {p['title'][:60]}")
                persist_results()
                continue

            if action == "create":
                payload = build_create_payload(p, area_path, iteration, tag)
                create_url = (f"{API_BASE}/wit/workitems/"
                              f"{urllib.parse.quote('$' + wi_type)}"
                              f"?api-version={API_VERSION}")
                new_item = ado_request("POST", create_url, payload,
                                       content_type="application/json-patch+json")
                new_id = new_item.get("id")
                url = web_url(new_id)
                results[key] = {"url": url, "action": "created"}
                created += 1
                print(f"[new ] #{new_id}  ←  {p['title'][:60]}")
                persist_results()
                continue
        except Exception as e:
            persist_results()
            print(f"[FAIL] {p['title'][:60]}: {e}")
            print(f"[FAIL] partial results saved to {args.proposals}; re-run execute to resume")
            raise

        print(f"[warn] unknown action {action!r} for: {p['title']}")
        skipped += 1

    # Write results back to subtopics CSV — atomic via temp file + rename.
    # Also collect dash_links / models / path per row to write those columns.
    dash_link_map, models_map, path_map = _collect_dash_writeback_maps(proposals)

    try:
        update_subtopics_csv(subtopics_path, results, dash_link_map,
                             models_map=models_map, path_map=path_map)
        print(f"\n[ado-sync] updated {subtopics_path}")
    except PermissionError as e:
        print(f"\n[ado-sync] CSV write failed: {e}")
        print(f"[ado-sync] ADO actions completed successfully and are saved in {args.proposals}")
        print(f"[ado-sync] Close the CSV (likely open in Excel) and re-run:")
        print(f"           python scripts/ado_sync.py execute --proposals {args.proposals} --retry-csv-update")
        sys.exit(2)

    print(f"\n[ado-sync] done: {created} created, {linked} linked, "
          f"{skipped} skipped, {already_done} already done")
    print("[ado-sync] re-run ocv-publish-report to render ADO links in the dashboard.")


def update_subtopics_csv(path: str, results: Dict[str, Dict[str, str]],
                         dash_link_map: Optional[Dict[str, List[str]]] = None,
                         models_map: Optional[Dict[str, List[str]]] = None,
                         path_map: Optional[Dict[str, List[str]]] = None) -> None:
    rows, fieldnames = load_subtopics(path)
    for col in ("ADO URL", "ADO action", "Dash links", "Resolved Models", "Path"):
        if col not in fieldnames:
            fieldnames.append(col)

    dash_link_map = dash_link_map or {}
    models_map = models_map or {}
    path_map = path_map or {}
    for r in rows:
        key = subtopic_key(r)
        if key in results:
            r["ADO URL"] = results[key]["url"]
            r["ADO action"] = results[key]["action"]
        else:
            r.setdefault("ADO URL", r.get("ADO URL", ""))
            r.setdefault("ADO action", r.get("ADO action", ""))
        if key in dash_link_map:
            r["Dash links"] = " | ".join(dash_link_map[key])
        else:
            r.setdefault("Dash links", r.get("Dash links", ""))
        if key in models_map:
            r["Resolved Models"] = " | ".join(models_map[key])
        else:
            r.setdefault("Resolved Models", r.get("Resolved Models", ""))
        if key in path_map:
            r["Path"] = " | ".join(path_map[key])
        else:
            r.setdefault("Path", r.get("Path", ""))

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Phase 3 (separate): retroactively ASSIGN owners to already-created items
# ---------------------------------------------------------------------------

def _ado_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/edit/(\d+)", url) or re.search(r"/(\d+)$", url)
    return m.group(1) if m else None


def assign_owners(args: argparse.Namespace) -> None:
    rows, _ = load_subtopics(args.subtopics)
    if not rows:
        sys.exit("No rows in subtopics CSV.")

    owners_cfg = load_owner_config(args.owners_config)
    if not owners_cfg.get("rules"):
        print(f"[assign-owners] WARN: owners config has no rules "
              f"({args.owners_config}); only --override entries will be used.")
    overrides = parse_assignee_overrides(args.override)

    created_rows = []
    for r in rows:
        action = (r.get("ADO action") or "").strip().lower()
        url = (r.get("ADO URL") or "").strip()
        if action != "created" or not url:
            continue
        aid = _ado_id_from_url(url)
        if not aid:
            continue
        title = (r.get("Brief title") or r.get("Brief Title")
                 or r.get("Issue description") or "").strip()
        category = r.get("Category", "")
        topic = r.get("Parent Topic", "")
        # Compute base assignee from rules
        email, name, label = compute_assignee(title, category, topic, owners_cfg)
        # Per-ID override wins
        if aid in overrides:
            email = overrides[aid]
            name = email.split("@", 1)[0]
            label = "override"
        created_rows.append({
            "aid": aid, "url": url, "title": title,
            "priority": r.get("Priority", ""), "category": category,
            "email": email, "name": name, "rule": label,
        })

    if not created_rows:
        print("[assign-owners] no 'created' rows in subtopics CSV; nothing to do.")
        return

    mapped = [r for r in created_rows if r["email"]]
    unmapped = [r for r in created_rows if not r["email"]]

    print()
    print("=" * 62)
    print("ASSIGN-OWNERS PLAN")
    print("=" * 62)
    print(f"  created bugs in CSV    : {len(created_rows)}")
    print(f"  with computed owner    : {len(mapped)}")
    print(f"  unmapped (no rule, no override) : {len(unmapped)}")
    print(f"  owners config          : {args.owners_config}")
    if overrides:
        print(f"  overrides              : {len(overrides)} "
              f"({', '.join(f'#{k}->{v}' for k,v in overrides.items())})")
    print(f"  reassign existing      : {args.reassign}")
    print("=" * 62)
    print()
    print("Plan (per item):")
    from collections import Counter
    by_owner = Counter(r["email"] for r in mapped)
    for r in mapped:
        print(f"  #{r['aid']} [{r['priority']:<3s}] {r['category']:<14s} "
              f"-> {r['email']:<28s} ({r['name']})  "
              f"{r['title'][:55]}")
    if unmapped:
        print()
        print(f"Unmapped (will be SKIPPED, no patch):")
        for r in unmapped:
            print(f"  #{r['aid']} [{r['priority']:<3s}] {r['category']:<14s}  "
                  f"{r['title'][:65]}")
    print()
    print("Per-owner totals:")
    for em, n in sorted(by_owner.items(), key=lambda kv: -kv[1]):
        print(f"  {em:<32s} {n} item(s)")
    print()

    if not mapped:
        print("[assign-owners] nothing mappable. Exiting.")
        return

    if args.dry_run:
        print("[assign-owners] --dry-run set; NOT calling ADO.")
        return

    if not args.yes:
        try:
            answer = input(
                f"Type 'yes' to PATCH AssignedTo on {len(mapped)} item(s), "
                f"anything else to abort: "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("[assign-owners] aborted by user.")
            return
    else:
        print("[assign-owners] --yes set; bypassing interactive gate "
              "(the agent must have shown the plan to the user already).")

    patched = 0
    skipped_already = 0
    failed = 0
    for r in mapped:
        aid = r["aid"]
        email = r["email"]
        try:
            # Fetch current state to log existing assignee + avoid stomp
            get_url = (f"{API_BASE}/wit/workitems/{aid}"
                       f"?$fields=System.AssignedTo,System.Title"
                       f"&api-version={API_VERSION}")
            existing = ado_request("GET", get_url)
            cur = (existing.get("fields", {}).get("System.AssignedTo") or {})
            cur_email = cur.get("uniqueName") if isinstance(cur, dict) else None
            if cur_email and not args.reassign:
                print(f"[skip] #{aid} already assigned to {cur_email} "
                      f"(use --reassign to overwrite)")
                skipped_already += 1
                continue
            patch_url = (f"{API_BASE}/wit/workitems/{aid}"
                         f"?api-version={API_VERSION}")
            patch = [
                {"op": "add", "path": "/fields/System.AssignedTo",
                 "value": email},
                {"op": "add", "path": "/fields/System.History",
                 "value": (f"<p><em>Auto-assigned to {esc(email)} by "
                           f"ocv-extraction/ocv-ticket-sync owner-routing "
                           f"(rule: {esc(r.get('rule') or 'default')}).</em></p>")},
            ]
            ado_request("PATCH", patch_url, patch,
                        content_type="application/json-patch+json")
            print(f"[assigned] #{aid} -> {email}  ({r['title'][:50]})")
            patched += 1
        except Exception as e:
            print(f"[FAIL] #{aid}: {e}")
            failed += 1

    print()
    print(f"[assign-owners] done: {patched} patched, "
          f"{skipped_already} skipped (already assigned), {failed} failed.")
    if unmapped:
        print(f"[assign-owners] {len(unmapped)} unmapped item(s) left "
              "unassigned — assign manually in ADO or extend the owners "
              "config and re-run.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync OCV subtopics with Azure DevOps work items.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Phase 1: propose match-or-create for each P0/P1/P2 row
  python scripts/ado_sync.py propose \\
      --subtopics data/ocv_outlook-agent_2026-05-18_subtopics.csv

  # (Review the JSON, edit decision.action / match_id per row)

  # Phase 2: execute the decisions and write ADO URLs back into the CSV
  python scripts/ado_sync.py execute \\
      --proposals data/ado_proposals_ocv_outlook-agent_2026-05-18.json
        """,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prop = sub.add_parser("propose", help="Phase 1: find candidate matches")
    p_prop.add_argument("--subtopics", required=True,
                        help="Path to ocv-analyze-and-ticket subtopics CSV")
    p_prop.add_argument("--out", default=None,
                        help="Proposals JSON output path (default: data/ado_proposals_<base>.json)")
    p_prop.add_argument("--area-path", default=DEFAULT_AREA_PATH,
                        help=f"ADO Area Path to search under (default: {DEFAULT_AREA_PATH!r})")
    p_prop.add_argument("--iteration", default=DEFAULT_ITERATION,
                        help=f"Iteration Path for NEW items (default: {DEFAULT_ITERATION!r})")
    p_prop.add_argument("--tag", default=DEFAULT_TAG,
                        help=f"Tag to add to NEW items and filter candidates by (default: {DEFAULT_TAG!r}; pass '' to disable filter)")
    p_prop.add_argument("--work-item-type", default=DEFAULT_WORK_ITEM_TYPE,
                        help=f"ADO work item type for NEW items (default: {DEFAULT_WORK_ITEM_TYPE!r})")
    p_prop.add_argument("--dash-csv", default=None,
                        help="Optional dash_ocv CSV (from ocv-extract-dash) used to "
                             "join each OCV item to its Copilot Dash ticket; Dash links "
                             "are included in the ADO body and written back to the "
                             "subtopics CSV.")
    p_prop.add_argument("--owners-config", default=DEFAULT_OWNERS_CONFIG,
                        help=f"Owners JSON config (rules mapping subtopic -> ADO "
                             f"assignee email). Default: {DEFAULT_OWNERS_CONFIG}. "
                             f"Pass '' to disable auto-assignment.")
    p_prop.set_defaults(func=propose)

    p_exec = sub.add_parser("execute", help="Phase 2: apply the proposals")
    p_exec.add_argument("--proposals", required=True,
                        help="Proposals JSON from a previous `propose` run")
    p_exec.add_argument("--retry-csv-update", action="store_true",
                        help="Skip all ADO calls; only re-apply execute_results to the subtopics CSV "
                             "(use after fixing a file-lock or permission error)")
    p_exec.add_argument("--dry-run", action="store_true",
                        help="Print the Gate B plan and exit; never touch ADO.")
    p_exec.add_argument("--yes", action="store_true",
                        help="Bypass the interactive Gate B confirmation. ONLY for "
                             "automated re-runs after a human has already approved "
                             "the plan; the agent must never pass --yes without "
                             "first showing the user the create/link counts.")
    p_exec.set_defaults(func=execute)

    p_assn = sub.add_parser(
        "assign-owners",
        help="Retroactively assign owners to already-created ADO items "
             "listed in the subtopics CSV (rows where 'ADO action' = "
             "'created')."
    )
    p_assn.add_argument("--subtopics", required=True,
                        help="Path to subtopics CSV with populated 'ADO URL' / "
                             "'ADO action' columns (from `execute`).")
    p_assn.add_argument("--owners-config", default=DEFAULT_OWNERS_CONFIG,
                        help=f"Owners JSON config. Default: {DEFAULT_OWNERS_CONFIG}.")
    p_assn.add_argument("--override", action="append", default=None,
                        metavar="ID=email",
                        help="Force a specific ADO item to a specific assignee email "
                             "(repeatable). Example: --override 432566=user@microsoft.com")
    p_assn.add_argument("--include-unmapped", action="store_true",
                        help="By default rows that match no owner rule and have "
                             "no override are skipped. Set this to fail-loud "
                             "instead (still won't patch them; just lists them).")
    p_assn.add_argument("--reassign", action="store_true",
                        help="By default, items that already have an assignee on "
                             "ADO are left alone. Set this to overwrite existing "
                             "assignees with the computed owner.")
    p_assn.add_argument("--dry-run", action="store_true",
                        help="Print the plan and exit; never touch ADO.")
    p_assn.add_argument("--yes", action="store_true",
                        help="Bypass the interactive confirmation gate. The agent "
                             "must never pass --yes without first showing the user "
                             "the per-row assignment plan.")
    p_assn.set_defaults(func=assign_owners)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

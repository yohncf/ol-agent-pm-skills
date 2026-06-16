"""Build subtopics CSV + manifest JSON + report MD from classifications.

Reads four JSON inputs produced by the classification pass:
    data/_classifications_<week>.json
    data/_negatives_<week>.json
    data/_aggregates_<week>.json
    data/_descriptions_<week>.json
and emits:
    data/ocv_<area>_<week>_subtopics.csv
    data/manifests/ocv_<area>_<week>_manifest.json
    data/ocv_<area>_<week>_report.md
"""
import argparse, json, csv, os, codecs
from collections import Counter, defaultdict
from datetime import date

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--week', required=True, help='YYYY-MM-DD (end of week)')
parser.add_argument('--area', default='outlook-agent')
parser.add_argument('--date-range-human', required=True,
                    help='Human date range string, e.g. "May 18-24, 2026"')
parser.add_argument('--source-csv', required=True,
                    help='Path to the source range CSV (relative to repo root)')
parser.add_argument('--prior-manifest', default=None,
                    help='Optional prior manifest for WoW; omit if no prior week')
parser.add_argument('--dash-csv', default=None,
                    help='Optional dash_ocv CSV for joining Dash links')
parser.add_argument('--taxonomy-version', default='13-topic-2026-05-19')
args = parser.parse_args()

WEEK = args.week
AREA = args.area
DATE_RANGE_HUMAN = args.date_range_human
SOURCE_CSV = args.source_csv
PRIOR_MANIFEST = args.prior_manifest
TAXONOMY_VERSION = args.taxonomy_version
DASH_CSV = args.dash_csv or f'data/dash_ocv_{WEEK}.csv'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# --- Load inputs ---
with open(f'data/_classifications_{WEEK}.json', 'r', encoding='utf-8') as f:
    classifications = json.load(f)
with open(f'data/_negatives_{WEEK}.json', 'r', encoding='utf-8') as f:
    negatives = json.load(f)
with open(f'data/_aggregates_{WEEK}.json', 'r', encoding='utf-8') as f:
    aggregates = json.load(f)
with open(f'data/_descriptions_{WEEK}.json', 'r', encoding='utf-8') as f:
    descriptions = json.load(f)

prior = {}
if PRIOR_MANIFEST and os.path.exists(PRIOR_MANIFEST):
    with open(PRIOR_MANIFEST, 'r', encoding='utf-8') as f:
        prior = json.load(f)
else:
    print(f'[build] No prior manifest ({PRIOR_MANIFEST!r}); WoW table will be skipped.')

# Dash join: OcvId -> Dash ticket id + resolved models + path slug
# (used both for ticket context and for the manifest's `paths` aggregate)
dash_links = {}
dash_models = {}   # OcvId -> [model name, ...]
dash_path = {}     # OcvId -> path slug ("CodeGen-Claude", "Sydney-Tools+WorkBerry", ...)
try:
    with open(DASH_CSV, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ocv_link = (row.get('OCV ticket') or '').strip()
            dash_link = (row.get('Dash ticket') or '').strip()
            if ocv_link and dash_link:
                # OCV link format: https://ocv.microsoft.com/#/item/<OcvId>
                ocv_id = ocv_link.rsplit('/', 1)[-1]
                dash_links[ocv_id] = dash_link
                raw_models = (row.get('Resolved Models') or '').strip()
                dash_models[ocv_id] = [m.strip() for m in raw_models.split(';') if m.strip()] if raw_models else []
                dash_path[ocv_id] = (row.get('Path') or '').strip()
except FileNotFoundError:
    pass

by_id = {n['OcvId']: n for n in negatives}
TOPIC_NAMES = {
    1: 'Action not executed',
    2: 'HitL violation',
    3: "Output doesn't match intent",
    4: 'Constraints ignored',
    5: 'Unnecessary clarifying question',
    6: 'Reliability failure',
    7: 'Inaccurate or fabricated content',
    8: 'Wrong context / grounding',
    9: 'Tone, language, or format quality',
    10: 'File I/O failure',
    11: 'Calendar correctness',
    12: 'Capability refusal for in-scope request',
    13: 'Intrusive Outlook Agent UI',
}

# --- Aggregate into subtopic rows ---
clusters = defaultdict(list)
for c in classifications:
    clusters[c['subtopic_key']].append(c)

CORE_ACTION_CATS = {'Drafting', 'Replying', 'Scheduling', 'Triage'}
CORE_INFO_CATS = {'Search', 'Summarization', 'Triage'}

def priority(topic, category, count, signal_blob, submodes, ticket_worthy):
    # P0
    if topic == 2:
        return 'P0'
    if topic == 1 and ('falsely reports success' in signal_blob.lower()
                       or 'false success' in signal_blob.lower()
                       or 'false-success' in signal_blob.lower()
                       or 'reports success' in signal_blob.lower()
                       or 'claimed success' in signal_blob.lower()):
        return 'P0'
    if topic == 11 and count >= 2:
        return 'P0'
    # P1
    if count >= 4:
        return 'P1'
    if topic == 1 and category in CORE_ACTION_CATS and count >= 2:
        return 'P1'
    if topic == 6 and 'error_string' in submodes and category in CORE_INFO_CATS and count >= 2:
        return 'P1'
    if topic == 10 and count >= 2:
        return 'P1'
    # P2
    if count >= 2:
        return 'P2'
    if topic == 8:
        return 'P2'
    if topic == 4:
        return 'P2'
    return 'P3'

def aggregate_worthy(worthies):
    counts = Counter(worthies)
    # Majority wins; tie-break: Yes > Needs Triage > No
    if counts['Yes'] >= counts['Needs Triage'] and counts['Yes'] >= counts['No']:
        return 'Yes'
    if counts['Needs Triage'] >= counts['No']:
        return 'Needs Triage'
    return 'No'

rows = []
for key, items in clusters.items():
    topics = Counter(it['topic'] for it in items)
    cats = Counter(it['category'] for it in items)
    submodes = Counter(it.get('submode') for it in items if it.get('submode'))
    worthies = [it['ticket_worthy'] for it in items]
    notes_blob = ' '.join(it.get('note', '') for it in items)

    topic = topics.most_common(1)[0][0]
    category = cats.most_common(1)[0][0]
    submode = submodes.most_common(1)[0][0] if submodes else None
    count = len(items)

    signal_blob = ' '.join(it.get('note', '') for it in items) + ' ' + key
    p = priority(topic, category, count, signal_blob, set(submodes.keys()), aggregate_worthy(worthies))
    tw = aggregate_worthy(worthies)
    # Multi-item promotion: never below P2
    if count > 1 and p == 'P3':
        p = 'P2'
    # Ticket-worthy No → push to P3 unless P0/P1
    if tw == 'No' and p not in ('P0', 'P1'):
        p = 'P3'

    desc = descriptions.get(key, f'See cluster items for details (key: {key}).')
    ocv_links = [f'https://ocv.microsoft.com/#/item/{it["ocv_id"]}' for it in items[:5]]

    parent_topic = f'{topic}. {TOPIC_NAMES[topic]}'
    brief = key  # already in "<Category>: <issue>" form

    rows.append({
        'Priority': p,
        'Ticket Worthy': tw,
        'Item Count': count,
        'Parent Topic': parent_topic,
        'Category': category,
        'Brief Title': brief,
        'Issue Description': desc,
        'OCV Item Links': ';'.join(ocv_links),
        '_topic_num': topic,
        '_submode': submode,
    })

# Sort: Priority asc (P0→P3), Item Count desc, Parent Topic asc
PORDER = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
rows.sort(key=lambda r: (PORDER[r['Priority']], -r['Item Count'], r['_topic_num']))

# --- Write subtopics CSV (UTF-8 with BOM, ASCII hyphens) ---
csv_path = f'data/ocv_{AREA}_{WEEK}_subtopics.csv'
fieldnames = ['Priority', 'Ticket Worthy', 'Item Count', 'Parent Topic',
              'Category', 'Brief Title', 'Issue Description', 'OCV Item Links']

with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        out = {k: (str(r[k]).replace('\u2014', '-').replace('\u2013', '-') if isinstance(r.get(k), str) else r[k])
               for k in fieldnames}
        w.writerow(out)
print(f'Wrote {csv_path} ({len(rows)} rows)')

# --- Build manifest ---
def submode_key(topic, sub):
    return f'{topic}.{sub}' if sub else None

submode_counts = Counter()
for c in classifications:
    if c.get('submode'):
        submode_counts[f"{c['topic']}.{c['submode']}"] += 1

topic_counts = {str(t): 0 for t in range(1, 14)}
for c in classifications:
    topic_counts[str(c['topic'])] += 1
# Drop zero-count topics to keep manifest clean
topic_counts = {k: v for k, v in topic_counts.items() if v > 0}

neg = sum(topic_counts.values())
topic_pcts = {k: round(100.0 * v / neg, 1) for k, v in topic_counts.items()}

category_counts = Counter(c['category'] for c in classifications)
languages = aggregates['language']
clients = aggregates['client']
sentiment = aggregates['sentiment']
rating = aggregates['rating']

# --- Routing-path aggregation (resolved models from Dash) ---
# Per-item path attribution. "Unknown" comes from the rule engine when
# resolved models are present but no rule matches (e.g. classifier-only).
# "unknown_or_missing" counts items with no Dash record at all (no model
# info available at extraction time).
path_by_path = Counter()
path_by_path_x_surface = defaultdict(Counter)
path_unknown_or_missing = 0
for c in classifications:
    oid = c['ocv_id']
    cat = c['category']
    p = (dash_path.get(oid) or '').strip()
    if not p:
        path_unknown_or_missing += 1
        continue
    path_by_path[p] += 1
    path_by_path_x_surface[p][cat] += 1

paths_block = {
    'by_path': dict(path_by_path),
    'by_path_x_surface': {k: dict(v) for k, v in path_by_path_x_surface.items()},
    'unknown_or_missing': path_unknown_or_missing,
    'reference_version': 'scripts/agent_models_reference.json',
}

per_item = []
for c in classifications:
    per_item.append({
        'ocv_id': c['ocv_id'],
        'topic': c['topic'],
        'category': c['category'],
        'submode': c.get('submode'),
        'subtopic_key': c['subtopic_key'],
        'ticket_worthy': c['ticket_worthy'],
        'note': c.get('note', ''),
    })

manifest = {
    'schema_version': '1.0',
    'taxonomy_version': TAXONOMY_VERSION,
    'week': WEEK,
    'date_range': DATE_RANGE_HUMAN,
    'source_csv': SOURCE_CSV,
    'analysis_date': str(date.today()),
    'classification_method': 'model-assisted manual, strict priority-rule application; subtopic_keys clustered then re-merged',
    'methodology_notes': [
        'Negative items selected via Sentiment == "Negative".',
        'Each item classified into exactly one of 13 topics with Category and (where applicable) SubMode.',
        'subtopic_key produced per-item, then re-clustered to merge near-duplicate keys sharing topic+category+failure-mode.',
        'Priority follows the ocv-analyze-and-ticket doctrine: P0 trust/data-integrity; P1 pervasive core-capability failures; P2 recurring; P3 anecdotal/low-leverage.',
        'Multi-item rows (count > 1) promoted to at least P2.',
        'Ticket Worthy aggregated by majority across cluster items; tie-break: Yes > Needs Triage > No.',
    ],
    'caveats': [
        'OCV feedback is self-selected and skews toward dissatisfied users (sampling bias).',
        'Any topic or subtopic with n < 3 is anecdotal and not statistically meaningful.',
        'Negative share reflects the OCV pipeline\'s sentiment-filtered ingestion, not user satisfaction.',
    ] + ([
        f'Prior week ({prior.get("week", "n/a")}) used the same taxonomy_version ({prior.get("taxonomy_version", "n/a")}); WoW deltas are direct.'
    ] if prior else [
        'No prior week manifest available; this report is the WoW baseline (no deltas shown).'
    ]),
    'total_items': aggregates['total_rows'],
    'negative_items': neg,
    'sentiment': sentiment,
    'rating': rating,
    'languages': languages,
    'clients': clients,
    'topic_counts': topic_counts,
    'topic_percentages': topic_pcts,
    'category_counts': dict(category_counts),
    'submode_counts': dict(submode_counts),
    'paths': paths_block,
    'per_item_classifications': per_item,
    'wow_basis': ({
        'prior_manifest': PRIOR_MANIFEST,
        'prior_week': prior.get('week'),
        'prior_negative_items': prior.get('negative_items'),
        'prior_taxonomy_version': prior.get('taxonomy_version'),
    } if prior else None),
    'taxonomy_migration': prior.get('taxonomy_migration', {}),
}

manifest_path = f'data/manifests/ocv_{AREA}_{WEEK}_manifest.json'
os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(f'Wrote {manifest_path}')

# --- Build report MD ---
def pct(n, d):
    if not d:
        return '0.0%'
    return f'{100.0 * n / d:.1f}%'

prior_topic_counts = prior.get('topic_counts', {})
prior_neg = prior.get('negative_items', 0) or 0
prior_cat_counts = prior.get('category_counts', {})

def fmt_delta(this_pct, prior_pct):
    d = this_pct - prior_pct
    if abs(d) < 0.05:
        return '-'
    return f'{"▲" if d > 0 else "▼"}{abs(d):.1f}pp'

# Topic table rows
topic_rows = sorted(topic_counts.items(), key=lambda kv: -kv[1])
TOPIC_SHORT_DESC = {
    1: 'Copilot claimed success but action did not happen (drafts, calendar, rules, triage).',
    2: 'Action executed without surfacing content for user approval (HitL).',
    3: 'Generated content unrelated to user request.',
    4: 'Explicit user constraint (format, length, recipients, recurrence) violated.',
    5: 'Copilot asked a clarifying question when the prompt was already actionable.',
    6: 'System-level failure (empty / canned error / hang) on non-action requests.',
    7: 'Facts wrong or fabricated on the right source.',
    8: 'Wrong source consulted (wrong email, lost prior context, inbox-wide scan).',
    9: 'Wrong language, robotic tone, or unreadable formatting.',
    10: 'File ingestion or generation failed.',
    11: 'Suggested meeting time wrong on its face (conflict / wrong day).',
    12: 'Canned capability statement for an in-scope request.',
    13: 'Complaint about the Outlook Agent surface itself being intrusive.',
}

# Category table rows
cat_rows = sorted(category_counts.items(), key=lambda kv: -kv[1])

def top_topics_for_cat(cat):
    cts = Counter(c['topic'] for c in classifications if c['category'] == cat)
    top3 = cts.most_common(3)
    return ', '.join(f'T{t} ({n})' for t, n in top3)

# WoW for category — based on this week's vs prior's same-category share of negatives
def cat_wow(cat, this_count):
    prior_count = prior_cat_counts.get(cat, 0)
    this_pct = 100.0 * this_count / neg if neg else 0
    prior_pct = 100.0 * prior_count / prior_neg if prior_neg else 0
    return fmt_delta(this_pct, prior_pct)

p_counts = Counter(r['Priority'] for r in rows)
worthy_counts = Counter(r['Ticket Worthy'] for r in rows)

md = []
md.append(f'# OCV Outlook Agent — Weekly Report: {DATE_RANGE_HUMAN}')
md.append('')
md.append(f'**Source:** `{SOURCE_CSV}` · **Total submissions:** {aggregates["total_rows"]} (verbatim) · **Negative items analyzed:** {neg}')
md.append('')
md.append('## TL;DR')
md.append('')
md.append(f'This week, {neg} negative verbatim items were classified into the 13-topic Outlook Agent taxonomy. '
          f'The largest topic was **T{topic_rows[0][0]} ({TOPIC_NAMES[int(topic_rows[0][0])]})** at {topic_rows[0][1]} items ({topic_pcts[topic_rows[0][0]]}%), '
          f'followed by **T{topic_rows[1][0]} ({TOPIC_NAMES[int(topic_rows[1][0])]})** at {topic_rows[1][1]} ({topic_pcts[topic_rows[1][0]]}%) '
          f'and **T{topic_rows[2][0]} ({TOPIC_NAMES[int(topic_rows[2][0])]})** at {topic_rows[2][1]} ({topic_pcts[topic_rows[2][0]]}%).')
md.append('')

md.append('## Key Findings')
md.append('')
# 5 findings: top 3 topics + priority breakdown + WoW shift (largest delta)
finding_topic1 = int(topic_rows[0][0])
finding_topic2 = int(topic_rows[1][0])
finding_topic3 = int(topic_rows[2][0])
md.append(f'- **T{finding_topic1} ({TOPIC_NAMES[finding_topic1]})** is the largest topic at {topic_rows[0][1]} items ({topic_pcts[topic_rows[0][0]]}% of negatives). Failure patterns concentrate in {", ".join(c for c, _ in Counter(x["category"] for x in classifications if x["topic"]==finding_topic1).most_common(3))}.')
md.append(f'- **T{finding_topic2} ({TOPIC_NAMES[finding_topic2]})** is #2 at {topic_rows[1][1]} items ({topic_pcts[topic_rows[1][0]]}%). Often surfaces as wrong-source retrieval in search and inbox scans.')
md.append(f'- **Engineering queue shape:** {p_counts.get("P0",0)} P0 (trust / false-success), {p_counts.get("P1",0)} P1 (pervasive core capability), {p_counts.get("P2",0)} P2 (recurring), {p_counts.get("P3",0)} P3 (anecdotal). Multi-item clusters (count > 1): {sum(1 for r in rows if r["Item Count"] > 1)} of {len(rows)} subtopic rows.')
if prior:
    # Find biggest WoW topic delta
    deltas = []
    for t_str, this_count in topic_counts.items():
        prior_count = prior_topic_counts.get(t_str, 0)
        this_p = 100.0 * this_count / neg if neg else 0
        prior_p = 100.0 * prior_count / prior_neg if prior_neg else 0
        deltas.append((int(t_str), this_p - prior_p, this_count, prior_count))
    deltas.sort(key=lambda x: -abs(x[1]))
    big = deltas[0]
    arrow = '▲' if big[1] > 0 else '▼'
    md.append(f'- **Largest WoW shift:** T{big[0]} ({TOPIC_NAMES[big[0]]}) moved {arrow}{abs(big[1]):.1f}pp ({big[3]} → {big[2]} items).')
md.append(f'- **Long tail:** {sum(1 for r in rows if r["Item Count"] == 1)} subtopic rows are singletons (anecdotal per the n<3 rule); these are P3 unless promoted by topic-specific P0 criteria.')
md.append('')

# Topic shifts (merged: primary topic ranking + WoW)
md.append('## Topic shifts (week over week)')
md.append('')
if prior:
    md.append(f'Sorted by this-week volume. Prior baseline: **{prior.get("week","n/a")}** ({prior.get("date_range","")}, {prior_neg} negative items).')
    md.append('')
    md.append('| # | Topic | This Week (Count · %) | Last Week (Count · %) | Δ (pp) |')
    md.append('|---|---|---|---|---|')
    all_topics = set(topic_counts.keys()) | set(prior_topic_counts.keys())
    rows_for_md = []
    for t_str in all_topics:
        t = int(t_str)
        this_c = topic_counts.get(t_str, 0)
        prior_c = prior_topic_counts.get(t_str, 0)
        this_p = 100.0 * this_c / neg if neg else 0
        prior_p = 100.0 * prior_c / prior_neg if prior_neg else 0
        rows_for_md.append((t, this_c, this_p, prior_c, prior_p))
    rows_for_md.sort(key=lambda r: (r[1], r[3]), reverse=True)
    for t, this_c, this_p, prior_c, prior_p in rows_for_md:
        if this_c == 0 and prior_c == 0:
            continue
        md.append(f'| T{t:02d} | {TOPIC_NAMES[t]} | {this_c} · {this_p:.1f}% | {prior_c} · {prior_p:.1f}% | {fmt_delta(this_p, prior_p)} |')
    md.append('')
else:
    md.append('Sorted by this-week volume. _No prior-week manifest available; this report establishes the WoW baseline (Δ column will populate next week)._')
    md.append('')
    md.append('| # | Topic | This Week (Count · %) | Last Week (Count · %) | Δ (pp) |')
    md.append('|---|---|---|---|---|')
    for t_str, cnt in topic_rows:
        t = int(t_str)
        md.append(f'| T{t:02d} | {TOPIC_NAMES[t]} | {cnt} · {topic_pcts[t_str]}% | — | — |')
    md.append('')

# Category breakdown
md.append('## Category Breakdown')
md.append('')
md.append('| Category | Negative Items (Count · %) | Top 3 Topics | WoW Δ (pp) |')
md.append('|---|---|---|---|')
for cat, cnt in cat_rows:
    md.append(f'| {cat} | {cnt} · {pct(cnt, neg)} | {top_topics_for_cat(cat)} | {cat_wow(cat, cnt)} |')
md.append('')

md.append('## Engineering Ticket Queue')
md.append('')
md.append(f'See `{csv_path}` for the granular {len(rows)}-row subtopic CSV with Priority, Brief Title, Issue Description, and up to 5 OCV item links per row.')
md.append('')
md.append(f'- **P0 (trust / data-integrity):** {p_counts.get("P0",0)} rows')
md.append(f'- **P1 (pervasive core-capability):** {p_counts.get("P1",0)} rows')
md.append(f'- **P2 (recurring):** {p_counts.get("P2",0)} rows')
md.append(f'- **P3 (anecdotal / low-leverage):** {p_counts.get("P3",0)} rows')
md.append('')
md.append('Top 10 rows by priority then volume:')
md.append('')
md.append('| Priority | Count | Topic | Brief Title |')
md.append('|---|---|---|---|')
for r in rows[:10]:
    md.append(f'| {r["Priority"]} | {r["Item Count"]} | T{r["_topic_num"]} | {r["Brief Title"]} |')
md.append('')

report_path = f'data/ocv_{AREA}_{WEEK}_report.md'
with open(report_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))
print(f'Wrote {report_path}')
print(f'Final: {len(rows)} subtopic rows | priorities: {dict(p_counts)}')

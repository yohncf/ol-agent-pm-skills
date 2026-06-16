"""Ephemeral helper: write a compact per-item summary for classification."""
import json, re, os, sys

src = 'data/_negatives_2026-05-24.json'
dst = 'data/_negatives_compact.txt'

with open(src, 'r', encoding='utf-8') as f:
    items = json.load(f)

def truncate(s, n):
    s = (s or '').replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) <= n:
        return s
    return s[:n] + '...'

def detect_flags(item):
    r = (item.get('ResponseInEnglish') or '').strip().lower()
    p = (item.get('PromptInEnglish') or '').strip()
    flags = []
    if not r:
        flags.append('R-EMPTY')
    if not p:
        flags.append('P-EMPTY')
    if "i'm sorry, i wasn't able to process" in r:
        flags.append('ERR-IM-SORRY')
    if 'something went wrong' in r:
        flags.append('ERR-SWW')
    if 'error during execution' in r:
        flags.append('ERR-EXEC')
    if "i'm the outlook agent" in r:
        flags.append('CAP-REFUSAL')
    return ','.join(flags) if flags else ''

with open(dst, 'w', encoding='utf-8') as f:
    for i, it in enumerate(items):
        flags = detect_flags(it)
        f.write(
            f"#{i+1} OCV={it['OcvId']} {it['Date']} | Client={it['Client']} "
            f"Lang={it['Language']} Scen={it['Scenario']} "
            f"Intent={it['CopilotIntent']} Themes={truncate(it['SentimentThemes'],80)}\n"
        )
        if flags:
            f.write(f"  FLAGS: {flags}\n")
        f.write(f"  P: {truncate(it['PromptInEnglish'],400)}\n")
        f.write(f"  R: {truncate(it['ResponseInEnglish'],400)}\n")
        f.write(f"  C: {truncate(it['Comment'],400)}\n\n")

print('wrote', os.path.getsize(dst), 'bytes ->', dst)

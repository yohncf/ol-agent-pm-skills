"""Build _negatives_<week>.json + _aggregates_<week>.json from a range CSV.

These two files are deterministic (derived directly from CSV columns).
The two AI-produced files (_classifications + _descriptions) are written
separately by the classification pass.
"""
import argparse, csv, json, os, sys
from collections import Counter

p = argparse.ArgumentParser()
p.add_argument('--csv', required=True, help='Source range CSV')
p.add_argument('--week', required=True, help='YYYY-MM-DD (used in output names)')
p.add_argument('--outdir', default='data')
args = p.parse_args()

if not os.path.exists(args.csv):
    sys.exit(f'CSV not found: {args.csv}')

# Load all rows. The CSV may contain HTML-escaped text; build script handles
# that downstream. We just pass strings through.
with open(args.csv, 'r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f'Loaded {len(rows)} rows from {args.csv}')

# --- Negatives: filter to Sentiment == "Negative", keep all source columns ---
negatives = [r for r in rows if (r.get('Sentiment') or '').strip() == 'Negative']
print(f'Negative items: {len(negatives)}')

neg_path = os.path.join(args.outdir, f'_negatives_{args.week}.json')
with open(neg_path, 'w', encoding='utf-8') as f:
    json.dump(negatives, f, ensure_ascii=False, indent=2)
print(f'Wrote {neg_path}')

# --- Aggregates: counts across ALL rows (not just negatives) ---
sentiment = Counter((r.get('Sentiment') or '').strip() for r in rows)
rating    = Counter((r.get('Rating')    or '').strip() for r in rows)
language  = Counter((r.get('Language')  or '').strip() for r in rows)
client    = Counter((r.get('Client')    or '').strip() for r in rows)

aggregates = {
    'total_rows': len(rows),
    'sentiment': dict(sentiment),
    'rating':    dict(rating),
    'language':  dict(language),
    'client':    dict(client),
}

agg_path = os.path.join(args.outdir, f'_aggregates_{args.week}.json')
with open(agg_path, 'w', encoding='utf-8') as f:
    json.dump(aggregates, f, ensure_ascii=False, indent=2)
print(f'Wrote {agg_path}')
print()
print('Summary:')
print(f'  Total rows:    {aggregates["total_rows"]}')
print(f'  Sentiment:     {dict(sentiment)}')
print(f'  Rating:        {dict(rating)}')
print(f'  Top languages: {dict(language.most_common(5))}')
print(f'  Clients:       {dict(client)}')

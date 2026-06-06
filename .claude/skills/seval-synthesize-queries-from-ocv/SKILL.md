---
name: seval-synthesize-queries-from-ocv
description: >
  Synthesize evaluation queries (with assertions + segments) from
  extracted user utterances, following the org-wide eval doctrine in
  `docs/EVAL_DOCTRINE.md` so the synthesized YAML is mergeable straight
  into HeroEval. Use when the user asks to "create eval queries from
  feedback", "generate test cases from utterances", "build a Graph API
  Gaps YAML", "turn user prompts into eval queries", or provides two or
  more CSVs of feedback and asks for a YAML of generic queries + assertions.
  Reads any combination of dash-OCV CSVs ("Utterance" column) and OCV
  CSVs ("PromptInEnglish" column), clusters them into the most common
  user intents, and emits a YAML file (plus sibling TSV) with up to 10
  generic queries and frontend-evaluable assertions. Do NOT use for theme
  discovery or executive summaries from raw OCV data — use `ocv-analyze` for
  general analysis.
---

# Synthesize Evaluation Queries from User Feedback

Turn real user utterances (from Copilot Dash + OCV) into a curated YAML of
up to **10 generic evaluation queries**, each with assertions the eval
harness can score against the agent's response text, plus a sibling TSV
that pairs each query with its segment for HeroEval merge.

## Doctrine — read this first

The authoritative spec for **assertion writing** and **segment naming**
is [`docs/EVAL_DOCTRINE.md`](../../../docs/EVAL_DOCTRINE.md). Read it
before drafting. The synth helper script enforces it — malformed segments
and hard-fail anti-patterns abort the emit. Treat the doc as the spec;
this skill is just the workflow.

The doctrine is pinned to a version. The helper script and this skill
must stay in sync with `Doctrine-Version` in the doc.

## Reference format

```yaml
- id: <uuid>
  query: <generic query string>
  assertions:
  - text: <substantive outcome check>
    level: critical
  - text: <quality/scope check>
    level: expected
  - text: <conditional polish check>
    level: aspirational
```

Plus a sibling TSV row per query:

```
<Utterance>\t<Segment 2>\t<annotation>\t<query_hash>\t<user_id>\t<timestamp>
```

`query_hash` always equals the YAML `id`. The helper emits both files
from a single in-memory object so IDs can't drift.

## Parsing the user's request

Resolve:

- **inputs**: Two or more CSV paths. Typical pairing is one Copilot Dash
  CSV (`Utterance` column) + one OCV CSV (`PromptInEnglish` column),
  but the script accepts N inputs of either flavor.
- **count**: How many queries to emit. Default **10**, max **10**
  (the helper rejects more).
- **out**: Output YAML path. If the user doesn't specify, default to
  `data/eval_queries_<YYYY-MM-DD>.yaml`. Sibling TSV is auto-created at
  the same basename with `.tsv` extension.

If only one CSV is provided, that's fine — work with what you have, but
note the smaller signal in the final summary.

## What to do

### 1. Locate the project root

This skill lives in `ocv-extraction/`. All paths are relative to that
directory unless absolute. Run scripts from there.

### 2. Dump and dedupe utterances (mechanical — helper script)

```
python scripts/synthesize_eval_queries.py dump <csv1> <csv2> [...]
```

The helper:

- Auto-detects the utterance column per file (`Utterance` or
  `PromptInEnglish` — case-insensitive). Use `--column <name>` to override.
- HTML-unescapes, trims, and dedupes near-duplicates by lowercase /
  whitespace / trailing-punctuation key.
- Writes `data/.synth_input_<timestamp>.json` containing the unified,
  numbered list:
  ```json
  {
    "generated_at": "...",
    "inputs": [{"file": ..., "rows_with_text": N}, ...],
    "total_raw": <int>,
    "total_unique": <int>,
    "utterances": [
      {"id": 1, "text": "...", "occurrences": 1,
       "sources": [{"file": "...", "column": "...", "row": 42}]},
      ...
    ]
  }
  ```

Read this file before proceeding.

### 3. Cluster utterances into intent buckets (LLM — you)

Read the deduped list and group utterances by **intent**, not by surface
wording. For each cluster, capture:

- **Intent label** (3–6 words, e.g., "Summarize emails about a topic",
  "Find emails with attachments from a person").
- **Cluster size** (sum of `occurrences` across grouped items).
- **2–4 representative utterance IDs** for traceability.
- **Specifics observed** — recurring constraints in user phrasing
  (date windows, sender names, formats requested, languages used).

Then rank clusters by size and select the top ones — typically the top
**8–10** that have meaningful volume and are distinct intents
(don't pick two clusters that overlap heavily).

**Drop or merge** clusters that:

- Represent noise (single-word complaints, non-actionable verbatim).
- Are duplicates of an intent already covered by another cluster.
- Are too narrow to generalize (one user's very specific one-off).

### 4. Draft a generic query per selected cluster

The query is what the agent will execute during evaluation. It must be
runnable against **any inbox**, so:

**Do:**
- Use placeholders that refer to plausible inbox content
  ("emails from my manager", "this email", "the thread about Q2 budget").
- Preserve structural constraints from the cluster
  (number of bullets, target language, specific output format) — the
  assertion will check these.
- Keep queries natural and one-sentence — they mirror how real users
  ask, not how a test author would phrase a spec.

**Don't:**
- Hard-code specific dates (`May 18 2026` ❌ → "from last week" ✅).
- Hard-code person names that wouldn't exist in any inbox.
- Hard-code counts unless the user intent itself is "give me N of X".
- Reproduce verbatim a complaint from the feedback.

### 5. Assign a segment per query

Pick from the known segments in `docs/EVAL_DOCTRINE.md` → "Known
segments" (35 as of doctrine v2026-06-04). The most common ones:

| Segment | When to use |
|---|---|
| `Search` | Looking up emails by sender/subject/attachment/etc. |
| `Draft` | Composing a new email (any kind) |
| `Reply` | Replying to an existing email |
| `Summarize` | Summarizing emails (no draft step) |
| `Triage` | Multi-action inbox cleanup ("catch me up", "clear my inbox") |
| `Rules` | Inbox-rule CRUD |
| `Folders` | Folder CRUD |
| `Categorize` | Category CRUD |
| `Calendar_OOO` | Auto-replies (with or without secondary actions) |
| `Meetings_Prep` | Meeting prep (briefings, recent threads pull) |
| `Tasks_Create` / `Tasks_Update` | Outlook Tasks |
| `Contacts_Create` / `Contacts_Update` | Contacts |

**Rules** (full text in the doctrine doc):
- Max 2 tokens joined by `_` in CapCase.
- Drop `Inbox_` prefix — it's intrinsic.
- Keep domain prefix for `Calendar_*`, `Meetings_*`, `Tasks_*`, `Contacts_*`.
- For 2-tool combos (rare), use CapCase joined: `Pin_Flag`, `Rules_Folders`.
- Don't reuse `Inbox_noSend` — that's a HeroEval V2.6 test marker, not a real segment.

**If no known segment fits**, propose a new one that matches the shape
`^[A-Z][A-Za-z]+(_[A-Za-z]+)?$` and surface it to the user for
validation. The helper will warn but allow new segments through.

### 6. Draft 1–4 assertions per query — following doctrine

Calibrate the number and strictness to the query's specificity:

| Query type | Typical assertion count | Distribution |
|---|---|---|
| Vague / open-ended ("Catch me up on emails") | 2 | 1 critical (substantive), 1 expected |
| Structured request (filters, time windows, formats) | 3 | 1 critical (filter applied correctly), 1 expected (scope), 1 aspirational (format) |
| Multi-step / multi-output ("find X and draft Y") | 3–4 | 1 critical per step + 1 expected |

**Hard rules** (helper will reject if violated):

1. **Use the formula** — Every assertion starts with `The response ...`,
   `The <tool> call ...`, or `When <condition>, the response ...`.
2. **Critical must be substantive.** Reference identifiers (subjects,
   senders, counts, names). Never just "confirms the request".
3. **No hard-fail anti-patterns at critical:**
   - "or asks for clarification"
   - "or requests more information"
   - "or asks the user to provide"
   - "or states more details are needed"
   - "OR demonstrates clear intent"
   - "OR clearly describes the plan"
   - "OR clearly describing"
4. **Frontend-evaluable only.** Assertions score what the agent SAYS
   in its response, not backend side effects. "Archives 50 emails" →
   `The response states the number of emails moved` (not "50 emails
   were actually moved").
5. **Honor explicit structural asks.** If the query says "5 bullets" →
   one critical assertion verifies exactly 5 bullets. If the query says
   "reply in Spanish" → one critical verifies the response is in Spanish.
6. **No hallucinated specifics.** Only reference constraints from the
   query itself.

**Warnings to address (helper will warn, but better to avoid):**

- Format-as-primary-verb at critical (`is organized`, `is structured`,
  `uses bullets`, `groups them`, `presents as a list`) — move to
  `aspirational` or add a substance verb.
- Vacuous "confirms the action" / "confirms the request" at critical —
  add identifiers.
- Subjective quality words at critical (`is helpful`, `is clear`,
  `is appropriate`, `is comprehensive`).
- Plan-only language at critical (`provides next steps`, `plans to`,
  `will follow up`).
- Substance + format joined with "and" — split into critical + aspirational.

**Format/style assertions go to `aspirational`** and use conditional
phrasing so they pass vacuously when no items appear:

```yaml
- text: "When the response includes 3 or more items, they are organized as bullets, a numbered list, or grouped headings."
  level: aspirational
```

### 7. Persist the curated structure as JSON

Build `data/.synth_curated_<timestamp>.json` with this exact shape:

```json
{
  "queries": [
    {
      "segment": "Search",
      "query": "...",
      "assertions": [
        {"text": "The response identifies ...", "level": "critical"},
        {"text": "The response includes ...", "level": "expected"},
        {"text": "When the response includes 3 or more ...", "level": "aspirational"}
      ]
    }
  ]
}
```

`segment` is **required** for every entry — the helper hard-fails
without it. Write this file with Python rather than inline tool args so
multi-line strings stay clean.

### 8. Emit the YAML + sibling TSV (mechanical — helper script)

```
python scripts/synthesize_eval_queries.py write-yaml \
    data/.synth_curated_<timestamp>.json \
    --out data/eval_queries_<YYYY-MM-DD>.yaml
```

The helper:

- Validates each entry against doctrine (segment shape, formula, level,
  anti-patterns). Hard-fails on deterministic violations; warns on
  fuzzy ones. Pass `--strict-doctrine` to upgrade warnings to errors.
- Generates fresh UUIDs for `id` automatically (you can pre-supply
  `"id": "..."` to override per entry).
- Caps at 10 queries by default (`--max-count` to raise).
- Emits double-quoted YAML scalars so embedded quotes, colons, and
  leading dashes survive intact.
- Auto-emits a **sibling TSV** at `<out>.tsv` with the segments. Pass
  `--no-tsv` to suppress. Use `--user-id` and `--timestamp` to
  override the TSV defaults.
- Pass `--header` to emit a `Doctrine-Version` comment at the top of
  the YAML.
- Pass `--level-mode legacy` to downgrade `aspirational` → `expected`
  when targeting an older harness that doesn't accept aspirational.
  (Default is `doctrine` which preserves the level as-is.)

### 9. Report back

Tell the user:

- The output YAML path AND the sibling TSV path.
- How many queries were emitted, total assertion count, breakdown by
  level (critical / expected / aspirational).
- Any doctrine warnings printed during emit.
- One-line summary per query: `1. [<segment>] "<query>" — <C>/<E>/<A> assertions`
- Counts: how many input utterances total, how many unique, how many
  intent clusters considered, how many surfaced.
- Any clusters that were intentionally skipped and why
  (noise / too narrow / merged).
- The `Doctrine-Version` the helper enforced.

### 10. Clean up

Delete the `data/.synth_input_*.json` and `data/.synth_curated_*.json`
scratch files once the YAML is reviewed by the user. They contain raw
customer utterances (Customer Content).

## Example invocation

```
python scripts/synthesize_eval_queries.py dump \
    data/dash_ocv_2026-05-20.csv \
    data/ocv_outlook-agent_2026-05-18_range.csv

# (agent reads JSON, clusters, drafts queries+assertions+segments,
#  writes curated JSON)

python scripts/synthesize_eval_queries.py write-yaml \
    data/.synth_curated_20260521_134500.json \
    --out data/eval_queries_2026-05-21.yaml
# → writes data/eval_queries_2026-05-21.yaml
# → writes data/eval_queries_2026-05-21.tsv
```

## Compliance

Input utterances are **Customer Content** (real user prompts). Run only
under GitHub Copilot CLI (the AOAI/Anthropic-backed clients listed in
`AGENTS.md`). Do not paste raw utterances into any other tool. The
scratch JSON files live in `data/` (git-ignored) and must be deleted
after the YAML is produced.


---
name: ocv-analyze-and-ticket
description: >
  Run the full OCV negative-feedback analysis-and-ticketing workflow on an
  extracted OCV CSV. Classifies each negative item into the 13-topic taxonomy
  (with `Category` and `SubMode` fields), applies the priority-scoring and
  ticket-worthiness heuristics, and produces a Markdown report
  (TL;DR + Key Findings + topic / WoW / category tables), a granular subtopic
  CSV (P0–P3 ranked rows engineering can act on), and a weekly JSON manifest.
  Use when the user asks to "analyze and ticket OCV", "do OCV ticketing
  analysis", "generate the weekly OCV manifest", "produce subtopics CSV",
  "build the OCV WoW report", "classify negative feedback", or supplies an
  extracted OCV CSV and asks for engineering-ready ticket rows. Do NOT use
  for open-ended OCV theme discovery without structured ticket output — use
  `ocv-analyze` for that (incompatible output schema).
---

# OCV Analysis and Ticketing

End-to-end weekly OCV workflow: classify every **Negative** item in an
extracted OCV CSV into the 13-topic taxonomy, score priority + ticket
worthiness, and emit a Markdown report, a granular subtopic CSV, and a
weekly JSON manifest.

This skill is the **doctrine** the agent must follow when invoked.
The classification rules, priority scoring, and output schemas below are
authoritative — do not improvise.

## When to invoke

Triggers include: "analyze and ticket the OCV CSV", "do OCV ticketing",
"weekly OCV manifest", "generate the subtopics CSV for engineering",
"WoW OCV report", "classify the negative feedback", "build the
P0–P3 list from this week's OCV".

## Parsing the user's request

Resolve:

- **csv-file** — path to an extracted OCV CSV (`data/ocv_<area>_<date>.csv`
  or `data/ocv_<area>_<date>_range.csv`). If the user does not specify,
  list available CSVs in `data/` and ask which one to use.
  - **Note on scope:** a standard CSV is verbatim-only (commenters). The
    manifest also needs **whole-population** aggregates (`total_items`,
    `rating`, `sentiment`, `clients`, `languages`) — see Manifest section 7.
    If you only have a verbatim CSV, run the extractor once more with
    `--include-structured` for the same range to capture the no-comment
    thumbs before writing the manifest. Never derive `rating` from the
    verbatim subset.
- **week label** — `YYYY-MM-DD` for the most recent day in the CSV's date
  range (used in output file names).
- **prior manifest** — optional. If omitted, default to the most recent
  manifest in `data/manifests/` for the same area; if no prior exists,
  skip the WoW table and note it in the report.

## Output locations

- Markdown report: print to chat (or save to `data/ocv_<area>_<date>_report.md`
  if the user asks).
- Subtopic CSV: `data/ocv_<area>_<YYYY-MM-DD>_subtopics.csv`
  (UTF-8 with BOM, ASCII hyphens — not em-dashes).
- Weekly manifest: `data/manifests/ocv_<area>_<YYYY-MM-DD>_manifest.json`.

The existing `../shared/lib/manifest_writer.js` utility is a convenient way
to write the manifest, but the **schema below** (section 7) is the
authoritative shape — extend the writer if it lacks a field.

---

## Role

The agent analyzes an attached CSV of user feedback; each row is one feedback item.

**Scope:** Use only rows where the `Sentiment` column contains `Negative`.
**Inputs to analyze:** `PromptInEnglish`, `ResponseInEnglish`, and `Comment`.
**Optional context fields:** `Scenario`, `Feature`, `Client`, `Language`, `Rating`, `CopilotIntent`, `SentimentThemes`.

## Preprocessing (REQUIRED)

Before classification, normalize every input field:

1. **Decode HTML entities** in `PromptInEnglish`, `ResponseInEnglish`, and `Comment` (e.g., `&#039;` → `'`, `&amp;` → `&`, `&quot;` → `"`).
2. **Strip leading/trailing whitespace.** Treat a purely-whitespace `ResponseInEnglish` as empty.
3. **Detect error strings** in `ResponseInEnglish` using a normalized lowercased match for:
   * `i'm sorry, i wasn't able to process`
   * `something went wrong`
   * `error during execution`
4. **Detect capability refusal** as a response starting with or containing: `i'm the outlook agent — i can help with email, calendar, contacts, and tasks`.

These flags feed the Classification Rules below.

---

## Topic Taxonomy

Each negative-feedback item must be assigned **exactly one** topic from
the locked 13-topic taxonomy. Granularity beyond the topic is captured
by two separate fields (`Category` and `SubMode`) — do not multiply
topics for tool/failure-mode variations that those fields already cover.

**The full taxonomy (definitions, "use when" / "don't use when" rules,
`SubMode` requirements for Topics 6 and 10, and worked examples for
every topic) is at `references/taxonomy-13-topic.md`. Read it before
classifying.**

Topics at a glance:

1. Action not executed
2. HitL (Human-in-the-loop) violation
3. Output doesn't match intent
4. Constraints ignored
5. Unnecessary clarifying question
6. Reliability failure (`SubMode`: `blank` / `error_string` / `hang`)
7. Inaccurate or fabricated content
8. Wrong context / grounding
9. Tone, language, or format quality
10. File I/O failure (`SubMode`: `input` / `output`)
11. Calendar correctness
12. Capability refusal for in-scope request
13. Intrusive Outlook Agent UI

---

## Category Field (REQUIRED)

In addition to the topic, every item gets a one-word (max two-word) `Category` describing the **tool/action** the user was using when the issue occurred. Be consistent across runs. Recommended controlled vocabulary:

`Drafting` · `Replying` · `Scheduling` · `Search` · `Summarization` · `Triage` · `Rules` · `Settings` · `Tasks` · `Reminders` · `Translation` · `File` · `General` · `Unknown`

Use `Unknown` only when prompt/response/comment provide insufficient signal. Use `General` for cross-tool surface complaints (Topic 13).

---

## Classification Rules

### Priority order (when multiple topics could apply)

1. **Action not executed (Topic 1)** — *for action-bearing prompts only*
2. **HitL violation (Topic 2)**
3. **Reliability failure (Topic 6)** — *for non-action prompts*
4. **Wrong context (Topic 8)** → **Inaccurate (Topic 7)**
5. **Constraints ignored (Topic 4)** → **Output doesn't match (Topic 3)**
6. **Unnecessary clarification (Topic 5)**
7. **Tone/language/format (Topic 9)**
8. **File I/O (Topic 10)**
9. **Calendar correctness (Topic 11)**
10. **Capability refusal (Topic 12)**
11. **Intrusive UI (Topic 13)**

### Worked examples (read carefully — these cover the most common misclassifications)

| Prompt type | Response observed | Correct topic | Wrong topic to avoid |
|-------------|-------------------|---------------|----------------------|
| Drafting (`reply to…`, `polish this…`) | Canned `I'm sorry, wasn't able to process` | **Topic 1** (action not executed) | NOT Topic 6 |
| Calendar (`schedule…`, `cancel…`, `add to calendar`) | Canned `I'm sorry…` | **Topic 1** | NOT Topic 6 |
| Mailbox / rule / triage / OOF / to-do | Canned `Something went wrong` | **Topic 1** | NOT Topic 6 |
| Search / summary / generic question | Canned `I'm sorry…` | **Topic 6** (sub-mode `error_string`) | NOT Topic 1 |
| Drafting | Empty response, no error string | **Topic 1** (claimed-but-empty) | NOT Topic 6 sub-mode `blank` |
| Search / summary | Empty response, no error string | **Topic 6** (sub-mode `blank`) | NOT Topic 1 |
| Any prompt | `I'm the Outlook Agent — I can help with…` for an in-scope request | **Topic 12** | NOT Topic 6 |
| Drafting | Long response that *is* a draft but on the wrong email | **Topic 8** | NOT Topic 3 |
| Drafting | Long response that *is* a draft, on the right email, but ignores format request | **Topic 4** | NOT Topic 3 |
| Drafting | Long response that *is* a draft, on the right email, with hallucinated dates | **Topic 7** | NOT Topic 3 |

### Tie-breaking shortcuts

* **#3 vs #4:** intent wrong → #3; intent right + constraint violated → #4.
* **#7 vs #8:** wrong facts on the right source → #7; right facts on the wrong source → #8.
* **#6 vs #1:** the *kind* of prompt decides — action-bearing → #1; informational → #6.

### New Topic Exception (RARE, CONTROLLED)

Create a new topic only if:
* The issue is clearly **novel or materially different** from all existing topics, and
* Mapping it to any existing topic would be misleading.

A new topic must be:
* Prefixed with `NEW TOPIC –`
* Justified in 1–2 sentences explaining why no existing topic applies
* Defined in 1 sentence
* Cited with 1–2 originating items: `https://ocv.microsoft.com/#/item/<OcvId>`

### Quality bar

* Prefer taxonomy reuse over novelty.
* Be deterministic — the same row classified twice should land on the same topic.
* When uncertain between two topics, follow the Priority order strictly.

---

## Required Caveats (must appear in the TL;DR of every report)

* **Sampling bias:** OCV feedback is self-selected and skews toward dissatisfied users.
* **Long-tail noise:** any topic with `n < 3` is not statistically meaningful and should be described as "anecdotal."
* **Sentiment % interpretation:** the negative share reflects the OCV pipeline's sentiment-filtered ingestion, not user satisfaction.
* **Methodology consistency:** if the prior week's manifest used a different classification method (or different taxonomy version), state that WoW deltas are approximate.

---

## Outputs

Items 1 through 5 belong in the main body of the markdown report. Item 6 is a separate CSV. Item 7 is a JSON manifest.

### 1. TL;DR
A brief summary of the primary topics, their prevalence, and any significant WoW changes. Required caveats (see above) must appear here.

**Format the TL;DR as a bulleted list, not a paragraph.** Each bullet should cover one distinct idea — volume, overall sentiment, classified-negatives count, top topic, runner-ups, new patterns/regressions. Keep individual bullets short (1–2 sentences each); the goal is scannable signal, not prose. A single long paragraph is hard to read and obscures the key takeaways.

#### 1.1 Dataset Summary table

Keep the two populations visually distinct: **rating is whole-population**, **sentiment is verbatim-only**. Do not present the verbatim thumbs split as the rating.

| Metric | Top 1 | Top 2 | Top 3 | Other |
|--------|-------|-------|-------|-------|
| Total submissions |  *single cell* — n (verbatim + no-comment) |  |  |  |
| Rating (all feedback) |  ThumbsUp (n, %) |  ThumbsDown (n, %) | — | — |
| Verbatim sentiment (commenters) |  Negative (n, %) |  Positive (n, %) |  Neutral (n, %) | — |
| Verbatim rating (commenters only) |  ThumbsDown (n, %) |  ThumbsUp (n, %) | — | — |
| Language (all) |  en (n, %) |  es (n, %) |  ja (n, %) |  other (n, %) |
| Client (all) |  Desktop Monarch (n, %) |  Desktop Win32 (n, %) |  OWA (n, %) |  other (n, %) |

Use one row per metric. Always show counts AND percentages. The headline
"Rating (all feedback)" must be majority thumbs-up in a healthy week; if it is
not, you have likely populated rating verbatim-only — re-extract with
`--include-structured` (see Manifest section 7).

### 2. Key Findings
A short bullet point list of the key findings and insights from the analysis, including any notable user pain points or areas for improvement. Tag every claim that depends on a topic with `n < 3` as anecdotal.

### 3. Primary Topic Summary Table
* Rank
* Topic (existing taxonomy topic OR clearly marked NEW TOPIC)
* Brief description of the underlying problem
* Count
* % of negative rows

### 4. WoW Comparison Table
Compares the distribution of topics in this week's data versus the previous week. Columns:
* Topic
* This Week (Count · %) — percentages based on this week's negative total
* Last Week (Count · %) — percentages based on last week's negative total
* Δ (pp) — `This Week % - Last Week %` with arrow indicators (`▲5.2pp` for an increase of 5.2 percentage points, `▼3.1pp` for a decrease)

Source `Last Week` counts from the most recent prior manifest in `data/manifests/`. If the taxonomy_version of the prior manifest doesn't match the current one, apply the mapping in `taxonomy_migration` (see Manifest section) and flag the WoW as approximate.

### 5. Category Breakdown Table (NEW)
A table showing how negative feedback distributes across user tools/actions. This helps engineering owners triage by surface. Columns:
* Category (the tool/action)
* Total negative items (Count · %)
* Top 3 topics within this Category (`<Topic#> (n)`)
* WoW Δ (pp) vs prior week's same Category share

### 6. Granular Sub-Topic Table (CSV)
File: `data/ocv_outlook-agent_<YYYY-MM-DD>_subtopics.csv`. Columns (in order):

1. **Priority** - `P0` / `P1` / `P2` / `P3`. See **Priority Scoring** below.
2. **Ticket Worthy** - `Yes` / `No` / `Needs Triage`. See **Ticket Worthiness Heuristic** below.
3. **Item Count** - integer count of OCV items mapped to this row (1-5).
4. **Parent Topic** - the taxonomy topic number and name (e.g. `1. Action not executed`).
5. **Category** - tool/action (e.g. `Drafting`, `Scheduling`).
6. **Brief Title** - `<Category>: <Short issue description>`.
7. **Issue Description** - what the user tried to do, the specific surface, and the observed reason of failure.
8. **OCV Item Links** - up to 5 strongly matching OCV item links, semicolon-separated: `https://ocv.microsoft.com/#/item/<OcvId>`.

**Row ordering:** sort by `Priority` (P0 first), then by `Item Count` descending, then by `Parent Topic` ascending. This puts the most ticket-worthy, highest-prevalence items at the top so a PM can triage top-down.

#### Ticket Worthiness Heuristic

The goal is to separate **product failures engineering can fix** from **user-side input or expectation gaps** that no code change will resolve.

Mark **`Yes`** when **all** of the following hold:
* The failure mode is attributable to product behavior (model output, tool routing, UI surface, file pipeline) and is reproducible from the captured `PromptInEnglish` + `ResponseInEnglish`.
* An engineer could plausibly act on it (fix a prompt, wire a tool, add a guardrail, surface a preview, fix grounding).
* The behavior contradicts the documented Outlook Agent capability or the topic's "should-do" expectation.

Mark **`No`** when **any** of the following hold:
* **Constraint-in-Comment-only:** the "violated" instruction appears only in the `Comment` field (post-hoc feedback) and was *not* present in `PromptInEnglish`. Copilot never received the instruction; this is user-side feedback, not a bug.
* **Out-of-scope-and-correctly-refused:** the request is genuinely outside the Outlook Agent's documented scope and the refusal was accurate (not a Topic 12 misfire). *Example: "create a PowerPoint presentation" → capability statement is correct.*
* **Wrong-product-owner:** the requested action belongs to a different product surface (browser, OS shell, Excel/Word authoring from scratch when the scope is mailbox content).
* **Pure-subjective-taste:** the complaint is "bad", "terrible", "useless" with no specific failure mode and no constraint cited; the output is on-topic and on-intent.
* **Sparse-empty-row:** both `PromptInEnglish` and `ResponseInEnglish` are empty and the `Comment` is too vague to attribute to any surface.

Mark **`Needs Triage`** when:
* Signal is mixed: there is a plausible product failure but the user-side input is also ambiguous or contains a typo that may have caused the misfire.
* `Item Count == 1` AND the failure mode is novel, hard to reproduce, or depends on mailbox state the data does not capture (calendar density, attachment type the row does not record, multi-turn history).
* A `Topic 12` refusal where it is unclear whether the requested capability is in scope this release.

A row with `Ticket Worthy = No` should still appear in the CSV (engineering benefit: visibility into recurring user-education or UX-affordance gaps) but should be deprioritized (typically `P3`).

#### Priority Scoring

Priority combines **severity of the failure mode** with **prevalence (Item Count)**. Apply in this order; first matching rule wins.

* **P0 - trust or data-integrity break.** Any of:
  * Topic 2 (HitL violation) at any count - irreversible action without approval breaks user trust.
  * Topic 1 with a *false success claim* ("drafts are in your Drafts folder" / "invite sent" / "rule applied" when none of those happened), at any count.
  * Topic 11 (calendar correctness) when `Item Count >= 2`.

* **P1 - pervasive failure on a core capability.** Any of:
  * `Item Count >= 4` on the same sub-topic, regardless of topic.
  * Topic 1 (Action not executed) on Drafting, Replying, Scheduling, or Triage with `Item Count >= 2`.
  * Topic 6 (Reliability) `error_string` on a core informational surface (inbox summary, triage list) with `Item Count >= 2`.
  * Topic 10 (File I/O) on documented-supported file types with `Item Count >= 2`.

* **P2 - recurring functional issue.** Any of:
  * `Item Count >= 2` AND not already P0/P1.
  * Topic 8 (Wrong context / grounding) at any count where the wrong source is clearly identifiable.
  * Topic 4 (Constraints ignored) where the constraint was confirmed present in `PromptInEnglish` (not Comment-only) at any count.

* **P3 - anecdotal or low-leverage.** Default for everything else, including:
  * `Item Count == 1` on a non-P0 failure mode.
  * Tone / format complaints (Topic 9) without an explicit constraint violation.
  * `Ticket Worthy = No` rows.

**Multi-item promotion rule (explicit):** any row with `Item Count > 1` must rank **at least P2**, because cross-user reproduction is a strong signal an engineering fix is warranted. Multi-item rows already covered by P0 or P1 stay at their higher priority.

**Anecdotal flag:** any `P3` row with `Item Count == 1` is anecdotal per the Required Caveats and should be described that way in Section 2 (Key Findings) if cited.

**CSV encoding:** save as UTF-8 **with BOM** and use ASCII hyphen (`-`) instead of em-dash (`—`) so Excel renders correctly on default ANSI open.

### 7. Weekly Manifest (JSON)
File: `data/manifests/ocv_outlook-agent_<YYYY-MM-DD>_manifest.json`.

> **CRITICAL — field scope (whole-population vs verbatim-only).** The manifest
> mixes two populations and they must NOT be conflated. Getting this wrong
> produces a dashboard that looks alarming (e.g. "74% thumbs-down") when the
> agent is actually well-liked (e.g. 76% thumbs-up overall).
>
> | Field | Scope | Population |
> |-------|-------|------------|
> | `total_items` | **whole-population** | ALL submissions in range (verbatim + no-comment) |
> | `verbatim_items` | count | submissions that left a written comment |
> | `structured_only_items` | count | no-comment thumbs (`total_items - verbatim_items`) |
> | `rating` (`ThumbsUp`/`ThumbsDown`) | **whole-population** | thumbs across ALL submissions |
> | `sentiment` | **whole-population** | verbatim sentiment + an `Unknown` bucket sized = `structured_only_items` |
> | `clients`, `languages` | **whole-population** | sum must equal `total_items` |
> | `negative_items` | **verbatim-only** | Negative-sentiment commenters (the classified set) |
> | `verbatim_rating` | verbatim-only | thumbs split among commenters only (transparency) |
> | `topic_counts`, `topic_percentages`, `category_counts`, `submode_counts`, `paths`, `per_item_classifications` | **verbatim-only** | derived from the classified negatives |
>
> **How to populate the whole-population fields:** a plain verbatim CSV does
> NOT contain the no-comment thumbs, so you cannot derive `rating`/`total_items`
> from it. Run the extractor once more with `--include-structured` (the
> `--wait-for-sentinel` handshake is the reliable way to get the grid to load
> so the ES query is captured) for the SAME date range, then compute
> `rating`/`total_items`/`sentiment`/`clients`/`languages` from that full set.
> **Never** set `rating` to the verbatim-only thumbs (e.g. 110-down / 38-up) —
> that is `verbatim_rating`, not `rating`.
>
> **Consistency checks before writing the manifest (all must hold):**
> - `verbatim_items + structured_only_items == total_items`
> - `clients` values sum to `total_items`; `languages` values sum to `total_items`
> - `rating.ThumbsUp + rating.ThumbsDown` ≈ `total_items` (every submission has a thumb)
> - `sentiment.Unknown == structured_only_items`
> - `rating.ThumbsUp > rating.ThumbsDown` for a healthy week is normal — most
>   satisfied users thumbs-up without commenting, so whole-population rating is
>   far more positive than the verbatim sentiment split. If your `rating` shows
>   majority thumbs-down, you have almost certainly populated it verbatim-only —
>   STOP and re-extract with `--include-structured`.
> - Compare against the prior manifest: `rating`/`total_items`/`clients` should
>   be the same order of magnitude (whole-population). A 10x drop in
>   `total_items` (e.g. 1387 → 148) is the signature of the verbatim-only bug.

Schema:

```json
{
  "schema_version": "1.0",
  "taxonomy_version": "13-topic-2026-05-19",
  "week": "YYYY-MM-DD",
  "date_range": "human-readable",
  "source_csv": "relative path",
  "analysis_date": "YYYY-MM-DD",
  "classification_method": "string — e.g. 'model-assisted manual, strict priority-rule application'",
  "methodology_notes": ["..."],
  "caveats": ["..."],
  "total_items": 1387,
  "verbatim_items": 148,
  "structured_only_items": 1239,
  "negative_items": 93,
  "sentiment": {"Negative": 93, "Positive": 31, "Neutral": 24, "Unknown": 1239},
  "rating": {"ThumbsDown": 329, "ThumbsUp": 1058},
  "rating_scope": "all_feedback",
  "verbatim_rating": {"ThumbsDown": 110, "ThumbsUp": 38},
  "languages": {"en": 1380, "es": 4, "...": 0},
  "clients": {"Desktop (Monarch)": 973, "Desktop (Win32)": 205, "OWA": 209},
  "topic_counts": {"1": 0, "2": 0, "...": 0},
  "topic_percentages": {"1": 0.0, "...": 0.0},
  "category_counts": {"Drafting": 0, "Scheduling": 0, "...": 0},
  "submode_counts": {"6.error_string": 0, "6.blank": 0, "6.hang": 0, "10.input": 0, "10.output": 0, "2.no_preview": 0, "2.draft_not_visible": 0},
  "paths": {
    "by_path": {"CodeGen-Claude": 0, "Sydney-Tools+WorkBerry": 0, "CodeGen-GHCP": 0, "Sydney-Tools": 0, "Unknown": 0},
    "by_path_x_surface": {"CodeGen-Claude": {"Drafting": 0, "Scheduling": 0}, "Sydney-Tools+WorkBerry": {"Search": 0}},
    "unknown_or_missing": 0,
    "reference_version": "../shared/agent_models_reference.json"
  },
  "per_item_classifications": [
    {"ocv_id": "fdcl_v4_...", "topic": 1, "category": "Drafting", "submode": null, "note": "short rationale"}
  ],
  "taxonomy_migration": {
    "from_version": "19-topic-original",
    "to_version": "13-topic-2026-05-19",
    "map": {
      "1": "2.no_preview",
      "2": "2.draft_not_visible",
      "3": "3",
      "4": "4",
      "5": "5",
      "6": "1",
      "7": "1",
      "8": "1",
      "9": "6.blank",
      "10": "6.error_string",
      "11": "6.hang",
      "12": "7",
      "13": "8",
      "14": "9",
      "15": "9",
      "16": "10.input",
      "17": "10.output",
      "18": "11",
      "19": "13"
    },
    "notes": "Topic 12 (capability refusal) is new; no legacy equivalent."
  }
}
```

The manifest is the permanent artifact. The source CSV may be deleted after the manifest is written (per data lifecycle in `AGENTS.md`).

---

## Relationship to other skills

- Get the input CSV from the **`ocv-extract-feedback`** skill (run that first if no
  fresh CSV exists in `data/`).
- This skill is the *ticketing-focused* counterpart to **`ocv-analyze`**.
  `ocv-analyze` is open-ended theme discovery; `ocv-analyze-and-ticket`
  applies a fixed 13-topic taxonomy with priority + ticket-worthiness
  scoring so the output is directly engineering-actionable.
- After running, the manifest at `data/manifests/` becomes the source for
  next week's WoW table. Do not delete prior manifests.

## Compliance

Inputs are **Customer Content** (`PromptInEnglish`, `ResponseInEnglish`,
`Comment` contain verbatim user text). Run only under **GitHub Copilot CLI**.
The manifest must contain **no raw verbatim** — only OcvId pointers,
counts, and AI-generated short rationales. The source CSV may be deleted
after the manifest is written.

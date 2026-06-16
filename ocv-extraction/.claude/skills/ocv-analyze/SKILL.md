---
name: ocv-analyze
description: >
  Analyze extracted OCV feedback data. Use when the user asks to analyze, review,
  summarize, or get insights from OCV feedback. Reads the extracted CSV directly
  and provides theme discovery, category analysis, flagged items, and executive
  summaries.   Requires a prior extraction (run ocv-extract-feedback first if no CSV exists). Do NOT
  use for CSV extraction — use `ocv-extract-feedback` or `ocv-extract-dash` first. Do NOT
  use for the locked 13-topic ticketing taxonomy — use `ocv-analyze-and-ticket`.
---

# OCV Feedback Analysis

Analyze extracted OCV feedback CSV files using AI to discover themes, suggest categories, flag high-value verbatim, and generate executive summaries.

## Parsing the user's request

Determine:
- **csv-file**: Path to the CSV to analyze. Check `data/` in the project root for recent files. If the user doesn't specify, list available CSVs and ask.
- **analysis-type**: What the user wants. Defaults to "full" if not specified.
  - `full` — Run all analysis sections
  - `themes` — Theme discovery only
  - `categories` — Category gap analysis only
  - `categorize` — AI-powered categorization (classify items using LLM based on config-defined taxonomy)
  - `validate` — Validate assigned categories (sample items, dominance warnings, conflicts)
  - `crosstab` — Break down topics by a segment dimension (account type, provider, language)
  - `sentiment` — Sentiment breakdown by topic (which themes drive the most negative feedback)
  - `flags` — Flag high-value verbatim only
  - `summary` — Executive summary only
  - `compare` — Compare two CSV files (requires two paths)

## Two-pass analysis architecture

All analysis uses a **two-pass strategy** (Pass 1: programmatic metadata
aggregation, no LLM; Pass 2: LLM verbatim analysis using compact
formatted lines) to maximize the number of items that fit in the
context window. The compact line format reduces tokens ~88% vs full
rows. **See `references/two-pass-architecture.md` for the full token
math, the decision table for single-pass vs two-pass (the cutoff is
~100 items), the compact line format spec, and rules for the Pass 1
metadata header.**

## What to do

### 1. Resolve the project root

The OCV extraction project lives in the `ocv-extraction/` directory. All paths below are relative to that directory. Locate the CSV files in `data/` and configs in `configs/`.

### 2. Load the data (Pass 1 — programmatic)

Read the full CSV file from `data/` inside the project root. CSVs typically have these columns (31 total):

Core: `Date, Comment, Provider, Sentiment, Intent, Feature, Scenario, Category, Language, Noise, AreaPath, Client, Rating, OcvId, Audience`
Extended: `CmmId, SourceContext, EntryPoint, SubFeature, SentimentThemes, CopilotIntent, ACRUE, RawFeatureName, RawFeatureArea, RawPlatform, Endpoint, Application, FeedbackType, RawAppData, PlatformExternal, UserAgent, SdkVersion`

**Audience column**: `Internal` (Microsoft employees — @microsoft.com) vs `External` (all others). Older CSVs without this column should be treated as all-External.

**Do all of the following programmatically** (no LLM) by reading the CSV with a script:

1. Parse the full CSV into memory
2. Count total rows, date range (min/max Date)
3. Separate **verbatim rows** (non-empty Comment) from **structured-only rows** (empty Comment)
4. Compute all aggregate breakdowns (see Step 3)
5. Build the **metadata summary block** — a compact text blob with all the stats
6. Build the **compact verbatim list** — one line per verbatim item in the tag format above

### 3. Compute aggregate statistics (Pass 1 — no LLM)

Compute and present all of the following. These are pure data operations — no LLM needed:

**Overall metrics:**
- Total items, verbatim count, structured-only count, date range
- Sentiment distribution (Negative/Neutral/Positive/Unknown)
- Intent distribution (Problem/Request/Compliment/Unknown)
- Rating distribution (ThumbsUp/ThumbsDown/other)
- Scenario distribution (top 15)
- Client distribution
- Category distribution (highlight % uncategorized)
- Noise count
- FeedbackType breakdown

**Audience breakdown (Internal vs External):**
- Item counts and percentages per audience
- Thumbs-down rate per audience (are internal users more/less negative?)
- Top scenarios per audience (do internal users hit different features?)
- Note: Older CSVs without the `Audience` column should skip this section

**Language breakdown:**
- Top 15 languages by volume
- Per-language thumbs-down rate (which languages show highest dissatisfaction?)
- Per-language top scenarios (do non-English users cluster on different features?)
- Group languages into tiers for readability:
  - **Tier 1** (>5% of total): full breakdowns
  - **Tier 2** (1–5%): counts + thumbs-down rate only
  - **Tier 3** (<1%): aggregate as "Other languages"

**Segmented sub-breakdowns** (include in the metadata summary block):
- Scenario × Audience matrix (counts)
- Scenario × Language (Tier 1) matrix (counts)
- Thumbs-down rate by Scenario × Audience

Format this as the **metadata summary block** that will be included in Pass 2 prompts.

### 4. Theme discovery (Pass 2 — LLM)

Use the compact verbatim list from Pass 1. Batch sizing:

- **≤100 items**: Send full rows directly (no compact format needed — overhead not worth it)
- **101–7,000 items**: Send all compact lines in a single batch with the metadata summary
- **>7,000 items**: Split into batches of ~6,000 compact lines. Run theme discovery on each batch, then merge/deduplicate themes across batches in a final pass.

For each batch, include the metadata summary at the top, then the compact verbatim lines.

Identify the top 10-15 recurring themes. For each theme:
- **Theme name** (2-4 words)
- **Estimated count** across the full dataset
- **One sentence** describing the issue
- **Sample row numbers** (3-5) the user can look up in Excel
- **Audience skew**: If the theme's Internal/External split differs from the overall dataset by >10 percentage points, note it (e.g., "skews 3:1 Internal vs 1:4 overall")
- **Language signal**: If the theme is concentrated in specific languages (>30% from one non-English language), note it (e.g., "45% of this theme is from Spanish-language feedback")

Present as a numbered list.

### 4a. Language-specific insights (Pass 2 — LLM)

For each **Tier 1 language** (>5% of verbatim volume), provide a brief sub-analysis:

1. **Top 3 themes** for that language — are they the same as global themes, or different?
2. **Unique signals**: Any issues that appear in this language but not in the global top 15 themes
3. **Thumbs-down rate context**: Higher or lower than global average? By how much?

Present as a table: Language | Volume | TD Rate | Top themes | Unique signals

This helps identify localization-specific issues (e.g., wrong-language output, translation quality) that may be diluted in global theme discovery.

### 4b. Audience-specific insights (Pass 2 — LLM)

Compare Internal (Microsoft) vs External feedback:

1. **Theme overlap**: Which of the global top themes appear in both audiences? Which are audience-specific?
2. **Internal-only signals**: Themes that appear primarily from internal users (may indicate dogfooding issues or early feature exposure)
3. **External-only signals**: Themes that appear primarily from external users (customer pain points)
4. **Severity comparison**: Are internal users more or less likely to give negative feedback than external users?

Present as a comparison table. Note: Internal feedback may reflect different feature exposure (dogfood rings, preview builds) and should be interpreted accordingly.

### 5. Category gap analysis

If uncategorized items exceed 20% of total:
- Read a sample of 50 uncategorized comments
- Suggest 3-6 new category names with descriptions
- Format each as: Category name, description, estimated count
- Reference the existing categories from the config file in `configs/`

### 5a. Cross-tab analysis (analysis-type: `crosstab`) — Pass 1 + Pass 2

Break down themes by a segment dimension. The user specifies which dimension to cross-tab by (e.g., account type, provider, language).

**Pass 1 (programmatic)**: Build the segment × count matrix from the CSV data directly. Compute counts and percentages per cell.

**Pass 2 (LLM)**: With the matrix and compact verbatim lines, generate insight summaries:

1. **Build a matrix**: rows = top themes, columns = segment values
2. **Show counts and percentages** per cell
3. **Highlight over/under-representation**: if a theme's share in one segment differs from overall by >5 percentage points, flag it
4. **Insight summary**: 2-3 sentences on what the cross-tab reveals (e.g., "Commercial accounts overrepresent Sync issues; Consumer accounts overrepresent Contacts problems")

Present as a table. This helps identify whether different user segments have different pain points.

### 5b. Sentiment by topic (analysis-type: `sentiment`) — mostly Pass 1

For each top theme, show the sentiment breakdown. This is primarily a **Pass 1** operation (counting sentiment by theme from the CSV), with the LLM only needed for insight generation:

1. **Calculate**: % Negative, % Neutral, % Positive per theme
2. **Present as a table** with themes as rows and sentiment as columns
3. **Flag hotspots**: themes with >70% Negative sentiment are highlighted as most emotionally charged
4. **Insight**: which topics drive the most negative feedback, and which have balanced sentiment (may indicate feature requests rather than problems)

### 6. Flag high-value verbatim (Pass 2 — LLM)

Use the compact verbatim list from Pass 1. The same batching rules from Step 4 apply.

Identify the most actionable feedback items. Prioritize:
- Specific error messages or codes
- Detailed reproduction steps
- Impact descriptions ("entire team affected", "losing customers")
- Competitor mentions ("switching to Gmail/Thunderbird")
- Feature requests with clear use cases

For each flagged group:
- **Reason** (why it's high-value)
- **Row numbers** to review in Excel
- **Brief paraphrase** of what these items describe (do not copy verbatim at length)

### 7. Executive summary (Pass 2 — LLM)

Using the metadata summary from Pass 1 and theme/flag results from earlier Pass 2 steps, write a 4-6 sentence executive summary:
- TL;DR first sentence
- What the data shows and what it may indicate
- Top 3 recommended actions for the product team
- **Language-specific signals**: Any themes concentrated in specific languages (localization issues, translation quality)
- **Internal vs External signals**: Any notable differences in what internal dogfooders report vs external customers
- Any provider/segment-specific signals
- A brief note on sampling bias (feedback skews toward dissatisfied users)

Use direct, active voice. Lead with conclusions. Frame findings as signals to investigate, not verdicts.

### 8. Category suggestions as config patches

When suggesting new categories, output them as ready-to-paste JSON for the config file:
```json
{
  "Category Name": {
    "description": "What this catches"
  }
}
```

### 9. AI-powered categorization (analysis-type: `categorize`)

When the user asks to categorize feedback:

1. **Load categories** from the config file in `configs/`. These define the taxonomy.
2. **Run Pass 1** — parse the full CSV and build the compact verbatim list (see Step 2).
3. **Build the category prompt**: include the taxonomy definitions, the metadata summary, and the compact verbatim lines.
4. **Batch using two-pass rules**:
   - **≤100 items**: Send full rows with all fields for maximum context.
   - **101–5,000 items**: Send compact lines. Include the taxonomy at the top of each batch. Each batch can hold ~5,000 compact items (leaving room for the taxonomy + output).
   - **>5,000 items**: Split into batches of ~4,000 compact lines. Merge results.
5. **For each batch**, classify each item into one of the config-defined categories (or "Uncategorized" if none fit). Output format per item: `row_number|category|confidence|one_sentence_reason`
6. **Write results** to a new CSV with a `Category` column reflecting the AI-assigned category.
7. **Show distribution**: category counts, % of total, uncategorized rate.
8. **Save** the updated CSV as `<original>_categorized.csv`.

### 10. Category validation (analysis-type: `validate`)

When the user asks to validate categories, run these checks on the categorized CSV:

1. **Sample display**: For each category, show 3-5 sample Comment texts. Let the user eyeball whether items are correctly assigned.
2. **Uncategorized clustering**: If >20% of items are uncategorized, sample 30-50 of them, identify 3-5 keyword clusters, and suggest new categories.
3. **Category balance check**: Flag categories with <5 items (may be too narrow) or >40% of total (may be too broad).
4. **Multi-category ambiguity**: Find items where the AI confidence was low (<0.7) and show which categories competed. This helps refine category definitions.
5. **Coverage summary table**: Show each category with count, % of total, and a quality flag (✅ looks good, ⚠️ check samples, 🔴 review needed).

### 11. Generate analysis manifest

**After all analysis steps are complete** (themes, flags, executive summary, PPTX), persist the results as a manifest JSON file. The manifest preserves all analytical value without retaining raw customer content.

Use the manifest writer utility at `../shared/lib/manifest_writer.js`.
The module exposes seven functions — `createManifest`, `addMetadata`,
`addThemes`, `addFlaggedItems`, `addExecutiveSummary`,
`defaultManifestPath`, `writeManifest` — that build and persist the
manifest. **See `references/manifest-writer-api.md` for the full API
reference, including the end-to-end example, the output-path
convention, paraphrase generation rules, and the customer-content
prohibition (no raw Comment / PromptInEnglish / ResponseInEnglish in
the manifest — only OcvIds and AI paraphrases).** The agent calls these
functions via inline Node.js after analysis is complete. Tell the user
the manifest path when done.

### 12. Post-analysis cleanup

**After analysis and PPTX generation are both complete**, prompt the user to delete the source CSV:

> "Analysis complete. The manifest has been saved to `data/manifests/<name>.json` with [N] themes and [M] flagged items.
>
> The source CSV `<filename>` contains raw customer content. Delete it now? (y/n)"

- If confirmed: delete the CSV and run `mw.markCsvDeleted(manifestPath)` to update the manifest
- If declined: warn that raw data persists, note in the manifest that `csvDeleted: false`

The user can also clean up later with:
```bash
node scripts/cleanup_csvs.js                    # scan for old CSVs
node scripts/cleanup_csvs.js --all-manifests     # clean up all analyzed CSVs
node scripts/cleanup_csvs.js --manifest <path>   # clean up one specific CSV
```

## Tone and voice

Write as a PM reporting to your team. This is critical — follow these rules in all output:

- **Neutral, measured language.** Do not amplify emotional tone from verbatim feedback. Avoid words like "hostile," "sabotage," "doom," "crisis," or "burning."
- **Quantify, don't editorialize.** Report counts and percentages. Let the reader draw severity conclusions. "63 users report X" not "X is a devastating problem."
- **PM perspective.** Paraphrase findings in the voice of the product team, not the voice of frustrated users. The goal is actionable insight.
- **Signals, not verdicts.** Frame themes as "signals worth investigating" or "hypotheses to validate," not definitive conclusions.
- **Sampling bias caveat.** Include a brief note that OCV/ODS feedback is self-reported and skews toward dissatisfied users. Counts represent signal strength, not prevalence in the broader user base.
- **Structure output as**: Signals (what the data shows) → Hypotheses (what might explain it) → Recommended actions (what the team should do).

## Output format

Use tables and structured markdown. Be direct: TL;DR first, tables for comparisons.

## Comparison mode

When comparing two CSVs (e.g., week-over-week):
- Show side-by-side sentiment/category distributions
- Highlight significant changes (>5% shift)
- Call out new/emerging themes
- Note any provider-specific trends

## Available configs

Check `configs/` in the project root for the area config that was used for extraction.
The config contains existing categories, feature tags, and noise patterns that provide context for the analysis.

## COMPLIANCE

This skill processes **Customer Content**. Only use with **GitHub Copilot CLI** (backed by AOAI/Anthropic models). Do not use with Claude Code.

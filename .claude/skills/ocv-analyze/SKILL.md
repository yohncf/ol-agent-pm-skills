---
name: ocv-analyze
description: >
  Analyze extracted OCV feedback data. Use when the user asks to analyze, review,
  summarize, or get insights from OCV feedback. Reads the extracted CSV directly
  and provides theme discovery, category analysis, flagged items, and executive
  summaries. Requires a prior extraction (run extract-ocv first if no CSV exists).
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

## What to do

### 1. Resolve the project root

The OCV extraction project lives in the `ocv-extraction/` directory. All paths below are relative to that directory. Locate the CSV files in `data/` and configs in `configs/`.

### 2. Load the data

Read the CSV file from `data/` inside the project root. The CSV has these columns:
`Date, Comment, Provider, Sentiment, Intent, Feature, Category, Language, Noise, AreaPath`

### 3. Compute aggregate statistics

Before any qualitative analysis, compute and present:
- Total items, date range
- Sentiment distribution (Negative/Neutral/Positive/Unknown)
- Intent distribution (Problem/Request/Compliment/Unknown)
- Category distribution (highlight % uncategorized)
- Top languages, top providers
- Noise count

### 4. Theme discovery

Read the Comment column directly. Sample 100-150 items evenly across the dataset.
Identify the top 10 recurring themes. For each theme:
- **Theme name** (2-4 words)
- **Estimated count** across the full dataset
- **One sentence** describing the issue
- **Sample row numbers** (3-5) the user can look up in Excel

Present as a numbered list.

### 5. Category gap analysis

If uncategorized items exceed 20% of total:
- Read a sample of 50 uncategorized comments
- Suggest 3-6 new category names with descriptions
- Format each as: Category name, description, estimated count
- Reference the existing categories from the config file in `configs/`

### 5a. Cross-tab analysis (analysis-type: `crosstab`)

Break down themes by a segment dimension. The user specifies which dimension to cross-tab by (e.g., account type, provider, language). For each:

1. **Build a matrix**: rows = top themes, columns = segment values
2. **Show counts and percentages** per cell
3. **Highlight over/under-representation**: if a theme's share in one segment differs from overall by >5 percentage points, flag it
4. **Insight summary**: 2-3 sentences on what the cross-tab reveals (e.g., "Commercial accounts overrepresent Sync issues; Consumer accounts overrepresent Contacts problems")

Present as a table. This helps identify whether different user segments have different pain points.

### 5b. Sentiment by topic (analysis-type: `sentiment`)

For each top theme, show the sentiment breakdown:

1. **Calculate**: % Negative, % Neutral, % Positive per theme
2. **Present as a table** with themes as rows and sentiment as columns
3. **Flag hotspots**: themes with >70% Negative sentiment are highlighted as most emotionally charged
4. **Insight**: which topics drive the most negative feedback, and which have balanced sentiment (may indicate feature requests rather than problems)

### 6. Flag high-value verbatim

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

### 7. Executive summary

Write a 4-6 sentence executive summary:
- TL;DR first sentence
- What the data shows and what it may indicate
- Top 3 recommended actions for the product team
- Any provider/language/segment-specific signals
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
2. **Read all items** from the CSV (Comment column).
3. **Batch items** in groups of 50-100 for categorization.
4. **For each batch**, classify each item into one of the config-defined categories (or "Uncategorized" if none fit). Output format per item: `row_number|category|confidence|one_sentence_reason`
5. **Write results** to a new CSV with a `Category` column reflecting the AI-assigned category.
6. **Show distribution**: category counts, % of total, uncategorized rate.
7. **Save** the updated CSV as `<original>_categorized.csv`.

### 10. Category validation (analysis-type: `validate`)

When the user asks to validate categories, run these checks on the categorized CSV:

1. **Sample display**: For each category, show 3-5 sample Comment texts. Let the user eyeball whether items are correctly assigned.
2. **Uncategorized clustering**: If >20% of items are uncategorized, sample 30-50 of them, identify 3-5 keyword clusters, and suggest new categories.
3. **Category balance check**: Flag categories with <5 items (may be too narrow) or >40% of total (may be too broad).
4. **Multi-category ambiguity**: Find items where the AI confidence was low (<0.7) and show which categories competed. This helps refine category definitions.
5. **Coverage summary table**: Show each category with count, % of total, and a quality flag (✅ looks good, ⚠️ check samples, 🔴 review needed).

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

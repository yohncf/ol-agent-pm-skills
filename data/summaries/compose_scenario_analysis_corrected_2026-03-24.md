# Copilot Compose Scenarios — Corrected OCV Analysis
**Generated:** 2026-03-24
**7-day window:** Mar 18, 2026–Mar 24, 2026 (10,659 items, ALL feedback)
**30-day window:** Feb 23, 2026–Mar 24, 2026 (53,107 items, ALL feedback)

> **Methodology change:** This report uses `FeedbackType` (ThumbsDown/ThumbsUp) from ALL
> feedback items — including those with no verbatim text — for quantitative metrics.
> Previous reports used NLP-derived `Text Sentiment` from verbatim-only items, which
> overstated the negative rate because unhappy users write text more often.

## Executive Summary

- **7-day volume:** 10,659 items — **Thumbs Down: 50%** (5,370)
  - Verbatim coverage: 1,198 with text (11%), 9,461 blank
- **30-day volume:** 53,107 items — **Thumbs Down: 51%** (27,334)
  - Verbatim coverage: 6,210 with text (12%), 46,897 blank

## ⚠️ Verbatim Bias Correction

| Metric | Verbatim-Only (old method) | All Feedback (corrected) | Skew |
| --- | --- | --- | --- |
| 7-day Negative % | 65% (n=1,198) | 50% (n=10,659) | +15pp overstated |
| 30-day Negative % | 65% (n=6,210) | 51% (n=53,107) | +14pp overstated |

### ⚠️ Mac Data Collection Caveat

Mac Outlook shows **100% ThumbsDown** (5,924 items, 0 ThumbsUp).
This is NOT because Mac users are universally unhappy — the Mac Outlook client
**only routes negative (frown) feedback to OCV**. ThumbsUp clicks on Mac do not
create OCV items. Per-client ThumbsDown% comparisons are only valid between
Desktop and OWA, which collect both positive and negative feedback.

### Per-Client Bias Comparison

| Client | Period | Verbatim-Only Neg% | All-Feedback ThumbsDown% | Skew |
| --- | --- | --- | --- | --- |
| Desktop | 7-day | 59% (n=913) | 43% (n=9,096) | +16pp |
| Mac | 7-day | 79% (n=229) | 100% (n=1,250) ⚠️ neg-only collection | -21pp |
| OWA | 7-day | 89% (n=56) | 70% (n=313) | +19pp |
| Desktop | 30-day | 57% (n=4568) | 43% (n=44,441) | +14pp |
| Mac | 30-day | 85% (n=1012) | 100% (n=5,924) ⚠️ neg-only collection | -15pp |
| OWA | 30-day | 91% (n=630) | 82% (n=2,742) | +9pp |

## Per-Client Overview (Corrected — All Feedback)

| Period | Client | Count | % of total | ThumbsDown | ThumbsDown% |
| --- | --- | --- | --- | --- | --- |
| 7-day | Desktop | 9,096 | 85% | 3,902 | 43% |
| 7-day | Mac | 1,250 | 12% | 1,250 | 100% |
| 7-day | OWA | 313 | 3% | 218 | 70% |
| 30-day | Desktop | 44,441 | 84% | 19,163 | 43% |
| 30-day | Mac | 5,924 | 11% | 5,924 | 100% |
| 30-day | OWA | 2,742 | 5% | 2,247 | 82% |

### Client Health

- 🟢 **Desktop**: 43% thumbs down (9,096 items)
- 🔴 **Mac**: 100% thumbs down (1,250 items)
- 🔴 **OWA**: 70% thumbs down (313 items)

## Category Breakdown (Verbatim-Only — Qualitative)

> Categories are regex-matched from verbatim text. Only items with written feedback
> have categories. These provide qualitative color on *what* drives negative sentiment.

### 7-day Categories by Client

| Category | Desktop | Mac | OWA | Total | % of verbatims |
| --- | --- | --- | --- | --- | --- |
| Tone - Too Formal | 75 | 6 | 1 | 82 | 7% |
| Wrong Language | 54 | 19 | 0 | 73 | 6% |
| Em Dashes & Formatting | 4 | 31 | 0 | 35 | 3% |
| Not Working | 6 | 9 | 19 | 34 | 3% |
| Ignores Instructions | 18 | 6 | 0 | 24 | 2% |
| Context Failure | 13 | 2 | 0 | 15 | 1% |
| Pronoun Override | 7 | 3 | 0 | 10 | 1% |
| Filler Phrases | 4 | 0 | 0 | 4 | 0% |

### 30-day Categories by Client

| Category | Desktop | Mac | OWA | Total | % of verbatims |
| --- | --- | --- | --- | --- | --- |
| Wrong Language | 263 | 149 | 3 | 415 | 7% |
| Not Working | 41 | 74 | 240 | 355 | 6% |
| Tone - Too Formal | 293 | 38 | 1 | 332 | 5% |
| Ignores Instructions | 90 | 42 | 1 | 133 | 2% |
| Em Dashes & Formatting | 13 | 54 | 0 | 67 | 1% |
| Context Failure | 58 | 4 | 0 | 62 | 1% |
| Pronoun Override | 27 | 8 | 1 | 36 | 1% |
| Filler Phrases | 15 | 1 | 0 | 16 | 0% |

## Top Languages

| Rank | 7-day | 30-day |
| --- | --- | --- |
| 1 | en (10,365, 97%) | en (51,556, 97%) |
| 2 | es (58, 1%) | es (279, 1%) |
| 3 | de (54, 1%) | de (243, 0%) |
| 4 | fr (41, 0%) | fr (194, 0%) |
| 5 | nl (30, 0%) | ja (164, 0%) |
| 6 | it (23, 0%) | nl (158, 0%) |
| 7 | pt (21, 0%) | it (142, 0%) |
| 8 | ja (19, 0%) | pt (128, 0%) |
| 9 | da (8, 0%) | sv (37, 0%) |
| 10 | sv (7, 0%) | da (35, 0%) |

## Methodology

- **Quantitative metrics** (thumbs-down %, per-client breakdown) use the `FeedbackType`
  field from ALL OCV feedback, including items with no verbatim text.
- **Qualitative metrics** (categories, top issues) use regex-matched categories from
  verbatim-only items. These explain *what* causes negative sentiment but represent a
  biased subset (users who write text skew more negative).
- **Text Sentiment** (NLP-derived) is shown for comparison but is NOT the primary metric.
- Extractions used `--include-blank` flag and `track_total_hits: true` for complete data.

---
*Analysis generated 2026-03-24 from OCV extraction data with corrected methodology.*
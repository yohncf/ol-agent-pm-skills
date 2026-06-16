# Two-pass analysis architecture

> **Parent skill:** `ocv-analyze/SKILL.md`
> Read this when authoring a Pass 2 batch, deciding whether to fall back
> to single-pass, or explaining the token math to a user.

## Contents

1. Strategy at a glance
2. Why two passes — token math
3. When to fall back to single-pass
4. Compact line format spec
5. Pass 1 metadata header

---

## 1. Strategy at a glance

All analysis uses a **two-pass strategy** to maximize the number of items
that fit in the context window:

| Pass | What | How | LLM? |
|------|------|-----|------|
| **Pass 1 — Metadata** | Aggregate stats, distributions, segment breakdowns | Read full CSV programmatically (Node.js / inline script) | No |
| **Pass 2 — Verbatim** | Theme discovery, flagging, categorization, executive summary | Send compact comment lines + Pass 1 summary as context | Yes |

## 2. Why two passes — token math

A full CSV row with all 31 fields averages ~205 tokens. Stripping to a
compact tag line (`[Rating|Scenario|Client] comment text`) averages
**~24 tokens** — an **88% reduction**. This means:

| Items | Single-pass (full row) | Two-pass (compact) | Batches needed |
|------:|----------------------:|--------------------|:--------------:|
| 50 | ~10K tokens | ~1.2K + overhead | 1 |
| 500 | ~103K tokens | ~12K | 1 |
| 2,000 | ~411K (exceeds window) | ~48K | 1 |
| 5,000 | ~1M (impossible) | ~120K | 1 |
| 10,000 | ~2M (impossible) | ~240K | 2 |

## 3. When to fall back to single-pass

For datasets **under ~100 items**, the overhead of the metadata summary
makes two-pass slightly less efficient. In that case, fall back to
sending full rows directly (skip the compact format).

## 4. Compact line format spec

For Pass 2, format each verbatim item as a single line:

```
<row_number>. [<Rating>|<Scenario>|<Client>|<Lang>|<Audience>] <Comment text>
```

Example:
```
42. [ThumbsDown|Elaborate|Desktop (Win32)|en|External] It sounds like it was written by someone from HR.
153. [ThumbsDown|DAB|Web (Monarch)|es|Internal] Copilot summary missed the key action items from the thread.
891. [ThumbsUp|Compose|Web (OWA)|de|External] Draft was perfect, saved me 10 minutes.
```

The `Lang` tag is the 2-letter language code (en, es, de, fr, ja, etc.).
The `Audience` tag is `Internal` or `External`. These add ~12 tokens per
line to the compact format but enable the LLM to spot language-specific
and audience-specific patterns during theme discovery.

## 5. Pass 1 metadata header

Include the **Pass 1 metadata summary** at the top of every batch so the
LLM has aggregate context while reading individual comments.

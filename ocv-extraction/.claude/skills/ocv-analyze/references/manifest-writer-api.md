# Manifest writer API — `../shared/lib/manifest_writer.js`

> **Parent skill:** `ocv-analyze/SKILL.md`
> Read this when authoring Step 11 (generate analysis manifest).
> The Node.js module is the canonical interface; do not hand-write
> the JSON.

## Contents

1. Module load and constants
2. `createManifest(csvPath, configPath, opts)`
3. `addMetadata(manifest, stats)`
4. `addThemes(manifest, themes)`
5. `addFlaggedItems(manifest, items)`
6. `addExecutiveSummary(manifest, text)`
7. `defaultManifestPath(csvPath)` and `writeManifest(manifest, path)`
8. End-to-end example
9. Output path convention
10. Paraphrase generation rules
11. Customer-content prohibition (critical)

---

## 1. Module load

```javascript
const mw = require('./../shared/lib/manifest_writer');
```

## 2. `createManifest(csvPath, configPath, opts)`

Creates a fresh manifest with source metadata:

```javascript
const manifest = mw.createManifest(csvPath, configPath, {
  dateRange: { start: '2026-03-26', end: '2026-04-02' },
  totalItems: 60800,
  verbatimItems: 9812,
});
```

## 3. `addMetadata(manifest, stats)`

Adds the Pass 1 aggregate stats produced by the metadata pass:

```javascript
mw.addMetadata(manifest, {
  sentimentDistribution: { Negative: 4200, Neutral: 3100, Positive: 2512 },
  ratingDistribution: { ThumbsDown: 45000, ThumbsUp: 15800 },
  scenarioDistribution: { Elaborate: 12000, DAB: 9500 },
  clientDistribution: { 'Desktop (Win32)': 20000, 'Web (Monarch)': 18000 },
  // ... all breakdowns from Step 3
});
```

## 4. `addThemes(manifest, themes)`

Each theme contains an OcvId pointer set and AI paraphrases for the
top 3–5 example items:

```javascript
mw.addThemes(manifest, [
  {
    name: 'Wrong Language Output',
    count: 342,
    description: 'Copilot responds in a different language than expected.',
    sentiment: { Negative: 310, Neutral: 25, Positive: 7 },
    examples: [
      { ocvId: 'fdcl_v4_abc123', paraphrase: 'User wrote in English but got a Spanish response in Elaborate.' },
      { ocvId: 'fdcl_v4_def456', paraphrase: 'Compose draft came back in French despite German UI settings.' },
    ],
  },
]);
```

## 5. `addFlaggedItems(manifest, items)`

```javascript
mw.addFlaggedItems(manifest, [
  {
    reason: 'Competitor mentions',
    ocvIds: ['fdcl_v4_x1', 'fdcl_v4_x2'],
    paraphrase: 'Users mention switching to Gmail/Gemini due to repeated language issues.',
  },
]);
```

## 6. `addExecutiveSummary(manifest, text)`

```javascript
mw.addExecutiveSummary(manifest, 'The executive summary text...');
```

## 7. `defaultManifestPath` and `writeManifest`

```javascript
const outputPath = mw.defaultManifestPath(csvPath);
mw.writeManifest(manifest, outputPath);
```

## 8. End-to-end example

```javascript
const mw = require('./../shared/lib/manifest_writer');

const manifest = mw.createManifest(csvPath, configPath, {
  dateRange: { start: '2026-03-26', end: '2026-04-02' },
  totalItems: 60800,
  verbatimItems: 9812,
});

mw.addMetadata(manifest, { /* Pass 1 stats */ });
mw.addThemes(manifest, [ /* themes */ ]);
mw.addFlaggedItems(manifest, [ /* flagged items */ ]);
mw.addExecutiveSummary(manifest, 'Summary text...');

const outputPath = mw.defaultManifestPath(csvPath);
mw.writeManifest(manifest, outputPath);
```

## 9. Output path convention

`data/manifests/<csv_basename>_manifest.json`

`defaultManifestPath(csvPath)` derives this for you — do not assemble
the path by hand.

## 10. Paraphrase generation rules

During Pass 2 theme discovery, for each theme's top 3–5 example items,
generate a **1-sentence AI paraphrase** that captures the gist without
reproducing the user's words verbatim. The paraphrase is written in PM
voice (e.g., "User reports Copilot responded in wrong language when
using Elaborate on Win32").

## 11. Customer-content prohibition (critical)

The manifest must contain **NO raw customer content** — no verbatim
`Comment` text, no `PromptInEnglish`, no `ResponseInEnglish`. Only
OcvIds (system identifiers) and AI-generated paraphrases. This is the
single most important constraint of the manifest format; violating it
breaks the entire data-handling story documented in the parent skill.

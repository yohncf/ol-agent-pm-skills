#!/usr/bin/env node
// OCV Local Analysis Script
// Analyzes extracted CSV feedback using a local LLM (Ollama + Phi-4-mini).
// All processing happens on your machine — no customer content leaves your device.
//
// Usage:
//   node scripts/analyze_local.js data/ocv_accounts_2026-03-10_range.csv
//   node scripts/analyze_local.js data/ocv_accounts_2026-03-10_range.csv --top 20
//   node scripts/analyze_local.js data/ocv_accounts_2026-03-10_range.csv --model phi4-mini
//
// Output: Aggregate insights only (themes, category suggestions, flagged row numbers).
// Never prints customer verbatim text.

const fs = require('fs');
const path = require('path');

// --- CLI Args ---

const args = process.argv.slice(2);
const csvPath = args.find(a => !a.startsWith('--'));
const topN = parseInt(args.find(a => a.startsWith('--top'))?.split('=')[1] || '10');
const modelName = args.find(a => a.startsWith('--model'))?.split('=')[1] || 'phi4-mini';
const OLLAMA_URL = 'http://localhost:11434/api/generate';
const BATCH_SIZE = 25;

if (!csvPath || !fs.existsSync(csvPath)) {
  console.error('Usage: node scripts/analyze_local.js <csv-file> [--top=N] [--model=phi4-mini]');
  console.error('  csv-file: Path to extracted OCV CSV');
  console.error('  --top=N:  Number of themes to extract (default: 10)');
  console.error('  --model:  Ollama model name (default: phi4-mini)');
  process.exit(1);
}

// --- CSV Parser ---

const { parseCSV } = require('./lib/csv_parser');

// --- Ollama API ---

async function ollamaGenerate(prompt, options = {}) {
  const body = {
    model: modelName,
    prompt,
    stream: false,
    options: {
      temperature: 0.3,
      num_predict: 1024,
      ...options,
    },
  };

  try {
    const res = await fetch(OLLAMA_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Ollama HTTP ${res.status}: ${text.slice(0, 200)}`);
    }
    const data = await res.json();
    return data.response || '';
  } catch (e) {
    if (e.cause?.code === 'ECONNREFUSED') {
      console.error('ERROR: Ollama is not running. Start it with: ollama serve');
      process.exit(1);
    }
    throw e;
  }
}

// --- Analysis Functions ---

async function extractThemes(comments, batchLabel) {
  // Send comments in batches, ask the LLM to identify themes
  // LLM sees the text locally but output is themes only
  const prompt = `You are analyzing customer feedback for a software product (Microsoft Outlook email accounts).
Below are ${comments.length} customer comments. Identify the top recurring themes/issues.

For each theme, provide:
1. A short theme name (2-4 words)
2. How many comments relate to this theme (approximate count)
3. One sentence describing the issue

Format your response as a numbered list. Only output the themes, never quote customer text.

COMMENTS:
${comments.map((c, i) => `[${i + 1}] ${c}`).join('\n')}

THEMES:`;

  return ollamaGenerate(prompt);
}

async function suggestCategories(comments, existingCategories) {
  const prompt = `You are analyzing uncategorized customer feedback for Microsoft Outlook.
The current category patterns are: ${existingCategories.join(', ')}

Below are ${comments.length} feedback comments that did NOT match any existing category.
Suggest 3-5 new category names with regex keyword patterns that would match these comments.

Format each suggestion as:
CATEGORY: [Name]
KEYWORDS: [comma-separated regex patterns]
COUNT: [approximate number of comments this would match]

Only output category suggestions. Never quote customer text verbatim.

UNCATEGORIZED COMMENTS:
${comments.map((c, i) => `[${i + 1}] ${c}`).join('\n')}

SUGGESTIONS:`;

  return ollamaGenerate(prompt);
}

async function flagHighValue(comments, rowNums) {
  const prompt = `You are triaging customer feedback for Microsoft Outlook.
Below are ${comments.length} customer comments with their row numbers.

Identify the most actionable/valuable items — feedback that contains:
- Specific error messages or codes
- Detailed reproduction steps
- Impact descriptions (e.g., "entire team affected")
- Mentions of switching to a competitor

Return ONLY the row numbers of the top 15-20 most valuable items, grouped by reason.
Format:
REASON: [short description]
ROWS: [comma-separated row numbers]

Never quote customer text. Only output row numbers and reason labels.

COMMENTS:
${comments.map((c, i) => `[ROW ${rowNums[i]}] ${c}`).join('\n')}

FLAGGED:`;

  return ollamaGenerate(prompt);
}

async function generateExecutiveSummary(themeResults, stats) {
  const prompt = `You are a PM analyzing customer feedback data for Microsoft Outlook email accounts.
Based on the following aggregate statistics and theme analysis, write a 3-4 sentence executive summary.
Focus on: what's broken, how severe it is, and what to investigate first.

STATISTICS:
- Total items: ${stats.total}
- Date range: ${stats.dateRange}
- Sentiment: ${stats.sentiment}
- Intent: ${stats.intent}
- Top categories: ${stats.categories}
- Top providers: ${stats.providers}
- Languages: ${stats.languages}

THEME ANALYSIS:
${themeResults}

Write the summary in direct, active voice. No hedging. Start with "TL;DR:".

SUMMARY:`;

  return ollamaGenerate(prompt);
}

// --- Main ---

async function main() {
  console.log('=== OCV Local Analysis ===');
  console.log(`CSV:   ${csvPath}`);
  console.log(`Model: ${modelName} (local via Ollama)`);
  console.log('');

  // Check Ollama is running
  try {
    await fetch('http://localhost:11434/api/tags');
  } catch {
    console.error('ERROR: Ollama is not running.');
    console.error('  1. Install Ollama: winget install Ollama.Ollama');
    console.error('  2. Pull the model: ollama pull phi4-mini');
    console.error('  3. Start Ollama: ollama serve');
    console.error('  Then run this script again.');
    process.exit(1);
  }

  // Parse CSV
  const raw = fs.readFileSync(csvPath, 'utf8');
  const { rows } = parseCSV(raw);
  console.log(`Loaded ${rows.length} items.\n`);

  if (rows.length === 0) {
    console.log('No data to analyze.');
    return;
  }

  // --- Basic Stats (no LLM needed) ---
  console.log('--- Aggregate Statistics ---');
  const dist = (field) => {
    const counts = {};
    for (const r of rows) {
      const val = r[field] || 'Unknown';
      counts[val] = (counts[val] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  };
  const fmt = (entries, limit) =>
    entries.slice(0, limit || entries.length).map(([k, v]) => `${k}=${v}`).join('  ');

  const dates = rows.map(r => r.Date).filter(Boolean);
  const dateRange = dates.length > 0 ? `${dates[dates.length - 1]} to ${dates[0]}` : 'N/A';
  const sentimentDist = dist('Sentiment');
  const intentDist = dist('Intent');
  const catDist = dist('Category');
  const langDist = dist('Language');
  const provDist = dist('Provider').filter(([k]) => k !== '' && k !== '[CUSTOM_DOMAIN]');

  const stats = {
    total: rows.length,
    dateRange,
    sentiment: fmt(sentimentDist),
    intent: fmt(intentDist),
    categories: fmt(catDist, 10),
    providers: fmt(provDist, 8),
    languages: fmt(langDist, 8),
  };

  console.log(`Items:      ${stats.total}`);
  console.log(`Date range: ${stats.dateRange}`);
  console.log(`Sentiment:  ${stats.sentiment}`);
  console.log(`Intent:     ${stats.intent}`);
  console.log(`Categories: ${stats.categories}`);
  console.log(`Languages:  ${stats.languages}`);
  if (provDist.length > 0) console.log(`Providers:  ${stats.providers}`);
  console.log('');

  // --- LLM Analysis ---
  const comments = rows.map(r => r.Comment || '').filter(Boolean);
  const uncategorized = rows.filter(r => !r.Category || r.Category === '');
  const uncatComments = uncategorized.map(r => r.Comment || '').filter(Boolean);
  const existingCats = [...new Set(rows.map(r => r.Category).filter(c => c && c !== ''))];

  // Theme extraction (process in batches, then consolidate)
  console.log('--- Theme Discovery (local LLM) ---');
  console.log(`Analyzing ${comments.length} items in batches of ${BATCH_SIZE}...`);

  const batchThemes = [];
  // Sample evenly across the dataset for theme discovery
  const sampleSize = Math.min(comments.length, BATCH_SIZE * 4);
  const step = Math.max(1, Math.floor(comments.length / sampleSize));
  const sampledComments = [];
  for (let i = 0; i < comments.length && sampledComments.length < sampleSize; i += step) {
    sampledComments.push(comments[i]);
  }

  for (let i = 0; i < sampledComments.length; i += BATCH_SIZE) {
    const batch = sampledComments.slice(i, i + BATCH_SIZE);
    const batchNum = Math.floor(i / BATCH_SIZE) + 1;
    const totalBatches = Math.ceil(sampledComments.length / BATCH_SIZE);
    process.stdout.write(`  Batch ${batchNum}/${totalBatches}...`);
    const themes = await extractThemes(batch, `batch ${batchNum}`);
    batchThemes.push(themes);
    console.log(' done');
  }

  // Consolidate themes across batches
  if (batchThemes.length > 1) {
    console.log('  Consolidating themes...');
    const consolidatePrompt = `Below are theme analyses from ${batchThemes.length} batches of customer feedback.
Merge them into a single list of the top ${topN} most important themes.
For each theme: name, estimated total count, one-sentence description.
Format as a numbered list. Never quote customer text.

${batchThemes.map((t, i) => `BATCH ${i + 1}:\n${t}`).join('\n\n')}

CONSOLIDATED TOP ${topN} THEMES:`;
    const consolidated = await ollamaGenerate(consolidatePrompt);
    console.log('\n' + consolidated);
  } else if (batchThemes.length === 1) {
    console.log('\n' + batchThemes[0]);
  }
  console.log('');

  // Category suggestions for uncategorized items
  if (uncatComments.length > 0 && uncatComments.length > rows.length * 0.2) {
    console.log('--- Category Gap Analysis (local LLM) ---');
    console.log(`${uncatComments.length} of ${rows.length} items (${Math.round(uncatComments.length / rows.length * 100)}%) are uncategorized.`);

    // Sample uncategorized items
    const uncatSample = [];
    const uncatStep = Math.max(1, Math.floor(uncatComments.length / 50));
    for (let i = 0; i < uncatComments.length && uncatSample.length < 50; i += uncatStep) {
      uncatSample.push(uncatComments[i]);
    }

    process.stdout.write('  Analyzing uncategorized items...');
    const catSuggestions = await suggestCategories(uncatSample, existingCats);
    console.log(' done\n');
    console.log(catSuggestions);
    console.log('');
  }

  // Flag high-value items
  console.log('--- High-Value Feedback (flagged row numbers) ---');
  // Sample and flag in batches
  const negativeEnglish = rows.filter(r =>
    r.Sentiment === 'Negative' && (r.Language === 'en' || r.Language === '') &&
    (r.Comment || '').length > 50
  );

  const flagSample = negativeEnglish.slice(0, 50);
  if (flagSample.length > 0) {
    process.stdout.write(`  Analyzing ${flagSample.length} candidates...`);
    const flagged = await flagHighValue(
      flagSample.map(r => r.Comment),
      flagSample.map(r => r._rowNum)
    );
    console.log(' done\n');
    console.log(flagged);
    console.log('');
    console.log('Open the CSV in Excel and use Ctrl+G (Go To) to jump to these rows.');
  } else {
    console.log('  No negative English feedback found to flag.');
  }
  console.log('');

  // Executive summary
  console.log('--- Executive Summary (local LLM) ---');
  const themeText = batchThemes.join('\n');
  process.stdout.write('  Generating...');
  const summary = await generateExecutiveSummary(themeText, stats);
  console.log(' done\n');
  console.log(summary);
  console.log('');

  console.log('=== Analysis Complete ===');
  console.log('All processing was performed locally via Ollama. No data left your machine.');
}

main().catch(err => {
  console.error('Analysis failed:', err.message);
  process.exit(1);
});

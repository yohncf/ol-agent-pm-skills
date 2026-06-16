#!/usr/bin/env node
// Compose Scenario Analysis — Corrected Quantitative Report
//
// Produces a two-layer analysis:
//   1. Quantitative: % thumbs-down per client from ALL feedback (Rating/FeedbackType)
//   2. Qualitative: Category breakdowns + sample verbatims from verbatim-only items
//
// Usage:
//   node scripts/compose_scenario_analysis.js --7d data/7d.csv --30d data/30d.csv [--output report.md]

const fs = require('fs');
const path = require('path');
const { parseCSV } = require('../../shared/lib/csv_parser');

// --- CLI Args ---
const args = process.argv.slice(2);
function getArg(flag) {
  const idx = args.indexOf(flag);
  if (idx === -1) return null;
  return args[idx + 1];
}

const file7d = getArg('--7d');
const file30d = getArg('--30d');
const derive7d = args.includes('--derive-7d');  // derive 7d slice from 30d data
const outputPath = getArg('--output') || path.join(__dirname, '..', 'data', 'summaries',
  `compose_scenario_analysis_corrected_${new Date().toISOString().slice(0, 10)}.md`);

if (!file7d && !file30d) {
  console.error('Usage: node compose_scenario_analysis.js --7d <7d.csv> [--30d <30d.csv>]');
  process.exit(1);
}

// --- Load Data ---
function loadCSV(filepath) {
  const text = fs.readFileSync(filepath, 'utf8');
  const { headers, rows } = parseCSV(text);
  console.log(`Loaded ${filepath}: ${rows.length} rows, columns: ${headers.join(', ')}`);
  return rows;
}

const data7d = file7d ? loadCSV(file7d) : [];
const data30d = file30d ? loadCSV(file30d) : [];

// Derive 7d slice from 30d data if requested
let derived7d = [];
if (derive7d && data30d.length > 0) {
  const range = dateRange(data30d);
  if (range.max) {
    const cutoff = new Date(range.max);
    cutoff.setDate(cutoff.getDate() - 6); // 7 days inclusive
    cutoff.setHours(0, 0, 0, 0);
    derived7d = data30d.filter(r => {
      const d = parseDate(r.Date);
      return d && d >= cutoff;
    });
    console.log(`Derived 7d slice: ${derived7d.length} items (${fmtDate(cutoff)}–${fmtDate(range.max)})`);
  }
}
const effective7d = data7d.length > 0 ? data7d : derived7d;

// --- Helpers ---
const pct = (n, d) => d === 0 ? '—' : `${Math.round(n / d * 100)}%`;
const pctNum = (n, d) => d === 0 ? 0 : Math.round(n / d * 100);

function dist(rows, field) {
  const counts = {};
  for (const r of rows) {
    const val = r[field] || 'Unknown';
    counts[val] = (counts[val] || 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

function parseDate(ds) {
  if (!ds) return null;
  const m = ds.match(/^(\d{2})\/(\d{2})\/(\d{2})/);
  if (!m) return null;
  return new Date(2000 + parseInt(m[3]), parseInt(m[1]) - 1, parseInt(m[2]));
}

function dateRange(rows) {
  const dates = rows.map(r => parseDate(r.Date)).filter(Boolean);
  if (dates.length === 0) return { min: null, max: null };
  return {
    min: new Date(Math.min(...dates)),
    max: new Date(Math.max(...dates)),
  };
}

function fmtDate(d) {
  if (!d) return '?';
  return `${d.toLocaleString('en-US', { month: 'short' })} ${d.getDate()}, ${d.getFullYear()}`;
}

// --- Analysis Functions ---

function analyzeDataset(rows, label) {
  const total = rows.length;
  const withVerbatim = rows.filter(r => r.Comment && r.Comment.trim());
  const blank = total - withVerbatim.length;

  // Rating-based sentiment (from FeedbackType: ThumbsDown/ThumbsUp)
  const thumbsDown = rows.filter(r => r.Rating === 'ThumbsDown').length;
  const thumbsUp = rows.filter(r => r.Rating === 'ThumbsUp').length;
  const unknownRating = total - thumbsDown - thumbsUp;

  // Per-client breakdown (all feedback)
  const clients = ['Desktop', 'Mac', 'OWA'];
  const clientStats = {};
  for (const c of clients) {
    const clientRows = rows.filter(r => r.Client === c);
    const clientDown = clientRows.filter(r => r.Rating === 'ThumbsDown').length;
    const clientUp = clientRows.filter(r => r.Rating === 'ThumbsUp').length;
    clientStats[c] = {
      total: clientRows.length,
      thumbsDown: clientDown,
      thumbsUp: clientUp,
      downPct: pctNum(clientDown, clientRows.length),
    };
  }

  // Handle any clients not in the standard list
  const otherRows = rows.filter(r => !clients.includes(r.Client));
  if (otherRows.length > 0) {
    const otherDown = otherRows.filter(r => r.Rating === 'ThumbsDown').length;
    clientStats['Other'] = {
      total: otherRows.length,
      thumbsDown: otherDown,
      thumbsUp: otherRows.filter(r => r.Rating === 'ThumbsUp').length,
      downPct: pctNum(otherDown, otherRows.length),
    };
  }

  // Verbatim-only NLP sentiment (for comparison)
  const verbatimNeg = withVerbatim.filter(r =>
    r.Sentiment === 'Negative').length;
  const verbatimNegPct = pctNum(verbatimNeg, withVerbatim.length);

  // Verbatim-only per-client NLP sentiment
  const verbatimClientStats = {};
  for (const c of clients) {
    const cVerb = withVerbatim.filter(r => r.Client === c);
    const cNeg = cVerb.filter(r => r.Sentiment === 'Negative').length;
    verbatimClientStats[c] = {
      total: cVerb.length,
      negative: cNeg,
      negPct: pctNum(cNeg, cVerb.length),
    };
  }

  // Categories (from verbatim-only items)
  const catDist = dist(withVerbatim, 'Category')
    .filter(([k]) => k !== 'Unknown' && k !== '');

  // Categories per client (verbatim-only)
  const catByClient = {};
  for (const [catName] of catDist) {
    catByClient[catName] = {};
    for (const c of clients) {
      catByClient[catName][c] = withVerbatim.filter(r =>
        r.Category === catName && r.Client === c).length;
    }
    catByClient[catName].total = withVerbatim.filter(r => r.Category === catName).length;
  }

  // Languages
  const langDist = dist(rows, 'Language').slice(0, 10);

  // Date range
  const range = dateRange(rows);

  return {
    label,
    total,
    withVerbatim: withVerbatim.length,
    blank,
    thumbsDown,
    thumbsUp,
    unknownRating,
    clientStats,
    verbatimNegPct,
    verbatimClientStats,
    catDist,
    catByClient,
    langDist,
    range,
    clients,
  };
}

// --- Report Generation ---

function generateReport(analysis7d, analysis30d) {
  const lines = [];
  const w = (s) => lines.push(s);

  const range7d = analysis7d?.range;
  const range30d = analysis30d?.range;

  w('# Copilot Compose Scenarios — Corrected OCV Analysis');
  w(`**Generated:** ${new Date().toISOString().slice(0, 10)}`);
  if (analysis7d) {
    w(`**7-day window:** ${fmtDate(range7d.min)}–${fmtDate(range7d.max)} (${analysis7d.total.toLocaleString()} items, ALL feedback)`);
  }
  if (analysis30d) {
    w(`**30-day window:** ${fmtDate(range30d.min)}–${fmtDate(range30d.max)} (${analysis30d.total.toLocaleString()} items, ALL feedback)`);
  }
  w('');
  w('> **Methodology change:** This report uses `FeedbackType` (ThumbsDown/ThumbsUp) from ALL');
  w('> feedback items — including those with no verbatim text — for quantitative metrics.');
  w('> Previous reports used NLP-derived `Text Sentiment` from verbatim-only items, which');
  w('> overstated the negative rate because unhappy users write text more often.');
  w('');

  // --- Executive Summary ---
  w('## Executive Summary');
  w('');
  for (const a of [analysis7d, analysis30d].filter(Boolean)) {
    const downPct = pct(a.thumbsDown, a.total);
    w(`- **${a.label} volume:** ${a.total.toLocaleString()} items — **Thumbs Down: ${downPct}** (${a.thumbsDown.toLocaleString()})`);
    w(`  - Verbatim coverage: ${a.withVerbatim.toLocaleString()} with text (${pct(a.withVerbatim, a.total)}), ${a.blank.toLocaleString()} blank`);
  }
  w('');

  // --- Bias Correction ---
  w('## ⚠️ Verbatim Bias Correction');
  w('');
  w('| Metric | Verbatim-Only (old method) | All Feedback (corrected) | Skew |');
  w('| --- | --- | --- | --- |');
  for (const a of [analysis7d, analysis30d].filter(Boolean)) {
    const corrected = pctNum(a.thumbsDown, a.total);
    const skew = a.verbatimNegPct - corrected;
    w(`| ${a.label} Negative % | ${a.verbatimNegPct}% (n=${a.withVerbatim.toLocaleString()}) | ${corrected}% (n=${a.total.toLocaleString()}) | ${skew > 0 ? '+' : ''}${skew}pp overstated |`);
  }
  w('');

  // Mac data collection warning
  for (const a of [analysis30d, analysis7d].filter(Boolean)) {
    const macStats = a.clientStats['Mac'];
    if (macStats && macStats.thumbsUp === 0 && macStats.total > 100) {
      w('### ⚠️ Mac Data Collection Caveat');
      w('');
      w(`Mac Outlook shows **100% ThumbsDown** (${macStats.total.toLocaleString()} items, 0 ThumbsUp).`);
      w('This is NOT because Mac users are universally unhappy — the Mac Outlook client');
      w('**only routes negative (frown) feedback to OCV**. ThumbsUp clicks on Mac do not');
      w('create OCV items. Per-client ThumbsDown% comparisons are only valid between');
      w('Desktop and OWA, which collect both positive and negative feedback.');
      w('');
      break;  // only print once
    }
  }

  // Per-client bias (exclude Mac from bias table since it's all negative by design)
  w('### Per-Client Bias Comparison');
  w('');
  w('| Client | Period | Verbatim-Only Neg% | All-Feedback ThumbsDown% | Skew |');
  w('| --- | --- | --- | --- | --- |');
  for (const a of [analysis7d, analysis30d].filter(Boolean)) {
    for (const c of a.clients) {
      const all = a.clientStats[c];
      const verb = a.verbatimClientStats[c];
      if (!all || all.total === 0) continue;
      const macNote = (all.thumbsUp === 0 && all.total > 100) ? ' ⚠️ neg-only collection' : '';
      const skew = (verb?.negPct || 0) - all.downPct;
      w(`| ${c} | ${a.label} | ${verb?.negPct || 0}% (n=${verb?.total || 0}) | ${all.downPct}% (n=${all.total.toLocaleString()})${macNote} | ${skew > 0 ? '+' : ''}${skew}pp |`);
    }
  }
  w('');

  // --- Per-Client Overview (corrected) ---
  w('## Per-Client Overview (Corrected — All Feedback)');
  w('');
  w('| Period | Client | Count | % of total | ThumbsDown | ThumbsDown% |');
  w('| --- | --- | --- | --- | --- | --- |');
  for (const a of [analysis7d, analysis30d].filter(Boolean)) {
    for (const c of [...a.clients, 'Other']) {
      const s = a.clientStats[c];
      if (!s || s.total === 0) continue;
      w(`| ${a.label} | ${c} | ${s.total.toLocaleString()} | ${pct(s.total, a.total)} | ${s.thumbsDown.toLocaleString()} | ${s.downPct}% |`);
    }
  }
  w('');

  // --- Client Health ---
  w('### Client Health');
  w('');
  for (const a of [analysis7d].filter(Boolean)) {
    for (const c of a.clients) {
      const s = a.clientStats[c];
      if (!s || s.total === 0) continue;
      const icon = s.downPct >= 60 ? '🔴' : s.downPct >= 50 ? '🟡' : '🟢';
      w(`- ${icon} **${c}**: ${s.downPct}% thumbs down (${s.total.toLocaleString()} items)`);
    }
  }
  w('');

  // --- Category Breakdown (verbatim-only) ---
  w('## Category Breakdown (Verbatim-Only — Qualitative)');
  w('');
  w('> Categories are regex-matched from verbatim text. Only items with written feedback');
  w('> have categories. These provide qualitative color on *what* drives negative sentiment.');
  w('');

  for (const a of [analysis7d, analysis30d].filter(Boolean)) {
    w(`### ${a.label} Categories by Client`);
    w('');
    w('| Category | Desktop | Mac | OWA | Total | % of verbatims |');
    w('| --- | --- | --- | --- | --- | --- |');
    for (const [catName] of a.catDist) {
      const cb = a.catByClient[catName];
      w(`| ${catName} | ${cb.Desktop || 0} | ${cb.Mac || 0} | ${cb.OWA || 0} | ${cb.total} | ${pct(cb.total, a.withVerbatim)} |`);
    }
    w('');
  }

  // --- Language Distribution ---
  w('## Top Languages');
  w('');
  w('| Rank | 7-day | 30-day |');
  w('| --- | --- | --- |');
  const maxLangs = Math.max(
    analysis7d?.langDist.length || 0,
    analysis30d?.langDist.length || 0
  );
  for (let i = 0; i < Math.min(maxLangs, 10); i++) {
    const l7 = analysis7d?.langDist[i];
    const l30 = analysis30d?.langDist[i];
    const f7 = l7 ? `${l7[0]} (${l7[1].toLocaleString()}, ${pct(l7[1], analysis7d.total)})` : '';
    const f30 = l30 ? `${l30[0]} (${l30[1].toLocaleString()}, ${pct(l30[1], analysis30d.total)})` : '';
    w(`| ${i + 1} | ${f7} | ${f30} |`);
  }
  w('');

  // --- Methodology Notes ---
  w('## Methodology');
  w('');
  w('- **Quantitative metrics** (thumbs-down %, per-client breakdown) use the `FeedbackType`');
  w('  field from ALL OCV feedback, including items with no verbatim text.');
  w('- **Qualitative metrics** (categories, top issues) use regex-matched categories from');
  w('  verbatim-only items. These explain *what* causes negative sentiment but represent a');
  w('  biased subset (users who write text skew more negative).');
  w('- **Text Sentiment** (NLP-derived) is shown for comparison but is NOT the primary metric.');
  w('- Extractions used `--include-blank` flag and `track_total_hits: true` for complete data.');
  w('');
  w('---');
  w(`*Analysis generated ${new Date().toISOString().slice(0, 10)} from OCV extraction data with corrected methodology.*`);

  return lines.join('\n');
}

// --- Main ---
const a7d = effective7d.length > 0 ? analyzeDataset(effective7d, '7-day') : null;
const a30d = data30d.length > 0 ? analyzeDataset(data30d, '30-day') : null;

const report = generateReport(a7d, a30d);

// Ensure output directory exists
const outDir = path.dirname(outputPath);
if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

fs.writeFileSync(outputPath, report, 'utf8');
console.log(`\nReport written to: ${outputPath}`);

// Print key findings to console
console.log('\n=== KEY FINDINGS ===');
for (const a of [a7d, a30d].filter(Boolean)) {
  const corrected = pctNum(a.thumbsDown, a.total);
  const skew = a.verbatimNegPct - corrected;
  console.log(`\n${a.label}:`);
  console.log(`  Total: ${a.total.toLocaleString()} | Verbatim: ${a.withVerbatim.toLocaleString()} (${pct(a.withVerbatim, a.total)})`);
  console.log(`  ThumbsDown: ${corrected}% (corrected) vs ${a.verbatimNegPct}% (verbatim-only) → ${skew}pp skew`);
  for (const c of a.clients) {
    const all = a.clientStats[c];
    const verb = a.verbatimClientStats[c];
    if (!all || all.total === 0) continue;
    const s = (verb?.negPct || 0) - all.downPct;
    console.log(`  ${c}: ${all.downPct}% (n=${all.total.toLocaleString()}) [was ${verb?.negPct || 0}%, skew ${s > 0 ? '+' : ''}${s}pp]`);
  }
}

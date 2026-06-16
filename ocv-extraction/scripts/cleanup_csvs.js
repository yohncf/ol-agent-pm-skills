#!/usr/bin/env node
// CSV Cleanup — scans for CSVs containing verbatim customer feedback and prompts for deletion.
//
// Standalone:  node scripts/cleanup_csvs.js
// Via npm:     npm run cleanup
// Manifest:    node scripts/cleanup_csvs.js --manifest data/manifests/week2_manifest.json
// Scan all:    node scripts/cleanup_csvs.js --all-manifests [--dry-run]
// From extract_standalone.js: called automatically after extraction (skip with --no-cleanup)

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { readManifest, markCsvDeleted } = require('../../shared/lib/manifest_writer');

const VERBATIM_COLUMNS = ['Comment', 'Text', 'ProblemStatement', 'CustomerVerbatimTranslated'];
const DATA_DIR = path.join(__dirname, '..', 'data');
const PROJECT_ROOT = path.join(__dirname, '..');
const MANIFESTS_DIR = path.join(DATA_DIR, 'manifests');
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Reads the first line of a CSV and checks for verbatim-containing columns.
 */
function hasVerbatimColumn(filePath) {
  try {
    const fd = fs.openSync(filePath, 'r');
    const buf = Buffer.alloc(2048);
    const bytesRead = fs.readSync(fd, buf, 0, 2048, 0);
    fs.closeSync(fd);
    const header = buf.toString('utf8', 0, bytesRead).split(/\r?\n/)[0];
    return VERBATIM_COLUMNS.some(col => header.includes(col));
  } catch {
    return false;
  }
}

/**
 * Scans data/ and project root for CSVs with verbatim feedback.
 */
function findVerbatimCsvs() {
  const results = [];
  const seen = new Set();

  for (const dir of [DATA_DIR, PROJECT_ROOT]) {
    if (!fs.existsSync(dir)) continue;
    let files;
    try { files = fs.readdirSync(dir).filter(f => f.endsWith('.csv')); }
    catch { continue; }

    for (const file of files) {
      const fullPath = path.join(dir, file);
      const resolved = path.resolve(fullPath);
      if (seen.has(resolved)) continue;
      seen.add(resolved);

      if (!hasVerbatimColumn(fullPath)) continue;

      const stat = fs.statSync(fullPath);
      results.push({
        path: fullPath,
        resolved,
        name: file,
        dir: path.dirname(fullPath),
        size: stat.size,
        modified: stat.mtime,
        ageMs: Date.now() - stat.mtimeMs,
      });
    }
  }

  return results.sort((a, b) => b.ageMs - a.ageMs); // oldest first
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatAge(ms) {
  const minutes = ms / 60000;
  if (minutes < 60) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)} hours ago`;
  const days = hours / 24;
  if (days < 2) return '1 day ago';
  return `${Math.round(days)} days ago`;
}

function printCsvLine(csv) {
  const flag = csv.ageMs > ONE_DAY_MS ? ' ⚠️  >1 day old' : '';
  const rel = path.relative(PROJECT_ROOT, csv.path);
  console.log(`  📄 ${rel}  (${formatSize(csv.size)}, ${formatAge(csv.ageMs)})${flag}`);
}

async function prompt(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => {
    rl.question(question, answer => {
      rl.close();
      resolve(answer.trim().toLowerCase());
    });
  });
}

/**
 * Post-extraction cleanup. Called from extract_standalone.js after CSV is written.
 * @param {string} [justCreatedPath] - Path to the CSV just created (won't be auto-prompted for deletion)
 */
async function postExtractionCleanup(justCreatedPath) {
  const csvs = findVerbatimCsvs();
  if (csvs.length === 0) return;

  const justCreatedResolved = justCreatedPath ? path.resolve(justCreatedPath) : null;
  const old = csvs.filter(c => c.ageMs > ONE_DAY_MS && c.resolved !== justCreatedResolved);
  const current = csvs.filter(c => c.ageMs <= ONE_DAY_MS && c.resolved !== justCreatedResolved);

  // Prompt for old CSVs (>1 day)
  if (old.length > 0) {
    console.log(`\n⚠️  Found ${old.length} CSV${old.length > 1 ? 's' : ''} with verbatim feedback older than 1 day:`);
    for (const csv of old) printCsvLine(csv);

    const answer = await prompt(`\nDelete ${old.length} old CSV${old.length > 1 ? 's' : ''}? [y/N] `);
    if (answer === 'y' || answer === 'yes') {
      for (const csv of old) {
        fs.unlinkSync(csv.path);
        updateManifestForCsv(csv.resolved);
        console.log(`  🗑️  Deleted ${path.relative(PROJECT_ROOT, csv.path)}`);
      }
    } else {
      console.log('  Skipped.');
    }
  }

  // Prompt for other recent CSVs (not the one just created)
  if (current.length > 0) {
    console.log(`\n📋 Other recent CSVs with verbatim feedback (from today):`);
    for (const csv of current) printCsvLine(csv);

    const answer = await prompt(`Delete ${current.length} recent CSV${current.length > 1 ? 's' : ''}? [y/N] `);
    if (answer === 'y' || answer === 'yes') {
      for (const csv of current) {
        fs.unlinkSync(csv.path);
        updateManifestForCsv(csv.resolved);
        console.log(`  🗑️  Deleted ${path.relative(PROJECT_ROOT, csv.path)}`);
      }
    } else {
      console.log('  Kept.');
    }
  }

  // Note the just-created file
  if (justCreatedResolved) {
    const rel = path.relative(PROJECT_ROOT, justCreatedResolved);
    console.log(`\n📋 Current extraction: ${rel}`);
    console.log('   Run analysis to create a manifest, then clean up the CSV.');
    console.log('   Run \`npm run cleanup\` to delete manually after analysis.');
  }
}

/**
 * Find the manifest that references a given CSV path. Returns null if none found.
 */
function findManifestForCsv(csvResolvedPath) {
  if (!fs.existsSync(MANIFESTS_DIR)) return null;
  const csvBase = path.basename(csvResolvedPath);
  const files = fs.readdirSync(MANIFESTS_DIR).filter(f => f.endsWith('.json'));
  for (const f of files) {
    try {
      const m = readManifest(path.join(MANIFESTS_DIR, f));
      if (m.source && m.source.csvFile === csvBase) {
        return path.join(MANIFESTS_DIR, f);
      }
    } catch { /* skip malformed manifests */ }
  }
  return null;
}

/**
 * After deleting a CSV, update its manifest if one exists.
 */
function updateManifestForCsv(csvResolvedPath) {
  const manifestPath = findManifestForCsv(csvResolvedPath);
  if (manifestPath) {
    try {
      markCsvDeleted(manifestPath);
    } catch { /* best effort */ }
  }
}

/**
 * Manifest-based cleanup: delete the CSV referenced by a specific manifest.
 */
async function cleanupByManifest(manifestPath, dryRun = false) {
  let manifest;
  try {
    manifest = readManifest(manifestPath);
  } catch (e) {
    console.error(`  ✗ Could not read manifest: ${manifestPath}`);
    return false;
  }

  if (manifest.csvDeleted) {
    console.log(`  ○ Already cleaned: ${manifest.source.csvFile} (deleted ${manifest.csvDeletedAt})`);
    return false;
  }

  let csvPath = manifest.source.csvPath;
  if (!path.isAbsolute(csvPath)) {
    csvPath = path.resolve(PROJECT_ROOT, csvPath);
  }

  if (!fs.existsSync(csvPath)) {
    console.log(`  ○ CSV not found (already deleted?): ${manifest.source.csvFile}`);
    if (!dryRun) {
      markCsvDeleted(manifestPath);
      console.log(`    Updated manifest.`);
    }
    return false;
  }

  const sizeKB = Math.round(fs.statSync(csvPath).size / 1024);
  console.log(`  → ${manifest.source.csvFile} (${formatSize(fs.statSync(csvPath).size)}, ${manifest.source.totalItems} items)`);
  console.log(`    Date range: ${manifest.source.dateRange.start || '?'} — ${manifest.source.dateRange.end || '?'}`);
  console.log(`    Themes: ${manifest.themes.length}, Flagged: ${manifest.flaggedItems.length}`);

  if (dryRun) {
    console.log(`    [DRY RUN] Would delete: ${csvPath}`);
    return true;
  }

  const answer = await prompt(`    Delete this CSV? [y/N] `);
  if (answer === 'y' || answer === 'yes') {
    fs.unlinkSync(csvPath);
    markCsvDeleted(manifestPath);
    console.log(`    ✓ Deleted CSV and updated manifest.`);
    return true;
  }
  console.log(`    — Skipped.`);
  return false;
}

/**
 * Scan all manifests and clean up CSVs that have been analyzed.
 */
async function cleanupAllManifests(dryRun = false) {
  if (!fs.existsSync(MANIFESTS_DIR)) {
    console.log('No manifests directory found. Run an analysis first.');
    return;
  }

  const files = fs.readdirSync(MANIFESTS_DIR).filter(f => f.endsWith('.json'));
  if (files.length === 0) {
    console.log('No manifests found in data/manifests/.');
    return;
  }

  if (dryRun) console.log('[DRY RUN — no files will be deleted]\n');
  console.log(`Scanning ${files.length} manifest(s)...\n`);

  let deleted = 0;
  for (const f of files.sort()) {
    const result = await cleanupByManifest(path.join(MANIFESTS_DIR, f), dryRun);
    if (result) deleted++;
  }

  console.log(`\n${deleted} CSV(s) ${dryRun ? 'would be' : ''} deleted.`);
}

// --- Standalone mode ---
if (require.main === module) {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const manifestIdx = args.indexOf('--manifest');
  const manifestArg = manifestIdx >= 0 ? args[manifestIdx + 1] : null;
  const allManifests = args.includes('--all-manifests');

  (async () => {
    // Mode 1: Clean up by specific manifest
    if (manifestArg) {
      const resolved = path.isAbsolute(manifestArg) ? manifestArg : path.resolve(manifestArg);
      if (!fs.existsSync(resolved)) {
        console.error(`Manifest not found: ${resolved}`);
        process.exit(1);
      }
      await cleanupByManifest(resolved, dryRun);
      return;
    }

    // Mode 2: Scan all manifests
    if (allManifests) {
      await cleanupAllManifests(dryRun);
      return;
    }

    // Mode 3: Default — scan for verbatim CSVs (original behavior)
    console.log('🔍 Scanning for CSVs with verbatim customer feedback...\n');
    const csvs = findVerbatimCsvs();

    if (csvs.length === 0) {
      console.log('No verbatim CSVs found. ✅');
      return;
    }

    const old = csvs.filter(c => c.ageMs > ONE_DAY_MS);
    const recent = csvs.filter(c => c.ageMs <= ONE_DAY_MS);

    if (old.length > 0) {
      console.log(`⚠️  ${old.length} CSV${old.length > 1 ? 's' : ''} older than 1 day:`);
      for (const csv of old) printCsvLine(csv);

      const answer = await prompt(`\nDelete ${old.length} old CSV${old.length > 1 ? 's' : ''}? [y/N] `);
      if (answer === 'y' || answer === 'yes') {
        for (const csv of old) {
          fs.unlinkSync(csv.path);
          updateManifestForCsv(csv.resolved);
          console.log(`  🗑️  Deleted ${path.relative(PROJECT_ROOT, csv.path)}`);
        }
      } else {
        console.log('  Skipped.');
      }
      console.log('');
    }

    if (recent.length > 0) {
      console.log(`📋 ${recent.length} recent CSV${recent.length > 1 ? 's' : ''} (today):`);
      for (const csv of recent) printCsvLine(csv);

      const answer = await prompt(`\nDelete ${recent.length} recent CSV${recent.length > 1 ? 's' : ''}? [y/N] `);
      if (answer === 'y' || answer === 'yes') {
        for (const csv of recent) {
          fs.unlinkSync(csv.path);
          updateManifestForCsv(csv.resolved);
          console.log(`  🗑️  Deleted ${path.relative(PROJECT_ROOT, csv.path)}`);
        }
      } else {
        console.log('  Kept.');
      }
    }

    console.log('\nDone.');
  })();
}

module.exports = { postExtractionCleanup, findVerbatimCsvs, hasVerbatimColumn, cleanupByManifest, cleanupAllManifests };

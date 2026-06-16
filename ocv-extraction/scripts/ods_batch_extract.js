#!/usr/bin/env node
// ODS Batch Extraction — Tags + Problem Statement
// Reads URLs from a CSV file, navigates to each ticket in a single browser session,
// and extracts Tags + Problem Statement metadata.
//
// Usage:
//   node scripts/ods_batch_extract.js --input <csv> --limit 20
//   node scripts/ods_batch_extract.js --input <csv> --limit 20 --output data/ods_results.csv

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('Error: Playwright is not installed.');
  console.error('Run "npm install" in the ocv-extraction directory, then try again.');
  process.exit(1);
}
const fs = require('fs');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, '..', '.browser-profile-ods');

// --- Parse args ---
const args = process.argv.slice(2);
function getArg(name, defaultVal) {
  const idx = args.indexOf(name);
  return idx !== -1 && args[idx + 1] ? args[idx + 1] : defaultVal;
}

const inputFile = getArg('--input', null);
const limit = parseInt(getArg('--limit', '9999'), 10);
const offset = parseInt(getArg('--offset', '0'), 10);
const outputFile = getArg('--output', null);
const appendMode = args.includes('--append');

if (!inputFile) {
  console.error('Usage: node scripts/ods_batch_extract.js --input <csv> [--limit N] [--offset N] [--output <csv>] [--append]');
  process.exit(1);
}

// --- Read URLs from CSV ---
function parseInputCSV(filePath, maxRows, skipRows) {
  const raw = fs.readFileSync(filePath, 'utf8');
  const lines = raw.split(/\r?\n/).filter(l => l.trim());
  const results = [];
  let skipped = 0;

  for (const line of lines) {
    if (results.length >= maxRows) break;
    const urlMatch = line.match(/(https:\/\/ods\.office\.net\S+)/);
    if (!urlMatch) continue;

    // Apply offset (skip already-processed rows)
    if (skipped < skipRows) {
      skipped++;
      continue;
    }

    // Parse CSV fields: date, product, type, category, url
    const fields = [];
    let current = '';
    let inQuotes = false;
    for (const ch of line) {
      if (ch === '"') { inQuotes = !inQuotes; continue; }
      if (ch === ',' && !inQuotes) { fields.push(current.trim()); current = ''; continue; }
      current += ch;
    }
    fields.push(current.trim());

    results.push({
      date: fields[0] || '',
      product: fields[1] || '',
      type: fields[2] || '',
      category: fields[3] || '',
      url: urlMatch[1],
    });
  }
  return results;
}

// --- Wait for ODS page content ---
async function waitForContent(page) {
  try {
    await page.waitForFunction(() => {
      const spinners = document.querySelectorAll(
        '[class*="spinner"], [class*="Spinner"], [class*="loading"], [class*="Loading"], ' +
        '[class*="progress"], [role="progressbar"], .md-spinner, md-progress-circular'
      );
      for (const s of spinners) {
        if (s.offsetParent !== null || getComputedStyle(s).display !== 'none') return false;
      }
      return true;
    }, { timeout: 30000 });
  } catch {}
  await page.waitForTimeout(5000);
  try {
    await page.waitForSelector('table', { timeout: 15000 });
  } catch {}
  await page.waitForTimeout(2000);
}

// --- Extract just metadata from the ticket page ---
async function extractMetadata(page) {
  return page.evaluate(() => {
    const metadata = {};
    const tables = document.querySelectorAll('table');
    if (tables.length > 0) {
      const rows = tables[0].querySelectorAll('tr');
      for (const row of rows) {
        const cells = row.querySelectorAll('td, th');
        if (cells.length >= 2) {
          const key = cells[0]?.textContent?.trim();
          const val = cells[1]?.textContent?.trim();
          if (key && val) metadata[key] = val;
        }
      }
    }
    return metadata;
  });
}

// --- Browser lifecycle ---
async function launchBrowser() {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: 'msedge',
    headless: false,
    viewport: { width: 1400, height: 900 },
  });
  const page = context.pages()[0] || await context.newPage();
  return { context, page };
}

function isContextCrash(err) {
  const msg = (err.message || '').toLowerCase();
  return msg.includes('context') || msg.includes('browser has been closed') || msg.includes('target page');
}

// --- CSV helpers ---
function csvEscape(value) {
  return `"${String(value || '').replace(/"/g, '""')}"`;
}

function writeCSV(results, outPath, append) {
  const header = 'TicketId,Date,Category,Tags,ProblemStatement,URL';
  const rows = results.map(r =>
    [r.ticketId, r.date, r.category, r.tags, r.problemStatement, r.url].map(csvEscape).join(',')
  );

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  if (append && fs.existsSync(outPath)) {
    fs.appendFileSync(outPath, '\n' + rows.join('\n'), 'utf8');
    console.log(`  CSV appended: ${outPath} (+${results.length} rows)`);
  } else {
    fs.writeFileSync(outPath, [header, ...rows].join('\n'), 'utf8');
    console.log(`  CSV saved: ${outPath}`);
  }
}

// --- Main ---
async function main() {
  const tickets = parseInputCSV(path.resolve(inputFile), limit, offset);
  console.log(`=== ODS Batch Extraction ===`);
  console.log(`Input: ${inputFile}`);
  console.log(`Offset: ${offset}, Limit: ${limit}, Append: ${appendMode}`);
  console.log(`Tickets to process: ${tickets.length}\n`);

  if (tickets.length === 0) {
    console.error('No valid ODS URLs found in input file.');
    process.exit(1);
  }

  let { context, page } = await launchBrowser();
  let restartCount = 0;
  const MAX_RESTARTS = 10;
  const results = [];

  try {
    for (let i = 0; i < tickets.length; i++) {
      const ticket = tickets[i];
      const ticketId = ticket.url.split('/').pop();
      console.log(`[${i + 1}/${tickets.length}] ${ticketId}...`);

      try {
        await page.goto(ticket.url, { waitUntil: 'networkidle', timeout: 60000 });

        // SSO check (only likely on first ticket or after restart)
        const currentUrl = page.url();
        if (currentUrl.includes('login.microsoftonline.com') || currentUrl.includes('login.microsoft.com')) {
          console.log('  SSO login required. Complete login in the browser...');
          await page.waitForURL('**/ods.office.net/**', { timeout: 120000 });
          await waitForContent(page);
        }

        await waitForContent(page);
        const metadata = await extractMetadata(page);

        const tags = metadata['Tags'] || '';
        const problemStatement = metadata['Problem Statement'] || '';

        results.push({
          ticketId,
          date: ticket.date,
          category: ticket.category,
          tags,
          problemStatement,
          url: ticket.url,
        });

        console.log(`  Tags: ${tags || '(none)'}`);
        console.log(`  Problem: ${problemStatement ? problemStatement.slice(0, 80) + (problemStatement.length > 80 ? '...' : '') : '(none)'}`);

      } catch (err) {
        if (isContextCrash(err) && restartCount < MAX_RESTARTS) {
          restartCount++;
          console.log(`\n  ⚠ Browser context crashed. Restarting browser (attempt ${restartCount}/${MAX_RESTARTS})...`);
          try { await context.close(); } catch {}
          await new Promise(r => setTimeout(r, 3000));
          ({ context, page } = await launchBrowser());
          console.log('  ✔ Browser restarted. Retrying ticket...\n');
          i--; // retry this ticket
          continue;
        }

        console.error(`  ERROR: ${err.message}`);
        results.push({
          ticketId,
          date: ticket.date,
          category: ticket.category,
          tags: 'ERROR',
          problemStatement: err.message,
          url: ticket.url,
        });
      }
    }

    // --- Summary ---
    console.log(`\n=== Results: ${results.length} tickets ===`);
    console.log(`  Successful: ${results.filter(r => r.tags !== 'ERROR').length}`);
    console.log(`  Errors: ${results.filter(r => r.tags === 'ERROR').length}`);
    console.log(`  Browser restarts: ${restartCount}`);

    // --- Write CSV ---
    const outPath = outputFile
      ? path.resolve(outputFile)
      : path.join(__dirname, '..', 'data', `ods_batch_${new Date().toISOString().slice(0, 10)}.csv`);
    writeCSV(results, outPath, appendMode);

    console.log('\n=== Batch Extraction Complete ===');

  } catch (err) {
    console.error('Batch extraction failed:', err.message);
    process.exit(1);
  } finally {
    try { await context.close(); } catch {}
  }
}

main().catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});

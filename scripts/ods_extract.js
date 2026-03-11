#!/usr/bin/env node
// ODS Sara Ticket Extraction — POC
// Extracts chat verbatim and user domain from a single ODS Sara ticket.
// Uses Playwright (Edge) with persistent profile for SSO, same pattern as OCV extraction.
//
// Usage:
//   node scripts/ods_extract.js <ticket-url>
//   node scripts/ods_extract.js https://ods.office.net/#/saratickets/<id>
//   node scripts/ods_extract.js <ticket-url> --json          Output as JSON
//   node scripts/ods_extract.js <ticket-url> --csv <file>    Save chat to CSV
//
// Output: Prints ticket metadata + chat transcript to terminal.

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
const args = process.argv.slice(2);
const url = args.find(a => !a.startsWith('--'));
const jsonFlag = args.includes('--json');
const csvIdx = args.indexOf('--csv');
const csvPath = csvIdx !== -1 ? args[csvIdx + 1] : null;

if (!url || !url.includes('ods.office.net')) {
  console.error('Usage: node scripts/ods_extract.js <ods-ticket-url> [--json] [--csv output.csv]');
  process.exit(1);
}

async function waitForContent(page) {
  // Wait for spinner to clear
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

  // Wait for chat messages to appear
  try {
    await page.waitForSelector('div.messages', { timeout: 30000 });
  } catch {
    console.log('  Chat messages not found after 30s, proceeding...');
  }

  await page.waitForTimeout(2000);
}

async function extractTicketData(page) {
  return page.evaluate(() => {
    const result = {
      metadata: {},
      chat: [],
    };

    // --- Metadata from Table 0 (ticket overview) ---
    const tables = document.querySelectorAll('table');
    if (tables.length > 0) {
      const rows = tables[0].querySelectorAll('tr');
      for (const row of rows) {
        const cells = row.querySelectorAll('td, th');
        if (cells.length >= 2) {
          const key = cells[0]?.textContent?.trim();
          const val = cells[1]?.textContent?.trim();
          if (key && val) result.metadata[key] = val;
        }
      }
    }

    // --- User domain from UPN ---
    const upn = result.metadata['User Upn'] || '';
    if (upn.includes('@')) {
      result.metadata['_userDomain'] = upn.split('@')[1];
    }

    // --- Chat messages ---
    const messages = document.querySelectorAll('div.messages');
    for (const msg of messages) {
      const isClient = msg.classList.contains('client-message');
      const isAgent = msg.classList.contains('agent-message');
      const role = isClient ? 'customer' : isAgent ? 'agent' : 'unknown';

      // First child typically has sender + timestamp, second has the text
      const children = msg.children;
      let sender = '';
      let timestamp = '';
      let text = '';

      if (children.length >= 2) {
        // Header line: "Name • timestamp"
        const headerText = children[0]?.textContent?.trim() || '';
        const bulletSplit = headerText.split('•');
        if (bulletSplit.length >= 2) {
          sender = bulletSplit[0].trim();
          timestamp = bulletSplit.slice(1).join('•').trim();
        } else {
          sender = headerText;
        }
        text = children[1]?.textContent?.trim() || '';
      } else if (children.length === 1) {
        text = children[0]?.textContent?.trim() || '';
      } else {
        text = msg.textContent?.trim() || '';
      }

      if (text) {
        result.chat.push({ role, sender, timestamp, text });
      }
    }

    return result;
  });
}

function scrubPII(text) {
  return text
    .replace(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g, '[REDACTED_EMAIL]')
    .replace(/(\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/g, '[REDACTED_PHONE]');
}

async function main() {
  if (!jsonFlag) {
    console.log('=== ODS Sara Ticket Extraction (POC) ===');
    console.log(`URL: ${url}\n`);
  }

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: 'msedge',
    headless: false,
    viewport: { width: 1400, height: 900 },
  });

  const page = context.pages()[0] || await context.newPage();

  try {
    // Navigate
    if (!jsonFlag) console.log('Navigating to ODS...');
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });

    // SSO check
    const currentUrl = page.url();
    if (currentUrl.includes('login.microsoftonline.com') || currentUrl.includes('login.microsoft.com')) {
      if (!jsonFlag) console.log('SSO login required. Complete login in the browser...');
      await page.waitForURL('**/ods.office.net/**', { timeout: 120000 });
    }

    // Wait for content
    if (!jsonFlag) console.log('Waiting for ticket to load...');
    await waitForContent(page);

    // Extract
    if (!jsonFlag) console.log('Extracting ticket data...\n');
    const data = await extractTicketData(page);

    // Scrub PII from chat messages
    let piiCount = 0;
    for (const msg of data.chat) {
      const scrubbed = scrubPII(msg.text);
      if (scrubbed !== msg.text) piiCount++;
      msg.text = scrubbed;
      // Scrub sender too (customer IDs are already hashed by ODS)
      msg.sender = scrubPII(msg.sender);
    }

    // --- Output ---
    if (jsonFlag) {
      console.log(JSON.stringify(data, null, 2));
    } else {
      // Metadata
      console.log('--- Ticket Metadata ---');
      const keyFields = [
        'Ticket Status', 'Created time', 'Chat last modified time',
        'User Upn', '_userDomain', 'Problem Statement', 'Tags',
        'Ticketing System', 'Authentication', 'App Version',
      ];
      for (const key of keyFields) {
        if (data.metadata[key]) {
          const label = key === '_userDomain' ? 'User Domain' : key;
          console.log(`  ${label}: ${data.metadata[key]}`);
        }
      }

      // Chat transcript
      console.log(`\n--- Chat Transcript (${data.chat.length} messages) ---`);
      for (const msg of data.chat) {
        const roleTag = msg.role === 'customer' ? '[CUSTOMER]' : msg.role === 'agent' ? '[AGENT]' : '[UNKNOWN]';
        const time = msg.timestamp ? ` (${msg.timestamp})` : '';
        console.log(`\n${roleTag}${time}`);
        // Truncate long messages for terminal display
        const preview = msg.text.length > 300 ? msg.text.slice(0, 300) + '...' : msg.text;
        console.log(`  ${preview}`);
      }

      // Summary
      console.log('\n--- Summary ---');
      console.log(`  Messages: ${data.chat.length}`);
      console.log(`  Customer messages: ${data.chat.filter(m => m.role === 'customer').length}`);
      console.log(`  Agent messages: ${data.chat.filter(m => m.role === 'agent').length}`);
      console.log(`  User domain: ${data.metadata['_userDomain'] || 'N/A'}`);
      if (piiCount > 0) console.log(`  PII scrubbed: ${piiCount} messages`);
    }

    // CSV export
    if (csvPath) {
      function csvEscape(value) {
        return `"${String(value || '').replace(/"/g, '""')}"`;
      }
      const header = 'Role,Sender,Timestamp,Text';
      const rows = data.chat.map(m =>
        [m.role, m.sender, m.timestamp, m.text].map(csvEscape).join(',')
      );
      const csv = [header, ...rows].join('\n');
      const outPath = path.resolve(csvPath);
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
      fs.writeFileSync(outPath, csv, 'utf8');
      if (!jsonFlag) console.log(`\n  CSV saved: ${outPath}`);
    }

    if (!jsonFlag) console.log('\n=== Extraction Complete ===');

  } catch (err) {
    console.error('Extraction failed:', err.message);
    try {
      const errPath = path.join(__dirname, '..', 'data', 'ods_error_screenshot.png');
      fs.mkdirSync(path.dirname(errPath), { recursive: true });
      await page.screenshot({ path: errPath });
      console.log(`Error screenshot: ${errPath}`);
    } catch {}
    process.exit(1);
  } finally {
    await context.close();
  }
}

main().catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});

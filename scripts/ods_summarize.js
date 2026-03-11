#!/usr/bin/env node
// Summarize an ODS chat transcript using local LLM (Ollama + Phi-4-mini)
// Usage: node scripts/ods_summarize.js data/ods_dc66f1a1.csv

const fs = require('fs');

const csvPath = process.argv[2];
if (!csvPath || !fs.existsSync(csvPath)) {
  console.error('Usage: node scripts/ods_summarize.js <ods-csv-file>');
  process.exit(1);
}

// Shared CSV parser
const { parseCSV } = require('./lib/csv_parser');

const text = fs.readFileSync(csvPath, 'utf8');
const { rows } = parseCSV(text);

// Condense: skip automated follow-ups and very short acks
const filtered = rows.filter(r => {
  const t = (r.Text || '').toLowerCase();
  if (t.includes('we are following up to see') || t.includes('will be archiving the ticket')) return false;
  if (t.includes('thank you for contacting microsoft. our in-app support')) return false;
  if (t.length < 15) return false;
  return true;
});

const transcript = filtered
  .map(r => {
    const tag = r.Role === 'customer' ? 'CUSTOMER' : 'AGENT';
    return `[${tag}] (${r.Timestamp}): ${(r.Text || '').slice(0, 300)}`;
  })
  .join('\n');

console.log(`Loaded ${rows.length} messages, condensed to ${filtered.length} substantive messages (${transcript.length} chars)`);
console.log('Sending to Phi-4-mini (local)...\n');

async function run() {
  // Check Ollama is running
  try {
    await fetch('http://localhost:11434/api/tags');
  } catch {
    console.error('ERROR: Ollama is not running.');
    console.error('  Start it with: ollama serve');
    process.exit(1);
  }

  const prompt = [
    'You are summarizing a Microsoft Outlook support chat. Write a structured summary:',
    '',
    '1. TL;DR (one sentence)',
    '2. Customer Issue (what the customer reported, 2-3 bullets)',
    '3. Troubleshooting Steps (what agents tried, numbered list)',
    '4. Resolution Status (resolved / unresolved / abandoned)',
    '5. Key Takeaways (actionable insights for the product team, 2-3 bullets)',
    '',
    'Be direct. Active voice. Paraphrase only, never quote verbatim.',
    '',
    'TRANSCRIPT:',
    transcript,
    '',
    'SUMMARY:',
  ].join('\n');

  const res = await fetch('http://localhost:11434/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'phi4-mini',
      prompt,
      stream: false,
      options: { temperature: 0.3, num_predict: 800 },
    }),
  });

  if (!res.ok) {
    console.error(`Ollama error: HTTP ${res.status}`);
    process.exit(1);
  }

  const data = await res.json();
  console.log(data.response);
  console.log('\n--- All processing performed locally via Ollama. No data left your machine. ---');
}

run().catch(err => {
  console.error('Failed:', err.message);
  process.exit(1);
});

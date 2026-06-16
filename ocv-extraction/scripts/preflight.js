#!/usr/bin/env node
// Preflight check — verifies all dependencies are installed and working.
// Run: node scripts/preflight.js   or   npm run check

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const PROJECT_ROOT = path.join(__dirname, '..');
let passed = 0;
let failed = 0;

let warnings = 0;

function check(label, fn) {
  try {
    const result = fn();
    console.log(`  ✅ ${label}${result ? ` — ${result}` : ''}`);
    passed++;
  } catch (e) {
    console.log(`  ❌ ${label}`);
    console.log(`     → ${e.message}`);
    failed++;
  }
}

function checkOptional(label, fn) {
  try {
    const result = fn();
    console.log(`  ✅ ${label}${result ? ` — ${result}` : ''}`);
    passed++;
  } catch (e) {
    console.log(`  ⚠️  ${label} (optional)`);
    console.log(`     → ${e.message}`);
    warnings++;
  }
}

console.log('\n=== OCV Extraction — Preflight Check ===\n');

// 1. Node.js version
check('Node.js >= 18', () => {
  const major = parseInt(process.versions.node.split('.')[0]);
  if (major < 18) throw new Error('Node.js 18+ is required. Download from https://nodejs.org');
  return `v${process.versions.node}`;
});

// 2. npm dependencies installed
check('npm dependencies installed', () => {
  const playwrightPath = path.join(PROJECT_ROOT, 'node_modules', 'playwright');
  if (!fs.existsSync(playwrightPath)) {
    throw new Error('Run "npm install" in the ocv-extraction directory first.');
  }
  const pkg = JSON.parse(fs.readFileSync(path.join(playwrightPath, 'package.json'), 'utf8'));
  return `playwright ${pkg.version}`;
});

// 3. Microsoft Edge
check('Microsoft Edge browser', () => {
  const edgePaths = [
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
  ].filter(Boolean);

  const found = edgePaths.find(p => fs.existsSync(p));
  if (!found) throw new Error('Microsoft Edge not found. Install from https://www.microsoft.com/edge');
  return found.split('\\').slice(-3).join('\\');
});

// 4. Config files
check('Config files', () => {
  const configDir = path.join(PROJECT_ROOT, 'configs');
  if (!fs.existsSync(configDir)) throw new Error('configs/ directory not found.');
  const configs = fs.readdirSync(configDir).filter(f => f.endsWith('.json') && !f.startsWith('_'));
  if (configs.length === 0) throw new Error('No config files found. Run the ocv-setup skill to create one.');
  return configs.map(f => f.replace('.json', '')).join(', ');
});

// 5. Data directory
check('Output directory (data/)', () => {
  const dataDir = path.join(PROJECT_ROOT, 'data');
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
    return 'created';
  }
  const csvFiles = fs.readdirSync(dataDir).filter(f => f.endsWith('.csv'));
  return csvFiles.length > 0 ? `${csvFiles.length} existing CSV files` : 'empty (ready)';
});

// Summary
console.log('\n' + '─'.repeat(45));
if (failed === 0) {
  const warnMsg = warnings > 0 ? ` (${warnings} optional warning${warnings > 1 ? 's' : ''})` : '';
  console.log(`\n  All ${passed} required checks passed${warnMsg}. You're ready to extract!\n`);
  console.log('  Quick start:');
  console.log('    npm run extract:accounts    Extract yesterday\'s accounts feedback');
  console.log('    npm run extract:7d          Extract last 7 days\n');
} else {
  console.log(`\n  ${passed} passed, ${failed} failed. Fix the issues above and run again.\n`);
  process.exit(1);
}

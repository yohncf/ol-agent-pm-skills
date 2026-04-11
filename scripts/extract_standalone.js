#!/usr/bin/env node
// OCV Standalone Extraction Script
// Requires: Node.js 18+, Playwright, Microsoft Edge
// Usage: node scripts/extract_standalone.js [--config <config.json>] [--url <ocv-url>] [output.csv] [--date <value>] [--include-structured]
//
// Config-driven extraction. Each area (Accounts, Calendar, Copilot, etc.) has its own
// config file in configs/ that defines the OCV URL, category keywords, feature tags,
// entity identification, and noise patterns.
//
// If no --config is provided, uses configs/accounts.json as default.
// If --url is provided, it overrides the config's ocv_url.
//
// CSV Columns (universal):
//   Date      — When the feedback was submitted (from CreatedDate)
//   Comment   — Verbatim customer text, translated to English (from TranslatedTextPiiRedacted)
//   Sentiment — Positive / Negative / Neutral (from Classifications)
//   Intent    — Problem / Request / Compliment / Unknown (from Classifications)
//   Language  — Original language code (from OriginalTextLanguage)
//
// CSV Columns (config-driven):
//   Provider  — Entity identification, e.g., ISP name (from config entity settings)
//   Feature   — Product area tags (from config feature_tags whitelist)
//   Category  — Issue bucket (from config categories keyword matching)
//   Noise     — Flagged as noise by config noise_patterns (true/empty)
//
// Date options:
//   --date today        Today (default)
//   --date yesterday    Yesterday only
//   --date 7d / 14d / 30d / 3m / 6m / all
//   --date 2026-02-20:2026-02-24   Custom range (YYYY-MM-DD)
//
// Blank feedback:
//   --include-blank     Include feedback with no verbatim text (empty Comment field).
//                       By default, only feedback with text is extracted. Use this flag
//                       to get ALL feedback for accurate sentiment/client metrics.

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

// --- CLI Argument Parsing ---

const rawArgs = process.argv.slice(2);

function extractArg(flag) {
  const idx = rawArgs.indexOf(flag);
  if (idx === -1) return null;
  const val = rawArgs[idx + 1];
  rawArgs.splice(idx, 2);
  return val;
}

const configPath = extractArg('--config') || path.join(__dirname, '..', 'configs', 'accounts.json');
const urlOverride = extractArg('--url');
const dateArg = extractArg('--date');

// Boolean flags
const summaryFlag = rawArgs.includes('--summary');
if (summaryFlag) rawArgs.splice(rawArgs.indexOf('--summary'), 1);

const dumpFieldsFlag = rawArgs.includes('--dump-fields');
if (dumpFieldsFlag) rawArgs.splice(rawArgs.indexOf('--dump-fields'), 1);

const weeklySummaryFlag = rawArgs.includes('--weekly-summary');
if (weeklySummaryFlag) rawArgs.splice(rawArgs.indexOf('--weekly-summary'), 1);

// --include-structured (or legacy alias --include-blank): include feedback with no verbatim text
const includeStructured = rawArgs.includes('--include-structured') || rawArgs.includes('--include-blank');
if (rawArgs.includes('--include-structured')) rawArgs.splice(rawArgs.indexOf('--include-structured'), 1);
if (rawArgs.includes('--include-blank')) rawArgs.splice(rawArgs.indexOf('--include-blank'), 1);

// Remaining positional args: [output.csv]
const outputFile = rawArgs[0] || `ocv_extract_${new Date().toISOString().slice(0, 10)}.csv`;

// --- Load Config ---

if (!fs.existsSync(configPath)) {
  console.error(`Config file not found: ${configPath}`);
  console.error('Usage: node extract_standalone.js [--config configs/accounts.json] [--date 7d] [output.csv]');
  process.exit(1);
}

let config;
try {
  config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
} catch (e) {
  console.error(`Invalid JSON in config file: ${configPath}`);
  console.error(`  ${e.message}`);
  process.exit(1);
}

const ocvUrl = urlOverride || config.ocv_url;

if (!ocvUrl) {
  console.error('No OCV URL. Provide --url or set ocv_url in the config file.');
  process.exit(1);
}

console.log(`Config: ${config.name} (${path.basename(configPath)})`);
if (includeBlankFlag) console.log('Mode: Including feedback with blank verbatims (--include-blank)');

// --- Build Runtime Objects from Config ---

// Feature tags whitelist
const FEATURE_TAGS = new Set(config.feature_tags || []);

// Category matchers: compile regex patterns from config
const CATEGORY_MATCHERS = [];
for (const [name, def] of Object.entries(config.categories || {})) {
  if (name.startsWith('_')) continue;
  try {
    CATEGORY_MATCHERS.push({
      name,
      match: def.match ? new RegExp(def.match.join('|'), 'i') : null,
      require: def.require && def.require.length > 0 ? new RegExp(def.require.join('|'), 'i') : null,
      exclude: def.exclude && def.exclude.length > 0 ? new RegExp(def.exclude.join('|'), 'i') : null,
    });
  } catch (e) {
    console.warn(`Warning: Category "${name}" has invalid regex pattern: ${e.message}. Skipping.`);
  }
}

// Noise patterns
const NOISE_PATTERNS = [];
for (const p of (config.noise_patterns || []).filter(p => !p.startsWith('_'))) {
  try {
    NOISE_PATTERNS.push(new RegExp(p, 'i'));
  } catch (e) {
    console.warn(`Warning: Noise pattern "${p}" is invalid regex: ${e.message}. Skipping.`);
  }
}

// Entity (provider) whitelist — resolve relative to config file, then script dir
let domainMap = null;
if (config.entity && config.entity.whitelist_file) {
  const configDir = path.dirname(path.resolve(configPath));
  const candidates = [
    path.join(configDir, config.entity.whitelist_file),
    path.join(__dirname, config.entity.whitelist_file),
  ];
  const whitelistPath = candidates.find(p => fs.existsSync(p));
  if (whitelistPath) {
    try {
      const data = JSON.parse(fs.readFileSync(whitelistPath, 'utf8'));
      domainMap = {};
      for (const [providerName, domains] of Object.entries(data.providers)) {
        for (const domain of domains) {
          domainMap[domain.toLowerCase()] = providerName;
        }
      }
    } catch (e) {
      console.warn(`Warning: Failed to parse whitelist file ${whitelistPath}: ${e.message}`);
    }
  } else {
    console.warn(`Warning: Entity whitelist file "${config.entity.whitelist_file}" not found. Provider column will be empty.`);
  }
}

const REDACTED_LABEL = config.entity?.redacted_label || '[CUSTOM_DOMAIN]';

// --- Constants ---

const PROFILE_DIR = path.join(__dirname, '..', '.browser-profile');
const MAX_SCROLL_ATTEMPTS = 50;
const SCROLL_WAIT_MS = 1200;
const STABLE_CHECKS_TO_STOP = 3;

const OFFSET_MAP = {
  'today': { relDateType: 'day', offset: 0 },
  'yesterday': { relDateType: 'day', offset: -1, filterDate: true },
  '7d': { relDateType: 'day', offset: -6 },
  '14d': { relDateType: 'day', offset: -13 },
  '30d': { relDateType: 'day', offset: -29 },
  '3m': { relDateType: 'day', offset: -89 },
  '6m': { relDateType: 'day', offset: -179 },
  'all': { relDateType: 'all', offset: 0 },
};

const CSV_HEADER = 'Date,Comment,Provider,Sentiment,Intent,Feature,Scenario,Category,Language,Noise,AreaPath,Client,Rating,OcvId,CmmId,SourceContext,EntryPoint,SubFeature,SentimentThemes,CopilotIntent,ACRUE,RawFeatureName,RawFeatureArea,RawPlatform,Endpoint,Application,FeedbackType,RawAppData,PlatformExternal,UserAgent,SdkVersion';

// --- CSV Helpers ---

function csvEscape(value) {
  if (value == null) return '""';
  const str = String(value);
  return `"${str.replace(/"/g, '""')}"`;
}

// --- Functions ---

function categorizeComment(comment) {
  for (const cat of CATEGORY_MATCHERS) {
    if (!cat.match || !cat.match.test(comment)) continue;
    if (cat.require && !cat.require.test(comment)) continue;
    if (cat.exclude && cat.exclude.test(comment)) continue;
    return cat.name;
  }
  return '';
}

function detectNoise(comment) {
  for (const pattern of NOISE_PATTERNS) {
    if (pattern.test(comment)) return 'true';
  }
  return '';
}

function resolveProvider(email) {
  if (!email || !domainMap) return '';
  const atIdx = email.lastIndexOf('@');
  if (atIdx === -1) return '';
  const domain = email.slice(atIdx + 1).toLowerCase().trim();
  if (!domain) return '';
  return domainMap[domain] || REDACTED_LABEL;
}

// Extract deduplicated part values from a Classification entry
function extractClassParts(c) {
  if (c.Tags && c.Tags.length > 0) return [...new Set(c.Tags)];
  if (c.LabelGroupings) {
    const parts = [];
    for (const lg of c.LabelGroupings) {
      if (lg.Parts) {
        for (const p of lg.Parts) {
          if (p.Value) parts.push(p.Value);
        }
      }
    }
    return [...new Set(parts)];
  }
  return [];
}

function parseHit(src, ocvId) {
  // Comment: prefer translated (English) PII-redacted text
  const comment = src.TranslatedTextPiiRedacted || src.OriginalTextPiiRedacted
    || src.TranslatedText || src.OriginalText || src.CustomerVerbatimOriginal || '';
  if (!comment && !includeStructured) return null;

  const language = src.OriginalTextLanguage || '';

  // Date: format to MM/DD/YY HH:MMAM/PM
  let date = '';
  if (src.CreatedDate) {
    const d = new Date(src.CreatedDate);
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const yy = String(d.getFullYear()).slice(-2);
    let hours = d.getHours();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12 || 12;
    const mins = String(d.getMinutes()).padStart(2, '0');
    date = `${mm}/${dd}/${yy} ${hours}:${mins}${ampm}`;
  }

  // Provider: email domain → whitelist lookup
  const provider = resolveProvider(src.Email);

  // Area path: extract from OcvAreas for cross-area analysis
  let areaPath = '';
  if (src.OcvAreas && Array.isArray(src.OcvAreas)) {
    const paths = src.OcvAreas.map(a => a.Path || '').filter(Boolean);
    areaPath = paths.join('|');
  }

  // Sentiment + Intent + Classifier metadata: from Classifications array
  let sentiment = '';
  let intent = '';
  let sentimentThemes = '';
  let copilotIntent = '';
  let acrue = '';
  if (src.Classifications && Array.isArray(src.Classifications)) {
    for (const c of src.Classifications) {
      if (c.Name === 'Text Sentiment' && c.Tags && c.Tags.length > 0) {
        sentiment = c.Tags[0];
      }
      if (c.Name === 'Text Intent' && c.Tags && c.Tags.length > 0) {
        intent = c.Tags[0];
      }
      if (c.Name === 'Copilot Sentiment Themes') {
        sentimentThemes = extractClassParts(c).join('|');
      }
      if (c.Name === 'Copilot Canonical Intents') {
        copilotIntent = extractClassParts(c).join('|');
      }
      if (c.Name === 'ACRUE') {
        acrue = extractClassParts(c).join('|');
      }
    }
  }

  // Feature: from CustomTags, filtered through config whitelist
  const features = [];
  if (src.CustomTags && Array.isArray(src.CustomTags)) {
    for (const tag of src.CustomTags) {
      const name = typeof tag === 'string' ? tag : (tag.Name || tag.Value || '');
      if (FEATURE_TAGS.has(name)) {
        features.push(name);
      }
    }
  }

  // Client: normalize Platform field into standard buckets
  let client = '';
  const platform = (src.Platform || '').toLowerCase();

  // Scenario: raw FeatureName from OCV; fall back to FeatureArea if FeatureName is empty
  let scenario = src.FeatureName || src.FeatureArea || '';

  // CmmId: extract from AppData JSON (scenario identifier, not PII)
  let cmmId = '';
  let appDataParsed = {};
  if (src.AppData) {
    try {
      appDataParsed = typeof src.AppData === 'string' ? JSON.parse(src.AppData) : src.AppData;
      cmmId = appDataParsed.Cmmid || appDataParsed.cmmId || appDataParsed.CmmId || appDataParsed.CMMID || '';
    } catch {}
  }

  // Scenario override: CMMId-based items often have misleading FeatureName (e.g., "Theming")
  // Source: ScenarioCMMIds.ts (OWA), PKM365InvokeChatScenario+FromCode.swift (iOS), ChatIntentBuilder.kt (Android)
  const CMMID_SCENARIO_MAP = {
    // Compose / Vibe Writing
    'cmmyz8p6paw': 'VibeWriting',
    'cmmq2silgi7': 'VibeWritingReply',
    'cmmeiat63t6': 'StartChatDraft',
    // Mobile-native SUAs
    'cmmj1vx5zke': 'CalendarTopThree',
    'cmmuwjzeuoy': 'InboxBriefingMorning',
    'cmmtw8e0u46': 'InboxBriefingEvening',
    'cmmil8c3lm6': 'VoiceCatchup',
    'cmmjskixxn0': 'TodaysPlan',
    // Attachment Summarization
    'cmmaii3bpr3': 'AttachmentSummary',
    'cmm0qu0dq94': 'AttachmentSummary',
    'cmmtxb10jc3': 'AttachmentSummary',
    'cmmeqyuilr4': 'AttachmentSummary',
    'cmmoewnwc3d': 'AttachmentSummary',
    'cmmtjb5s87j': 'AttachmentSummary',
    'cmmk62sxys0': 'AttachmentSummary',
    'cmmuntkm37u': 'AttachmentSummary',
    // Search
    'cmm847v5ypw': 'SearchSuggestions',
    'cmmr8vd788b': 'SearchSuggestions',
    // Calendar
    'cmm6dymueiy': 'CalendarImmersiveSearch',
    'cmmlaqq6pcx': 'MeetingPrep',
    'cmmsydqyyio': 'CalendarDraftAssistant',
    'cmmp0zg5aes': 'MeetingAssistant',
    'cmm6vifevnf': 'ScheduleFromEmail',
    'cmmeek7bcwh': 'CalendarTopPriorities',
    'cmmbuv6hz0j': 'CalendarInstructions',
    'cmmnuszr5mh': 'CalendarInstructions',
    'cmmbm8ohwah': 'CalendarInstructions',
    'cmmbxdghsq4': 'CalendarInstructions',
    'cmmzbgobp81': 'CalendarInstructions',
    'cmmuzd535im': 'CalendarInstructions',
    'cmmi3ec0b43': 'CalendarInstructions',
    'cmm5b3f2pw4': 'CalendarInstructions',
    'cmmwtssir2n': 'CalendarInstructions',
    'cmmz2d4s9vq': 'CalendarInstructions',
    'cmm4o76y4u5': 'CalendarInstructions',
    'cmmfl2azi95': 'CalendarInstructions',
    // Other
    'cmmgd9favq8': 'Insights',
    'cmm37ofay86': 'NotificationsSummarize',
  };
  if (cmmId && CMMID_SCENARIO_MAP[cmmId]) {
    scenario = CMMID_SCENARIO_MAP[cmmId];
  }

  // Priority 0: Product field is the simplest Monarch signal (available in ES API + OCV Discover).
  const product = (src.Product || '').toLowerCase();

  if (/windows\s*desktop|win32|win64/.test(platform)) {
    // Distinguish Monarch (New Outlook) from Win32 (Classic Outlook) on Windows Desktop.
    // Priority 0: Product="Outlook Monarch" is definitive when present.
    // Priority 1: AppData.UiHost/clientName (DAB/BizChat items).
    // Priority 2: PlatformExternal + SdkVersion (on-canvas items).
    const uiHost = (appDataParsed.UiHost || '').toLowerCase();
    const clientName = (appDataParsed.clientName || '').toLowerCase();
    const platExt = (src.PlatformExternal || '').toLowerCase();
    const sdkVer = (src.SdkVersion || '').toLowerCase();

    if (product.includes('outlook monarch')) {
      client = 'Desktop (Monarch)';
    } else if (product === 'outlook' && !product.includes('monarch')) {
      client = 'Desktop (Win32)';
    } else if (uiHost.includes('monarch') || clientName.includes('monarch')) {
      client = 'Desktop (Monarch)';
    } else if (uiHost.includes('classic') || clientName.includes('outlookocv')) {
      client = 'Desktop (Win32)';
    } else if (platExt === 'web' || sdkVer.includes('scc-react-feedback-plugin')) {
      client = 'Desktop (Monarch)';
    } else if (platExt === 'windows desktop' || platExt === 'windows' || sdkVer.includes('mso v')) {
      client = 'Desktop (Win32)';
    } else {
      client = 'Desktop';
    }
  }
  else if (/\bweb\b|owa|browser/.test(platform)) client = 'OWA';
  else if (/\bmac\b|macos|osx/.test(platform)) client = 'Mac';
  else if (/ios|iphone|ipad/.test(platform)) client = 'Mobile (iOS)';
  else if (/android/.test(platform)) client = 'Mobile (Android)';
  else if (/mobile/.test(platform)) client = 'Mobile';
  else if (platform) client = platform;

  // Rating: user's thumbs up/down from OCV feedback fields
  const rating = src.FeedbackRating ?? src.OriginalRating ?? src.FeedbackScore ?? '';
  const feedbackType = src.FeedbackType || '';

  const sourceContext = src.SourceContext || '';
  const entryPoint = src.AppEntryPoint || src.EntryPoint || '';
  const subFeature = src.SubFeatureName || '';

  // Raw metadata for diagnostic/cross-reference (non-PII)
  const rawAppData = src.AppData
    ? (typeof src.AppData === 'string' ? src.AppData : JSON.stringify(src.AppData))
    : '';

  return {
    date,
    comment,
    provider,
    sentiment,
    intent,
    feature: features.join('|'),
    scenario,
    category: categorizeComment(comment),
    language,
    noise: detectNoise(comment),
    areaPath,
    client,
    rating: feedbackType || String(rating),
    ocvId: ocvId || '',
    cmmId,
    sourceContext,
    entryPoint,
    subFeature,
    sentimentThemes,
    copilotIntent,
    acrue,
    rawFeatureName: src.FeatureName || '',
    rawFeatureArea: src.FeatureArea || '',
    rawPlatform: src.Platform || '',
    endpoint: src.Endpoint || src.EndPoint || '',
    application: src.Application || '',
    feedbackType,
    rawAppData,
    platformExternal: src.PlatformExternal || '',
    userAgent: src.UserAgent || '',
    sdkVersion: src.SdkVersion || '',
  };
}

// --- PII Scrubbing ---

function scrubPII(results) {
  const patterns = [
    { regex: /\[PII:\s*Email\]/gi, replacement: '[REDACTED_EMAIL]', type: 'ocvTags' },
    { regex: /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g, replacement: '[REDACTED_EMAIL]', type: 'emails' },
    { regex: /(\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/g, replacement: '[REDACTED_PHONE]', type: 'phones' },
  ];

  const stats = { emails: 0, phones: 0, ocvTags: 0, total: 0 };

  for (const item of results) {
    for (const { regex, replacement, type } of patterns) {
      regex.lastIndex = 0;
      const matches = item.comment.match(regex);
      if (matches) {
        stats[type] += matches.length;
        stats.total += matches.length;
        item.comment = item.comment.replace(regex, replacement);
      }
    }
  }

  return stats;
}

// --- API Extraction ---

async function fetchES(page, query, headers) {
  return page.evaluate(async ({ query, headers }) => {
    try {
      const fetchHeaders = {};
      if (headers) {
        for (const key of ['authorization', 'x-csrf-token', 'x-requested-with', '__requestverificationtoken', 'cookie']) {
          if (headers[key]) fetchHeaders[key] = headers[key];
        }
      }
      fetchHeaders['Content-Type'] = 'application/json';

      const res = await fetch('/api/es/ocv/_search', {
        method: 'POST',
        headers: fetchHeaders,
        credentials: 'include',
        body: JSON.stringify(query),
      });
      if (!res.ok) return { error: `HTTP ${res.status}: ${res.statusText}` };
      const text = await res.text();
      try { return JSON.parse(text); }
      catch { return { error: `JSON parse error. Body starts with: ${text.slice(0, 200)}` }; }
    } catch (e) { return { error: e.message }; }
  }, { query, headers });
}

async function extractViaAPI(page, capturedQuery, capturedHeaders) {
  if (!capturedQuery) {
    console.log('  Could not capture API query. Falling back to DOM scraping...');
    return await extractViaDOM(page);
  }

  const PAGE_SIZE = 200;
  capturedQuery.size = PAGE_SIZE;
  capturedQuery.from = 0;
  delete capturedQuery.highlight;

  // Get accurate total count (ES defaults to capping at 10,000)
  capturedQuery.track_total_hits = true;

  // Ensure a sort field exists for search_after pagination
  if (!capturedQuery.sort || capturedQuery.sort.length === 0) {
    capturedQuery.sort = [{ CreatedDate: 'desc' }, { _doc: 'asc' }];
  }

  const allItems = [];
  let structuredOnlyCount = 0;
  let total = null;
  let searchAfter = null;
  let pageNum = 0;

  while (true) {
    // First page uses from=0; subsequent pages use search_after
    const query = { ...capturedQuery };
    if (searchAfter) {
      query.search_after = searchAfter;
      delete query.from;
    }

    const batch = await fetchES(page, query, capturedHeaders);

    if (batch.error) {
      console.log(`  API error on page ${pageNum + 1}: ${batch.error}`);
      if (pageNum === 0) {
        console.log('  Falling back to DOM scraping...');
        return await extractViaDOM(page);
      }
      break;
    }

    if (!batch.hits || !batch.hits.hits) break;

    if (total === null) {
      total = batch.hits.total?.value || batch.hits.total || 0;
      console.log(`  Total results: ${total}`);
    }

    const hits = batch.hits.hits;
    if (hits.length === 0) break;

    // --dump-fields: log all source field names and client-related values from first hit
    if (dumpFieldsFlag && pageNum === 0 && hits.length > 0) {
      const sample = hits[0]._source;
      console.log('\n--- ALL _source KEYS ---');
      console.log(Object.keys(sample).sort().join('\n'));
      console.log('--- END ALL KEYS ---\n');
      const clientKeys = Object.keys(sample).sort().filter(k =>
        /app|client|source|platform|device|channel|ring|build|version|agent|meta|prop|telemetry|flight/i.test(k));
      console.log('\n--- DUMP FIELDS (first hit _source keys) ---');
      console.log(clientKeys.join('\n'));
      console.log('\n--- CLIENT-RELATED FIELD VALUES ---');
      for (const k of clientKeys) {
        if (sample[k] !== undefined) {
          const val = typeof sample[k] === 'object' ? JSON.stringify(sample[k]).slice(0, 300) : sample[k];
          console.log(`  ${k}: ${val}`);
        }
      }
      // Unique Platform + DeviceType values across all hits in this batch
      const platCounts = {}, devCounts = {}, ratingCounts = {}, ftypeCounts = {};
      for (const h of hits) {
        const s = h._source;
        const p = s.Platform || '(empty)';
        const d = s.DeviceType || '(empty)';
        const r = String(s.FeedbackRating ?? s.OriginalRating ?? '(empty)');
        const ft = s.FeedbackType || '(empty)';
        platCounts[p] = (platCounts[p] || 0) + 1;
        devCounts[d] = (devCounts[d] || 0) + 1;
        ratingCounts[r] = (ratingCounts[r] || 0) + 1;
        ftypeCounts[ft] = (ftypeCounts[ft] || 0) + 1;
      }
      console.log('\n--- UNIQUE Platform VALUES (this batch) ---');
      Object.entries(platCounts).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => console.log(`  ${k}: ${v}`));
      console.log('\n--- UNIQUE DeviceType VALUES (this batch) ---');
      Object.entries(devCounts).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => console.log(`  ${k}: ${v}`));
      console.log('\n--- UNIQUE FeedbackRating VALUES (this batch) ---');
      Object.entries(ratingCounts).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => console.log(`  ${k}: ${v}`));
      console.log('\n--- UNIQUE FeedbackType VALUES (this batch) ---');
      Object.entries(ftypeCounts).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => console.log(`  ${k}: ${v}`));
      console.log('--- END DUMP ---\n');
    }

    for (const hit of hits) {
      const src = hit._source;
      if (!src) continue;
      const item = parseHit(src, hit._id);
      if (item) {
        allItems.push(item);
      } else {
        structuredOnlyCount++;
      }
    }

    pageNum++;
    console.log(`  Fetched ${allItems.length} of ${total} items...`);

    // Stop when we've fetched all items or this page was incomplete (end of data)
    if (hits.length < PAGE_SIZE) break;

    // Use last hit's sort values for next page
    const lastHit = hits[hits.length - 1];
    if (lastHit.sort) {
      searchAfter = lastHit.sort;
    } else {
      console.log('  No sort values on last hit — cannot paginate further.');
      break;
    }

    await page.waitForTimeout(200);
  }

  console.log(`Extraction source: Elasticsearch API (${allItems.length} items)`);
  return { items: allItems, structuredOnlyCount };
}

// --- DOM Fallback ---

async function extractViaDOM(page) {
  console.log('  Note: DOM fallback is limited to ~400 items and has no provider/sentiment/language data.');
  await setPageSize(page);
  await scrollToLoadAll(page);

  const rawItems = await page.evaluate(() => {
    const rows = document.querySelectorAll('.ui-grid-row');
    const items = [];
    rows.forEach(row => {
      const cells = row.querySelectorAll('[role="gridcell"]');
      if (cells.length >= 9) {
        const comment = cells[2]?.textContent?.trim() || '';
        const date = cells[3]?.textContent?.trim() || '';
        if (comment) {
          items.push({ date, comment: comment.replace(/"/g, '""') });
        }
      }
    });
    return items;
  });

  return rawItems.map(item => ({
    ...item,
    provider: '',
    sentiment: '',
    intent: '',
    feature: '',
    category: categorizeComment(item.comment),
    language: '',
    noise: detectNoise(item.comment),
    areaPath: '',
    ocvId: '',
  }));
}

// --- Date Handling ---

function applyDateToUrl(originalUrl, dateValue) {
  const key = dateValue.toLowerCase();
  const mapping = OFFSET_MAP[key];

  if (mapping) {
    let modified = originalUrl
      .replace(/relDateType=\w+/, `relDateType=${mapping.relDateType}`)
      .replace(/offset=[-\d]+/, `offset=${mapping.offset}`);
    console.log(`  URL date params: relDateType=${mapping.relDateType}, offset=${mapping.offset}`);
    return modified;
  }

  if (dateValue.includes(':')) {
    return originalUrl
      .replace(/relDateType=\w+/, 'relDateType=all')
      .replace(/offset=[-\d]+/, 'offset=0');
  }

  console.log(`  Unknown date value: "${dateValue}". Using URL as-is.`);
  return originalUrl;
}

async function applyCustomDateRange(page, startDate, endDate) {
  console.log(`Setting custom date range: ${startDate} to ${endDate}`);
  const trigger = await page.$('[date-range-picker]');
  if (trigger) {
    await trigger.click();
    await page.waitForTimeout(500);
  }

  const applied = await page.evaluate(({ start, end }) => {
    const el = document.querySelector('[date-range-picker]');
    if (!el) return { success: false, reason: 'no element' };
    const jqEl = window.jQuery ? window.jQuery(el) : null;
    if (!jqEl) return { success: false, reason: 'no jQuery' };
    const picker = jqEl.data('daterangepicker');
    if (!picker) return { success: false, reason: 'no picker instance' };
    picker.setStartDate(start);
    picker.setEndDate(end);
    picker.clickApply();
    return { success: true };
  }, { start: startDate, end: endDate });

  if (applied.success) console.log(`  Applied: ${startDate} to ${endDate}`);
  else console.log(`  daterangepicker API failed: ${applied.reason}`);

  await page.waitForTimeout(3000);
  try { await page.waitForSelector('.ui-grid-row', { timeout: 15000 }); }
  catch { console.log('  No results appeared.'); await page.waitForTimeout(2000); }
}

function formatMMDDYY(date) {
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const yy = String(date.getFullYear()).slice(-2);
  return `${mm}/${dd}/${yy}`;
}

// --- Page Controls (DOM fallback) ---

async function setPageSize(page) {
  try {
    await page.selectOption('select.page-size-dropdown', '200');
    await page.waitForTimeout(2000);
    console.log('Page size set to 200.');
  } catch {}
}

async function scrollToLoadAll(page) {
  let previousCount = 0;
  let stableCount = 0;
  console.log('Scrolling to load all items...');
  for (let i = 0; i < MAX_SCROLL_ATTEMPTS; i++) {
    await page.evaluate(() => {
      const viewport = document.querySelector('.ui-grid-viewport');
      if (viewport) viewport.scrollTop = viewport.scrollHeight;
      window.scrollTo(0, document.body.scrollHeight);
    });
    await page.waitForTimeout(SCROLL_WAIT_MS);
    const currentCount = await page.evaluate(() => document.querySelectorAll('.ui-grid-row').length);
    if (i % 5 === 0 || currentCount !== previousCount) console.log(`  Scroll ${i + 1}: ${currentCount} items loaded`);
    if (currentCount === previousCount) {
      stableCount++;
      if (stableCount >= STABLE_CHECKS_TO_STOP) { console.log(`All items loaded (${currentCount} total).`); return currentCount; }
    } else { stableCount = 0; }
    previousCount = currentCount;
  }
  console.log(`Reached max scroll attempts. ${previousCount} items loaded.`);
  return previousCount;
}

// --- Main ---

async function main() {
  console.log('Launching browser (Edge)...');

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: 'msedge',
    headless: false,
    viewport: { width: 1400, height: 900 },
  });

  const page = context.pages()[0] || await context.newPage();

  try {
    // Step 1: Capture API request before navigation
    let capturedQuery = null;
    let capturedHeaders = null;
    const requestHandler = req => {
      if (req.url().includes('/api/es/ocv/_search') && req.method() === 'POST') {
        try {
          const body = JSON.parse(req.postData());
          if (body.size > 0 && !capturedQuery) {
            capturedQuery = body;
            capturedHeaders = req.headers();
          }
        } catch {}
      }
    };
    page.on('request', requestHandler);

    // Step 2: Navigate
    const navUrl = dateArg ? applyDateToUrl(ocvUrl, dateArg) : ocvUrl;
    const isCustomRange = dateArg && dateArg.includes(':');

    console.log('Navigating to OCV...');
    if (dateArg) console.log(`Date filter: --date ${dateArg}`);
    await page.goto(navUrl, { waitUntil: 'domcontentloaded', timeout: 120000 });

    // Step 3: Wait for auth
    console.log('Waiting for OCV to load (complete SSO login if prompted)...');
    await page.waitForSelector('.ui-grid-row, [role="gridcell"], .discover-list, [date-range-picker]', { timeout: 120000 });
    console.log('OCV loaded.');
    page.off('request', requestHandler);

    // Step 4: Custom date ranges
    if (isCustomRange) {
      const [startDate, endDate] = dateArg.split(':');
      capturedQuery = null;
      page.on('request', requestHandler);
      await applyCustomDateRange(page, startDate, endDate);
      await page.waitForTimeout(3000);
      page.off('request', requestHandler);
    }

    // Step 5: Extract
    console.log('Extracting feedback data via API...');
    let structuredOnlyCount = 0;
    let apiResult = await extractViaAPI(page, capturedQuery, capturedHeaders);
    let results;
    if (apiResult && apiResult.items) {
      results = apiResult.items;
      structuredOnlyCount = apiResult.structuredOnlyCount || 0;
    } else {
      // DOM fallback returns a plain array
      results = apiResult;
    }

    // Step 6: Filter for "yesterday"
    if (dateArg && OFFSET_MAP[dateArg.toLowerCase()]?.filterDate) {
      const todayStr = formatMMDDYY(new Date());
      const beforeCount = results.length;
      results = results.filter(item => !item.date.startsWith(todayStr));
      console.log(`Filtered to yesterday only: ${results.length} items (removed ${beforeCount - results.length} from today).`);
    }

    if (results.length === 0) {
      console.log('No items found for this date range.');
      const outputPath = path.resolve(outputFile);
      fs.mkdirSync(path.dirname(outputPath), { recursive: true });
      fs.writeFileSync(outputPath, CSV_HEADER + '\n', 'utf8');
      console.log(`Output: ${outputPath} (empty)`);
      return;
    }

    console.log(`Extracted ${results.length} items.`);

    // Step 7: Scrub PII
    const piiStats = scrubPII(results);
    if (piiStats.total > 0) {
      console.log(`PII scrubbed: ${piiStats.total} redactions (${piiStats.emails} emails, ${piiStats.phones} phones, ${piiStats.ocvTags} OCV tags)`);
    } else {
      console.log('PII check passed.');
    }

    // Step 8: Write CSV
    const rows = results.map(item =>
      [item.date, item.comment, item.provider, item.sentiment, item.intent,
       item.feature, item.scenario || '', item.category, item.language, item.noise, item.areaPath || '', item.client || '', item.rating || '', item.ocvId || '',
       item.cmmId || '', item.sourceContext || '', item.entryPoint || '', item.subFeature || '',
       item.sentimentThemes || '', item.copilotIntent || '', item.acrue || '',
       item.rawFeatureName || '', item.rawFeatureArea || '', item.rawPlatform || '',
       item.endpoint || '', item.application || '', item.feedbackType || '', item.rawAppData || '',
       item.platformExternal || '', item.userAgent || '', item.sdkVersion || '']
        .map(csvEscape).join(',')
    );
    const csv = [CSV_HEADER, ...rows].join('\n');

    const outputPath = path.resolve(outputFile);
    try {
      fs.mkdirSync(path.dirname(outputPath), { recursive: true });
      fs.writeFileSync(outputPath, csv, 'utf8');
    } catch (e) {
      console.error(`Failed to write output file: ${outputPath}`);
      console.error(`  ${e.message}`);
      throw e;
    }

    // Summary
    console.log('\n--- Extraction Complete ---');
    console.log(`Config: ${config.name}`);
    console.log(`Items:  ${results.length}`);
    if (structuredOnlyCount > 0 && !includeStructured) {
      console.log(`Scope:  Verbatim-only (${structuredOnlyCount} structured-only submissions excluded; use --include-structured to include them)`);
    } else if (includeStructured && structuredOnlyCount === 0) {
      console.log(`Scope:  All submissions (no structured-only items found)`);
    }
    const verbatimCount = results.filter(r => r.comment).length;
    const structuredCount = results.length - verbatimCount;
    if (includeStructured && structuredCount > 0) {
      console.log(`Scope:  ${verbatimCount} with verbatim + ${structuredCount} structured-only`);
    }
    console.log(`Output: ${outputPath}`);
    if (dateArg) console.log(`Filter: --date ${dateArg}`);
    if (results.length > 0) {
      const dates = results.map(r => r.date).filter(Boolean);
      console.log(`Dates:  ${dates[dates.length - 1]} to ${dates[0]}`);
      const noiseCount = results.filter(r => r.noise).length;
      if (noiseCount > 0) console.log(`Noise:  ${noiseCount} items flagged`);
    }

    // Step 9: Print aggregate summary (--summary flag)
    if (summaryFlag && results.length > 0) {
      const dist = (field) => {
        const counts = {};
        for (const r of results) {
          const val = r[field] || 'Unknown';
          counts[val] = (counts[val] || 0) + 1;
        }
        return Object.entries(counts)
          .sort((a, b) => b[1] - a[1]);
      };

      const fmt = (entries, limit) =>
        entries.slice(0, limit || entries.length)
          .map(([k, v]) => `${k}=${v}`)
          .join('  ');

      console.log('\n--- Summary (aggregate stats, no customer content) ---');
      console.log(`Items:      ${results.length}`);
      if (includeStructured) {
        const withVerbatim = results.filter(r => r.comment).length;
        const blank = results.length - withVerbatim;
        console.log(`Verbatims:  ${withVerbatim} with text, ${blank} structured-only (${Math.round(blank / results.length * 100)}% blank)`);
      } else if (structuredOnlyCount > 0) {
        const totalSubmissions = results.length + structuredOnlyCount;
        const verbatimPct = Math.round(results.length / totalSubmissions * 100);
        console.log(`Scope:      Verbatim-only (${results.length} of ${totalSubmissions} total submissions, ${verbatimPct}%). Sentiment metrics may differ from OCV Discover reports.`);
      }
      const summaryDates = results.map(r => r.date).filter(Boolean);
      if (summaryDates.length > 0) {
        console.log(`Date range: ${summaryDates[summaryDates.length - 1]} to ${summaryDates[0]}`);
      }
      console.log(`PII:        ${piiStats.total} redactions (${piiStats.emails} emails, ${piiStats.phones} phones, ${piiStats.ocvTags} tags)`);
      console.log(`Sentiment:  ${fmt(dist('sentiment'))}`);
      console.log(`Rating:     ${fmt(dist('rating'))}`);
      console.log(`Intent:     ${fmt(dist('intent'))}`);

      const catDist = dist('category');
      if (catDist.length > 0 && !(catDist.length === 1 && catDist[0][0] === 'Unknown')) {
        console.log(`Categories: ${fmt(catDist, 10)}`);
      }

      console.log(`Languages:  ${fmt(dist('language'), 10)}`);

      const clientDist = dist('client');
      if (clientDist.length > 0 && !(clientDist.length === 1 && clientDist[0][0] === 'Unknown')) {
        console.log(`Clients:    ${fmt(clientDist)}`);
      }

      if (domainMap) {
        const provDist = dist('provider').filter(([k]) => k !== 'Unknown' && k !== REDACTED_LABEL);
        if (provDist.length > 0) {
          console.log(`Providers:  ${fmt(provDist, 10)}`);
        }
      }

      const noiseCount = results.filter(r => r.noise).length;
      console.log(`Noise:      ${noiseCount} flagged`);
    }

    // Step 10: Generate weekly summary markdown (--weekly-summary flag)
    if (weeklySummaryFlag && results.length > 0) {
      generateWeeklySummary(results, config, piiStats);
    }

  } catch (err) {
    console.error('Extraction failed:', err.message);
    process.exit(1);
  } finally {
    await context.close();
  }
}

// --- Weekly Summary Generation ---

function generateWeeklySummary(results, config, piiStats) {
  // Determine ISO week from the most recent item's date
  const parseDateStr = (ds) => {
    const m = ds.match(/^(\d{2})\/(\d{2})\/(\d{2})/);
    if (!m) return null;
    return new Date(2000 + parseInt(m[3]), parseInt(m[1]) - 1, parseInt(m[2]));
  };

  const dates = results.map(r => parseDateStr(r.date)).filter(Boolean);
  if (dates.length === 0) return;

  const latest = new Date(Math.max(...dates));
  const earliest = new Date(Math.min(...dates));

  // ISO week number
  const getISOWeek = (d) => {
    const tmp = new Date(d.getTime());
    tmp.setHours(0, 0, 0, 0);
    tmp.setDate(tmp.getDate() + 3 - ((tmp.getDay() + 6) % 7));
    const week1 = new Date(tmp.getFullYear(), 0, 4);
    return 1 + Math.round(((tmp.getTime() - week1.getTime()) / 86400000 - 3 + ((week1.getDay() + 6) % 7)) / 7);
  };

  const isoWeek = getISOWeek(latest);
  const year = latest.getFullYear();
  const weekStr = `${year}-W${String(isoWeek).padStart(2, '0')}`;

  // Config name slug for filename
  const nameSlug = (config.name || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/_+$/, '');
  const summaryDir = path.join(path.dirname(path.resolve(outputFile)), 'summaries');
  const summaryFile = path.join(summaryDir, `${nameSlug}_${weekStr}.md`);

  if (fs.existsSync(summaryFile)) {
    console.log(`Weekly summary already exists: ${summaryFile} (skipped)`);
    return;
  }

  // Distribution helper
  const dist = (field) => {
    const counts = {};
    for (const r of results) {
      const val = r[field] || 'Unknown';
      counts[val] = (counts[val] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  };

  const pct = (n, total) => total > 0 ? Math.round(n / total * 100) : 0;
  const fmtDate = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const total = results.length;

  // Build markdown
  const lines = [];
  const ln = (s) => lines.push(s);

  ln(`# ${config.name} — Weekly Summary (${weekStr})`);
  ln('');
  ln(`**Date range:** ${fmtDate(earliest)} to ${fmtDate(latest)}`);
  ln(`**Total items:** ${total}`);
  ln(`**Generated:** ${new Date().toISOString().slice(0, 10)}`);
  ln('');

  // Sentiment table
  ln('## Sentiment');
  ln('');
  ln('| Sentiment | Count | % |');
  ln('|-----------|-------|---|');
  for (const [k, v] of dist('sentiment')) {
    ln(`| ${k} | ${v} | ${pct(v, total)}% |`);
  }
  ln('');

  // Intent table
  ln('## Intent');
  ln('');
  ln('| Intent | Count | % |');
  ln('|--------|-------|---|');
  for (const [k, v] of dist('intent')) {
    ln(`| ${k} | ${v} | ${pct(v, total)}% |`);
  }
  ln('');

  // Client breakdown
  const clientDist = dist('client');
  if (clientDist.length > 0 && !(clientDist.length === 1 && clientDist[0][0] === 'Unknown')) {
    ln('## Client Breakdown');
    ln('');
    ln('| Client | Count | % | Neg% |');
    ln('|--------|-------|---|------|');
    for (const [client, count] of clientDist) {
      const negCount = results.filter(r => (r.client || 'Unknown') === client && r.sentiment === 'Negative').length;
      ln(`| ${client} | ${count} | ${pct(count, total)}% | ${pct(negCount, count)}% |`);
    }
    ln('');
  }

  // Category breakdown
  const catDist = dist('category');
  if (catDist.length > 0 && !(catDist.length === 1 && catDist[0][0] === 'Unknown')) {
    ln('## Categories');
    ln('');

    // Build header with client columns
    const clientNames = clientDist.filter(([k]) => k !== 'Unknown').map(([k]) => k);
    const catHeader = ['Category', 'Total', '%'];
    for (const c of clientNames) catHeader.push(c);
    ln('| ' + catHeader.join(' | ') + ' |');
    ln('| ' + catHeader.map(() => '---').join(' | ') + ' |');

    for (const [cat, count] of catDist) {
      if (cat === 'Unknown') continue;
      const row = [cat, String(count), `${pct(count, total)}%`];
      for (const c of clientNames) {
        const clientCount = results.filter(r => r.category === cat && (r.client || 'Unknown') === c).length;
        row.push(String(clientCount));
      }
      ln('| ' + row.join(' | ') + ' |');
    }

    const uncategorized = results.filter(r => !r.category || r.category === 'Unknown').length;
    if (uncategorized > 0) {
      const row = ['_Uncategorized_', String(uncategorized), `${pct(uncategorized, total)}%`];
      for (const c of clientNames) {
        const cc = results.filter(r => (!r.category || r.category === 'Unknown') && (r.client || 'Unknown') === c).length;
        row.push(String(cc));
      }
      ln('| ' + row.join(' | ') + ' |');
    }
    ln('');
  }

  // Language breakdown (top 10)
  ln('## Languages (top 10)');
  ln('');
  ln('| Language | Count | % |');
  ln('|----------|-------|---|');
  for (const [k, v] of dist('language').slice(0, 10)) {
    ln(`| ${k} | ${v} | ${pct(v, total)}% |`);
  }
  ln('');

  // Daily volume
  ln('## Daily Volume');
  ln('');
  const dailyData = {};
  for (const r of results) {
    const d = parseDateStr(r.date);
    if (!d) continue;
    const day = fmtDate(d);
    if (!dailyData[day]) dailyData[day] = { total: 0, neg: 0 };
    dailyData[day].total++;
    if (r.sentiment === 'Negative') dailyData[day].neg++;
  }
  ln('| Day | Total | Neg | Neg% |');
  ln('|-----|-------|-----|------|');
  for (const day of Object.keys(dailyData).sort()) {
    const dd = dailyData[day];
    ln(`| ${day} | ${dd.total} | ${dd.neg} | ${pct(dd.neg, dd.total)}% |`);
  }
  ln('');

  // Red flags
  const redFlags = {
    'HTML in output': /html|font.?size|<div|<span|style=/i,
    'Keep-it disappears': /keep.?it.*disappear|click.*keep.*disappear|disappear.*keep/i,
    'Rate limit hit': /hourly.?limit|rate.?limit|limit.*request|reached.*limit/i,
    'Cannot dismiss': /can.?t get out|didn.?t request this feature|don.?t want.*copilot|how.*turn.*off|disable.*copilot/i,
    'Wrong word substitution': /changed.*word|wrong.*word|billing.*killing|substitut|replaced.*word/i,
  };

  const flagCounts = {};
  for (const [flag, rx] of Object.entries(redFlags)) {
    const count = results.filter(r => rx.test(r.comment)).length;
    if (count > 0) flagCounts[flag] = count;
  }

  if (Object.keys(flagCounts).length > 0) {
    ln('## Red Flags');
    ln('');
    ln('| Flag | Count |');
    ln('|------|-------|');
    for (const [flag, count] of Object.entries(flagCounts).sort((a, b) => b[1] - a[1])) {
      ln(`| ${flag} | ${count} |`);
    }
    ln('');
  }

  // PII stats
  ln('## Data Quality');
  ln('');
  ln(`- PII redactions: ${piiStats.total} (${piiStats.emails} emails, ${piiStats.phones} phones, ${piiStats.ocvTags} OCV tags)`);
  ln(`- Noise flagged: ${results.filter(r => r.noise).length}`);
  ln('');

  // Write file
  fs.mkdirSync(summaryDir, { recursive: true });
  fs.writeFileSync(summaryFile, lines.join('\n'), 'utf8');
  console.log(`Weekly summary: ${summaryFile}`);
}

main().catch(err => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});

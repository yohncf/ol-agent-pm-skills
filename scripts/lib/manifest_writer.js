// Analysis manifest writer — persists analysis results without raw customer content.
// Manifests store: aggregate stats, themes with OcvId pointers + AI paraphrases,
// flagged items, and executive summaries.
//
// Usage:
//   const mw = require('./lib/manifest_writer');
//   const manifest = mw.createManifest('data/extract.csv', 'configs/all_scenarios.json');
//   mw.addMetadata(manifest, { sentimentDistribution: {...}, ... });
//   mw.addThemes(manifest, [{ name: 'Wrong Language', count: 42, ... }]);
//   mw.writeManifest(manifest, 'data/manifests/analysis_2026-04-09.json');

const fs = require('fs');
const path = require('path');

const MANIFEST_VERSION = '1.0';

/**
 * Create a new manifest with source metadata.
 * @param {string} csvPath - Path to the source CSV
 * @param {string} configPath - Path to the config file used for extraction
 * @param {{ dateRange?: {start: string, end: string}, totalItems?: number, verbatimItems?: number }} opts
 * @returns {object} manifest object
 */
function createManifest(csvPath, configPath, opts = {}) {
  return {
    version: MANIFEST_VERSION,
    generatedAt: new Date().toISOString(),
    source: {
      csvFile: path.basename(csvPath),
      csvPath: csvPath,
      config: configPath ? path.basename(configPath) : null,
      dateRange: opts.dateRange || { start: null, end: null },
      totalItems: opts.totalItems || 0,
      verbatimItems: opts.verbatimItems || 0,
      structuredOnlyItems: (opts.totalItems || 0) - (opts.verbatimItems || 0),
    },
    metadata: {},
    themes: [],
    flaggedItems: [],
    executiveSummary: null,
    csvDeleted: false,
    csvDeletedAt: null,
  };
}

/**
 * Add Pass 1 aggregate metadata to the manifest.
 * @param {object} manifest
 * @param {object} stats - Keys: sentimentDistribution, ratingDistribution,
 *   scenarioDistribution, clientDistribution, categoryDistribution,
 *   languageDistribution, feedbackTypeDistribution, intentDistribution,
 *   audienceDistribution, noiseCount,
 *   byAudience (per-audience breakdowns), byLanguage (per-language breakdowns), etc.
 */
function addMetadata(manifest, stats) {
  manifest.metadata = { ...manifest.metadata, ...stats };
}

/**
 * Add discovered themes to the manifest.
 * @param {object} manifest
 * @param {Array<{name: string, count: number, description: string, sentiment?: object, audienceSkew?: string, languageSignal?: string, examples: Array<{ocvId: string, paraphrase: string}>}>} themes
 */
function addThemes(manifest, themes) {
  manifest.themes = themes.map(t => ({
    name: t.name,
    count: t.count || 0,
    description: t.description || '',
    sentiment: t.sentiment || {},
    audienceSkew: t.audienceSkew || null,
    languageSignal: t.languageSignal || null,
    examples: (t.examples || []).map(ex => ({
      ocvId: ex.ocvId,
      paraphrase: ex.paraphrase || '',
    })),
  }));
}

/**
 * Add flagged high-value items to the manifest.
 * @param {object} manifest
 * @param {Array<{reason: string, ocvIds: string[], paraphrase: string}>} items
 */
function addFlaggedItems(manifest, items) {
  manifest.flaggedItems = items.map(f => ({
    reason: f.reason,
    ocvIds: f.ocvIds || [],
    paraphrase: f.paraphrase || '',
  }));
}

/**
 * Add the executive summary text.
 * @param {object} manifest
 * @param {string} summary
 */
function addExecutiveSummary(manifest, summary) {
  manifest.executiveSummary = summary;
}

/**
 * Write the manifest to a JSON file.
 * Creates the output directory if it doesn't exist.
 * @param {object} manifest
 * @param {string} outputPath
 */
function writeManifest(manifest, outputPath) {
  const dir = path.dirname(outputPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(outputPath, JSON.stringify(manifest, null, 2), 'utf-8');
  return outputPath;
}

/**
 * Read an existing manifest from disk.
 * @param {string} manifestPath
 * @returns {object} parsed manifest
 */
function readManifest(manifestPath) {
  const content = fs.readFileSync(manifestPath, 'utf-8');
  return JSON.parse(content);
}

/**
 * Mark the source CSV as deleted in the manifest.
 * @param {string} manifestPath
 */
function markCsvDeleted(manifestPath) {
  const manifest = readManifest(manifestPath);
  manifest.csvDeleted = true;
  manifest.csvDeletedAt = new Date().toISOString();
  writeManifest(manifest, manifestPath);
}

/**
 * Generate a default manifest output path from a CSV filename.
 * e.g., "data/all_scenarios_week2.csv" → "data/manifests/all_scenarios_week2_manifest.json"
 * @param {string} csvPath
 * @returns {string}
 */
function defaultManifestPath(csvPath) {
  const base = path.basename(csvPath, '.csv');
  const dir = path.join(path.dirname(csvPath), 'manifests');
  return path.join(dir, `${base}_manifest.json`);
}

module.exports = {
  createManifest,
  addMetadata,
  addThemes,
  addFlaggedItems,
  addExecutiveSummary,
  writeManifest,
  readManifest,
  markCsvDeleted,
  defaultManifestPath,
  MANIFEST_VERSION,
};

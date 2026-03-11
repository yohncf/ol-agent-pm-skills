// Shared CSV parser — handles multi-line quoted fields (RFC 4180 compliant)
// Usage: const { parseCSV } = require('./lib/csv_parser');

function parseCSV(text) {
  const { headers, records } = parseCSVRecords(text);
  if (headers.length === 0) return { headers: [], rows: [] };

  const rows = [];
  for (const { fields, rowNum } of records) {
    const row = {};
    headers.forEach((h, idx) => row[h] = fields[idx] || '');
    row._rowNum = rowNum;
    rows.push(row);
  }
  return { headers, rows };
}

function parseCSVRecords(text) {
  const headers = [];
  const records = [];
  let pos = 0;
  let rowNum = 1;

  const headerResult = parseNextRecord(text, pos);
  if (!headerResult) return { headers, records };
  headers.push(...headerResult.fields);
  pos = headerResult.nextPos;
  rowNum++;

  while (pos < text.length) {
    if (text[pos] === '\n') { pos++; rowNum++; continue; }
    if (text[pos] === '\r' && text[pos + 1] === '\n') { pos += 2; rowNum++; continue; }
    if (text[pos] === '\r') { pos++; rowNum++; continue; }

    const result = parseNextRecord(text, pos);
    if (!result) break;

    if (result.fields.length === headers.length) {
      records.push({ fields: result.fields, rowNum });
    }
    const consumed = text.slice(pos, result.nextPos);
    const newlines = (consumed.match(/\n/g) || []).length;
    rowNum += Math.max(1, newlines);
    pos = result.nextPos;
  }

  return { headers, records };
}

function parseNextRecord(text, startPos) {
  const fields = [];
  let current = '';
  let inQuotes = false;
  let i = startPos;

  while (i < text.length) {
    const ch = text[i];

    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') {
        current += '"';
        i += 2;
      } else if (ch === '"') {
        inQuotes = false;
        i++;
      } else {
        current += ch;
        i++;
      }
    } else {
      if (ch === '"') {
        inQuotes = true;
        i++;
      } else if (ch === ',') {
        fields.push(current);
        current = '';
        i++;
      } else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
        fields.push(current);
        const skip = ch === '\r' ? 2 : 1;
        return { fields, nextPos: i + skip };
      } else if (ch === '\r') {
        fields.push(current);
        return { fields, nextPos: i + 1 };
      } else {
        current += ch;
        i++;
      }
    }
  }

  if (current || fields.length > 0) {
    fields.push(current);
  }
  return fields.length > 0 ? { fields, nextPos: i } : null;
}

module.exports = { parseCSV };

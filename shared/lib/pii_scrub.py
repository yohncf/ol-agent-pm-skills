"""
Shared PII scrubbing utilities for ODS and other Python extraction scripts.

Mirrors the PII patterns used in extract_standalone.js (OCV extraction) so that
all extraction outputs have consistent redaction.

Usage:
    from lib.pii_scrub import scrub_text, scrub_row, PII_PATTERNS
"""

import re


# --- PII Patterns ---
# Matches the JS patterns in extract_standalone.js scrubPII()

PII_PATTERNS = [
    {
        "name": "ocv_tags",
        "regex": re.compile(r"\[PII:\s*Email\]", re.IGNORECASE),
        "replacement": "[REDACTED_EMAIL]",
    },
    {
        "name": "emails",
        "regex": re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "replacement": "[REDACTED_EMAIL]",
    },
    {
        "name": "phones",
        "regex": re.compile(
            r"(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"
        ),
        "replacement": "[REDACTED_PHONE]",
    },
    {
        # Catches phone formats like 086/3801088 common in European feedback
        "name": "phones_eu",
        "regex": re.compile(r"\b\d{3,4}[/\-]\d{6,8}\b"),
        "replacement": "[REDACTED_PHONE]",
    },
    {
        # Sign-off names: "Regards, John Smith" / "Mvh Firstname Lastname"
        "name": "signoff_names",
        "regex": re.compile(
            r"(?:regards?,?\s*|sincerely,?\s*|thanks?,?\s*|thank you,?\s*"
            r"|cheers,?\s*|best,?\s*|mvh\s+|met vriendelijke groet\s+"
            r"|cordialement,?\s*|mit freundlichen gr[üu](?:e?ssen|ßen),?\s*"
            r"|atenciosamente,?\s*|saludos,?\s*)"
            r"([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)",
            re.IGNORECASE,
        ),
        "replacement_group": 1,
        "replacement": "[REDACTED_NAME]",
    },
]


def scrub_text(text: str) -> tuple[str, dict[str, int]]:
    """
    Scrub PII from a single text string.

    Returns:
        (scrubbed_text, stats_dict)
        where stats_dict maps pattern name -> count of redactions
    """
    if not text:
        return text, {}

    stats: dict[str, int] = {}

    for pattern in PII_PATTERNS:
        name = pattern["name"]
        regex = pattern["regex"]

        if "replacement_group" in pattern:
            # Replace only the captured group, not the full match
            group = pattern["replacement_group"]
            replacement_text = pattern["replacement"]

            def _replace(m, _g=group, _r=replacement_text):
                full = m.group(0)
                captured = m.group(_g)
                return full.replace(captured, _r)

            new_text = regex.sub(_replace, text)
            count = len(regex.findall(text))
        else:
            matches = regex.findall(text)
            count = len(matches)
            new_text = regex.sub(pattern["replacement"], text)

        if count > 0:
            stats[name] = stats.get(name, 0) + count
            text = new_text

    return text, stats


def scrub_row(row: list[str], columns: list[int] | None = None) -> dict[str, int]:
    """
    Scrub PII from specific columns of a CSV row (in-place).

    Args:
        row: List of string values (modified in-place)
        columns: Indices of columns to scrub. If None, scrubs all columns.

    Returns:
        Aggregate stats dict mapping pattern name -> count
    """
    stats: dict[str, int] = {}
    indices = columns if columns is not None else range(len(row))

    for i in indices:
        if i < len(row) and row[i]:
            scrubbed, col_stats = scrub_text(row[i])
            row[i] = scrubbed
            for name, count in col_stats.items():
                stats[name] = stats.get(name, 0) + count

    return stats

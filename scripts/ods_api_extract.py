"""
ODS API Extraction — Extract ticket details from ODS via REST API.

Reads a CSV of ODS ticket URLs (or IDs), acquires a bearer token via Azure
DefaultAzureCredential, and calls the ODS REST API for each ticket. Outputs
a CSV with ProblemStatement, Tags, Symptom, and other fields.

No browser or SSO required — uses your existing `az login` credentials.

Prerequisites:
    pip install azure-identity

Usage:
    python scripts/ods_api_extract.py --input data/ticket_urls.csv
    python scripts/ods_api_extract.py --input data/ticket_urls.csv --output data/results.csv
    python scripts/ods_api_extract.py --input data/ticket_urls.csv --fields all
    python scripts/ods_api_extract.py --input data/ticket_urls.csv --offset 100 --limit 200

Input CSV format:
    - No header required
    - URL in the last column (https://ods.office.net/#/saratickets/{id})
    - OR a single column of ticket IDs
    - Additional columns (date, account type, etc.) are preserved in output
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
from azure.identity import DefaultAzureCredential

# Add scripts/ to path so lib.pii_scrub is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.pii_scrub import scrub_text

API_BASE = "https://portal.diagnostics.office.com/v1/diagnosticsession/?id="
CLIENT_ID = "57da3f69-2d82-4c17-9e57-2e6d78b2dc60"
TICKET_ID_PATTERN = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.IGNORECASE)

# Core fields always included in output
CORE_FIELDS = [
    "TicketId", "ProblemStatement", "Symptom", "DataClassification",
    "UserLocale", "ProductName",
]

# Extended fields available with --fields all
EXTENDED_FIELDS = [
    "Tags", "TicketTier", "EntitlementGroup", "OCVArea",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract ODS ticket details via REST API (no browser needed)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --input data/urls.csv
  %(prog)s --input data/urls.csv --output data/results.csv --fields all
  %(prog)s --input data/urls.csv --offset 100 --limit 50
        """,
    )
    parser.add_argument("--input", required=True, help="Input CSV with ODS ticket URLs or IDs")
    parser.add_argument("--output", default=None, help="Output CSV path (default: input_results.csv)")
    parser.add_argument("--fields", choices=["core", "all"], default="core",
                        help="Field set: 'core' (default) or 'all' (adds Tags, TicketTier, OCVArea)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N tickets (for resume)")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N tickets")
    parser.add_argument("--append", action="store_true", help="Append to output file instead of overwriting")
    return parser.parse_args()


def read_tickets(input_path: str) -> list[dict]:
    """Read tickets from input CSV. Extracts ticket ID from URL or raw ID."""
    tickets = []
    with open(input_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            # Try to find a ticket ID in any column
            ticket_id = None
            url = ""
            for col in reversed(row):
                match = TICKET_ID_PATTERN.search(col)
                if match:
                    ticket_id = match.group(0)
                    if "ods.office.net" in col:
                        url = col
                    break

            if ticket_id:
                # Preserve extra columns from input as metadata
                meta = row[:-1] if len(row) > 1 else []
                if not url:
                    url = f"https://ods.office.net/#/saratickets/{ticket_id}"
                tickets.append({"id": ticket_id, "meta": meta, "url": url})

    return tickets


def acquire_token() -> str:
    """Acquire bearer token via DefaultAzureCredential (uses az login)."""
    cred = DefaultAzureCredential()
    token_obj = cred.get_token(f"{CLIENT_ID}/.default")
    return token_obj.token


def extract_ticket(ticket_id: str, token: str) -> dict:
    """Call ODS API for a single ticket. Returns parsed JSON."""
    req = urllib.request.Request(API_BASE + ticket_id)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        if isinstance(data, list):
            data = data[0] if data else {}
        return data


def format_row(ticket: dict, data: dict, include_extended: bool) -> tuple[list[str], dict[str, int]]:
    """Format a single ticket's data into a CSV row. Returns (row, pii_stats)."""
    problem = data.get("ProblemStatement", "")
    problem = problem.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()

    # Scrub PII from ProblemStatement
    problem, pii_stats = scrub_text(problem)

    # Core fields
    row = ticket["meta"] + [
        ticket["id"],
        problem,
        data.get("Symptom", ""),
        data.get("DataClassification", ""),
        data.get("UserLocale", ""),
        data.get("ProductName", ""),
    ]

    if include_extended:
        # Tags as semicolon-separated string
        tags = data.get("Tags", [])
        if isinstance(tags, list):
            tag_names = [t.get("Name", "") if isinstance(t, dict) else str(t) for t in tags]
            tags_str = "; ".join(tag_names)
        else:
            tags_str = str(tags)

        # TicketTier and EntitlementGroup from top-level
        ticket_tier = data.get("TicketTier", "")
        entitlement = data.get("EntitlementGroup", "")

        # OCVArea from DiagnosticSessionAttributes
        attrs = data.get("DiagnosticSessionAttributes", {})
        if isinstance(attrs, list):
            attrs_dict = {a.get("Key", ""): a.get("Value", "") for a in attrs if isinstance(a, dict)}
        elif isinstance(attrs, dict):
            attrs_dict = attrs
        else:
            attrs_dict = {}
        ocv_area = attrs_dict.get("OCVArea", "")

        row.extend([tags_str, ticket_tier, entitlement, ocv_area])

    row.append(ticket["url"])
    return row, pii_stats


def build_header(meta_count: int, include_extended: bool) -> list[str]:
    """Build CSV header row."""
    meta_headers = [f"Meta{i+1}" for i in range(meta_count)]
    header = meta_headers + CORE_FIELDS
    if include_extended:
        header.extend(EXTENDED_FIELDS)
    header.append("URL")
    return header


def main():
    args = parse_args()
    include_extended = args.fields == "all"

    # Read input
    tickets = read_tickets(args.input)
    if not tickets:
        print("No tickets found in input file.", file=sys.stderr)
        sys.exit(1)

    # Apply offset/limit
    total_input = len(tickets)
    tickets = tickets[args.offset:]
    if args.limit:
        tickets = tickets[:args.limit]

    print(f"Input: {total_input} tickets, processing {len(tickets)} (offset={args.offset})")

    # Output path
    output = args.output
    if not output:
        base = args.input.rsplit(".", 1)[0]
        output = f"{base}_results.csv"

    # Acquire token
    print("Acquiring Azure token...")
    token = acquire_token()
    print("Token acquired.")

    # Determine metadata column count from first ticket
    meta_count = len(tickets[0]["meta"]) if tickets else 0
    header = build_header(meta_count, include_extended)

    # Extract
    mode = "a" if args.append else "w"
    start = time.time()
    success = 0
    errors = 0
    pii_totals: dict[str, int] = {}

    with open(output, mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        if not args.append:
            writer.writerow(header)

        for i, ticket in enumerate(tickets):
            try:
                data = extract_ticket(ticket["id"], token)
                row, pii_stats = format_row(ticket, data, include_extended)
                writer.writerow(row)
                success += 1
                for name, count in pii_stats.items():
                    pii_totals[name] = pii_totals.get(name, 0) + count
            except Exception as e:
                error_row = ticket["meta"] + [ticket["id"], f"ERROR: {e}"]
                error_row.extend([""] * (len(header) - len(error_row) - 1))
                error_row.append(ticket["url"])
                writer.writerow(error_row)
                errors += 1

            if (i + 1) % 50 == 0:
                elapsed = time.time() - start
                rate = (i + 1) / elapsed
                print(f"  [{i+1}/{len(tickets)}] {elapsed:.1f}s, {rate:.1f} tickets/sec, {errors} errors")

    elapsed = time.time() - start
    rate = success / elapsed if elapsed > 0 else 0
    print(f"\nDone: {success} extracted, {errors} errors in {elapsed:.1f}s ({rate:.1f} tickets/sec)")
    print(f"Output: {output}")

    # PII summary
    total_pii = sum(pii_totals.values())
    if total_pii > 0:
        emails = pii_totals.get("emails", 0) + pii_totals.get("ocv_tags", 0)
        phones = pii_totals.get("phones", 0) + pii_totals.get("phones_eu", 0)
        names = pii_totals.get("signoff_names", 0)
        print(f"PII scrubbed: {total_pii} redactions ({emails} emails, {phones} phones, {names} names)")
    else:
        print("PII check passed — no PII detected.")


if __name__ == "__main__":
    main()

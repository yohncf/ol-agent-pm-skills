"""
Generate a stratified random sample of Cloud Cache support ticket URLs from Kusto.

Queries odsprodkusto.eastus/ods for DiagnosticSession records classified as
Support / RecoveryAndContactSupport, stratifies by AccountType, and outputs
a CSV of ODS ticket URLs compatible with ods_batch_extract.js.

Usage:
    python scripts/generate_sample_urls.py [--sample-size 600] [--days 30]
"""

import argparse
import csv
import os
import math
import random
from datetime import datetime, timedelta, timezone

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.identity import DefaultAzureCredential


CLUSTER = "https://odsprodkusto.eastus.kusto.windows.net"
DATABASE = "ods"
ODS_BASE_URL = "https://ods.office.net/#/saratickets/"

ACCOUNT_TYPE_QUERY = """
let _end = datetime({end_time});
let _start = datetime({start_time});
ods
| where UserPUID startswith "C8C9"
| where ClassName == "DiagnosticSession" and (ClientType == "MONARCH" or ClientType == "MOZILLA")
| where ServerTimeStampUtc between (_start .. _end)
| where tostring(ClassJson.DataClassification) == "Support"
    and tostring(ClassJson.Symptom) == "RecoveryAndContactSupport"
| summarize arg_max(ServerTimeStampUtc, *) by SessionId
| extend AllTags = tostring(ClassJson)
| extend AccountType = case(
    AllTags has 'IMAPCloudCache', 'IMAP',
    AllTags has 'GmailCloudCache', 'Gmail',
    AllTags has 'YahooCloudCache', 'Yahoo',
    AllTags has 'POP3CloudCache', 'Pop',
    AllTags has 'iCloudCloudCache', 'ICloud',
    'Other')
| extend Created = todatetime(ClassJson.CreatedTime)
| extend DiagnosticSessionId = tostring(ClassJson.DiagnosticSessionId)
| where isnotempty(DiagnosticSessionId)
| project DiagnosticSessionId, AccountType, Created, ServerTimeStampUtc
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Generate stratified sample of ODS ticket URLs")
    parser.add_argument("--sample-size", type=int, default=600, help="Total sample size (default: 600)")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    return parser.parse_args()


def query_kusto(days: int) -> list[dict]:
    """Query Kusto for all support sessions in the time window."""
    end_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_time = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = ACCOUNT_TYPE_QUERY.format(end_time=end_time, start_time=start_time)

    print(f"Querying Kusto for support sessions ({days}-day window)...")
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(CLUSTER, DefaultAzureCredential())
    client = KustoClient(kcsb)
    response = client.execute(DATABASE, query)

    rows = []
    for row in response.primary_results[0]:
        rows.append({
            "SessionId": row["DiagnosticSessionId"],
            "AccountType": row["AccountType"],
            "Created": str(row["Created"]),
            "ServerTimeStampUtc": str(row["ServerTimeStampUtc"]),
        })

    print(f"  Total sessions found: {len(rows)}")
    return rows


def stratified_sample(rows: list[dict], sample_size: int, seed: int) -> list[dict]:
    """Take a stratified random sample proportional to AccountType."""
    random.seed(seed)

    # Group by AccountType
    groups = {}
    for row in rows:
        acct = row["AccountType"]
        groups.setdefault(acct, []).append(row)

    # Print population breakdown
    print("\n  Population breakdown:")
    for acct in sorted(groups, key=lambda k: len(groups[k]), reverse=True):
        pct = len(groups[acct]) / len(rows) * 100
        print(f"    {acct}: {len(groups[acct])} ({pct:.1f}%)")

    # Proportional allocation (minimum 1 per group)
    total = len(rows)
    allocation = {}
    allocated = 0
    for acct, group in sorted(groups.items(), key=lambda x: len(x[1])):
        n = max(1, math.floor(len(group) / total * sample_size))
        allocation[acct] = min(n, len(group))
        allocated += allocation[acct]

    # Distribute remaining slots to largest groups
    remaining = sample_size - allocated
    for acct in sorted(groups, key=lambda k: len(groups[k]), reverse=True):
        if remaining <= 0:
            break
        add = min(remaining, len(groups[acct]) - allocation[acct])
        allocation[acct] += add
        remaining -= add

    # Sample from each group
    sampled = []
    print(f"\n  Stratified sample allocation (n={sample_size}):")
    for acct in sorted(allocation, key=lambda k: allocation[k], reverse=True):
        n = allocation[acct]
        picked = random.sample(groups[acct], n)
        sampled.extend(picked)
        print(f"    {acct}: {n}")

    random.shuffle(sampled)
    return sampled


def write_csv(sampled: list[dict], output_path: str):
    """Write sample to CSV compatible with ods_batch_extract.js input."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in sampled:
            url = f"{ODS_BASE_URL}{row['SessionId']}"
            # Format: Date, AccountType, Type, Category, URL
            # ods_batch_extract.js reads: fields[0]=date, fields[3]=category, URL via regex
            writer.writerow([
                row["Created"],
                row["AccountType"],
                "Support",
                "RecoveryAndContactSupport",
                url,
            ])

    print(f"\n  Wrote {len(sampled)} URLs to {output_path}")


def main():
    args = parse_args()

    output = args.output or os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data",
        "cloud_cache_sample_urls.csv",
    )

    rows = query_kusto(args.days)
    sampled = stratified_sample(rows, args.sample_size, args.seed)
    write_csv(sampled, output)

    print("\nDone. Next step:")
    print(f'  node scripts/ods_batch_extract.js --input "{output}"')


if __name__ == "__main__":
    main()

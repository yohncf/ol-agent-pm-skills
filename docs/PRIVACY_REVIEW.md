# OCV Extraction Tool — Privacy & Data Handling Review

**Date:** 2026-02-27 (updated)
**Original date:** 2026-02-25
**Author:** Lucas Reis (PM, Microsoft Outlook)
**Tool:** ocv-extraction (standalone Node.js CLI)
**Purpose:** Extract verbatim customer feedback from OCV for Monarch IMAP tracking

---

## 1. Overview

This tool automates the extraction of customer feedback from One Customer Voice (OCV) into CSV files for analysis. It runs locally on the user's machine and does not transmit data to any external service.

**What it does:**
- Opens OCV in a local Edge browser via Playwright (browser automation)
- Reads feedback items from the rendered page (DOM scraping)
- Scrubs personally identifiable information (PII) in-memory
- Writes a cleaned CSV to the user's local disk

**What it does NOT do:**
- Send data to external APIs, cloud services, or third-party processors
- Store raw (unscrubbed) customer data on disk
- Transmit credentials or session tokens outside the local machine

---

## 2. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Microsoft Network Boundary                   │
│                                                                 │
│   ┌──────────┐    HTTPS     ┌──────────────────────────────┐    │
│   │          │ ──────────►  │  User's Local Machine        │    │
│   │   OCV    │              │                              │    │
│   │  Server  │              │  ┌────────┐   ┌───────────┐  │    │
│   │          │  (internal)  │  │  Edge   │──►│  Node.js  │  │    │
│   └──────────┘              │  │ Browser │   │  Process  │  │    │
│                             │  └────────┘   └─────┬─────┘  │    │
│                             │                     │         │    │
│                             │              PII Scrubbing    │    │
│                             │              (in-memory)      │    │
│                             │                     │         │    │
│                             │                     ▼         │    │
│                             │              ┌────────────┐   │    │
│                             │              │  CSV File  │   │    │
│                             │              │ (OneDrive  │   │    │
│                             │              │  for Biz)  │   │    │
│                             │              └────────────┘   │    │
│                             └──────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

All components operate within Microsoft's network boundary. No data exits the corporate tenant at any point in the pipeline.

---

## 3. Data Processing Steps

### Step 1: OCV to Browser (Network)

| Aspect | Detail |
|--------|--------|
| Source | OCV Discover (ocv.microsoft.com) |
| Transport | HTTPS over Microsoft internal network |
| Authentication | Microsoft SSO (same as manual OCV access) |
| Data accessed | Only feedback items the user is already authorized to view |
| Difference from manual use | None. Equivalent to the user opening OCV in Edge and reading the page. |

### Step 2: Browser to Node.js Process (Local)

| Aspect | Detail |
|--------|--------|
| Mechanism | Playwright's `page.evaluate()` — executes JavaScript inside the browser tab |
| What is read | `.textContent` of rendered DOM elements (same as copy/paste) |
| Data transferred | JSON array of objects: `{type, date, comment, tags}` |
| Transport | Inter-process communication (IPC) on local machine. No network involved. |

### Step 3: PII Scrubbing (In-Memory)

| Aspect | Detail |
|--------|--------|
| When it runs | After extraction, before any data is written to disk |
| Location | Node.js process memory (RAM) |
| Patterns scrubbed | See Section 4 below |
| Raw data on disk? | No. Raw data exists only in RAM and is overwritten by the scrubbed version. |

### Step 4: CSV to Disk (Local/OneDrive)

| Aspect | Detail |
|--------|--------|
| Output location | User's local filesystem (OneDrive for Business sync folder) |
| Storage service | Microsoft OneDrive for Business (corporate tenant) |
| Contents | Scrubbed feedback data (PII removed) |
| Encryption at rest | OneDrive for Business default encryption (BitLocker + per-file encryption) |
| Access control | Inherits OneDrive folder permissions (user's account by default) |

### Step 5: Browser Profile Storage

| Aspect | Detail |
|--------|--------|
| Location | `.browser-profile/` directory (local disk, within project folder) |
| Contents | Chromium profile data: SSO session cookies, cache, local storage |
| Sensitivity | Equivalent to Edge's own profile directory (`%LOCALAPPDATA%\Microsoft\Edge\User Data`) |
| Credentials in plaintext? | No. Session tokens are stored in Chromium's standard cookie store. |

---

## 4. PII Scrubbing Details

The `scrubPII()` function processes all comment text in-memory before the CSV is written. It applies the following patterns in order:

| # | Pattern | Matches | Replaced With |
|---|---------|---------|---------------|
| 1 | `\[PII:\s*Email\]` | OCV's own redaction tags (e.g., `[PII: Email]`) | `[REDACTED_EMAIL]` |
| 2 | Email regex | Raw email addresses (e.g., `user@example.com`) | `[REDACTED_EMAIL]` |
| 3 | Phone regex | Phone numbers in common formats (e.g., `(555) 123-4567`, `+1 555.123.4567`) | `[REDACTED_PHONE]` |

**What OCV already does:**
- OCV redacts some email addresses server-side, replacing them with `[PII: Email]` before rendering.
- The tag `ValidEmailPresent` indicates the original feedback contained an email, even if redacted.

**What this tool adds:**
- Normalizes OCV's `[PII: Email]` tags to a consistent `[REDACTED_EMAIL]` format.
- Catches raw email addresses that OCV's server-side scrubber may have missed.
- Catches phone numbers, which OCV does not redact.

**Limitations:**
- Does not detect names, physical addresses, or account IDs embedded in free-text comments. These would require NLP-based detection or a more comprehensive PII library.
- Regex-based scrubbing may have false negatives for unusual formats (e.g., emails with unicode characters).

### 4b. Email Domain Handling and ISP Whitelist

Per Microsoft Information Security policy, PII is any information that can identify, contact, or locate a specific individual — either on its own or when **reasonably linked** to an individual through other data. Email addresses are explicitly PII. Email domains present a nuanced case:

- **Public ISP domains** (e.g., `@gmail.com`, `@comcast.net`) are used by millions of users. The domain alone does not narrow the population enough to identify anyone, even when combined with feedback text. These are **not PII**.
- **Custom/corporate domains** (e.g., `@smallcompany.com`) may have a small user population. Combined with feedback text ("I'm the IT admin and can't sync"), the domain could reasonably identify an individual. These **are PII**.
- **Sensitive organizational domains** (e.g., healthcare, religious, educational institutions) add additional classification risk even if the user population is large.

**Measure taken:** The tool uses a **Tier 1 ISP whitelist** (`isp_whitelist.json`) containing only public consumer ISPs with large user populations — providers like AOL, Comcast, GMX, T-Online, etc. When extracting provider information from email domains:

- Domains matching the whitelist are extracted as the **provider name** (e.g., "Comcast"), not the raw domain.
- Domains not on the whitelist are **redacted entirely** and replaced with `[CUSTOM_DOMAIN]`.
- The raw email address is never written to disk — only the provider label or the redacted placeholder.

The whitelist currently contains ~55 public ISPs with ~150 domain patterns, sourced from the Monarch IMAP accounts team's provider tracking list. It can be extended over time as new public ISPs are identified, but **only public consumer ISPs with large user populations should be added.** The following categories are explicitly excluded:

- Small organizations, churches, schools, or personal domains
- Healthcare organizations (e.g., NHS)
- Religious organizations (e.g., jwpub.org)
- Hosting/infrastructure providers where customers use custom domains (e.g., GoDaddy, OVH)

### 4c. Elasticsearch API Field Selection

The tool extracts data via OCV's Elasticsearch API, which returns ~200+ fields per feedback item. Many of these fields are PII or PII-adjacent. The tool **only extracts the following fields:**

### 4d. ODS Ticket Extraction PII Scrubbing

The ODS extraction script (`scripts/ods_api_extract.py`) retrieves support ticket data from the ODS REST API. Unlike OCV, the ODS API provides **no server-side PII redaction** — ProblemStatement fields contain raw customer-entered text including email addresses, phone numbers, and sign-off names.

**Scrubbing implementation:** The script uses a shared PII module (`scripts/lib/pii_scrub.py`) that applies the same pattern families as the OCV JavaScript scrubber, plus additional patterns for multilingual coverage:

| # | Pattern | Matches | Replaced With |
|---|---------|---------|---------------|
| 1 | `\[PII:\s*Email\]` | OCV redaction tags (if present in cross-referenced text) | `[REDACTED_EMAIL]` |
| 2 | Email regex | Email addresses (`user@example.com`) | `[REDACTED_EMAIL]` |
| 3 | Phone regex (US/intl) | Phone numbers (`(555) 123-4567`, `+44 20 7946 0958`) | `[REDACTED_PHONE]` |
| 4 | Phone regex (EU slash format) | European formats (`086/3801088`) | `[REDACTED_PHONE]` |
| 5 | Sign-off name patterns | Names after salutations in 10 languages (e.g., `Regards, John Smith`, `Mvh Pia Granlund`, `Cordialement, Marie Dupont`) | `[REDACTED_NAME]` |

**ODS-specific considerations:**

- ODS ProblemStatement is entirely user-authored free text (no structured redaction from the platform).
- ODS tickets are multilingual — the sign-off patterns cover English, Swedish, Dutch, French, German, Portuguese, and Spanish greetings.
- The scrubbing runs in-memory during extraction; raw PII is never written to disk.
- A PII summary (redaction counts by type) is printed to stdout at the end of each extraction run.

**Limitations (same as OCV, plus):**

- Name detection is limited to sign-off patterns. Names mentioned mid-sentence ("my name is John Smith") are not caught.
- Physical addresses, account IDs, and social security numbers are not detected.
- Non-Latin scripts (Arabic, CJK, Cyrillic) have limited phone/email pattern coverage.

| Field | Contains PII? | Measure |
|-------|--------------|---------|
| `OriginalTextPiiRedacted` | PII-bearing (verbatim text, server-side redacted) | Additional client-side PII scrubbing applied |
| `FeedbackType` | No | Extracted as-is |
| `CreatedDate` | No | Extracted as-is |
| `CustomTags` | No (aggregated labels) | Extracted as-is |
| `Classifications` | No (aggregated ML labels) | `Text Sentiment`, `Text Intent` extracted as single values; `Copilot Sentiment Themes`, `Copilot Canonical Intents`, `ACRUE` extracted as pipe-delimited part values. All are ML-generated labels, not user content. |
| `AppData` | Contains source type (safe); may contain identifiers | `CmmId` (scenario identifier) and `SourceType` extracted; user/device identifiers ignored |
| `SourceContext` | No (scenario routing label) | Extracted as-is (e.g., "CopilotChatFeedback") |
| `AppEntryPoint` / `EntryPoint` | No (UI entry point label) | Extracted as-is (e.g., "Unknown", "SidePane") |
| `SubFeatureName` | No (sub-scenario label) | Extracted as-is |

The following PII-containing fields are **available in the API response but explicitly NOT written to disk:**

| Field | Why excluded | In-memory use? |
|-------|-------------|----------------|
| `Email` | Explicit PII — full email address | **Yes**: read in-memory to extract the domain portion for ISP whitelist lookup (see Section 4b). Only the provider name (e.g., "Comcast") or `[CUSTOM_DOMAIN]` is written to CSV. The raw email address is never written to disk. |
| `UserId`, `SqmUserId`, `Cid` | Account identifiers tied to individuals | No |
| `DeviceId`, `ClientId`, `SqmMachineId` | Device identifiers when user-linked | No |
| `TenantId` | Identifies organization; may narrow population | No |
| `OriginalText` | Raw verbatim text before PII redaction | No |
| `IP-related fields` | Explicitly PII per Microsoft policy | No |

This field selection follows the **principle of minimum necessary data**: we extract only what is needed for feedback analysis (comment text, date, type, tags) and nothing that could identify the user or their organization.

---

## 5. External Dependencies

| Dependency | Purpose | Network Activity |
|------------|---------|-----------------|
| Node.js | Runtime for the OCV extraction script | None (runs locally) |
| Playwright (npm package) | Browser automation library | None at runtime. Downloaded once during `npm install` from npmjs.com. |
| Microsoft Edge | Browser used to render OCV | Connects to ocv.microsoft.com (internal) |
| Python 3 | Runtime for the ODS extraction script | None (runs locally) |
| azure-identity (pip package) | Azure AD authentication for ODS API | Connects to login.microsoftonline.com (Microsoft) |

**Note:** Playwright does not download a separate browser. The script is configured to use the Edge binary already installed on the machine (`channel: 'msedge'`).

---

## 6. Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| PII in CSV despite scrubbing | Low | Three layers: (1) OCV server-side redaction, (2) client-side regex scrubbing for emails/phones, (3) ISP whitelist for domain extraction. Validated at 9K+ items: caught 5 emails and 33 phones that OCV missed. |
| Provider identification leaking PII | Very Low | ISP whitelist approach: only ~55 known public ISPs are extractable. All other domains redacted as `[CUSTOM_DOMAIN]`. Whitelist excludes small orgs, healthcare, religious, personal domains. |
| Excessive data extraction from API | None | Explicit field allowlist: only 6 fields extracted from ~200+ available. All user/device/org identifiers excluded by design. |
| Unauthorized data access | Very Low | Tool uses the user's own SSO credentials and OCV permissions. No privilege escalation. |
| Data exfiltration | None | No outbound network calls. All data stays on local machine / OneDrive for Business. No data sent to AI services. |
| Session token theft | Very Low | `.browser-profile/` has same security posture as Edge's own profile directory. Protected by Windows user account permissions and BitLocker. |
| Accidental sharing of CSV | Low | CSVs are PII-scrubbed. OneDrive sharing requires explicit action. |

---

## 7. Recommendations

1. **Verify data classification.** Confirm that the extracted OCV feedback (even after PII scrubbing) is classified appropriately for storage on OneDrive for Business.

2. **Review PII patterns.** If additional PII types are found in feedback (names, account IDs, addresses), add corresponding patterns to the shared PII module (`scripts/lib/pii_scrub.py` for Python scripts) and to `scrubPII()` in `extract_standalone.js` (for OCV extraction).

3. **Set a retention policy.** CSVs with raw customer content are temporary artifacts. After analysis, the `ocv-analyze` skill produces a manifest JSON (`data/manifests/`) that captures all analytical value (themes, counts, OcvId pointers, AI paraphrases) without retaining verbatim text. Delete CSVs after the manifest is generated. Run `node scripts/cleanup_csvs.js` to scan for old CSVs, or `--all-manifests` to clean up all analyzed CSVs. Manifests themselves contain no customer content and can be retained indefinitely.

4. **Restrict folder access.** Ensure the OneDrive folder containing CSVs is not shared beyond authorized team members.

5. **Add `.browser-profile/` to `.gitignore`.** If the project is ever version-controlled, the browser profile directory (containing session cookies) must not be committed.

6. **Audit log.** Each extraction run prints a summary to the terminal (item count, date range, PII redaction count). Consider redirecting this to a log file for audit trail purposes.

---

## 8. AI Developer Tool Boundaries (E+D Directive, March 2026)

The E+D Data Use Guidance (effective March 3, 2026) states: "Claude Code cannot be used with ANY customer data." OCV verbatim feedback is Customer Content. This section documents the guardrails in place.

### What Claude Code can do

| Action | Allowed? | Reason |
|--------|----------|--------|
| Edit source code (`scripts/extract_standalone.js`) | Yes | Source code is not customer data |
| Edit config files (`configs/*.json`) | Yes | Configuration is not customer data |
| Edit documentation (`README.md`, `docs/`) | Yes | Documentation is not customer data |
| Create new config files via `/setup-ocv` | Yes | Writes config JSON, no customer data |
| Generate extraction commands via `/extract-ocv` | Yes | Prints command text, does not execute |

### What Claude Code cannot do

| Action | Blocked by | Reason |
|--------|-----------|--------|
| Run the extraction script | Skill rewrite (no Bash tool) | Orchestrates Customer Content extraction |
| Read CSV files in `data/` | `.claudeignore` + skill rewrite | CSV contains Customer Content (verbatim feedback) |
| Read extraction stdout containing customer text | Skill rewrite (no Bash tool) | Stdout may contain customer text in non-summary mode |
| Analyze or summarize feedback content | `.claudeignore` + AGENTS.md instructions | Customer Content |

### Guardrails

1. **`.claudeignore`** blocks `data/`, `*.csv`, and `.browser-profile/` from Claude Code file access.
2. **Skill definitions** (`extract-ocv`, `setup-ocv`) have `Bash` removed from `allowed-tools`.
3. **`AGENTS.md`** contains explicit instructions that only GitHub Copilot CLI (backed by AOAI/Anthropic) is approved for customer data analysis. Claude Code is not approved.
4. **`--summary` flag** on the extraction script prints only aggregate statistics (counts, distributions) to stdout, never customer verbatim text. This lets users get feedback without involving AI tools.

---

## 9. Summary

| Question | Answer |
|----------|--------|
| Does data leave Microsoft's network? | No |
| Does data go to Claude/Anthropic/any AI service? | No |
| Does data go to any external API? | No |
| Is raw PII written to disk? | No — scrubbed in-memory first (both OCV and ODS scripts) |
| Is the data access authorized? | Yes — uses the user's own OCV permissions via SSO |
| Where is the output stored? | OneDrive for Business (Microsoft corporate tenant) |
| What PII protections are in place? | OCV: Three layers (server-side redaction, client-side regex, ISP whitelist). ODS: Client-side regex scrubbing with 5 pattern families covering multilingual sign-offs. |
| Are email addresses or domains extracted? | No. Only ISP provider names (from a whitelist of ~55 public ISPs). Non-ISP domains are redacted. |
| Are user/device/org identifiers extracted? | No. Explicit field allowlist — only comment, date, type, tags, and source type are extracted. |
| Is the approach aligned with Microsoft PII policy? | Yes. Follows "minimum necessary data" principle. Email domains treated as PII unless on the public ISP whitelist. |

---

*This document was prepared for privacy review of the ocv-extraction tool. For questions, contact Lucas Reis (lreisdesouza@microsoft.com).*

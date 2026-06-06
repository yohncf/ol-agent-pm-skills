# Eval Doctrine

**Doctrine-Version: 2026-06-04**

Single source of truth for how evaluation queries and assertions are written
in this org — applies to **HeroEval** YAMLs (`HeroV2.5+`) and to anything the
`seval-synthesize-queries-from-ocv` skill emits. Any tool that produces or consumes
eval queries should reference this version and follow the rules below.

When the doctrine changes, bump `Doctrine-Version` and update every consumer
that pins a version (the synth helper's `DOCTRINE_VERSION` constant, the
HeroEval YAML header block, etc.).

---

## Why this exists

Two surfaces today produce eval queries — the HeroEval YAML rewrite and the
`seval-synthesize-queries-from-ocv` skill that turns real OCV/Dash user utterances into
generic eval queries. Eventually the synth output will merge into HeroEval.
That only works if **both surfaces follow the same assertion contract** so
the merged corpus doesn't have two incompatible quality bars.

Concrete failure this prevents: in run `538053`, 5 of 12 audited queries
were false-positive critical passes because the assertions had escape
hatches like "OR demonstrates clear intent" or were format-only at
critical level. Aligned doctrine prevents that class of false positive
from re-entering through synthesized queries.

---

## Level taxonomy

Every assertion has a `level`:

| Level | When the response gets it | If it fails |
|---|---|---|
| `critical` | The core deliverable the user asked for — named items, retrieved data, completed actions. | The response failed the user's intent. |
| `expected` | Quality/correctness a competent answer should hit — scope, completeness, source attribution, exclusions, accuracy. | The answer is degraded but not broken. |
| `aspirational` | Presentation/style — organization, brevity, ordering, scannability. | Pure polish miss; doesn't affect substantive correctness. |

**The level taxonomy is the load-bearing rule.** Most assertion bugs stem
from labeling a polish criterion as `critical`, so the response passes
critical just by being well-formatted even when the substantive task failed.

---

## Assertion principles

1. **Formula** — Every assertion starts with `The response …` or
   `The <tool> call …`. No exceptions.
2. **Atomic** — One claim per assertion. Split anything joined with `and`
   or `or` between independent claims.
3. **Outcome-focused at critical/expected.** Style → aspirational.
4. **Critical must be substantive.** Reference identifiers
   (subjects, senders, counts, names, IDs). Not "confirms X happened".
5. **No escape hatches at critical.** Drop "or asks for clarification",
   "or requests more information", "or explains why it cannot" — these
   let apologetic non-completions pass.
6. **Format assertions are conditional + binary**, always aspirational:
   "When the response includes 3 or more items, they are organized as
   bullets, a numbered list, or grouped headings." Passes vacuously when
   no items appear (acceptable for aspirational — won't pollute the
   pass-rate metric).
7. **No hallucinated specifics.** Only reference constraints from the
   query itself. Don't invent people, projects, dates, or content.
8. **Preserve `id` and `query` verbatim** when revising existing queries
   so the eval harness can diff old vs new runs.

---

## Assertion anti-patterns (hard-fail in tooling)

These patterns are deterministic violations and **must be rejected** by
synth/validation tooling at `critical` level:

| Pattern | Why it fails |
|---|---|
| `or asks for clarification` | Lets apologetic non-completion pass. |
| `or requests more information` | Same — escape hatch. |
| `or asks the user to provide` | Same — escape hatch. |
| `or states more details are needed` | Same — escape hatch. |
| `OR demonstrates clear intent` | Fired on 3 of 4 auto-reply queries in 538053. |
| `OR clearly describes the plan` | Format-of-intent passes substantive failure. |
| `OR clearly describing` | Variant of the above. |

## Assertion anti-patterns (warning — review case-by-case)

These patterns are usually wrong but occasionally justified. Tooling
**warns** at `critical` level:

| Pattern | Why it's risky |
|---|---|
| Format as primary verb (`organizes`, `formats`, `presents as a list`, `groups`, `separates`, `orders`, `is structured`, `is scannable`, `uses bullets`) without a substance verb (`identifies`, `lists`, `names`, `returns`, `states the count`, `confirms <action> by <id>`) | Pure format check that passes on apologies. |
| Vacuous confirm (`confirms the action`, `confirms the request`) without named identifiers | Passes when the response says "Sure I'll do that" with no proof. |
| Subjective quality words (`helpful`, `clear`, `appropriate`, `reasonable`, `comprehensive`, `actionable`) as the primary claim | Not binary; judge-prompted LLM defaults to "yes". |
| Plan-only language (`provides next steps`, `plans to`, `will [do X]`) as primary | Promise of future action passes substantive failure now. |
| Atomicity: substance + format joined with `and` | Split into critical (substance) + aspirational (format). |

`--strict-doctrine` upgrades all warnings to hard fails.

---

## Segment taxonomy

Every query gets a `segment` — a tool-aware label written to the TSV
`Segment 2` column (HeroEval) or the synth sidecar TSV. Used to
group/pivot regression analysis by tool family.

### Naming convention

- **Max 2 tokens** joined by `_` in CapCase: `Pin_Flag`, `Rules_Folders`,
  `Categorize_Move`.
- **Drop `Inbox_` prefix** when the action is intrinsically inbox-bound
  (`Triage`, `Rules`, `Folders`, `Draft`, `Reply`, `Search`).
- **Keep the domain prefix** for non-Inbox surfaces: `Calendar_*`,
  `Meetings_*`, `Tasks_*`, `Contacts_*`.
- **Collapse overlapping concepts** — followup ⊂ draft; reply IS a draft.
  Pick the more action-oriented token.
- `Inbox_noSend` is a special **test marker** for V2.6 send-removal
  validation queries — not a regular segment.

### Shape regex

`^[A-Z][A-Za-z]+(_[A-Za-z]+)?$`

(First token must start with capital letter; optional second token after
single `_`; alpha only.)

### Known segments (35 as of 2026-06-04)

Inbox bare 1-token actions:
`Search`, `Delete`, `Junk`, `Unflag`, `Unread`, `Archive`, `Categorize`,
`Rules`, `Folders`, `Triage`, `Quota`, `Unsubscribe`, `ActionItems`,
`Summarize`, `Draft`, `Reply`

Inbox 2-token combos:
`Pin_Flag`, `Flag_Reminder`, `Categorize_Summarize`, `Categorize_Move`,
`Delete_Rules`, `Rules_Folders`, `Reply_Receipts`, `Forward_Receipts`

Calendar: `Calendar_OOO`, `Calendar_WorkingHours`

Meetings: `Meetings_ActionItems`, `Meetings_Decisions`, `Meetings_Prep`

Contacts: `Contacts_Create`, `Contacts_Update`

Tasks: `Tasks_Create`, `Tasks_Update`, `Tasks_Subtasks`

V2.6 test marker: `Inbox_noSend`

### Proposing new segments

Synth tooling **warns** (does not block) when a segment isn't in the
known list. New segments are fine but they should be proposed for human
validation before landing in HeroEval. They must match the shape regex.

---

## Workflow notes for consumers

- **HeroEval YAML** — write the doctrine header block in the file
  preamble (level taxonomy + reference to this doctrine version).
- **`seval-synthesize-queries-from-ocv`** — the helper script's `DOCTRINE_VERSION`
  constant must match this file's `Doctrine-Version`. CI/skill execution
  should fail loud if they drift.
- **TSV pairing** — when emitting both YAML and TSV from the same source,
  generate them in a single pass so `query_hash` (TSV) === `id` (YAML)
  always.

---

## Changelog

- **2026-06-04** — Initial doctrine. Codifies level taxonomy (critical,
  expected, aspirational), the 8 assertion principles, hard-fail and
  warning anti-pattern lists, and the segment taxonomy (35 segments,
  shape regex, naming convention). Triggered by the V2.5/V2.6 HeroEval
  rewrite and the 538053 regrade audit.

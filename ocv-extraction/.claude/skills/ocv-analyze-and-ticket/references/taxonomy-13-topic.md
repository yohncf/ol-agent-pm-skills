# 13-Topic Taxonomy — Outlook Agent OCV ticketing

> **Status:** Authoritative. This taxonomy is locked for week-over-week comparability.
> **Version tag in manifest:** `13-topic-2026-05-19`
> **Parent skill:** `ocv-analyze-and-ticket/SKILL.md`

Each negative-feedback item must be assigned **exactly one** topic from
this list of 13. Granularity beyond the topic is captured by two
separate fields (`Category` and `SubMode`) — do not multiply topics for
tool/failure-mode variations that those fields already cover.

## Contents

1. Action not executed
2. HitL (Human-in-the-loop) violation
3. Output doesn't match intent
4. Constraints ignored
5. Unnecessary clarifying question
6. Reliability failure (`SubMode`: `blank` / `error_string` / `hang`)
7. Inaccurate or fabricated content
8. Wrong context / grounding
9. Tone, language, or format quality
10. File I/O failure (`SubMode`: `input` / `output`)
11. Calendar correctness
12. Capability refusal for in-scope request
13. Intrusive Outlook Agent UI

---

## 1. Action not executed
**Description:** Copilot claimed (or implied) that an action was completed — draft saved, invite sent, rule applied, email archived, meeting accepted, task created, item moved — but the actual state did not change.
**Use when:** the user's complaint is that the system *did not do what it said it did*, or *did not do the action at all*, for any drafting / calendar / mailbox / rule / triage / task / setting action.
**Don't use when:** the user got a generic error string with no claim of success (→ Topic 6) OR the user is complaining about the *content* of an action that did happen (→ Topic 3 or 4).
**Examples:**
* "Copilot says my 5 drafts are in Drafts folder and they're not."
* "Created an inbox rule but failed to apply it to existing inbox."
* "Tried to cancel my 1:1s next week — nothing changed on the calendar."

## 2. HitL (Human-in-the-loop) violation
**Description:** Copilot sent an email, archived items, or executed an action without first surfacing the content for the user to review — OR — a draft exists but is not visible inline so the user cannot read/copy/tweak it before approving.
**Use when:** the trust/approval boundary was broken (no preview) or the review surface is missing/incomplete.
**Don't use when:** the user reviewed the content and is unhappy with it (→ Topic 3, 4, 7, or 10).
**Examples:**
* "I asked it to suggest emails to archive, and it archived them without my approval, with no way to undo."
* "I asked for a draft to review and it sent the reply automatically."
* "Approve/Reject card showed but I couldn't see the full reply inline before approving."

## 3. Output doesn't match intent
**Description:** Generated content is unrelated, off-topic, or a completely different answer than what was asked; Copilot misread the user's overall intent.
**Use when:** the top-level intent was wrong.
**Don't use when:** the intent was right but a stated constraint was dropped (→ Topic 4) OR the content is factually wrong but on-topic (→ Topic 7).
**Examples:**
* "Asked to polish a draft about northeastern co-op; got a totally different email."
* "Asked for product updates summary; got a generic article."

## 4. Constraints ignored
**Description:** Intent was understood, but user-provided constraints were dropped — length, recipients, tone, "draft only," "don't add steps," language, format/layout, recurrence, scope, "don't translate."
**Use when:** intent right + at least one explicit user instruction violated.
**Don't use when:** no explicit constraint was given and output is just hard to read (→ Topic 9).
**Examples:**
* "Asked for one-sentence summaries; got verbose generic output."
* "Said schedule a *weekly* sync; got a one-time event."
* "Said 'do not include checkboxes'; checkboxes were included."

## 5. Unnecessary clarifying question
**Description:** Copilot asks who/what/when even though the prompt was already actionable; interrupts a workflow that should have proceeded.
**Use when:** the prompt was unambiguously actionable but Copilot stalled with a clarification question.
**Don't use when:** the clarification was legitimately needed (e.g., the attachment really didn't come through → Topic 8).
**Example:**
* "Said 'reply yes to Sarah's email about the data sharing agreement'; Copilot replied 'Which Sarah do you mean?' when there's only one Sarah in the thread."

## 6. Reliability failure
**Description:** Copilot failed to return a useful response at the system level — empty output, canned error string, or stuck/never returning.
**Required `SubMode` field:**
* `blank` — empty response with no error string, widget didn't render.
* `error_string` — canned `I'm sorry, I wasn't able to process your request` or `Something went wrong`.
* `hang` — stuck loading, spinning, never returns, user abandoned the turn.
**Use when:** the request was not action-bearing (search, summary, generic question) AND the system simply failed.
**Don't use when:** the request was an action (drafting / calendar / mailbox) — those route to Topic 1 even if an error string was returned.
**Examples:**
* `error_string`: "Asked for an inbox summary; got 'I'm sorry, I wasn't able to process your request.'"
* `blank`: "Asked a question; widget went empty."
* `hang`: "Spinner for 10 minutes; never returned an answer."

## 7. Inaccurate or fabricated content
**Description:** Facts are wrong (dates, names, numbers, counts, days of the week) or invented details that aren't in the source emails/context.
**Use when:** content is on-topic but factually incorrect or hallucinated.
**Don't use when:** the content is from the wrong source/email entirely (→ Topic 8).
**Examples:**
* "Hallucinated email read/unread counts."
* "Said May 26 is a Monday — it's a Tuesday."
* "Reminder date is past the deadline cited in the email."

## 8. Wrong context / grounding
**Description:** Input-side grounding errors. Copilot replied to the wrong email/thread, pulled unrelated emails or files as context, scanned the full inbox instead of the explicitly selected item, edited the wrong paragraph, or *lost context the user established in earlier conversation turns*.
**Use when:** the source material Copilot consulted was wrong.
**Don't use when:** the source was right but the *answer* contains made-up facts (→ Topic 7).
**Examples:**
* "Reply was sent for a different email than the one I selected."
* "Edited the wrong paragraph."
* "Did not keep email context from my prior 3 turns."

## 9. Tone, language, or format quality
**Description:** Output is in the wrong language, sounds robotic / abrupt / overly formal, doesn't match the user's voice, is missing greeting/sign-off — OR — is unreadable (wall of text, encoding/gibberish, raw metadata exposed) without an explicit user format request.
**Use when:** style/voice/language complaint, or unreadable output where no format was specified.
**Don't use when:** the user explicitly requested a format that was violated (→ Topic 4).
**Examples:**
* "Asked in Spanish; answered in English."
* "Response sounds like a robot wrote it."
* "Dumped raw search metadata with no formatting."

## 10. File I/O failure
**Description:** File handling failed on either the input or output side.
**Required `SubMode` field:**
* `input` — attachment / file ingestion fails (PDF, spreadsheet, attached email, image, screenshot not read or processed).
* `output` — user wanted a downloadable file (Word / Excel / PPT / CSV / ICS / VCF); Copilot didn't produce one.
**Use when:** any file-handling problem.
**Examples:**
* `input`: "Asked Copilot to read the attached Word redline; it couldn't process it."
* `output`: "Asked for VCF contact file; got a chat blurb instead."

## 11. Calendar correctness
**Description:** Scheduling correctness issues *before* a calendar write is attempted — suggested times are in the wrong timezone, on the wrong day/weekend, conflict with existing meetings, or ignore working hours.
**Use when:** the suggested time is wrong on its face.
**Don't use when:** the calendar write itself failed (→ Topic 1).
**Example:**
* "Suggested alternative meeting time conflicts with an existing meeting."

## 12. Capability refusal for in-scope request
**Description:** Copilot returns the canned `I'm the Outlook Agent — I can help with email, calendar, contacts, and tasks` capability statement for a request that *should* be in scope (e.g., respond in a specific language, create a team calendar, draft from an attached document).
**Use when:** the refusal is a deliberate scope statement, not a generic error.
**Don't use when:** the response is a generic error string (→ Topic 6, SubMode `error_string`) or the system simply failed silently (→ Topic 6, SubMode `blank`).
**Examples:**
* "Asked for a draft in Spanish; got 'I'm the Outlook Agent — I can help with email, calendar, contacts, and tasks.'"
* "Asked to create a team calendar; got the same capability message."

## 13. Intrusive Outlook Agent UI
**Description:** User's complaint is about the Outlook Agent surface/pop-up itself appearing unwanted, not about content quality or action outcome.
**Use when:** the complaint is about the surface existing/intruding.
**Example:**
* "How do I remove Copilot from Outlook completely?"

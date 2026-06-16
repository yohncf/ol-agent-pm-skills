# OL Agent PM Skills

A collection of agent skills + supporting scripts for two end-to-end PM
pipelines on the Outlook AI Agent:

1. **OCV / Dash pipeline** — extract customer feedback (OCV verbatim,
   Copilot Dash, ODS support tickets), analyze it into themes and
   subtopics, render a self-contained HTML weekly report, and file ADO
   bugs for the high-signal rows.
2. **SEVAL pipeline** — diff two SEVAL HeroEval runs (Mainline vs
   CodeGen, or any control vs experiment), surface real assertion
   regressions, render a published HTML report, and file one ADO bug
   per (failing_side, topic, category) cluster.

The skills are authored to be portable across **Claude Code**,
**Gemini CLI**, and **GitHub Copilot CLI** discovery conventions; the
canonical layout is `.claude/skills/<skill-name>/SKILL.md`.

---

## Repository layout

```
.claude/skills/         # 15 agent skills (the orchestration layer)
configs/                # OCV area configs, ADO owner-routing rules
scripts/                # Python + Node helpers the skills wrap
docs/                   # Long-form pipeline notes, doctrine, privacy
AGENTS.md               # Top-level skills index for AI assistants
.github/copilot-instructions.md   # Copilot CLI-specific skills index
.gemini/GEMINI.md       # Gemini CLI-specific overlay
```

## The 15 skills

### OCV / Dash pipeline (10)

| Skill | Role |
|-------|------|
| `ocv-setup` | First-time PM-guided configuration |
| `ocv-extract-feedback` | Pull OCV verbatim CSV |
| `ocv-extract-dash` | Pull Copilot Dash feedback |
| `ocv-extract-ods` | Pull ODS support ticket details |
| `ocv-analyze` | Two-pass analyze utility (themes, flags, summary) |
| `ocv-analyze-and-ticket` | Main analyze + ticket-prep pass using the locked 13-topic taxonomy |
| `ocv-publish-report` | Render the local self-contained HTML dashboard |
| `ocv-publish-github` | Push the HTML to `gim-home/OCV-Weekly` (GitHub Pages) |
| `ocv-draft-email` | Build a leadership announcement email and save a local Classic Outlook draft |
| `ocv-ticket-sync` | Match-or-create ADO Bugs from the subtopics CSV |
| `ocv-weekly` | End-to-end orchestrator over the pipeline |

### SEVAL pipeline (5)

| Skill | Role |
|-------|------|
| `seval-synthesize-queries-from-ocv` | Convert an OCV manifest into SEVAL eval rows |
| `seval-regression-analyze` | Diff two HeroEval runs; pre-fill `why_failed` per regression |
| `seval-regression-publish` | Publish the HTML regression report to GitHub Pages |
| `seval-regression-ticket-sync` | File one ADO Bug per cluster (always net-new) |
| `seval-regression` | End-to-end orchestrator over the SEVAL pipeline |

## Pipeline diagrams

```
OCV / Dash:
  ocv-setup (once) -> ocv-extract-feedback + ocv-extract-dash
   -> ocv-analyze-and-ticket
   -> ocv-publish-report -> [optional ocv-ticket-sync] -> [optional ocv-publish-github]

SEVAL:
  [optional seval-synthesize-queries-from-ocv]
   -> seval-regression-analyze
   -> seval-regression-publish
   -> [optional seval-regression-ticket-sync]
```

## Authoring conventions

All `SKILL.md` files follow a unified rubric synthesized from:
- Anthropic's official Agent Skills documentation
- The community `skills-best-practices` rubric
- Gemini CLI's skill authoring docs

Key rules:
- Body uses Markdown headings (the canonical format across Claude + Gemini)
- Body stays under **500 lines**; bulk reference content is offloaded to `references/<topic>.md`
- YAML `description` is verb-led, includes positive triggers AND a
  `Do NOT use for X — use sister-skill` negative trigger
- All cross-skill references use the relative single-segment form `<skill-name>/SKILL.md`
- Owner names and repo paths are derived dynamically (git config /
  `Path(__file__)`); no hardcoded usernames in scripts

## Privacy

OCV verbatim feedback and Dash transcripts are **Customer Content** per
the E+D Data Use Guidance. The pipeline deliberately:
- Excludes `data/`, `data/manifests/*`, and all browser profiles
  from git via `.gitignore`
- Writes manifests that contain **only** OcvIds + AI paraphrases — no
  raw `Comment`, `PromptInEnglish`, or `ResponseInEnglish`
- Prompts the PM to delete raw CSVs after analysis completes

See `docs/PRIVACY_REVIEW.md` for the full data-handling spec.

## License

Internal Microsoft tooling. Not for external distribution.
# Medici — AI Assistant Context

## Architecture

Medici is the DebtStack chat assistant. Key facts:

- **Model:** Gemini 2.5 Pro (not Claude — Claude Code helps develop Medici, but the runtime agent is Gemini)
- **Tools:** 9 DebtStack API endpoints: `search_companies`, `search_bonds`, `resolve_bond`, `get_guarantors`, `get_corporate_structure`, `search_pricing`, `search_documents`, `get_changes`, `search_covenants`
- **Read-only:** The agent queries the database but never writes to it. The extraction pipeline is the operator's tool, not the agent's.
- **Frontend:** Next.js chat UI at `debtstack-website/app/dashboard/chat/`
- **Backend:** FastAPI on Railway, Neon PostgreSQL, Upstash Redis

## Related Codebases

| Location | What's There |
|----------|-------------|
| `debtstack-website/lib/chat/` | Tool definitions, system prompt, chat executor |
| `debtstack-website/app/dashboard/chat/` | Chat UI components |
| `credible/app/api/primitives.py` | FastAPI endpoints the tools call |

## Knowledge Base Rules

When adding knowledge files to `knowledge/`:

- **Markdown only** — all files must be `.md`
- **One topic per file** — keep files focused and self-contained
- **Title and summary first** — every file starts with `# Title` followed by a 1-2 sentence summary
- **Right subfolder:**
  - `frameworks/` — Credit analysis workflows and methodologies
  - `industry/` — Industry-specific analysis guides (banks, REITs, energy, etc.)
  - `case-studies/` — Example analyses demonstrating good output
  - `glossary/` — Definitions, taxonomies, naming conventions
  - `api-patterns/` — Which tools to use for which questions, multi-step workflows

## Learnings Pipeline

- **Never write directly to `learnings/approved/`** — always write to `learnings/proposed/`
- The operator reviews proposed learnings and moves approved ones to `approved/`
- Each learning file should describe: the pattern observed, evidence (session examples), and the proposed guidance

## Updating the Roadmap

When updating `ROADMAP.md`:

- Add entries to the **Session Log** table at the bottom with the current date and a concise description
- Update **Status** fields on levels when work progresses (e.g., "Planned" → "In progress" → "Done")
- Keep the existing structure — don't reorganize sections

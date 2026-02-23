# Medici — AI Assistant Context

> **See also:** `debtstack-website/CLAUDE.md` for the frontend chat architecture, and `credible/CLAUDE.md` for the backend API and database schema.

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
| `debtstack-website/lib/chat/` | Tool definitions, system prompt, knowledge retrieval, chat executor |
| `debtstack-website/lib/chat/knowledge.ts` | RAG retrieval: embeds query → pgvector search → returns chunks |
| `debtstack-website/app/api/chat/route.ts` | Chat SSE route — injects knowledge context into system prompt |
| `debtstack-website/app/dashboard/chat/` | Chat UI components |
| `credible/app/api/primitives.py` | FastAPI endpoints the tools call |
| `credible/medici/scripts/ingest_knowledge.py` | Ingestion: markdown → chunks → embeddings → Neon pgvector |
| `credible/alembic/versions/027_add_knowledge_chunks.py` | Migration: pgvector extension + `knowledge_chunks` table |

## RAG Pipeline (Knowledge Base → Gemini)

Knowledge files in `knowledge/` are embedded into Neon pgvector and auto-injected into Gemini's system prompt at runtime. No new tools for the agent — knowledge is always available.

```
Knowledge files (git)
    ↓
Ingestion script (one-time, re-run on changes)
    ↓ embed with Gemini gemini-embedding-001 (768 dims)
knowledge_chunks table (Neon + pgvector)
    ↓
User sends message → /api/chat route
    ↓ embed user query with Gemini
    ↓ vector similarity search → top 3 chunks
    ↓ prepend to system prompt as "## Credit Analysis Frameworks"
    ↓
Gemini gets: system prompt + relevant frameworks + user message
```

**Ingestion:** `python credible/medici/scripts/ingest_knowledge.py`
- Parses markdown by `## ` headings, prepends file title/summary to each chunk
- Embeds with `gemini-embedding-001` (output_dimensionality=768 for HNSW compatibility)
- Upserts to `knowledge_chunks` table (idempotent — deletes old chunks per file, inserts new)
- Supports `--dry-run` and `--file <relative-path>`

**Retrieval** (`debtstack-website/lib/chat/knowledge.ts`):
- Embeds user query → cosine similarity search → top 3 chunks above 0.3 threshold
- ~150ms latency (embedding + vector search), best-effort (errors don't block chat)
- ~1,500 tokens added to system prompt per turn

**Re-ingestion:** Run the ingestion script whenever knowledge files change. The script deletes old chunks for modified files and inserts new ones.

**Current state:** 48 chunks across 7 files (~15K tokens total, ~$0.00015 embedding cost).

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

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

**Current state:** 66 chunks across 13 files (~24K tokens total, ~$0.00024 embedding cost). "Medici Tools" sections are filtered out during ingestion to prevent API reference boilerplate from polluting vector search results.

## Knowledge File Inventory

All files live in `knowledge/`. To add, edit, or remove: modify the markdown, then run `python credible/medici/scripts/ingest_knowledge.py` to re-embed.

### Frameworks (Moyer — "Corporate Financial Distress" textbook)

| File | Chunks | What It Teaches Medici |
|------|--------|----------------------|
| `frameworks/credit-metrics.md` | 10 | Leverage, coverage, maturity profile, sector context, trajectory |
| `frameworks/capital-structure-analysis.md` | 6 | Priority of claims, secured vs unsecured, holdco vs opco, guarantor analysis |
| `frameworks/covenant-analysis.md` | 9 | Maintenance vs incurrence, headroom, baskets, cov-lite, step-downs |
| `frameworks/recovery-analysis.md` | 7 | Waterfall, fulcrum security, par vs market value, bond price signals |
| `frameworks/structural-subordination.md` | 7 | Holdco risk, guarantees, unrestricted subs, VIEs, severity assessment |
| `frameworks/distress-indicators.md` | 6 | Market pricing signals, fundamental deterioration, covenant warnings |
| `api-patterns/credit-analysis-workflow.md` | 3 | Seven-step multi-tool workflow, variations, presentation guidelines |

### Frameworks (Whitman — "Distress Investing" textbook)

| File | Chunks | What It Teaches Medici |
|------|--------|----------------------|
| `frameworks/causes-of-distress.md` | 3 | Four triggers (capital access, operating, GAAP, contingent liabilities) |
| `frameworks/distress-valuation.md` | 5 | Three valuation modes (going-concern, resource conversion, liquidation) |
| `frameworks/distress-investing-risks.md` | 5 | Priority alteration, valuation disputes, process risks, Five Basic Truths |

### Case Studies

| File | Chunks | What It Teaches Medici |
|------|--------|----------------------|
| `case-studies/toys-r-us-lbo-failure.md` | 6 | LBO leverage fragility, maturity walls, structural vs cyclical decline, liquidation |
| `case-studies/caesars-entertainment-restructuring.md` | 7 | Asset stripping, fraudulent conveyance, OpCo/PropCo/REIT split, blocking positions |
| `case-studies/jcrew-nine-west-covenant-exploitation.md` | 4 | Trap door provisions, unrestricted subs, guarantee quality, professional cost drag |

### How to Manage Knowledge Files

**Add a file:** Create `.md` in the right subfolder → run ingestion → commit both the file and the updated chunk count here.

**Edit a file:** Modify the markdown → run ingestion (auto-deletes old chunks for that file, inserts new ones).

**Remove a file:** Delete the `.md` file from disk. **Important:** the ingestion script only deletes chunks for files it finds on disk, so orphaned chunks for deleted files will remain in the database. Clean them up manually:
```sql
-- Find orphaned chunks (files no longer on disk)
SELECT DISTINCT source_file FROM knowledge_chunks ORDER BY source_file;
-- Delete orphans
DELETE FROM knowledge_chunks WHERE source_file = 'path/to/deleted-file.md';
```

**Verify:** After ingestion, check the output for chunk counts per file. Update this inventory if counts change significantly.

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

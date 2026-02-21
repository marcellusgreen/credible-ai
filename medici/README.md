# Medici — DebtStack Chat Assistant

Medici is the AI chat assistant for [DebtStack](https://debtstack.com), a credit data platform. It uses **Gemini 2.5 Pro** with **9 API tools** to answer credit analysis questions against the DebtStack database. The agent is read-only — it queries data but never writes to the database.

This folder organizes Medici's roadmap, knowledge base, and learnings pipeline.

## Folder Structure

```
credible/medici/
├── README.md           ← You are here
├── CLAUDE.md           ← AI assistant context for Claude Code
├── ROADMAP.md          ← 5-level agentic architecture plan
│
├── knowledge/          ← Operator-curated domain knowledge (exogenous)
│   ├── frameworks/     ← Credit analysis workflows
│   ├── industry/       ← Industry-specific guides
│   ├── case-studies/   ← Example analyses
│   ├── glossary/       ← Terms, taxonomies, conventions
│   └── api-patterns/   ← Tool selection, common workflows
│
└── learnings/          ← System-discovered patterns (endogenous)
    ├── proposed/       ← Agent-proposed, awaiting operator review
    └── approved/       ← Operator-reviewed, active in knowledge base
```

## Knowledge Base

The `knowledge/` folder holds operator-curated domain expertise that gets ingested into the Medici knowledge base (PgVector). To add knowledge:

1. Write a markdown file covering one topic
2. Include a clear title (`# Title`) and a brief summary at the top
3. Drop it in the appropriate subfolder:
   - `frameworks/` — Credit analysis workflows (e.g., "How to analyze a leveraged buyout")
   - `industry/` — Industry-specific guides (e.g., "Bank credit analysis uses PPNR not EBITDA")
   - `case-studies/` — Example analyses showing good output format
   - `glossary/` — Terms, taxonomies, conventions (e.g., covenant types, seniority hierarchy)
   - `api-patterns/` — Tool selection guides, common multi-tool workflows
4. Commit to git — the ingestion script reads from these folders

## Learnings Pipeline

Medici can propose learnings from patterns it discovers across sessions. These go through a human-in-the-loop review:

1. **System proposes:** Agent writes a learning to `learnings/proposed/`
2. **Operator reviews:** Human reads, edits if needed, decides whether to keep
3. **Operator approves:** Move the file from `proposed/` to `approved/`
4. **Ingestion:** Approved learnings get embedded and upserted into the same PgVector knowledge base as curated knowledge

Never write directly to `approved/` — everything goes through `proposed/` first.

## Related Code

- **Chat tools & system prompt:** `debtstack-website/lib/chat/`
- **Chat UI:** `debtstack-website/app/dashboard/chat/`
- **API endpoints (tools call these):** `credible/app/api/primitives.py`
- **Roadmap:** [ROADMAP.md](./ROADMAP.md)

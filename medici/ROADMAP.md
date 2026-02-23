# Medici Agent — Agentic Architecture Roadmap

Based on the "5 Levels of Agentic Software" framework, adapted for the DebtStack chat assistant (Medici).

## Current State

**Model:** Gemini 2.5 Pro
**Tools:** 9 DebtStack API endpoints + `research_company` (live SEC, temporary)
**Frontend:** Full-page chat at `/dashboard/chat` — SSE streaming, starter prompts, watchlists, chat history (localStorage)
**Backend:** FastAPI on Railway, Neon PostgreSQL, Upstash Redis

---

## Level 1: Agent with Tools (CURRENT)

**Status: Done**

The agent has two paths to answer questions:

- **Fast path (API tools):** 9 pre-computed DebtStack endpoints — `search_companies`, `search_bonds`, `resolve_bond`, `get_guarantors`, `get_corporate_structure`, `search_pricing`, `search_documents`, `get_changes`, `search_covenants`. Instant, cheap, structured data from the database.
- **Deep path (live SEC research):** `research_company` — fetches SEC filings on the fly for non-covered companies. Returns analysis but does NOT write to the database. This is a temporary stopgap until database coverage expands. Once all target companies are covered, this tool goes away.

### Design Decision: Read-Only Agent

The extraction pipeline (extraction services, utilities, scripts) stays with the human operator. The agent never writes to the database. Reasons:

- Extraction has too many edge cases (Neon connection drops, Gemini rate limits, scale detection, dedup logic)
- One bad extraction could corrupt data that took hours to build
- The pipeline requires human inspection before committing results
- Cost control — extraction calls cost $0.01-0.15 each in Gemini calls

The extraction services and utilities are the *operator's* tools, not the agent's. The database is the knowledge. The extraction pipeline is how the operator builds it offline.

### What's Working

- Tool-use loop (up to 5 rounds per turn)
- Starter prompt library (14 prompts, 4 categories)
- Chat history with search (localStorage)
- Ticker watchlists (localStorage)
- Suggested follow-ups parsed from Gemini response
- Cost tracking per session ($0.05-$0.15 per tool call)

### What's Missing

- No server-side session persistence — refresh the page and history is gone (unless in localStorage)
- No domain knowledge beyond what's in the system prompt
- No memory across sessions — every conversation starts from zero
- No user preference learning

---

## Level 2: Agent with Storage and Knowledge (NEXT)

**Status: Planned — ready to implement**

### 2A. Session Storage (Implementation Plan)

Persist chat sessions to Neon PostgreSQL so history survives browser clears and syncs across devices.

**Architecture:**
- **Frontend-owned**: New `chat_sessions` table queried directly from Next.js API routes via `lib/db.ts` pg Pool (same pattern as `app/api/user/route.ts`)
- **Keyed on Clerk `userId`**: No FK to backend `users` table (which has no `clerk_id` column)
- **Dual-write**: localStorage (instant, synchronous) + server (async, debounced ~1s) — localStorage acts as cache and fallback
- **Graceful degradation**: If DB is unavailable, everything works exactly as before via localStorage

**Database Schema — `chat_sessions` table:**

```sql
CREATE TABLE chat_sessions (
    id          UUID PRIMARY KEY,
    clerk_id    VARCHAR(255) NOT NULL,
    title       VARCHAR(255) NOT NULL,
    messages    JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_cost  NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_sessions_clerk_id ON chat_sessions (clerk_id);
CREATE INDEX idx_chat_sessions_clerk_created ON chat_sessions (clerk_id, created_at DESC);
```

- `messages` stores full `Message[]` as JSONB (~5-15KB per session, max 50 sessions per user)
- No separate `chat_messages` table needed — JSONB is simpler and sufficient at this scale
- No FK to `users` table — chat is frontend-owned, identified by Clerk userId

**New Files:**

| File | Purpose |
|------|---------|
| `credible/alembic/versions/027_add_chat_sessions.py` | Alembic migration (follows pattern from `026_add_benchmark_total_debt.py`) |
| `debtstack-website/app/api/chat-sessions/route.ts` | GET: list sessions (metadata only), POST: upsert session |
| `debtstack-website/app/api/chat-sessions/[id]/route.ts` | GET: load full session, DELETE: delete session |
| `debtstack-website/lib/chat/session-storage.ts` | Client module: `fetchSessionList()`, `fetchSession()`, `saveSession()`, `deleteSessionRemote()`, `migrateFromLocalStorage()` |

**Modified Files:**

| File | Changes |
|------|---------|
| `debtstack-website/app/dashboard/chat/components/ChatLayout.tsx` | Replace localStorage-only with dual-write. Sidebar uses `SessionSummary[]` (no messages). Clicking session fetches full messages from server. Debounced server saves. Migration logic on mount. `serverAvailable` state for degradation. |

**API Routes (all use Clerk `auth()` + `getPool()` from `lib/db.ts`):**

- `GET /api/chat-sessions` → List sessions (id, title, total_cost, created_at, updated_at). No messages. `ORDER BY updated_at DESC LIMIT 50`.
- `POST /api/chat-sessions` → Upsert session. `INSERT ... ON CONFLICT (id) DO UPDATE` with `WHERE clerk_id = $2` guard.
- `GET /api/chat-sessions/[id]` → Load single session with full messages. `WHERE id = $1 AND clerk_id = $2`.
- `DELETE /api/chat-sessions/[id]` → Delete session. `WHERE id = $1 AND clerk_id = $2`.

**ChatLayout.tsx Changes:**

On mount:
1. Load from localStorage immediately (no flash of empty state)
2. Fetch session list from server in parallel
3. If first load + localStorage has data + server is empty → run migration, set `debtstack_chat_migrated_v1` flag
4. If server fetch fails → set `serverAvailable = false`, stay with localStorage

On message save:
1. Write to localStorage synchronously (existing behavior, unchanged)
2. Debounced server save (~1s) via `saveSession()` — fire-and-forget, errors caught silently

Sidebar:
- Session list is now `SessionSummary[]` (metadata only, no messages)
- Clicking a session calls `fetchSession(id)` to load full messages (brief loading state)

**Migration from localStorage → Server:**
- One-time migration on first load after deploy
- Gated by `debtstack_chat_migrated_v1` flag in localStorage
- Uploads sessions one by one; failures are skipped (sessions stay in localStorage)
- localStorage is NOT cleared — continues as local cache

**Implementation Order:**
1. Create Alembic migration → `alembic upgrade head`
2. Create `lib/chat/session-storage.ts`
3. Create API routes
4. Modify `ChatLayout.tsx` incrementally
5. Test locally → deploy migration → push Next.js changes

**Verification:**
- Fresh user: send message, refresh → session persists from server
- Migration: populate localStorage, reload → sessions appear on server
- Degradation: invalid DATABASE_URL → chat still works from localStorage
- Cross-device: log in elsewhere → same sessions
- Delete: delete session → gone from sidebar and database

### 2B. Knowledge Base

Seed a searchable knowledge base with domain knowledge that doesn't need to live in the system prompt. Two channels feed the knowledge base:

**Exogenous — `knowledge/` folder (operator-curated):**

Operator writes markdown files organized by category in `credible/medici/knowledge/`:

- `frameworks/` — Credit analysis workflows (e.g., "How to analyze a leveraged buyout")
- `industry/` — Industry-specific guides (e.g., "Banks use PPNR not EBITDA, REITs use FFO")
- `case-studies/` — Example analyses showing good output format
- `glossary/` — Terms, taxonomies, conventions (covenant types, seniority hierarchy, collateral types)
- `api-patterns/` — Tool selection guides, common multi-tool workflows

Ingestion pipeline: reads markdown files → chunks → embeds with OpenAI `text-embedding-3-small` → upserts to PgVector on Neon. All source files are version-controlled in git.

**Endogenous — `learnings/` folder (system-discovered):**

Patterns extracted from session data go through a human-in-the-loop pipeline:

1. System writes proposed learning to `learnings/proposed/`
2. Operator reviews — edits, approves, or discards
3. Approved learnings move to `learnings/approved/`
4. Approved learnings get ingested into the same PgVector knowledge base

**Implementation options:**
- PgVector extension on Neon (keeps everything in one database)
- Embed with OpenAI `text-embedding-3-small` (cheap, good quality)
- Hybrid search (semantic + keyword) for best recall

**When to build:** After session storage is solid. When the system prompt gets too long from stuffing domain knowledge into it.

---

## Level 3: Agent with Memory and Learning (FUTURE)

**Status: Not started**

Two-layer learning model, separated by risk and autonomy:

### Per-User Learning (Session-Level) — Autonomous

The agent learns individual user preferences from their sessions. Low risk — scoped to one user, easy to reset.

**What it learns:**
- Which companies/sectors the user tracks regularly
- Preferred output format (tables vs prose, detailed vs summary)
- Common follow-up patterns (user always asks about covenants after checking leverage)
- Analysis style preferences (comparative vs single-company, quantitative vs qualitative)

**How it works:**
- After each session, extract user-specific patterns
- Store per-user in the database (not in the shared knowledge base)
- Load at the start of each new session to personalize responses
- User can reset their profile at any time

**Adaptive behavior:**
- Session 1: User asks "What's RIG's leverage?" → agent returns standard response
- Session 50: Agent knows user always follows up with covenant analysis and pricing, so proactively includes relevant data
- Session 100: Agent knows user's portfolio and flags material changes on login

### Shared Learning (Knowledge-Level) — Human-in-the-Loop

System proposes learnings from patterns observed across many sessions. Higher risk — affects all users, so operator approval is required.

**How it works:**
1. System identifies recurring patterns across sessions (common questions, useful tool combinations, effective analysis approaches)
2. System writes proposed learning to `learnings/proposed/`
3. Operator reviews: edit, approve, or discard
4. Approved learnings move to `learnings/approved/` and get ingested into the PgVector knowledge base

This is safer than autonomous shared learning — the operator stays in the loop for anything that changes the agent's behavior for all users.

### When to Build This

After Level 2 is solid and there are repeat users generating enough sessions to make learning meaningful. Premature optimization if user base is small.

---

## Level 4: Multi-Agent Team (PROBABLY NOT NEEDED)

**Status: Deferred**

A single agent with good tools covers the Medici use case well. Multi-agent adds coordination complexity without clear benefit for credit analysis queries.

**If ever needed, natural decomposition would be:**
- **Researcher:** Calls DebtStack API, gathers raw data
- **Analyst:** Interprets data, runs comparisons, identifies patterns
- **Report Writer:** Formats output, generates summaries, creates charts

**Why it's probably not needed:**
- Gemini handles all three roles adequately in a single agent
- The tool-use loop already supports multi-step research (up to 5 rounds)
- Adding agent coordination introduces unpredictability
- Single agent is easier to debug and monitor

**Revisit if:** Tasks become complex enough that a single agent consistently fails (e.g., "Generate a full credit report with 10 sections" might benefit from decomposition).

---

## Level 5: Production System (PARTIALLY DONE)

**Status: Infrastructure exists, agent layer needs work**

### Already in Place
- FastAPI on Railway (production API)
- Neon PostgreSQL (production database)
- Upstash Redis (caching)
- Clerk authentication (user identity)
- Stripe billing (usage tracking, tier-based access)
- Sentry error tracking
- PostHog analytics (frontend + backend events)
- Slack alerts (error monitoring)

### Needs for Full Level 5
- Server-side session storage (Level 2 prerequisite)
- Tracing/observability for agent tool calls (beyond Sentry errors)
- Rate limiting per user for chat (currently only API rate limits)
- Cost controls (max Gemini spend per session/user)
- Admin dashboard for monitoring agent behavior

---

## Implementation Priority

1. **Level 2: Session Storage** — Move chat from localStorage to Postgres. This is the foundation for everything else.
2. **Level 2: Knowledge Base** — Seed with credit analysis domain knowledge. Reduces system prompt bloat and improves answer quality.
3. **Level 5: Tracing** — Add observability for agent tool calls in production.
4. **Level 3: Memory** — Only after there are repeat users generating enough data.
5. **Level 4: Multi-Agent** — Probably never, unless use case complexity grows significantly.

---

## Session Log

| Date | Update |
|------|--------|
| 2026-02-20 | Initial roadmap created. Current state: Level 1 (agent + API tools). `research_company` is temporary until database coverage expands. Agent is read-only by design — extraction pipeline stays with operator. |
| 2026-02-20 | Level 2A (Session Storage) plan completed. Architecture: frontend-owned `chat_sessions` table in Neon, Clerk userId as key, dual-write (localStorage + server), graceful degradation. 4 new files, 1 modified. Ready to implement. |
| 2026-02-21 | Medici organization created at `credible/medici/`. Knowledge base structure (exogenous `knowledge/` + endogenous `learnings/`) and two-layer learning model (per-user autonomous + shared human-in-the-loop) defined. Level 2B and Level 3 sections updated with concrete architecture. |
| 2026-02-22 | Knowledge base seeded with 7 files from Moyer's *Distressed Debt Analysis*. 6 frameworks: capital structure priority, recovery/fulcrum security, credit metrics, covenant analysis, structural subordination, distress indicators. 1 api-pattern: multi-tool credit analysis workflow (7-step sequence + variations). All files map Moyer's concepts to Medici's 9 API tools with specific field references. |

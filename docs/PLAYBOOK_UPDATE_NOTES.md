# Distribution Playbook Update Notes

**Date**: 2026-01-19
**Purpose**: Align playbook with actual Primitives API implementation

## Summary

The Distribution Playbook (DISTRIBUTION_PLAYBOOK.md) contained code samples targeting a legacy endpoint structure. The actual API uses 8 Primitives optimized for AI agents. This document summarizes what was updated and where.

## New SDK Implementation

Created complete SDK in `credible/sdk/`:

```
sdk/
├── debtstack/
│   ├── __init__.py      # Package exports
│   ├── client.py        # Main SDK (async + sync clients)
│   ├── langchain.py     # LangChain toolkit (7 tools)
│   └── mcp_server.py    # MCP server for Claude Desktop
├── pyproject.toml       # Package configuration
└── README.md            # SDK documentation
```

## API Mapping

| Playbook (Legacy) | Actual Primitives API |
|-------------------|----------------------|
| `GET /companies/{ticker}` | `GET /v1/companies?ticker=X` |
| `GET /companies/{ticker}/structure` | `POST /v1/entities/traverse` + `GET /v1/companies` |
| `GET /companies/{ticker}/debt` | `GET /v1/bonds?ticker=X` |
| `GET /companies` (list) | `GET /v1/companies` |
| *(not in playbook)* | `GET /v1/bonds/resolve` |
| *(not in playbook)* | `GET /v1/pricing` |
| *(not in playbook)* | `GET /v1/documents/search` |
| *(not in playbook)* | `POST /v1/batch` |
| *(not in playbook)* | `GET /v1/companies/{ticker}/changes` |

## SDK Methods (8 Primitives)

| Method | Primitive | Description |
|--------|-----------|-------------|
| `search_companies()` | search.companies | Horizontal company search with filters |
| `search_bonds()` | search.bonds | Bond search with yield/spread filters |
| `resolve_bond()` | resolve.bond | CUSIP/ISIN/fuzzy bond resolution |
| `traverse_entities()` | traverse.entities | Graph traversal for guarantors/structure |
| `search_pricing()` | search.pricing | FINRA TRACE bond pricing |
| `search_documents()` | search.documents | Full-text search across SEC filings |
| `batch()` | batch | Execute multiple primitives in parallel |
| `get_changes()` | changes | Diff/changelog since historical date |

## LangChain Tools (7 Tools)

| Tool Name | Maps To |
|-----------|---------|
| `debtstack_search_companies` | `GET /v1/companies` |
| `debtstack_search_bonds` | `GET /v1/bonds` |
| `debtstack_resolve_bond` | `GET /v1/bonds/resolve` |
| `debtstack_traverse_entities` | `POST /v1/entities/traverse` |
| `debtstack_search_pricing` | `GET /v1/pricing` |
| `debtstack_search_documents` | `GET /v1/documents/search` |
| `debtstack_get_changes` | `GET /v1/companies/{ticker}/changes` |

Note: Batch is available via SDK but not as a separate LangChain tool (agents can call individual tools).

## MCP Server Tools (8 Tools)

| Tool Name | Description |
|-----------|-------------|
| `search_companies` | Company search with leverage/sector filters |
| `search_bonds` | Bond search with yield/seniority filters |
| `resolve_bond` | Bond identifier resolution |
| `get_guarantors` | Get all entities guaranteeing a bond |
| `get_corporate_structure` | Get full entity hierarchy for company |
| `search_pricing` | Bond pricing from FINRA TRACE |
| `search_documents` | Full-text search of SEC filings |
| `get_changes` | Track debt structure changes over time |

## Key Differences from Playbook

### 1. Authentication Header
- Playbook: `X-API-KEY: {key}`
- Actual: `Authorization: Bearer {key}`

### 2. Base URL
- Playbook: `https://api.debtstack.ai/v1`
- Current: `https://credible-ai-production.up.railway.app/v1`
- Action needed: Set up custom domain before launch

### 3. Company Count
- Playbook: "38 companies"
- Actual: 177 companies
- Action needed: Update marketing copy

### 4. Additional Features Not in Playbook
- Field selection: `?fields=ticker,name,net_leverage_ratio`
- Sorting: `?sort=-net_leverage_ratio`
- CSV export: `?format=csv`
- ETag caching: `If-None-Match` header support
- Rate limiting: 100 req/min with `X-RateLimit-*` headers
- Extraction metadata: `?include_metadata=true`

## Playbook Sections to Update

### Section 1.2: Python SDK (PyPI Package)
Replace the code with `sdk/debtstack/client.py` contents.

### Section 3.1: LangChain Toolkit
Replace the code with `sdk/debtstack/langchain.py` contents.

### Section 4.1: MCP Server
Replace the code with `sdk/debtstack/mcp_server.py` contents.

### Section 5.1: LlamaIndex Integration
Add LlamaIndex tools (can be derived from langchain.py pattern).

### Hero Section Stats
Update to:
```
✓ 177 companies  ✓ 3,085 entities  ✓ 1,805 debt instruments  ✓ 5,456 SEC filing sections
```

## Pre-Launch Checklist (from Playbook)

Before executing Phase 1, these items need to be completed:

- [ ] Custom domain `api.debtstack.ai` configured (currently using Railway URL)
- [ ] API key authentication implemented (currently open API)
- [ ] User signup/dashboard system (Stripe integration)
- [ ] Free tier rate limiting by plan
- [ ] Publish SDK to PyPI as `debtstack-ai`
- [ ] Set up GitHub org `debtstack-ai`
- [ ] Create Discord server

## Files Created

| File | Description |
|------|-------------|
| `sdk/debtstack/__init__.py` | Package exports |
| `sdk/debtstack/client.py` | Async + sync SDK clients |
| `sdk/debtstack/langchain.py` | LangChain toolkit |
| `sdk/debtstack/mcp_server.py` | MCP server for Claude |
| `sdk/pyproject.toml` | Package configuration |
| `sdk/README.md` | SDK documentation |
| `docs/PLAYBOOK_UPDATE_NOTES.md` | This file |

## Next Steps

1. **Set up custom domain** - Configure `api.debtstack.ai` to point to Railway deployment
2. **Implement authentication** - Add API key validation and user tiers
3. **Publish SDK** - Push to PyPI as `debtstack-ai`
4. **Create GitHub org** - Set up `debtstack-ai` org with SDK repos
5. **Update playbook** - Replace code sections with new implementations
6. **Submit LangChain PR** - Use `sdk/debtstack/langchain.py` as basis
7. **Deploy MCP server** - Host at `mcp.debtstack.ai`

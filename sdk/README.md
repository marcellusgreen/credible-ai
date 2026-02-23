# DebtStack.ai Python SDK

**Corporate credit data for AI agents.**

[![PyPI](https://img.shields.io/pypi/v/debtstack-ai)](https://pypi.org/project/debtstack-ai/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## Why DebtStack?

Equity data is everywhere. Credit data isn't.

There's no "Yahoo Finance for bonds." Corporate debt structures, guarantor chains, and covenant details are buried in SEC filings—scattered across 10-Ks, 8-Ks, credit agreements, and indentures. An AI agent trying to answer "which telecom companies have leverage above 5x?" would need to read dozens of filings, extract the right numbers, and compute ratios manually.

**DebtStack fixes this.** We extract, normalize, and serve corporate credit data through an API built for AI agents.

### Three Things You Can't Do Elsewhere

**1. Cross-Company Credit Queries**
```python
# Find distressed telecom companies
GET /v1/companies?sector=Telecommunications&min_leverage=5&sort=-net_leverage_ratio
```
Screen ~300 companies by leverage, coverage ratios, or maturity risk in one call. No filing-by-filing analysis.

**2. Pre-Built Entity Relationships**
```python
# Who guarantees this bond?
POST /v1/entities/traverse
{"start": {"type": "bond", "id": "893830AK8"}, "relationships": ["guarantees"]}
```
Guarantor chains, parent-subsidiary hierarchies, structural subordination—mapped and queryable. This data exists nowhere else in machine-readable form.

**3. Agent-Ready Speed**
```
< 100ms response time
```
AI agents chain multiple calls. If each took 30 seconds (reading a filing), a portfolio analysis would take hours. DebtStack returns in milliseconds.

---

## Installation

```bash
pip install debtstack-ai
```

For LangChain integration:
```bash
pip install debtstack-ai[langchain]
```

## Quick Start

```python
from debtstack import DebtStackClient
import asyncio

async def main():
    async with DebtStackClient(api_key="your-api-key") as client:

        # Screen for high-leverage companies
        risky = await client.search_companies(
            sector="Telecommunications",
            min_leverage=4.0,
            fields="ticker,name,net_leverage_ratio,interest_coverage",
            sort="-net_leverage_ratio"
        )

        # Drill into the riskiest one
        ticker = risky["data"][0]["ticker"]
        bonds = await client.search_bonds(ticker=ticker, has_pricing=True)

        # Check guarantor coverage on their notes
        for bond in bonds["data"]:
            guarantors = await client.get_guarantors(bond["cusip"])
            print(f"{bond['name']}: {len(guarantors)} guarantors")

asyncio.run(main())
```

## Synchronous Usage

```python
from debtstack import DebtStackSyncClient

client = DebtStackSyncClient(api_key="your-api-key")
result = client.search_companies(sector="Energy", min_leverage=3.0)
```

## What's In The Data

| Coverage | Count |
|----------|-------|
| Companies | ~300 (S&P 100 + NASDAQ 100 + high-yield issuers) |
| Entities | ~39,000 (subsidiaries, holdcos, JVs, VIEs) |
| Debt Instruments | ~10,000 (bonds, loans, revolvers) with 96% document linkage |
| Bond Pricing | ~4,300 bonds with FINRA TRACE pricing (updated 3x daily) |
| SEC Filing Sections | ~25,000 (searchable full-text) |
| Covenants | ~1,800 structured covenant records |

**Pre-computed metrics:** Leverage ratios, interest coverage, maturity profiles, structural subordination scores.

**Relationships:** Guarantor chains, issuer-entity links, parent-subsidiary hierarchies.

## API Methods

| Method | What It Does |
|--------|--------------|
| `search_companies()` | Screen by leverage, sector, coverage, risk flags |
| `search_bonds()` | Filter by yield, spread, seniority, maturity |
| `resolve_bond()` | Look up CUSIP, ISIN, or "RIG 8% 2027" |
| `traverse_entities()` | Follow guarantor chains, map corporate structure |
| `search_pricing()` | FINRA TRACE bond prices, YTM, spreads |
| `search_documents()` | Full-text search across credit agreements, indentures |
| `batch()` | Run multiple queries in parallel |
| `get_changes()` | Track debt structure changes over time |

## Examples

### Which MAG7 company has the most debt?

```python
result = await client.search_companies(
    ticker="AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
    fields="ticker,name,total_debt,net_leverage_ratio",
    sort="-total_debt",
    limit=1
)
# Returns structured data in milliseconds, not minutes
```

### Find high-yield bonds trading at a discount

```python
result = await client.search_bonds(
    seniority="senior_unsecured",
    min_ytm=8.0,
    has_pricing=True,
    sort="-pricing.ytm"
)
```

### Who guarantees a specific bond?

```python
guarantors = await client.get_guarantors("893830AK8")
for g in guarantors:
    print(f"{g['name']} ({g['entity_type']}) - {g['jurisdiction']}")

# Output:
# Transocean Ltd. (holdco) - Switzerland
# Transocean Inc. (finco) - Cayman Islands
# Transocean Offshore Deepwater Drilling Inc. (opco) - Delaware
# ... 42 more entities
```

### Search for covenant language

```python
result = await client.search_documents(
    q="maintenance covenant",
    section_type="credit_agreement",
    ticker="CHTR"
)
# Returns matching sections with highlighted snippets
```

## LangChain Integration

Build AI agents that can autonomously analyze corporate credit data.

### Installation

```bash
pip install debtstack-ai[langchain]
```

### Available Tools

| Tool | Description |
|------|-------------|
| `debtstack_search_companies` | Screen companies by leverage, sector, coverage ratios, and risk flags |
| `debtstack_search_bonds` | Filter bonds by yield, spread, seniority, maturity, and pricing |
| `debtstack_resolve_bond` | Look up bonds by CUSIP, ISIN, or description (e.g., "RIG 8% 2027") |
| `debtstack_traverse_entities` | Follow guarantor chains, map corporate structure, trace ownership |
| `debtstack_search_pricing` | Get FINRA TRACE bond prices, YTM, and spreads |
| `debtstack_search_documents` | Full-text search across credit agreements, indentures, and SEC filings |
| `debtstack_get_changes` | Track debt structure changes over time (new issuances, maturities, leverage) |

### Full Example

```python
from debtstack.langchain import DebtStackToolkit
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_openai import ChatOpenAI
from langchain import hub

# Initialize toolkit with your API key
toolkit = DebtStackToolkit(api_key="your-api-key")
tools = toolkit.get_tools()

# Create an agent with GPT-4 (or any LangChain-compatible LLM)
llm = ChatOpenAI(temperature=0, model="gpt-4")
prompt = hub.pull("hwchase17/openai-functions-agent")
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Ask natural language questions about corporate credit
result = agent_executor.invoke({
    "input": "Which telecom companies are most at risk of default?"
})
print(result["output"])
```

### Example Queries

The agent can handle complex, multi-step credit analysis:

- "Which telecom companies have leverage above 5x and near-term maturities?"
- "Find all bonds yielding above 8% with senior secured status"
- "Who guarantees Transocean's 8.75% 2030 notes? How many entities are in the guarantee chain?"
- "Compare Charter and Altice's corporate structures - which has more structural subordination risk?"
- "What changed in RIG's debt structure since January 2025?"
- "Search for maintenance covenant language in Charter's credit agreements"
- "Find distressed bonds trading below 80 cents on the dollar"

## MCP Server (Claude Desktop, Claude Code, Cursor)

Give Claude (or any MCP client) direct access to corporate credit data.

### Installation

```bash
pip install debtstack-ai[mcp]
```

### Available Tools

| Tool | Description |
|------|-------------|
| `search_companies` | Search companies by ticker, sector, leverage ratio, and risk flags |
| `search_bonds` | Search bonds by ticker, seniority, yield, spread, and maturity |
| `resolve_bond` | Look up a bond by CUSIP, ISIN, or description (e.g., "RIG 8% 2027") |
| `get_guarantors` | Find all entities that guarantee a bond |
| `get_corporate_structure` | Get full parent-subsidiary hierarchy for a company |
| `search_pricing` | Get FINRA TRACE bond prices, YTM, and spreads |
| `search_documents` | Search SEC filing sections (debt footnotes, credit agreements, indentures) |
| `get_changes` | See what changed in a company's debt structure since a date |

### Claude Desktop

Add to your Claude Desktop config (`~/.config/claude/mcp.json` on Mac/Linux, `%APPDATA%\Claude\mcp.json` on Windows):

```json
{
    "mcpServers": {
        "debtstack-ai": {
            "command": "debtstack-mcp",
            "env": {
                "DEBTSTACK_API_KEY": "your-api-key"
            }
        }
    }
}
```

### Claude Code

Add to your Claude Code config (`~/.claude/mcp.json`):

```json
{
    "mcpServers": {
        "debtstack-ai": {
            "command": "debtstack-mcp",
            "env": {
                "DEBTSTACK_API_KEY": "your-api-key"
            }
        }
    }
}
```

### Cursor

Add to your Cursor MCP settings (`.cursor/mcp.json`):

```json
{
    "mcpServers": {
        "debtstack-ai": {
            "command": "debtstack-mcp",
            "env": {
                "DEBTSTACK_API_KEY": "your-api-key"
            }
        }
    }
}
```

### Alternative: Run with Python Module

If you prefer not to install the console script, you can use `python -m` instead:

```json
{
    "mcpServers": {
        "debtstack-ai": {
            "command": "python",
            "args": ["-m", "debtstack.mcp_server"],
            "env": {
                "DEBTSTACK_API_KEY": "your-api-key"
            }
        }
    }
}
```

### Example Queries

Once configured, ask Claude:

- "Which energy companies have near-term maturities and high leverage?"
- "Who guarantees the Transocean 8% 2027 notes?"
- "Compare Charter's debt structure to Altice"
- "Find all senior secured bonds yielding above 8%"
- "What are the financial covenants in Charter's credit agreement?"
- "Show me RIG's corporate structure - where does the debt sit?"
- "What changed in CVS's debt structure since June 2025?"
- "Search for change-of-control provisions in Altice's indentures"

## Pricing

DebtStack offers usage-based pricing with a free tier to get started.

See [debtstack.ai/pricing](https://debtstack.ai/pricing) for details.

## Links

- **Docs:** [docs.debtstack.ai](https://docs.debtstack.ai)
- **Discord:** [discord.gg/debtstack-ai](https://discord.gg/debtstack-ai)
- **Issues:** [GitHub](https://github.com/debtstack-ai/debtstack-python/issues)

## License

Apache-2.0

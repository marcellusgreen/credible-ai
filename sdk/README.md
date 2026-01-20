# DebtStack.ai Python SDK

**Corporate credit data for AI agents.**

[![PyPI](https://img.shields.io/pypi/v/debtstack-ai)](https://pypi.org/project/debtstack-ai/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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
Screen 177 companies by leverage, coverage ratios, or maturity risk in one call. No filing-by-filing analysis.

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
| Companies | 177 (S&P 100 + NASDAQ 100 + high-yield issuers) |
| Entities | 3,085 (subsidiaries, holdcos, JVs, VIEs) |
| Debt Instruments | 1,805 (bonds, loans, revolvers) |
| SEC Filing Sections | 5,456 (searchable full-text) |

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

```python
from debtstack.langchain import DebtStackToolkit
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_openai import ChatOpenAI
from langchain import hub

toolkit = DebtStackToolkit(api_key="your-api-key")
tools = toolkit.get_tools()

llm = ChatOpenAI(temperature=0, model="gpt-4")
prompt = hub.pull("hwchase17/openai-functions-agent")
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools)

result = agent_executor.invoke({
    "input": "Which telecom companies are most at risk of default?"
})
```

## MCP Server (Claude Desktop)

Add to `~/.config/claude/mcp.json`:

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

Then ask Claude:
- "Which energy companies have near-term maturities and high leverage?"
- "Who guarantees the Transocean 8% 2027 notes?"
- "Compare Charter's debt structure to Altice"

## Pricing

DebtStack offers usage-based pricing with a free tier to get started.

See [debtstack.ai/pricing](https://debtstack.ai/pricing) for details.

## Links

- **Docs:** [docs.debtstack.ai](https://docs.debtstack.ai)
- **Discord:** [discord.gg/debtstack-ai](https://discord.gg/debtstack-ai)
- **Issues:** [GitHub](https://github.com/debtstack-ai/debtstack-python/issues)

## License

MIT

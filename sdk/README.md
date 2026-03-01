# DebtStack.ai — Python SDK & MCP Server

Corporate credit data API for AI agents. Search bonds, leverage ratios, guarantor chains, covenants, and SEC filings across 300+ companies.

mcp-name: io.github.marcellusgreen/debtstack-ai

## Installation

```bash
pip install debtstack-ai
```

For MCP server support:

```bash
pip install debtstack-ai[mcp]
```

## Quick Start

```python
from debtstack import DebtStack

client = DebtStack(api_key="ds_xxxxx")

# Search companies by leverage
companies = client.companies(min_leverage=4.0, sector="Technology")

# Search bonds by yield
bonds = client.bonds(min_ytm=8.0, seniority="senior_secured", has_pricing=True)

# Resolve a bond by description
matches = client.resolve_bond("RIG 8% 2027")

# Get corporate structure
structure = client.corporate_structure("CHTR")
```

## MCP Server

Connect DebtStack to Claude Desktop, Claude Code, Cursor, and other MCP clients.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "debtstack-ai": {
      "command": "debtstack-mcp",
      "env": {
        "DEBTSTACK_API_KEY": "ds_xxxxx"
      }
    }
  }
}
```

### Claude Code

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "debtstack-ai": {
      "command": "debtstack-mcp",
      "env": {
        "DEBTSTACK_API_KEY": "ds_xxxxx"
      }
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `search_companies` | Search companies by ticker, sector, leverage ratio, and risk flags |
| `search_bonds` | Search bonds by ticker, seniority, yield, spread, and maturity |
| `resolve_bond` | Look up a bond by CUSIP, ISIN, or description |
| `get_guarantors` | Find all entities that guarantee a bond |
| `get_corporate_structure` | Get full parent-subsidiary hierarchy |
| `search_pricing` | Get FINRA TRACE bond prices, YTM, and spreads |
| `search_documents` | Search SEC filing sections |
| `get_changes` | See what changed in a company's debt structure since a date |

## Links

- [Documentation](https://docs.debtstack.ai)
- [API Reference](https://docs.debtstack.ai/api-reference/overview)
- [MCP Guide](https://docs.debtstack.ai/guides/mcp)
- [Website](https://debtstack.ai)

## License

Apache-2.0

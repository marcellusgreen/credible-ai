"""
DebtStack.ai MCP Server

Model Context Protocol server for DebtStack.ai credit data API.
Enables Claude Desktop, Cursor, and other MCP clients to access corporate debt data.

Usage:
    # Run locally
    python -m debtstack.mcp_server

    # Or with uvx
    uvx debtstack-mcp

Configuration for Claude Desktop (~/.config/claude/mcp.json):
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
"""

import os
import json
import logging
from typing import Any

# MCP SDK imports
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import stdio_server
except ImportError:
    raise ImportError(
        "MCP dependencies not installed. Install with: pip install mcp"
    )

import httpx

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
app = Server("debtstack-ai")

# API configuration
BASE_URL = os.getenv("DEBTSTACK_BASE_URL", "https://api.debtstack.ai/v1")
API_KEY = os.getenv("DEBTSTACK_API_KEY")


def get_headers() -> dict:
    """Get API headers."""
    if not API_KEY:
        raise ValueError("DEBTSTACK_API_KEY environment variable not set")
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


def api_get(endpoint: str, params: dict = None) -> dict:
    """Make GET request to DebtStack API."""
    response = httpx.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers=get_headers(),
        timeout=30.0
    )
    response.raise_for_status()
    return response.json()


def api_post(endpoint: str, body: dict) -> dict:
    """Make POST request to DebtStack API."""
    response = httpx.post(
        f"{BASE_URL}{endpoint}",
        json=body,
        headers=get_headers(),
        timeout=30.0
    )
    response.raise_for_status()
    return response.json()


# =============================================================================
# Formatting helpers
# =============================================================================

def format_company(c: dict) -> str:
    """Format company data for display."""
    lines = [f"**{c.get('name', 'Unknown')}** ({c.get('ticker', '?')})"]

    if c.get('sector'):
        lines.append(f"Sector: {c['sector']}")

    debt = c.get('total_debt')
    if debt:
        lines.append(f"Total Debt: ${debt / 100_000_000_000:.2f}B")

    lev = c.get('net_leverage_ratio')
    if lev:
        lines.append(f"Net Leverage: {lev:.1f}x")

    cov = c.get('interest_coverage')
    if cov:
        lines.append(f"Interest Coverage: {cov:.1f}x")

    if c.get('has_structural_sub'):
        lines.append("⚠️ Has structural subordination")

    if c.get('has_near_term_maturity'):
        lines.append("⚠️ Near-term maturities")

    return "\n".join(lines)


def format_bond(b: dict) -> str:
    """Format bond data for display."""
    lines = [f"**{b.get('name', 'Unknown Bond')}**"]

    if b.get('cusip'):
        lines.append(f"CUSIP: {b['cusip']}")

    if b.get('company_ticker'):
        lines.append(f"Issuer: {b.get('company_name', b['company_ticker'])}")

    if b.get('outstanding'):
        lines.append(f"Outstanding: ${b['outstanding'] / 100_000_000_000:.2f}B")

    if b.get('coupon_rate'):
        lines.append(f"Coupon: {b['coupon_rate']:.3f}%")

    if b.get('maturity_date'):
        lines.append(f"Maturity: {b['maturity_date']}")

    if b.get('seniority'):
        lines.append(f"Seniority: {b['seniority'].replace('_', ' ').title()}")

    pricing = b.get('pricing', {})
    if pricing:
        if pricing.get('last_price'):
            lines.append(f"Price: {pricing['last_price']:.2f}")
        if pricing.get('ytm'):
            lines.append(f"YTM: {pricing['ytm']:.2f}%")
        if pricing.get('spread'):
            lines.append(f"Spread: {pricing['spread']} bps")

    return "\n".join(lines)


def format_entity(e: dict) -> str:
    """Format entity data for display."""
    name = e.get('name', 'Unknown')
    etype = e.get('entity_type', 'entity').replace('_', ' ').title()
    jurisdiction = e.get('jurisdiction', '')

    parts = [f"• {name} ({etype})"]
    if jurisdiction:
        parts[0] += f" - {jurisdiction}"

    if e.get('is_guarantor'):
        parts.append("  ✓ Guarantor")
    if e.get('is_vie'):
        parts.append("  ⚠️ VIE")
    if e.get('debt_at_entity'):
        parts.append(f"  Debt: ${e['debt_at_entity'] / 100_000_000_000:.2f}B")

    return "\n".join(parts)


def format_document_result(d: dict) -> str:
    """Format document search result."""
    lines = [
        f"**{d.get('section_type', 'Document')}** - {d.get('ticker', '?')}",
        f"Filing: {d.get('doc_type', '?')} ({d.get('filing_date', '?')})"
    ]

    if d.get('snippet'):
        # Clean up HTML tags in snippet
        snippet = d['snippet'].replace('<b>', '**').replace('</b>', '**')
        lines.append(f"...{snippet}...")

    return "\n".join(lines)


# =============================================================================
# Tool definitions
# =============================================================================

@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available DebtStack tools."""
    return [
        Tool(
            name="search_companies",
            description=(
                "Search companies by ticker, sector, leverage ratio, and risk flags. "
                "Use to find companies with specific characteristics, compare leverage across peers, "
                "or screen for structural subordination risk. "
                "Example: 'Find tech companies with leverage above 4x'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Comma-separated tickers (e.g., 'AAPL,MSFT,GOOGL')"
                    },
                    "sector": {
                        "type": "string",
                        "description": "Filter by sector (e.g., 'Technology', 'Energy')"
                    },
                    "min_leverage": {
                        "type": "number",
                        "description": "Minimum leverage ratio"
                    },
                    "max_leverage": {
                        "type": "number",
                        "description": "Maximum leverage ratio"
                    },
                    "has_structural_sub": {
                        "type": "boolean",
                        "description": "Filter for structural subordination"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default 10)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="search_bonds",
            description=(
                "Search bonds by ticker, seniority, yield, spread, and maturity. "
                "Use for yield hunting, finding high-yield opportunities, or analyzing maturity walls. "
                "Example: 'Find senior unsecured bonds yielding above 8%'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker(s)"
                    },
                    "seniority": {
                        "type": "string",
                        "enum": ["senior_secured", "senior_unsecured", "subordinated"],
                        "description": "Bond seniority level"
                    },
                    "min_ytm": {
                        "type": "number",
                        "description": "Minimum yield to maturity (%)"
                    },
                    "has_pricing": {
                        "type": "boolean",
                        "description": "Only bonds with pricing data"
                    },
                    "maturity_before": {
                        "type": "string",
                        "description": "Maturity before date (YYYY-MM-DD)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default 10)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="resolve_bond",
            description=(
                "Look up a bond by CUSIP, ISIN, or description. "
                "Use when you have a partial bond identifier and need full details. "
                "Example: 'RIG 8% 2027' or 'CUSIP 893830AK8'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Bond identifier - CUSIP, ISIN, or description (e.g., 'RIG 8% 2027')"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_guarantors",
            description=(
                "Find all entities that guarantee a bond. "
                "Use to understand guarantee coverage and structural subordination risk. "
                "Pass a CUSIP or bond description."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bond_id": {
                        "type": "string",
                        "description": "Bond CUSIP or identifier"
                    }
                },
                "required": ["bond_id"]
            }
        ),
        Tool(
            name="get_corporate_structure",
            description=(
                "Get the full corporate structure for a company. "
                "Shows parent-subsidiary hierarchy, entity types, and debt at each level. "
                "Use to understand structural subordination and where debt sits in the org."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker (e.g., 'RIG', 'CHTR')"
                    }
                },
                "required": ["ticker"]
            }
        ),
        Tool(
            name="search_pricing",
            description=(
                "Get bond pricing from FINRA TRACE. "
                "Returns current price, yield to maturity, and spread to treasury. "
                "Use to find distressed bonds or compare relative value."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker(s)"
                    },
                    "cusip": {
                        "type": "string",
                        "description": "Bond CUSIP(s)"
                    },
                    "min_ytm": {
                        "type": "number",
                        "description": "Minimum yield to maturity (%)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default 10)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="search_documents",
            description=(
                "Search SEC filing sections for specific terms. "
                "Section types: debt_footnote, credit_agreement, indenture, covenants, mda_liquidity. "
                "Use to find covenant language, credit agreement terms, or debt descriptions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms"
                    },
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker(s)"
                    },
                    "section_type": {
                        "type": "string",
                        "enum": ["debt_footnote", "credit_agreement", "indenture", "covenants", "mda_liquidity", "exhibit_21", "guarantor_list"],
                        "description": "Section type to search"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results (default 10)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_changes",
            description=(
                "See what changed in a company's debt structure since a date. "
                "Returns new issuances, matured debt, leverage changes, and pricing movements. "
                "Use to monitor companies for material changes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Company ticker"
                    },
                    "since": {
                        "type": "string",
                        "description": "Compare since date (YYYY-MM-DD)"
                    }
                },
                "required": ["ticker", "since"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""

    try:
        if name == "search_companies":
            params = {k: v for k, v in arguments.items() if v is not None}
            params.setdefault("limit", 10)
            result = api_get("/companies", params)

            companies = result.get("data", [])
            if not companies:
                return [TextContent(type="text", text="No companies found matching criteria.")]

            text = f"Found {len(companies)} companies:\n\n"
            text += "\n\n---\n\n".join(format_company(c) for c in companies)
            return [TextContent(type="text", text=text)]

        elif name == "search_bonds":
            params = {k: v for k, v in arguments.items() if v is not None}
            params.setdefault("limit", 10)
            result = api_get("/bonds", params)

            bonds = result.get("data", [])
            if not bonds:
                return [TextContent(type="text", text="No bonds found matching criteria.")]

            text = f"Found {len(bonds)} bonds:\n\n"
            text += "\n\n---\n\n".join(format_bond(b) for b in bonds)
            return [TextContent(type="text", text=text)]

        elif name == "resolve_bond":
            query = arguments.get("query", "").strip()
            params = {}

            # Detect identifier type
            if len(query) == 9 and query.isalnum():
                params["cusip"] = query
            elif len(query) == 12 and query[:2].isalpha():
                params["isin"] = query
            else:
                params["q"] = query
                params["match_mode"] = "fuzzy"

            result = api_get("/bonds/resolve", params)
            matches = result.get("data", {}).get("matches", [])

            if not matches:
                return [TextContent(type="text", text=f"No bonds found matching '{query}'.")]

            text = f"Found {len(matches)} match(es) for '{query}':\n\n"
            for m in matches:
                conf = m.get("confidence", 0)
                bond = m.get("bond", {})
                text += f"**Confidence: {conf:.0%}**\n"
                text += format_bond(bond) + "\n\n"

            return [TextContent(type="text", text=text)]

        elif name == "get_guarantors":
            bond_id = arguments.get("bond_id", "").strip()
            body = {
                "start": {"type": "bond", "id": bond_id},
                "relationships": ["guarantees"],
                "direction": "inbound",
                "fields": ["name", "entity_type", "jurisdiction", "is_guarantor"]
            }
            result = api_post("/entities/traverse", body)

            data = result.get("data", {})
            start = data.get("start", {})
            entities = data.get("traversal", {}).get("entities", [])

            text = f"**Guarantors for {start.get('name', bond_id)}**\n\n"
            if not entities:
                text += "No guarantors found."
            else:
                text += f"{len(entities)} guarantor(s):\n\n"
                text += "\n".join(format_entity(e) for e in entities)

            return [TextContent(type="text", text=text)]

        elif name == "get_corporate_structure":
            ticker = arguments.get("ticker", "").upper()
            body = {
                "start": {"type": "company", "id": ticker},
                "relationships": ["subsidiaries"],
                "direction": "outbound",
                "depth": 10,
                "fields": ["name", "entity_type", "jurisdiction", "is_guarantor", "is_vie", "debt_at_entity"]
            }
            result = api_post("/entities/traverse", body)

            data = result.get("data", {})
            start = data.get("start", {})
            entities = data.get("traversal", {}).get("entities", [])

            text = f"**Corporate Structure for {start.get('name', ticker)}**\n\n"
            text += f"{len(entities)} entities in structure:\n\n"
            text += "\n".join(format_entity(e) for e in entities)

            return [TextContent(type="text", text=text)]

        elif name == "search_pricing":
            params = {k: v for k, v in arguments.items() if v is not None}
            params.setdefault("limit", 10)
            params["has_pricing"] = True
            result = api_get("/bonds", params)

            bonds = result.get("data", [])
            if not bonds:
                return [TextContent(type="text", text="No pricing data found.")]

            text = f"Bond pricing ({len(bonds)} bonds):\n\n"
            for b in bonds:
                text += f"**{b.get('name', b.get('cusip', '?'))}**\n"
                if b.get('company_ticker'):
                    text += f"Issuer: {b['company_ticker']}\n"
                pricing = b.get('pricing', {}) or {}
                if pricing.get('last_price'):
                    text += f"Price: {pricing['last_price']:.2f}\n"
                if pricing.get('ytm'):
                    text += f"YTM: {pricing['ytm']:.2f}%\n"
                if pricing.get('spread'):
                    text += f"Spread: {pricing['spread']} bps\n"
                text += "\n"

            return [TextContent(type="text", text=text)]

        elif name == "search_documents":
            params = {k: v for k, v in arguments.items() if v is not None}
            params.setdefault("limit", 10)
            result = api_get("/documents/search", params)

            docs = result.get("data", [])
            if not docs:
                return [TextContent(type="text", text=f"No documents found for '{params.get('q', '')}'.")]

            text = f"Found {len(docs)} matching sections:\n\n"
            text += "\n\n---\n\n".join(format_document_result(d) for d in docs)
            return [TextContent(type="text", text=text)]

        elif name == "get_changes":
            ticker = arguments.get("ticker", "").upper()
            since = arguments.get("since", "")
            result = api_get(f"/companies/{ticker}/changes", {"since": since})

            data = result.get("data", {})
            changes = data.get("changes", {})

            text = f"**Changes for {data.get('company_name', ticker)}**\n"
            text += f"Comparing {data.get('snapshot_date', '?')} → {data.get('current_date', 'now')}\n\n"

            # New debt
            new_debt = changes.get("new_debt", [])
            if new_debt:
                text += f"**New Debt ({len(new_debt)})**\n"
                for d in new_debt:
                    text += f"• {d.get('name', '?')} - ${d.get('principal', 0) / 100_000_000_000:.2f}B\n"
                text += "\n"

            # Removed debt
            removed = changes.get("removed_debt", [])
            if removed:
                text += f"**Removed/Matured Debt ({len(removed)})**\n"
                for d in removed:
                    text += f"• {d.get('name', '?')} ({d.get('reason', 'removed')})\n"
                text += "\n"

            # Metric changes
            metrics = changes.get("metric_changes", {})
            if metrics:
                text += "**Metric Changes**\n"
                for name, vals in metrics.items():
                    if isinstance(vals, dict) and 'previous' in vals:
                        prev = vals['previous']
                        curr = vals['current']
                        if isinstance(prev, (int, float)) and isinstance(curr, (int, float)):
                            change = curr - prev
                            sign = "+" if change > 0 else ""
                            text += f"• {name}: {prev} → {curr} ({sign}{change})\n"
                text += "\n"

            summary = data.get("summary", {})
            if summary:
                text += "**Summary**\n"
                if summary.get("new_issuances"):
                    text += f"• New issuances: {summary['new_issuances']}\n"
                if summary.get("maturities"):
                    text += f"• Maturities: {summary['maturities']}\n"
                if summary.get("net_debt_change"):
                    change = summary['net_debt_change'] / 100_000_000_000
                    text += f"• Net debt change: ${change:+.2f}B\n"

            return [TextContent(type="text", text=text)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        logger.error(f"API error: {e}")
        return [TextContent(type="text", text=f"API error: {e.response.status_code} - {e.response.text}")]
    except Exception as e:
        logger.error(f"Error calling {name}: {e}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]


# =============================================================================
# Main entry point
# =============================================================================

async def main():
    """Run the MCP server."""
    if not API_KEY:
        logger.error("DEBTSTACK_API_KEY environment variable not set")
        return

    logger.info("Starting DebtStack MCP server...")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


def run():
    """Sync entry point for console_scripts."""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()

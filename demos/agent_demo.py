#!/usr/bin/env python3
"""
DebtStack API Agent Demo

Shows an AI agent (Claude) using the DebtStack API to answer credit analysis questions.

Modes:
- Offline (default): Uses local JSON files from results/ directory
- Live (--live): Calls the running API at localhost:8000

Usage:
    python demos/agent_demo.py                    # Offline interactive
    python demos/agent_demo.py --live             # Live API interactive
    python demos/agent_demo.py "What is RIG's debt?"
    python demos/agent_demo.py --demo             # Run demo questions

Requirements:
    - ANTHROPIC_API_KEY in environment (or .env file)
    - For --live mode: API running at localhost:8000
"""

import argparse
import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import anthropic
import httpx

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CREDIBLE_API_URL = os.getenv("CREDIBLE_API_URL", "http://localhost:8000")
RESULTS_DIR = Path(__file__).parent.parent / "results"

# Sample pricing data for offline demo
SAMPLE_BONDS = [
    {"ticker": "RIG", "company": "Transocean Ltd.", "name": "8.00% Senior Notes due 2027", "seniority": "senior_unsecured", "price": 96.50, "ytm_pct": 8.92, "spread_bps": 458, "benchmark": "5Y"},
    {"ticker": "RIG", "company": "Transocean Ltd.", "name": "11.50% Senior Secured Notes due 2027", "seniority": "senior_secured", "price": 102.25, "ytm_pct": 10.85, "spread_bps": 651, "benchmark": "5Y"},
    {"ticker": "RIG", "company": "Transocean Ltd.", "name": "8.75% Senior Notes due 2030", "seniority": "senior_unsecured", "price": 91.75, "ytm_pct": 10.15, "spread_bps": 581, "benchmark": "7Y"},
    {"ticker": "ATUS", "company": "Altice USA, Inc.", "name": "5.50% Senior Notes due 2028", "seniority": "senior_unsecured", "price": 88.50, "ytm_pct": 8.75, "spread_bps": 441, "benchmark": "5Y"},
    {"ticker": "ATUS", "company": "Altice USA, Inc.", "name": "5.875% Senior Secured Notes due 2027", "seniority": "senior_secured", "price": 95.25, "ytm_pct": 7.25, "spread_bps": 291, "benchmark": "5Y"},
    {"ticker": "AAPL", "company": "Apple Inc.", "name": "3.25% Notes due 2029", "seniority": "senior_unsecured", "price": 98.50, "ytm_pct": 3.45, "spread_bps": 45, "benchmark": "5Y"},
    {"ticker": "AAPL", "company": "Apple Inc.", "name": "2.65% Notes due 2030", "seniority": "senior_unsecured", "price": 95.75, "ytm_pct": 3.25, "spread_bps": 35, "benchmark": "7Y"},
    {"ticker": "CRWV", "company": "CoreWeave, Inc.", "name": "7.50% Senior Secured Notes due 2029", "seniority": "senior_secured", "price": 99.25, "ytm_pct": 7.65, "spread_bps": 365, "benchmark": "5Y"},
]

DEMO_COMPANIES = {"rig": "Transocean Ltd.", "atus": "Altice USA, Inc.", "aapl": "Apple Inc.", "crwv": "CoreWeave, Inc."}

# Tool definitions for Claude
TOOLS = [
    {"name": "get_company_structure", "description": "Get corporate structure with subsidiaries and debt at each entity.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker (e.g., AAPL, RIG)"}}, "required": ["ticker"]}},
    {"name": "get_company_hierarchy", "description": "Get corporate structure as a nested tree showing parent-child relationships with debt at each level.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker"}}, "required": ["ticker"]}},
    {"name": "get_company_debt", "description": "Get all debt instruments with issuer, seniority, rates, maturity, guarantors.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker"}}, "required": ["ticker"]}},
    {"name": "get_company_overview", "description": "Get basic company info and summary metrics.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker"}}, "required": ["ticker"]}},
    {"name": "get_company_pricing", "description": "Get bond pricing (price, YTM, spread) for a company's bonds.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker"}}, "required": ["ticker"]}},
    {"name": "get_company_ownership", "description": "Get complex ownership relationships: joint ventures, multiple parents, ownership percentages.", "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "Stock ticker"}}, "required": ["ticker"]}},
    {"name": "search_bonds", "description": "Search bonds across all companies by yield, spread, issuer type, guarantors, and more.", "input_schema": {"type": "object", "properties": {
        "min_spread_bps": {"type": "integer", "description": "Minimum spread to treasury (basis points)"},
        "max_spread_bps": {"type": "integer", "description": "Maximum spread to treasury (basis points)"},
        "min_ytm_bps": {"type": "integer", "description": "Minimum yield to maturity (basis points)"},
        "max_ytm_bps": {"type": "integer", "description": "Maximum yield to maturity (basis points)"},
        "seniority": {"type": "string", "description": "senior_secured, senior_unsecured, or subordinated"},
        "issuer_type": {"type": "string", "description": "Filter by issuer entity type: holdco, opco, subsidiary, spv"},
        "has_guarantors": {"type": "boolean", "description": "Filter debt with/without guarantors"},
        "sector": {"type": "string", "description": "Filter by company sector"},
        "has_pricing": {"type": "boolean", "description": "Filter to bonds with pricing data"},
        "limit": {"type": "integer", "description": "Maximum results (default 20)"}
    }, "required": []}},
    {"name": "search_entities", "description": "Search entities across ALL companies. Find all VIEs, all Delaware entities, all guarantors, etc.", "input_schema": {"type": "object", "properties": {
        "entity_type": {"type": "string", "description": "holdco, opco, subsidiary, spv, jv, finco, vie"},
        "jurisdiction": {"type": "string", "description": "Filter by jurisdiction (e.g., Delaware, Cayman Islands)"},
        "is_guarantor": {"type": "boolean", "description": "Filter by guarantor status"},
        "is_vie": {"type": "boolean", "description": "Filter by VIE status"},
        "is_unrestricted": {"type": "boolean", "description": "Filter unrestricted subsidiaries"},
        "has_debt": {"type": "boolean", "description": "Filter entities with/without debt issued"},
        "q": {"type": "string", "description": "Text search on entity name"},
        "limit": {"type": "integer", "description": "Maximum results (default 50)"}
    }, "required": []}},
    {"name": "get_sector_analytics", "description": "Get sector-level analytics: average leverage, total debt, company counts. Without sector param returns all sectors.", "input_schema": {"type": "object", "properties": {
        "sector": {"type": "string", "description": "Optional: specific sector for detailed breakdown"}
    }, "required": []}},
    {"name": "list_available_companies", "description": "List all companies in the database.", "input_schema": {"type": "object", "properties": {}, "required": []}},
]

SYSTEM_PROMPT = """You are a credit analyst assistant with access to the DebtStack API for corporate structure and debt data.

**Data Units:**
- Amounts in CENTS (divide by 100000000 for millions)
- Rates in BASIS POINTS (divide by 100 for percentage)
- Prices are % of par (100 = par value)

**Company Tools:**
- get_company_overview: Basic info and metrics
- get_company_structure: Flat list of entities with debt
- get_company_hierarchy: Nested tree view of corporate structure
- get_company_debt: All debt instruments with terms
- get_company_pricing: Bond prices, YTM, spreads
- get_company_ownership: JVs, complex ownership, partial stakes

**Search Tools:**
- search_bonds: Search debt across all companies by yield, spread, issuer type, guarantors, sector
- search_entities: Find entities across ALL companies (all VIEs, all Delaware SPVs, all guarantors)
- get_sector_analytics: Sector-level aggregations (avg leverage, total debt by sector)
- list_available_companies: List all companies

Present data clearly. Cite "DebtStack API (extracted from SEC filings)" as source."""

DEMO_QUESTIONS = [
    "What companies do you have data for?",
    "What is Transocean's corporate structure?",
    "Show me Transocean's debt instruments.",
    "What pricing data do you have for RIG's bonds?",
    "What bonds have spreads over 400 basis points?",
    "Show me senior secured bonds issued by operating companies.",
    "Find all VIE entities across all companies in the database.",
    "What's the average leverage by sector?",
    "Show me bonds without guarantors.",
]


def load_extraction(ticker: str) -> Optional[dict]:
    """Load extraction data from results directory."""
    ticker_lower = ticker.lower()
    for pattern in [f"{ticker_lower}_iterative.json", f"{ticker_lower}_extraction.json"]:
        path = RESULTS_DIR / pattern
        if path.exists():
            with open(path) as f:
                return json.load(f)
    return None


# =============================================================================
# Live API Client
# =============================================================================

class DebtStackClient:
    """Client for the live DebtStack API."""

    def __init__(self):
        self.base_url = CREDIBLE_API_URL.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict = None) -> dict:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def execute(self, tool_name: str, tool_input: dict) -> str:
        try:
            ticker = tool_input.get("ticker", "").upper()
            if tool_name == "get_company_structure":
                result = self._get(f"/v1/companies/{ticker}/structure")
            elif tool_name == "get_company_hierarchy":
                result = self._get(f"/v1/companies/{ticker}/hierarchy")
            elif tool_name == "get_company_debt":
                result = self._get(f"/v1/companies/{ticker}/debt")
            elif tool_name == "get_company_overview":
                result = self._get(f"/v1/companies/{ticker}")
            elif tool_name == "get_company_pricing":
                result = self._get(f"/v1/companies/{ticker}/pricing")
            elif tool_name == "get_company_ownership":
                result = self._get(f"/v1/companies/{ticker}/ownership")
            elif tool_name == "list_available_companies":
                result = self._get("/v1/companies")
            elif tool_name in ["search_bonds_by_yield", "search_bonds"]:
                params = {}
                for key in ["min_spread_bps", "max_spread_bps", "min_ytm_bps", "max_ytm_bps",
                           "seniority", "issuer_type", "has_guarantors", "sector", "has_pricing", "limit"]:
                    if tool_input.get(key) is not None:
                        params[key] = tool_input[key]
                result = self._get("/v1/search/debt", params)
            elif tool_name == "search_entities":
                params = {}
                for key in ["entity_type", "jurisdiction", "is_guarantor", "is_vie",
                           "is_unrestricted", "has_debt", "q", "limit"]:
                    if tool_input.get(key) is not None:
                        params[key] = tool_input[key]
                result = self._get("/v1/search/entities", params)
            elif tool_name == "get_sector_analytics":
                sector = tool_input.get("sector")
                if sector:
                    result = self._get(f"/v1/analytics/sectors", {"sector": sector})
                else:
                    result = self._get("/v1/analytics/sectors")
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
            return json.dumps(result, indent=2, default=str)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"API error: {e.response.status_code}"})
        except httpx.ConnectError:
            return json.dumps({"error": f"Cannot connect to {CREDIBLE_API_URL}"})

    def close(self):
        self.client.close()


# =============================================================================
# Offline Mode
# =============================================================================

def execute_tool_offline(tool_name: str, tool_input: dict) -> str:
    """Execute tool using local JSON files."""
    if tool_name == "list_available_companies":
        companies = [{"ticker": t.upper(), "name": n} for t, n in DEMO_COMPANIES.items() if load_extraction(t)]
        return json.dumps({"companies": companies, "total": len(companies)}, indent=2)

    if tool_name in ["search_bonds_by_yield", "search_bonds"]:
        return _search_bonds_offline(tool_input)

    if tool_name == "search_entities":
        return _search_entities_offline(tool_input)

    if tool_name == "get_sector_analytics":
        return _get_sector_analytics_offline(tool_input)

    ticker = tool_input.get("ticker", "").upper()
    extraction = load_extraction(ticker)
    if not extraction:
        return json.dumps({"error": f"Company {ticker} not found"})

    if tool_name == "get_company_structure":
        result = _format_structure(extraction, ticker)
    elif tool_name == "get_company_hierarchy":
        result = _format_hierarchy(extraction, ticker)
    elif tool_name == "get_company_debt":
        result = _format_debt(extraction, ticker)
    elif tool_name == "get_company_overview":
        result = {"ticker": ticker, "name": extraction.get("company_name"), "total_entities": len(extraction.get("entities", [])), "total_debt": len(extraction.get("debt_instruments", []))}
    elif tool_name == "get_company_pricing":
        result = _format_pricing(extraction, ticker)
    elif tool_name == "get_company_ownership":
        result = _format_ownership(extraction, ticker)
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    return json.dumps(result, indent=2, default=str)


def _format_structure(extraction: dict, ticker: str) -> dict:
    entities = extraction.get("entities", [])
    debt = extraction.get("debt_instruments", [])
    return {
        "company": {"ticker": ticker, "name": extraction.get("company_name")},
        "entities": [{"name": e.get("name"), "type": e.get("entity_type"), "is_guarantor": e.get("is_guarantor", False), "debt_count": sum(1 for d in debt if d.get("issuer_name") == e.get("name"))} for e in entities],
        "total_entities": len(entities),
    }


def _format_debt(extraction: dict, ticker: str) -> dict:
    debt = extraction.get("debt_instruments", [])
    return {
        "company": {"ticker": ticker, "name": extraction.get("company_name")},
        "debt_instruments": debt,
        "total_count": len(debt),
        "total_outstanding_cents": sum(d.get("outstanding") or d.get("principal") or 0 for d in debt),
    }


def _format_pricing(extraction: dict, ticker: str) -> dict:
    debt = extraction.get("debt_instruments", [])
    ticker_bonds = [b for b in SAMPLE_BONDS if b["ticker"] == ticker.upper()]

    bonds_with_pricing = []
    for d in debt:
        name = d.get("name", "")
        match = next((b for b in ticker_bonds if b["name"].lower() in name.lower() or name.lower() in b["name"].lower()), None)
        if match:
            bonds_with_pricing.append({"name": name, "pricing": {"last_price": match["price"], "ytm_pct": match["ytm_pct"], "spread_bps": match["spread_bps"], "benchmark": match["benchmark"]}})

    return {"company": {"ticker": ticker, "name": extraction.get("company_name")}, "bonds": bonds_with_pricing, "summary": {"bonds_with_pricing": len(bonds_with_pricing)}}


def _search_bonds_offline(params: dict) -> str:
    results = SAMPLE_BONDS.copy()
    if params.get("min_spread_bps"):
        results = [b for b in results if b["spread_bps"] >= params["min_spread_bps"]]
    if params.get("max_spread_bps"):
        results = [b for b in results if b["spread_bps"] <= params["max_spread_bps"]]
    if params.get("seniority"):
        results = [b for b in results if b["seniority"] == params["seniority"]]
    if params.get("issuer_type"):
        # For demo, assume senior_secured = opco, senior_unsecured = holdco
        if params["issuer_type"] == "opco":
            results = [b for b in results if b["seniority"] == "senior_secured"]
        elif params["issuer_type"] == "holdco":
            results = [b for b in results if b["seniority"] == "senior_unsecured"]
    results = results[:params.get("limit", 20)]

    return json.dumps({
        "filters": {k: v for k, v in params.items() if v},
        "results": [{"ticker": b["ticker"], "name": b["name"], "seniority": b["seniority"],
                    "issuer": {"type": "opco" if b["seniority"] == "senior_secured" else "holdco"},
                    "ytm_pct": b["ytm_pct"], "spread_bps": b["spread_bps"]} for b in results],
        "total": len(results),
    }, indent=2)


def _format_hierarchy(extraction: dict, ticker: str) -> dict:
    """Format extraction as nested hierarchy."""
    entities = extraction.get("entities", [])
    debt = extraction.get("debt_instruments", [])

    # Build simple tree (offline demo simplified)
    def build_node(entity_data):
        name = entity_data.get("name", "")
        return {
            "name": name,
            "entity_type": entity_data.get("entity_type"),
            "is_guarantor": entity_data.get("is_guarantor", False),
            "debt_at_entity": {
                "count": sum(1 for d in debt if d.get("issuer_name") == name),
                "total": sum(d.get("outstanding") or 0 for d in debt if d.get("issuer_name") == name)
            },
            "children": []
        }

    # Simple hierarchy - holdco at top, then others
    holdcos = [e for e in entities if e.get("entity_type") == "holdco"]
    others = [e for e in entities if e.get("entity_type") != "holdco"]

    tree = []
    for h in holdcos:
        node = build_node(h)
        node["children"] = [build_node(o) for o in others[:5]]  # Simplified for demo
        tree.append(node)

    return {
        "company": {"ticker": ticker, "name": extraction.get("company_name")},
        "hierarchy": tree,
        "summary": {"total_entities": len(entities), "root_entities": len(holdcos)}
    }


def _format_ownership(extraction: dict, ticker: str) -> dict:
    """Format ownership info (simplified for offline demo)."""
    entities = extraction.get("entities", [])

    # Simple ownership from parent references
    simple_ownership = []
    for e in entities:
        if e.get("owners"):
            for owner in e["owners"]:
                simple_ownership.append({
                    "parent": {"name": owner},
                    "child": {"name": e.get("name"), "type": e.get("entity_type")},
                    "ownership_pct": 100.0,
                    "ownership_type": "direct"
                })

    return {
        "company": {"ticker": ticker, "name": extraction.get("company_name")},
        "ownership_links": [],
        "simple_hierarchy": simple_ownership,
        "joint_ventures": [],
        "summary": {"total_complex_links": 0, "total_simple_links": len(simple_ownership), "joint_ventures": 0}
    }


def _search_entities_offline(params: dict) -> str:
    """Search entities across all companies (offline demo)."""
    all_entities = []

    for ticker, company_name in DEMO_COMPANIES.items():
        extraction = load_extraction(ticker)
        if not extraction:
            continue

        for e in extraction.get("entities", []):
            entity = {
                "name": e.get("name"),
                "entity_type": e.get("entity_type"),
                "jurisdiction": e.get("jurisdiction"),
                "company": {"ticker": ticker.upper(), "name": company_name},
                "is_guarantor": e.get("is_guarantor", False),
                "is_vie": e.get("is_vie", False),
                "is_unrestricted": e.get("is_unrestricted", False),
            }
            all_entities.append(entity)

    # Apply filters
    results = all_entities
    if params.get("entity_type"):
        results = [e for e in results if e["entity_type"] == params["entity_type"]]
    if params.get("jurisdiction"):
        results = [e for e in results if params["jurisdiction"].lower() in (e.get("jurisdiction") or "").lower()]
    if params.get("is_guarantor") is not None:
        results = [e for e in results if e["is_guarantor"] == params["is_guarantor"]]
    if params.get("is_vie") is not None:
        results = [e for e in results if e["is_vie"] == params["is_vie"]]
    if params.get("is_unrestricted") is not None:
        results = [e for e in results if e["is_unrestricted"] == params["is_unrestricted"]]
    if params.get("q"):
        results = [e for e in results if params["q"].lower() in e["name"].lower()]

    results = results[:params.get("limit", 50)]

    return json.dumps({
        "results": results,
        "total": len(results),
        "filters_applied": {k: v for k, v in params.items() if v is not None},
    }, indent=2)


def _get_sector_analytics_offline(params: dict) -> str:
    """Get sector analytics (offline demo with sample data)."""
    # Sample sector data for demo
    sectors = [
        {"sector": "Energy", "company_count": 15, "total_debt": 45000000000000, "avg_leverage_ratio": 3.5},
        {"sector": "Technology", "company_count": 25, "total_debt": 30000000000000, "avg_leverage_ratio": 1.8},
        {"sector": "Telecom", "company_count": 8, "total_debt": 60000000000000, "avg_leverage_ratio": 4.2},
        {"sector": "Healthcare", "company_count": 12, "total_debt": 25000000000000, "avg_leverage_ratio": 2.5},
    ]

    sector_filter = params.get("sector")
    if sector_filter:
        matching = [s for s in sectors if s["sector"].lower() == sector_filter.lower()]
        if matching:
            return json.dumps({"data": {"sector": sector_filter, "companies": [], "aggregates": matching[0]}}, indent=2)
        return json.dumps({"error": f"Sector '{sector_filter}' not found"})

    return json.dumps({
        "data": {
            "sectors": sectors,
            "totals": {"sector_count": len(sectors), "total_companies": sum(s["company_count"] for s in sectors)}
        }
    }, indent=2)


# =============================================================================
# Agent Runner
# =============================================================================

def run_agent(question: str, live_mode: bool = False, verbose: bool = True) -> str:
    """Run the agent to answer a question."""
    if not ANTHROPIC_API_KEY:
        return "Error: ANTHROPIC_API_KEY not set"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    api_client = DebtStackClient() if live_mode else None
    messages = [{"role": "user", "content": question}]

    if verbose:
        mode = "LIVE" if live_mode else "OFFLINE"
        print(f"\n{'='*60}\n[{mode}] {question}\n{'='*60}\n")

    try:
        response = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=4096, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)

        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if verbose:
                    print(f"[Tool] {block.name}({json.dumps(block.input)})")
                result = api_client.execute(block.name, block.input) if live_mode else execute_tool_offline(block.name, block.input)
                if verbose:
                    print(f"[Result] {result[:500]}...\n" if len(result) > 500 else f"[Result] {result}\n")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            response = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=4096, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)

        final = "".join(block.text for block in response.content if hasattr(block, "text"))
        if verbose:
            print(f"{'='*60}\nAnswer:\n{'='*60}\n{final}")
        return final
    finally:
        if api_client:
            api_client.close()


def demo(live_mode: bool = False):
    """Run demo questions."""
    print(f"\n{'='*70}\nDEBTSTACK AGENT DEMO ({'LIVE' if live_mode else 'OFFLINE'})\n{'='*70}")
    for i, q in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[Question {i}/{len(DEMO_QUESTIONS)}]")
        run_agent(q, live_mode=live_mode)
        if i < len(DEMO_QUESTIONS):
            input("\nPress Enter for next question...")


def interactive(live_mode: bool = False):
    """Interactive mode."""
    print(f"\n{'='*70}\nDEBTSTACK AGENT ({'LIVE' if live_mode else 'OFFLINE'})\n{'='*70}")
    print("Ask about corporate structure, debt, or pricing. Type 'quit' to exit.\n")

    while True:
        try:
            q = input("You: ").strip()
            if not q:
                continue
            if q.lower() in ["quit", "exit", "q"]:
                break
            if q.lower() == "demo":
                demo(live_mode)
                continue
            run_agent(q, live_mode=live_mode)
        except KeyboardInterrupt:
            break
    print("Goodbye!")


def main():
    parser = argparse.ArgumentParser(description="DebtStack API Agent Demo")
    parser.add_argument("question", nargs="*", help="Question to ask")
    parser.add_argument("--live", action="store_true", help="Use live API")
    parser.add_argument("--demo", action="store_true", help="Run demo questions")
    args = parser.parse_args()

    if args.demo:
        demo(args.live)
    elif args.question:
        run_agent(" ".join(args.question), live_mode=args.live)
    else:
        interactive(args.live)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Credible API Agent Demo (Offline Version)

This demo shows how an AI agent uses the Credible API, but works offline
using the extracted JSON files in the results/ directory. No running API needed.

This is useful for:
- Demonstrating the integration without infrastructure
- Testing agent behavior with real extracted data
- Showing the value proposition to stakeholders

Usage:
    python demos/agent_demo_offline.py

    # Or with a specific question
    python demos/agent_demo_offline.py "What is Transocean's debt structure?"
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import anthropic

# Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
RESULTS_DIR = Path(__file__).parent.parent / "results"

# Tool definitions for Claude
TOOLS = [
    {
        "name": "get_company_structure",
        "description": """Get the corporate structure and hierarchy for a company, including all subsidiaries
and the debt instruments at each entity level. Returns the full entity tree with debt details at each node.
Available companies: AAPL, CRWV, RIG, ATUS""",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (AAPL, CRWV, RIG, or ATUS)"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_company_debt",
        "description": """Get all debt instruments for a company with full details including issuer,
seniority, security type, interest rates, maturity dates, and guarantors.
Available companies: AAPL, CRWV, RIG, ATUS""",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (AAPL, CRWV, RIG, or ATUS)"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "list_available_companies",
        "description": """List all companies available in the Credible database.""",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# System prompt
SYSTEM_PROMPT = """You are a credit analyst assistant with access to the Credible API, which provides
pre-computed corporate structure and debt data extracted from SEC filings.

When answering questions, use the available tools to fetch accurate data. The Credible API provides:

1. **Corporate Structure**: Entity hierarchy with parent-subsidiary relationships
2. **Debt Instruments**: Individual debt facilities with issuer, seniority, rates, maturity, guarantors

**Important notes:**
- Amounts are in CENTS (divide by 100 to get dollars, by 100000000 to get millions)
- Interest rates are in BASIS POINTS (divide by 100 to get percentage)
- Always cite the data source as "Credible API (extracted from SEC filings)"

Be concise but thorough. Format financial data clearly with proper dollar amounts and percentages.

Available companies: Apple (AAPL), CoreWeave (CRWV), Transocean (RIG), Altice USA (ATUS)"""


def load_extraction(ticker: str) -> Optional[dict]:
    """Load extraction data from results directory."""
    ticker_lower = ticker.lower()

    # Try different file patterns
    patterns = [
        f"{ticker_lower}_iterative.json",
        f"{ticker_lower}_extraction.json",
        f"{ticker_lower}.json",
    ]

    for pattern in patterns:
        file_path = RESULTS_DIR / pattern
        if file_path.exists():
            with open(file_path) as f:
                return json.load(f)

    return None


def format_as_structure_response(extraction: dict, ticker: str) -> dict:
    """Format extraction data as a structure API response."""
    entities = extraction.get("entities", [])
    debt = extraction.get("debt_instruments", [])

    # Build entity tree (simplified - just shows flat list with debt)
    def get_entity_debt(entity_name: str) -> list:
        return [d for d in debt if d.get("issuer_name") == entity_name]

    structure = {
        "company": {
            "ticker": ticker.upper(),
            "name": extraction.get("company_name", ticker.upper())
        },
        "entities": [
            {
                "name": e.get("name"),
                "type": e.get("entity_type"),
                "jurisdiction": e.get("jurisdiction"),
                "is_guarantor": e.get("is_guarantor", False),
                "is_borrower": e.get("is_borrower", False),
                "parent": e.get("owners", [{}])[0].get("parent_name") if e.get("owners") else None,
                "debt_instruments": get_entity_debt(e.get("name"))
            }
            for e in entities
        ],
        "total_entities": len(entities),
        "total_debt_instruments": len(debt)
    }

    return structure


def format_as_debt_response(extraction: dict, ticker: str) -> dict:
    """Format extraction data as a debt API response."""
    debt = extraction.get("debt_instruments", [])

    return {
        "company": {
            "ticker": ticker.upper(),
            "name": extraction.get("company_name", ticker.upper())
        },
        "debt_instruments": debt,
        "total_count": len(debt),
        "summary": {
            "total_outstanding_cents": sum(d.get("outstanding") or d.get("principal") or 0 for d in debt),
            "secured_count": len([d for d in debt if "secured" in (d.get("seniority") or "").lower()]),
            "unsecured_count": len([d for d in debt if "unsecured" in (d.get("seniority") or "").lower()]),
        }
    }


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool call using local data."""

    if tool_name == "list_available_companies":
        # Check what files exist
        companies = []
        ticker_names = {
            "aapl": "Apple Inc.",
            "crwv": "CoreWeave, Inc.",
            "rig": "Transocean Ltd.",
            "atus": "Altice USA, Inc."
        }

        for ticker, name in ticker_names.items():
            if load_extraction(ticker):
                companies.append({"ticker": ticker.upper(), "name": name})

        return json.dumps({
            "companies": companies,
            "total": len(companies)
        }, indent=2)

    ticker = tool_input.get("ticker", "").upper()
    extraction = load_extraction(ticker)

    if not extraction:
        return json.dumps({
            "error": f"Company {ticker} not found. Available: AAPL, CRWV, RIG, ATUS"
        })

    if tool_name == "get_company_structure":
        result = format_as_structure_response(extraction, ticker)
    elif tool_name == "get_company_debt":
        result = format_as_debt_response(extraction, ticker)
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    return json.dumps(result, indent=2, default=str)


def run_agent(question: str, verbose: bool = True) -> str:
    """Run the agent to answer a credit analysis question."""

    if not ANTHROPIC_API_KEY:
        return "Error: ANTHROPIC_API_KEY not set in environment"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": question}]

    if verbose:
        print(f"\n{'='*60}")
        print(f"Question: {question}")
        print(f"{'='*60}\n")

    # Initial call to Claude
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages
    )

    # Process tool calls in a loop
    while response.stop_reason == "tool_use":
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        tool_results = []

        for tool_use in tool_uses:
            if verbose:
                print(f"[Tool Call] {tool_use.name}({json.dumps(tool_use.input)})")

            result = execute_tool(tool_use.name, tool_use.input)

            if verbose:
                result_preview = result[:800] + "..." if len(result) > 800 else result
                print(f"[Tool Result]\n{result_preview}\n")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

    # Extract final text response
    final_response = ""
    for block in response.content:
        if hasattr(block, "text"):
            final_response += block.text

    if verbose:
        print(f"{'='*60}")
        print("Answer:")
        print(f"{'='*60}")
        print(final_response)

    return final_response


def demo():
    """Run demo with example questions."""

    print("\n" + "="*70)
    print("CREDIBLE API - AGENT INTEGRATION DEMO (OFFLINE)")
    print("="*70)
    print("""
This demo shows an AI agent using the Credible API to answer credit
analysis questions. It uses locally extracted data (no running API needed).

The agent will:
1. Understand your question about corporate structure or debt
2. Call the appropriate Credible API endpoint
3. Analyze the data and provide a clear answer

Available companies: AAPL, CRWV, RIG, ATUS
""")

    questions = [
        "What companies do you have data for?",
        "What is Transocean's corporate structure? Who are the main subsidiaries?",
        "Show me Transocean's debt instruments. What's their total debt and what are the largest facilities?",
        "Does Altice USA have any secured debt? Who issues it?",
    ]

    for i, q in enumerate(questions, 1):
        print(f"\n[Demo Question {i}/{len(questions)}]")
        run_agent(q, verbose=True)

        if i < len(questions):
            print("\n" + "-"*70)
            input("Press Enter for next question...")


def interactive():
    """Interactive mode."""

    print("\n" + "="*70)
    print("CREDIBLE API - INTERACTIVE MODE (OFFLINE)")
    print("="*70)
    print("""
Ask questions about corporate structure and debt.
Available: AAPL, CRWV, RIG, ATUS

Type 'demo' for example questions, 'quit' to exit.
""")

    while True:
        try:
            question = input("\nYou: ").strip()

            if not question:
                continue
            if question.lower() in ["quit", "exit", "q"]:
                print("Goodbye!")
                break
            if question.lower() == "demo":
                demo()
                continue

            run_agent(question, verbose=True)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        run_agent(question, verbose=True)
    else:
        interactive()


if __name__ == "__main__":
    main()

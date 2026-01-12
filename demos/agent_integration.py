#!/usr/bin/env python3
"""
Credible API Agent Integration Demo

This demo shows how an AI agent (Claude) can use the Credible API via function calling
to answer credit analysis questions. Instead of parsing SEC filings directly, the agent
gets instant access to pre-computed, quality-assured corporate structure and debt data.

Usage:
    python demos/agent_integration.py

    # Or with a specific question
    python demos/agent_integration.py "What is Transocean's debt structure?"

Requirements:
    - ANTHROPIC_API_KEY in environment
    - Credible API running at http://localhost:8000 (or set CREDIBLE_API_URL)
    - Company data loaded (run extract_iterative.py --save-db first)

Example questions:
    - "What subsidiaries does Apple have?"
    - "Show me Transocean's debt instruments"
    - "Does RIG have any secured debt?"
    - "What's the corporate structure of Altice USA?"
    - "Who guarantees CSC Holdings' debt?"
"""

import json
import os
import sys
from typing import Any

import anthropic
import httpx

# Configuration
CREDIBLE_API_URL = os.getenv("CREDIBLE_API_URL", "http://localhost:8000")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Tool definitions for Claude
TOOLS = [
    {
        "name": "get_company_structure",
        "description": """Get the corporate structure and hierarchy for a company, including all subsidiaries
and the debt instruments at each entity level. This is the primary tool for understanding
who owns what and where debt sits in the corporate structure. Returns the full entity tree
with debt details at each node.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., AAPL, RIG, ATUS)"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_company_debt",
        "description": """Get all debt instruments for a company with full details including issuer,
seniority, security type, interest rates, maturity dates, and guarantors. Use this when
you need detailed debt information without the full corporate structure.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., AAPL, RIG, ATUS)"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_company_overview",
        "description": """Get basic company information including name, sector, and summary metrics
like total debt, entity count, and structural subordination flags. Use this for a quick
overview before diving into details.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g., AAPL, RIG, ATUS)"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "list_available_companies",
        "description": """List all companies available in the Credible database with their tickers
and basic metrics. Use this to see what data is available or to find a company's ticker.""",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# System prompt for the agent
SYSTEM_PROMPT = """You are a credit analyst assistant with access to the Credible API, which provides
pre-computed corporate structure and debt data extracted from SEC filings.

When answering questions about companies, use the available tools to fetch accurate data rather than
relying on your training data. The Credible API provides:

1. **Corporate Structure**: Entity hierarchy showing parent-subsidiary relationships, including:
   - Entity types (holdco, opco, finco, SPV, subsidiary)
   - Ownership percentages
   - Consolidation methods
   - VIE status

2. **Debt Instruments**: Individual debt facilities with:
   - Issuer entity
   - Seniority (senior secured, senior unsecured, subordinated)
   - Security type (first lien, second lien, unsecured)
   - Interest rates (fixed or floating with spread)
   - Maturity dates
   - Guarantors
   - Outstanding amounts (in cents)

**Important notes:**
- Amounts are in CENTS (divide by 100 to get dollars)
- Interest rates are in BASIS POINTS (divide by 100 to get percentage)
- Use get_company_structure for questions about subsidiaries, ownership, or where debt sits
- Use get_company_debt for detailed debt instrument information
- Always cite the data source as "Credible API (extracted from SEC filings)"

Be concise but thorough. Format financial data clearly with proper dollar amounts and percentages."""


class CredibleClient:
    """Client for the Credible API."""

    def __init__(self, base_url: str = CREDIBLE_API_URL):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30.0)

    def get_company_structure(self, ticker: str) -> dict:
        """Get corporate structure with debt at each entity."""
        response = self.client.get(f"{self.base_url}/v1/companies/{ticker}/structure")
        response.raise_for_status()
        return response.json()

    def get_company_debt(self, ticker: str) -> dict:
        """Get all debt instruments."""
        response = self.client.get(f"{self.base_url}/v1/companies/{ticker}/debt")
        response.raise_for_status()
        return response.json()

    def get_company_overview(self, ticker: str) -> dict:
        """Get company overview."""
        response = self.client.get(f"{self.base_url}/v1/companies/{ticker}")
        response.raise_for_status()
        return response.json()

    def list_companies(self) -> dict:
        """List all available companies."""
        response = self.client.get(f"{self.base_url}/v1/companies")
        response.raise_for_status()
        return response.json()

    def close(self):
        self.client.close()


def execute_tool(credible: CredibleClient, tool_name: str, tool_input: dict) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if tool_name == "get_company_structure":
            result = credible.get_company_structure(tool_input["ticker"])
        elif tool_name == "get_company_debt":
            result = credible.get_company_debt(tool_input["ticker"])
        elif tool_name == "get_company_overview":
            result = credible.get_company_overview(tool_input["ticker"])
        elif tool_name == "list_available_companies":
            result = credible.list_companies()
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        return json.dumps(result, indent=2, default=str)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return json.dumps({"error": f"Company not found. Use list_available_companies to see available data."})
        return json.dumps({"error": f"API error: {e.response.status_code}"})
    except httpx.ConnectError:
        return json.dumps({"error": "Could not connect to Credible API. Is it running at " + CREDIBLE_API_URL + "?"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_agent(question: str, verbose: bool = True) -> str:
    """
    Run the agent to answer a credit analysis question.

    Args:
        question: The user's question about corporate structure or debt
        verbose: Whether to print intermediate steps

    Returns:
        The agent's final answer
    """
    if not ANTHROPIC_API_KEY:
        return "Error: ANTHROPIC_API_KEY not set"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    credible = CredibleClient()

    messages = [{"role": "user", "content": question}]

    if verbose:
        print(f"\n{'='*60}")
        print(f"Question: {question}")
        print(f"{'='*60}\n")

    try:
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
            # Find all tool use blocks
            tool_uses = [block for block in response.content if block.type == "tool_use"]

            # Build tool results
            tool_results = []
            for tool_use in tool_uses:
                if verbose:
                    print(f"[Tool Call] {tool_use.name}({json.dumps(tool_use.input)})")

                result = execute_tool(credible, tool_use.name, tool_use.input)

                if verbose:
                    # Print truncated result
                    result_preview = result[:500] + "..." if len(result) > 500 else result
                    print(f"[Tool Result] {result_preview}\n")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })

            # Continue conversation with tool results
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

    finally:
        credible.close()


def demo_conversation():
    """Run a demo conversation showing the agent's capabilities."""

    print("\n" + "="*70)
    print("CREDIBLE API - AGENT INTEGRATION DEMO")
    print("="*70)
    print("""
This demo shows how an AI agent can use the Credible API to answer
credit analysis questions using function calling.

The agent has access to:
- get_company_structure: Corporate hierarchy with debt at each entity
- get_company_debt: All debt instruments with full details
- get_company_overview: Basic company info and metrics
- list_available_companies: See what data is available

Let's see it in action!
""")

    # Demo questions
    questions = [
        "What companies do you have data for?",
        "Show me Apple's corporate structure",
        "What debt instruments does Transocean have? Focus on the secured debt.",
    ]

    for q in questions:
        run_agent(q, verbose=True)
        print("\n" + "-"*70 + "\n")
        input("Press Enter for next question...")


def interactive_mode():
    """Run in interactive mode where user can ask questions."""

    print("\n" + "="*70)
    print("CREDIBLE API - INTERACTIVE MODE")
    print("="*70)
    print("""
Ask questions about corporate structure and debt. The AI agent will
use the Credible API to fetch accurate, up-to-date data.

Type 'quit' or 'exit' to end the session.
Type 'demo' to see example questions.
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
                demo_conversation()
                continue

            run_agent(question, verbose=True)

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break


def main():
    """Main entry point."""

    if len(sys.argv) > 1:
        # Single question mode
        question = " ".join(sys.argv[1:])
        run_agent(question, verbose=True)
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()

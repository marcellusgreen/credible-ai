#!/usr/bin/env python3
"""
DebtStack API Demo

Shows sample API responses for developers evaluating the API.
Works offline using local JSON files - no running server needed.

Usage:
    python demos/api_demo.py              # Interactive menu
    python demos/api_demo.py --all        # Show all endpoints
    python demos/api_demo.py --endpoint structure
    python demos/api_demo.py --ticker AAPL --endpoint debt
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

RESULTS_DIR = Path(__file__).parent.parent / "results"

DEMO_COMPANIES = {
    "RIG": "Transocean Ltd.",
    "ATUS": "Altice USA, Inc.",
    "AAPL": "Apple Inc.",
    "CRWV": "CoreWeave, Inc.",
}

# Sample pricing data for demo (in production this comes from the API)
SAMPLE_PRICING = {
    "RIG": [
        {"name": "8.00% Senior Notes due 2027", "price": 96.50, "ytm": 8.92, "spread_bps": 458},
        {"name": "11.50% Senior Secured Notes due 2027", "price": 102.25, "ytm": 10.85, "spread_bps": 651},
        {"name": "8.75% Senior Notes due 2030", "price": 91.75, "ytm": 10.15, "spread_bps": 581},
    ],
    "ATUS": [
        {"name": "5.50% Senior Notes due 2028", "price": 88.50, "ytm": 8.75, "spread_bps": 441},
        {"name": "5.875% Senior Secured Notes due 2027", "price": 95.25, "ytm": 7.25, "spread_bps": 291},
    ],
    "AAPL": [
        {"name": "3.25% Notes due 2029", "price": 98.50, "ytm": 3.45, "spread_bps": 45},
        {"name": "2.65% Notes due 2030", "price": 95.75, "ytm": 3.25, "spread_bps": 35},
    ],
}


def load_extraction(ticker: str) -> Optional[dict]:
    """Load extraction data from results directory."""
    ticker_lower = ticker.lower()
    for pattern in [f"{ticker_lower}_iterative.json", f"{ticker_lower}_extraction.json", f"{ticker_lower}.json"]:
        file_path = RESULTS_DIR / pattern
        if file_path.exists():
            with open(file_path) as f:
                return json.load(f)
    return None


def format_cents(cents: Optional[int]) -> str:
    """Format cents as human-readable dollar amount."""
    if cents is None:
        return "N/A"
    dollars = cents / 100
    if dollars >= 1_000_000_000:
        return f"${dollars / 1_000_000_000:.1f}B"
    if dollars >= 1_000_000:
        return f"${dollars / 1_000_000:.1f}M"
    return f"${dollars:,.0f}"


def print_json(data: dict, title: str):
    """Pretty print JSON response with header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    print(json.dumps(data, indent=2, default=str))


def get_company_header(ticker: str, extraction: dict) -> dict:
    """Build standard company header for responses."""
    return {
        "ticker": ticker.upper(),
        "name": extraction.get("company_name", ticker.upper()),
    }


def demo_list_companies():
    """GET /v1/companies"""
    companies = [
        {"ticker": ticker, "name": name}
        for ticker, name in DEMO_COMPANIES.items()
        if load_extraction(ticker)
    ]
    response = {"data": {"companies": companies, "total": len(companies)}}
    print_json(response, "GET /v1/companies")
    print(f"\n  -> {len(companies)} companies available")


def demo_company_overview(ticker: str):
    """GET /v1/companies/{ticker}"""
    extraction = load_extraction(ticker)
    if not extraction:
        print(f"Company {ticker} not found")
        return

    entities = extraction.get("entities", [])
    debt = extraction.get("debt_instruments", [])
    total_debt = sum(d.get("outstanding") or d.get("principal") or 0 for d in debt)

    response = {
        "data": {
            "company": get_company_header(ticker, extraction),
            "summary": {
                "total_entities": len(entities),
                "total_debt_instruments": len(debt),
                "total_debt_cents": total_debt,
            }
        }
    }
    print_json(response, f"GET /v1/companies/{ticker.upper()}")
    print(f"\n  -> {len(entities)} entities, {len(debt)} debt instruments, {format_cents(total_debt)} total debt")


def demo_company_structure(ticker: str):
    """GET /v1/companies/{ticker}/structure"""
    extraction = load_extraction(ticker)
    if not extraction:
        print(f"Company {ticker} not found")
        return

    entities = extraction.get("entities", [])
    debt = extraction.get("debt_instruments", [])

    # Build simplified structure (first 5 entities)
    structure_preview = [
        {
            "name": e.get("name"),
            "type": e.get("entity_type"),
            "jurisdiction": e.get("jurisdiction"),
            "is_guarantor": e.get("is_guarantor", False),
            "debt_count": len([d for d in debt if d.get("issuer_name") == e.get("name")]),
        }
        for e in entities[:5]
    ]

    response = {
        "data": {
            "company": get_company_header(ticker, extraction),
            "entities": structure_preview,
            "total_entities": len(entities),
            "_note": f"Showing 5 of {len(entities)} entities"
        }
    }
    print_json(response, f"GET /v1/companies/{ticker.upper()}/structure")

    # Summary counts
    holdcos = sum(1 for e in entities if e.get("entity_type") == "holdco")
    opcos = sum(1 for e in entities if e.get("entity_type") == "opco")
    guarantors = sum(1 for e in entities if e.get("is_guarantor"))
    print(f"\n  -> {holdcos} holdco, {opcos} opco, {guarantors} guarantors")


def demo_company_debt(ticker: str):
    """GET /v1/companies/{ticker}/debt"""
    extraction = load_extraction(ticker)
    if not extraction:
        print(f"Company {ticker} not found")
        return

    debt = extraction.get("debt_instruments", [])
    total_debt = sum(d.get("outstanding") or d.get("principal") or 0 for d in debt)
    secured_count = sum(1 for d in debt if "secured" in (d.get("seniority") or "").lower())

    debt_preview = [
        {
            "name": d.get("name"),
            "seniority": d.get("seniority"),
            "outstanding_cents": d.get("outstanding") or d.get("principal"),
            "interest_rate_bps": d.get("interest_rate"),
            "maturity_date": d.get("maturity_date"),
            "issuer": d.get("issuer_name"),
        }
        for d in debt[:5]
    ]

    response = {
        "data": {
            "company": get_company_header(ticker, extraction),
            "debt_instruments": debt_preview,
            "summary": {
                "total_count": len(debt),
                "total_outstanding_cents": total_debt,
                "secured_count": secured_count,
                "unsecured_count": len(debt) - secured_count,
            },
            "_note": f"Showing 5 of {len(debt)} instruments"
        }
    }
    print_json(response, f"GET /v1/companies/{ticker.upper()}/debt")
    print(f"\n  -> {len(debt)} instruments, {format_cents(total_debt)} total, {secured_count} secured")


def demo_maturity_waterfall(ticker: str):
    """GET /v1/companies/{ticker}/maturity-waterfall"""
    extraction = load_extraction(ticker)
    if not extraction:
        print(f"Company {ticker} not found")
        return

    debt = extraction.get("debt_instruments", [])
    current_year = datetime.now().year

    # Group by year
    by_year = defaultdict(lambda: {"amount_cents": 0, "count": 0})
    for d in debt:
        mat_date = d.get("maturity_date")
        if mat_date:
            try:
                year = int(mat_date[:4])
                by_year[year]["amount_cents"] += d.get("outstanding") or d.get("principal") or 0
                by_year[year]["count"] += 1
            except (ValueError, TypeError):
                pass

    # Build waterfall for next 8 years
    waterfall = []
    total_debt = 0
    for year in range(current_year, current_year + 8):
        data = by_year.get(year, {"amount_cents": 0, "count": 0})
        total_debt += data["amount_cents"]
        waterfall.append({
            "year": year,
            "amount_cents": data["amount_cents"],
            "instrument_count": data["count"],
        })

    response = {
        "data": {
            "company": get_company_header(ticker, extraction),
            "waterfall": waterfall,
            "summary": {
                "total_debt_cents": total_debt,
                "total_instruments": len(debt),
            }
        }
    }
    print_json(response, f"GET /v1/companies/{ticker.upper()}/maturity-waterfall")

    # Visual waterfall
    print("\n  Maturity Waterfall:")
    max_amt = max((w["amount_cents"] for w in waterfall), default=1) or 1
    for w in waterfall:
        bar_len = int(40 * w["amount_cents"] / max_amt)
        bar = "#" * bar_len
        print(f"  {w['year']}: {bar} {format_cents(w['amount_cents'])} ({w['instrument_count']} instruments)")


def demo_pricing(ticker: str):
    """GET /v1/companies/{ticker}/pricing"""
    extraction = load_extraction(ticker)
    if not extraction:
        print(f"Company {ticker} not found")
        return

    pricing_data = SAMPLE_PRICING.get(ticker.upper(), [])
    bonds = [
        {
            "name": p["name"],
            "pricing": {
                "last_price": p["price"],
                "ytm_pct": p["ytm"],
                "spread_to_treasury_bps": p["spread_bps"],
                "price_source": "estimated",
            }
        }
        for p in pricing_data
    ]

    response = {
        "data": {
            "company": get_company_header(ticker, extraction),
            "bonds": bonds,
            "summary": {"bonds_with_pricing": len(bonds)},
        }
    }
    print_json(response, f"GET /v1/companies/{ticker.upper()}/pricing")

    if bonds:
        print("\n  Bond Pricing Summary:")
        for b in bonds:
            p = b["pricing"]
            print(f"  - {b['name'][:40]}")
            print(f"    Price: {p['last_price']:.2f}  |  YTM: {p['ytm_pct']:.2f}%  |  Spread: +{p['spread_to_treasury_bps']}bps")


def demo_search_debt():
    """GET /v1/search/debt?min_spread_bps=400"""
    # Bonds with spread > 400bps from sample data
    results = [
        {"ticker": "RIG", "name": "8.00% Senior Notes due 2027", "spread_bps": 458, "ytm": 8.92},
        {"ticker": "RIG", "name": "11.50% Senior Secured Notes due 2027", "spread_bps": 651, "ytm": 10.85},
        {"ticker": "RIG", "name": "8.75% Senior Notes due 2030", "spread_bps": 581, "ytm": 10.15},
        {"ticker": "ATUS", "name": "5.50% Senior Notes due 2028", "spread_bps": 441, "ytm": 8.75},
    ]

    response = {
        "data": {
            "filters": {"min_spread_bps": 400, "has_pricing": True},
            "results": [
                {
                    "company_ticker": r["ticker"],
                    "name": r["name"],
                    "pricing": {"ytm_pct": r["ytm"], "spread_to_treasury_bps": r["spread_bps"]},
                }
                for r in results
            ],
            "total": len(results),
        }
    }
    print_json(response, "GET /v1/search/debt?min_spread_bps=400&has_pricing=true")
    print(f"\n  -> {len(results)} bonds with spread > 400bps")


# Endpoint dispatch mapping
ENDPOINT_HANDLERS = {
    "companies": lambda _: demo_list_companies(),
    "overview": demo_company_overview,
    "structure": demo_company_structure,
    "debt": demo_company_debt,
    "waterfall": demo_maturity_waterfall,
    "pricing": demo_pricing,
    "search": lambda _: demo_search_debt(),
}


def interactive_menu():
    """Interactive demo menu."""
    print("\n" + "="*70)
    print("  DEBTSTACK API DEMO")
    print("="*70)
    print("""
  This demo shows sample API responses from local data.
  No running server needed.

  Available endpoints:
    1. GET /v1/companies              - List all companies
    2. GET /v1/companies/{ticker}     - Company overview
    3. GET /v1/companies/{ticker}/structure    - Corporate hierarchy
    4. GET /v1/companies/{ticker}/debt         - Debt instruments
    5. GET /v1/companies/{ticker}/maturity-waterfall  - Maturity waterfall
    6. GET /v1/companies/{ticker}/pricing      - Bond pricing
    7. GET /v1/search/debt            - Search bonds by criteria

  Available tickers: RIG, ATUS, AAPL, CRWV
""")

    menu_handlers = {
        "1": ("companies", False),
        "2": ("overview", True),
        "3": ("structure", True),
        "4": ("debt", True),
        "5": ("waterfall", True),
        "6": ("pricing", True),
        "7": ("search", False),
    }

    while True:
        try:
            choice = input("\n  Enter endpoint number (1-7) or 'q' to quit: ").strip()

            if choice.lower() in ['q', 'quit', 'exit']:
                print("  Goodbye!")
                break

            if choice not in menu_handlers:
                print("  Invalid choice. Enter 1-7 or 'q' to quit.")
                continue

            endpoint, needs_ticker = menu_handlers[choice]

            if needs_ticker:
                ticker = input("  Enter ticker (RIG, ATUS, AAPL, CRWV): ").strip().upper()
                if ticker not in DEMO_COMPANIES:
                    print(f"  Unknown ticker. Available: {', '.join(DEMO_COMPANIES.keys())}")
                    continue
                ENDPOINT_HANDLERS[endpoint](ticker)
            else:
                ENDPOINT_HANDLERS[endpoint](None)

        except KeyboardInterrupt:
            print("\n  Goodbye!")
            break


def run_all_demos(ticker: str = "RIG"):
    """Run all demo endpoints."""
    print("\n" + "="*70)
    print(f"  DEBTSTACK API DEMO - ALL ENDPOINTS (ticker: {ticker})")
    print("="*70)

    demo_list_companies()
    demo_company_overview(ticker)
    demo_company_structure(ticker)
    demo_company_debt(ticker)
    demo_maturity_waterfall(ticker)
    demo_pricing(ticker)
    demo_search_debt()

    print("\n" + "="*70)
    print("  Demo complete. 22 API endpoints available.")
    print("  See full docs at: https://debtstack.ai/docs")
    print("="*70)


def main():
    parser = argparse.ArgumentParser(
        description="DebtStack API Demo - Show sample API responses",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="Run all demo endpoints")
    parser.add_argument("--ticker", default="RIG", help="Company ticker (default: RIG)")
    parser.add_argument("--endpoint", choices=list(ENDPOINT_HANDLERS.keys()), help="Specific endpoint to demo")

    args = parser.parse_args()

    if args.all:
        run_all_demos(args.ticker.upper())
    elif args.endpoint:
        handler = ENDPOINT_HANDLERS[args.endpoint]
        if args.endpoint in ["companies", "search"]:
            handler(None)
        else:
            handler(args.ticker.upper())
    else:
        interactive_menu()


if __name__ == "__main__":
    main()

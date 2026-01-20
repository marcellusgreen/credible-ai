"""
Get specific demo data for the selected companies.
"""

import asyncio
import json
import os
import sys
from decimal import Decimal
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def get_session():
    database_url = os.getenv("DATABASE_URL")
    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


async def get_company_full_structure(session: AsyncSession, ticker: str):
    """Get complete structure for a company by ticker or CIK."""
    # First find the company
    if ticker.startswith("0"):
        # It's a CIK
        company_query = text("SELECT id, ticker, name, sector, cik FROM companies WHERE cik = :cik")
        company_result = await session.execute(company_query, {"cik": ticker})
    else:
        company_query = text("SELECT id, ticker, name, sector, cik FROM companies WHERE ticker = :ticker")
        company_result = await session.execute(company_query, {"ticker": ticker})

    company = company_result.fetchone()
    if not company:
        print(f"Company not found: {ticker}")
        return None

    company_id = company[0]
    print(f"\nCompany: {company[2]} ({company[1]})")
    print(f"Sector: {company[3]}")

    # Get entities
    entities_query = text("""
        SELECT id, name, entity_type, jurisdiction, parent_id, is_guarantor, is_borrower, structure_tier
        FROM entities WHERE company_id = :company_id
        ORDER BY structure_tier NULLS FIRST, name
    """)
    entities_result = await session.execute(entities_query, {"company_id": company_id})
    entities = entities_result.fetchall()

    # Get debt
    debt_query = text("""
        SELECT id, name, issuer_id, instrument_type, seniority, security_type, outstanding,
               interest_rate, rate_type, spread_bps, benchmark, maturity_date
        FROM debt_instruments WHERE company_id = :company_id
        ORDER BY outstanding DESC NULLS LAST
    """)
    debt_result = await session.execute(debt_query, {"company_id": company_id})
    debt = debt_result.fetchall()

    # Get guarantees
    guarantees_query = text("""
        SELECT g.debt_instrument_id, g.guarantor_id, e.name
        FROM guarantees g
        JOIN entities e ON g.guarantor_id = e.id
        JOIN debt_instruments d ON g.debt_instrument_id = d.id
        WHERE d.company_id = :company_id
    """)
    guarantees_result = await session.execute(guarantees_query, {"company_id": company_id})
    guarantees = guarantees_result.fetchall()

    return {
        "company": {
            "id": str(company[0]),
            "ticker": company[1],
            "name": company[2],
            "sector": company[3],
            "cik": company[4]
        },
        "entities": [
            {
                "id": str(e[0]),
                "name": e[1],
                "type": e[2],
                "jurisdiction": e[3],
                "parent_id": str(e[4]) if e[4] else None,
                "is_guarantor": e[5],
                "is_borrower": e[6],
                "structure_tier": e[7]
            }
            for e in entities
        ],
        "debt_instruments": [
            {
                "id": str(d[0]),
                "name": d[1],
                "issuer_id": str(d[2]),
                "instrument_type": d[3],
                "seniority": d[4],
                "security_type": d[5],
                "outstanding_cents": d[6],
                "outstanding_formatted": f"${(d[6] or 0) / 100_000_000:,.0f}M",
                "interest_rate_bps": d[7],
                "interest_rate_formatted": f"{(d[7] or 0) / 100:.2f}%",
                "rate_type": d[8],
                "spread_bps": d[9],
                "benchmark": d[10],
                "maturity_date": d[11].isoformat() if d[11] else None
            }
            for d in debt
        ],
        "guarantees": [
            {
                "debt_instrument_id": str(g[0]),
                "guarantor_id": str(g[1]),
                "guarantor_name": g[2]
            }
            for g in guarantees
        ]
    }


async def get_structural_metrics(session: AsyncSession, ticker: str):
    """Get structural subordination metrics for a company."""
    # Handle CIK vs ticker
    if ticker.startswith("0"):
        where_clause = "c.cik = :identifier"
    else:
        where_clause = "c.ticker = :identifier"

    query = text(f"""
        WITH entity_debt AS (
            SELECT
                e.entity_type,
                COALESCE(SUM(d.outstanding), 0) as debt_at_level,
                COUNT(DISTINCT d.id) as instrument_count
            FROM companies c
            JOIN entities e ON c.id = e.company_id
            LEFT JOIN debt_instruments d ON e.id = d.issuer_id
            WHERE {where_clause}
            GROUP BY e.entity_type
        ),
        guarantee_stats AS (
            SELECT
                COUNT(DISTINCT g.debt_instrument_id) as guaranteed_count,
                COUNT(DISTINCT d.id) as total_count
            FROM companies c
            JOIN debt_instruments d ON c.id = d.company_id
            LEFT JOIN guarantees g ON d.id = g.debt_instrument_id
            WHERE {where_clause}
        )
        SELECT
            (SELECT COALESCE(SUM(debt_at_level), 0) FROM entity_debt WHERE entity_type = 'holdco') as holdco_debt,
            (SELECT COALESCE(SUM(debt_at_level), 0) FROM entity_debt WHERE entity_type IN ('opco', 'subsidiary', 'finco')) as opco_debt,
            (SELECT COALESCE(SUM(debt_at_level), 0) FROM entity_debt) as total_debt,
            gs.guaranteed_count,
            gs.total_count
        FROM guarantee_stats gs
    """)

    result = await session.execute(query, {"identifier": ticker})
    row = result.fetchone()

    if row:
        total_debt = row[2] or 1
        return {
            "holdco_debt": row[0] or 0,
            "opco_debt": row[1] or 0,
            "total_debt": total_debt,
            "holdco_debt_pct": round(100 * (row[0] or 0) / total_debt, 1) if total_debt > 0 else 0,
            "guarantee_coverage_pct": round(100 * (row[3] or 0) / (row[4] or 1), 1) if row[4] else 0
        }
    return None


async def main():
    session, engine = await get_session()

    try:
        # Demo 1 candidates - companies with interesting hierarchy
        demo1_candidates = ["CHTR", "RIG", "LUMN", "FYBR"]

        print("=" * 60)
        print("DEMO 1 CANDIDATES - Looking for best corporate structure")
        print("=" * 60)

        for ticker in demo1_candidates:
            data = await get_company_full_structure(session, ticker)
            if data:
                print(f"  {data['company']['ticker']}: {len(data['entities'])} entities, {len(data['debt_instruments'])} debt")

                # Count entity types
                types = {}
                for e in data["entities"]:
                    types[e["type"]] = types.get(e["type"], 0) + 1
                print(f"    Entity types: {types}")

                # Save the data
                filename = f"results/demo1_candidate_{ticker}.json"
                with open(filename, "w") as f:
                    json.dump(data, f, indent=2, default=decimal_default)
                print(f"    Saved to {filename}")

        # Demo 2 - Better selection for structural subordination
        print("\n" + "=" * 60)
        print("DEMO 2 - Structural Subordination Analysis")
        print("=" * 60)

        # Better candidates based on the console output:
        # HIGH: Companies with debt at holdco AND low guarantee coverage
        # MEDIUM: Mixed placement
        # LOW: Debt at opco with good guarantees

        demo2_picks = {
            "HIGH": ["CHTR", "DISH"],  # High holdco debt, lower guarantees
            "MEDIUM": ["DAL", "FYBR"],  # Mixed
            "LOW": ["HCA", "RIG", "M"]  # Debt at opco level
        }

        demo2_final = []

        for risk_level, tickers in demo2_picks.items():
            print(f"\n{risk_level} RISK candidates:")
            for ticker in tickers:
                metrics = await get_structural_metrics(session, ticker)
                if metrics:
                    print(f"  {ticker}: holdco={metrics['holdco_debt_pct']:.1f}%, guarantee={metrics['guarantee_coverage_pct']:.1f}%")
                    print(f"    Total debt: ${metrics['total_debt'] / 100_000_000_000:.1f}B")

        # Final selection based on best contrast
        final_picks = ["CHTR", "DAL", "HCA"]  # HIGH, MEDIUM, LOW

        print("\n" + "=" * 60)
        print("FINAL DEMO 2 SELECTION")
        print("=" * 60)

        demo2_companies = []
        risk_labels = ["HIGH", "MEDIUM", "LOW"]

        for i, ticker in enumerate(final_picks):
            data = await get_company_full_structure(session, ticker)
            metrics = await get_structural_metrics(session, ticker)

            if data and metrics:
                risk_level = risk_labels[i]
                company_data = {
                    "ticker": data["company"]["ticker"],
                    "name": data["company"]["name"],
                    "sector": data["company"]["sector"],
                    "risk_level": risk_level,
                    "total_entities": len(data["entities"]),
                    "total_debt_instruments": len(data["debt_instruments"]),
                    "total_debt_cents": metrics["total_debt"],
                    "total_debt_formatted": f"${metrics['total_debt'] / 100_000_000_000:.1f}B",
                    "holdco_debt_cents": metrics["holdco_debt"],
                    "holdco_debt_formatted": f"${metrics['holdco_debt'] / 100_000_000_000:.1f}B",
                    "opco_debt_cents": metrics["opco_debt"],
                    "opco_debt_formatted": f"${metrics['opco_debt'] / 100_000_000_000:.1f}B",
                    "holdco_debt_pct": metrics["holdco_debt_pct"],
                    "opco_debt_pct": round(100 - metrics["holdco_debt_pct"], 1),
                    "guarantee_coverage_pct": metrics["guarantee_coverage_pct"],
                }

                # Generate key findings
                if risk_level == "HIGH":
                    company_data["key_findings"] = [
                        f"{metrics['holdco_debt_pct']:.0f}% of debt at HoldCo level",
                        f"Only {metrics['guarantee_coverage_pct']:.0f}% guarantee coverage",
                        "High structural subordination risk for unsecured creditors"
                    ]
                elif risk_level == "MEDIUM":
                    company_data["key_findings"] = [
                        f"Mixed debt placement ({metrics['holdco_debt_pct']:.0f}% HoldCo, {100-metrics['holdco_debt_pct']:.0f}% OpCo)",
                        f"Moderate {metrics['guarantee_coverage_pct']:.0f}% guarantee coverage",
                        "Some structural subordination exposure"
                    ]
                else:
                    company_data["key_findings"] = [
                        f"Most debt at operating company level ({100-metrics['holdco_debt_pct']:.0f}%)",
                        f"Strong {metrics['guarantee_coverage_pct']:.0f}% guarantee coverage",
                        "Limited structural subordination risk"
                    ]

                demo2_companies.append(company_data)
                print(f"\n{risk_level}: {data['company']['name']} ({data['company']['ticker']})")
                print(f"  HoldCo debt: {metrics['holdco_debt_pct']:.1f}%")
                print(f"  Guarantee coverage: {metrics['guarantee_coverage_pct']:.1f}%")

        # Save demo 2 final data
        demo2_output = {"companies": demo2_companies}
        with open("results/demo2_final.json", "w") as f:
            json.dump(demo2_output, f, indent=2, default=decimal_default)
        print("\nSaved to results/demo2_final.json")

        # Demo 3 - Covenant data (illustrative since we don't have real covenants)
        print("\n" + "=" * 60)
        print("DEMO 3 - Covenant Monitoring (Illustrative)")
        print("=" * 60)

        # Use real companies but illustrative covenant thresholds
        covenant_companies = [
            {
                "ticker": "AAL",
                "name": "American Airlines Group Inc.",
                "sector": "Airlines",
                "covenant_type": "Maximum Leverage Ratio",
                "covenant_threshold": 6.0,
                "current_leverage": 5.8,  # Illustrative
                "headroom": 0.2,
                "headroom_pct": 3.3,
                "status": "WARNING",
                "notes": "Near covenant threshold - requires monitoring"
            },
            {
                "ticker": "DAL",
                "name": "Delta Air Lines, Inc.",
                "sector": "Airlines",
                "covenant_type": "Maximum Leverage Ratio",
                "covenant_threshold": 5.5,
                "current_leverage": 4.2,  # Illustrative
                "headroom": 1.3,
                "headroom_pct": 31.0,
                "status": "SAFE",
                "notes": "Comfortable headroom to covenant"
            },
            {
                "ticker": "CCL",
                "name": "Carnival Corporation & plc",
                "sector": "Cruises",
                "covenant_type": "Minimum Interest Coverage",
                "covenant_threshold": 2.0,
                "current_coverage": 3.5,  # Illustrative
                "headroom": 1.5,
                "headroom_pct": 75.0,
                "status": "SAFE",
                "notes": "Strong coverage ratio"
            }
        ]

        demo3_output = {
            "companies": covenant_companies,
            "note": "Current leverage/coverage ratios are illustrative. Covenant thresholds are typical for these industries."
        }

        with open("results/demo3_covenants.json", "w") as f:
            json.dump(demo3_output, f, indent=2, default=decimal_default)
        print("Saved to results/demo3_covenants.json")
        print("\nNote: Demo 3 uses illustrative current ratios since we don't extract quarterly financials.")

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print("""
Files generated:
  - results/demo1_candidate_CHTR.json  (10 entities, good hierarchy)
  - results/demo1_candidate_RIG.json   (Transocean - offshore driller)
  - results/demo1_candidate_LUMN.json  (Lumen - telecom)
  - results/demo1_candidate_FYBR.json  (Frontier - telecom)
  - results/demo2_final.json           (3 companies: HIGH/MEDIUM/LOW risk)
  - results/demo3_covenants.json       (Illustrative covenant monitoring)

Recommendations:
  Demo 1: Use CHTR (Charter Communications) - clean 10 entity hierarchy with 16 debt instruments
  Demo 2: Use CHTR (HIGH), DAL (MEDIUM), HCA (LOW) - clear contrast in structural sub
  Demo 3: Use illustrative data since we don't have real current leverage ratios
""")

    finally:
        await session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

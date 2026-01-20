"""
Extract real data from database for DebtStack landing page demo visualizations.

Usage:
    python scripts/extract_demo_data.py
"""

import asyncio
import json
import os
import sys
from decimal import Decimal
from datetime import date

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

load_dotenv()


def decimal_default(obj):
    """JSON encoder for Decimal and date objects."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def get_session():
    """Create database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


async def get_all_companies(session: AsyncSession):
    """Get overview of all companies in database."""
    query = text("""
        SELECT
            c.ticker,
            c.name,
            c.sector,
            COUNT(DISTINCT e.id) as entity_count,
            COUNT(DISTINCT d.id) as debt_count,
            SUM(d.outstanding) as total_debt
        FROM companies c
        LEFT JOIN entities e ON c.id = e.company_id
        LEFT JOIN debt_instruments d ON c.id = d.company_id
        GROUP BY c.ticker, c.name, c.sector
        HAVING COUNT(DISTINCT d.id) > 0
        ORDER BY SUM(d.outstanding) DESC NULLS LAST
    """)
    result = await session.execute(query)
    return result.fetchall()


async def get_company_structure(session: AsyncSession, ticker: str):
    """Get full structure for a company."""
    # Get company info
    company_query = text("""
        SELECT id, ticker, name, sector, cik
        FROM companies
        WHERE ticker = :ticker
    """)
    company_result = await session.execute(company_query, {"ticker": ticker})
    company = company_result.fetchone()

    if not company:
        return None

    company_id = company[0]

    # Get entities
    entities_query = text("""
        SELECT
            e.id,
            e.name,
            e.entity_type,
            e.jurisdiction,
            e.parent_id,
            e.is_guarantor,
            e.is_borrower,
            e.is_restricted,
            e.structure_tier,
            e.ownership_pct
        FROM entities e
        WHERE e.company_id = :company_id
        ORDER BY e.structure_tier NULLS FIRST, e.name
    """)
    entities_result = await session.execute(entities_query, {"company_id": company_id})
    entities = entities_result.fetchall()

    # Get debt instruments
    debt_query = text("""
        SELECT
            d.id,
            d.name,
            d.issuer_id,
            d.instrument_type,
            d.seniority,
            d.security_type,
            d.outstanding,
            d.commitment,
            d.currency,
            d.interest_rate,
            d.rate_type,
            d.spread_bps,
            d.benchmark,
            d.maturity_date
        FROM debt_instruments d
        WHERE d.company_id = :company_id
        ORDER BY d.outstanding DESC NULLS LAST
    """)
    debt_result = await session.execute(debt_query, {"company_id": company_id})
    debt_instruments = debt_result.fetchall()

    # Get guarantees
    guarantees_query = text("""
        SELECT
            g.debt_instrument_id,
            g.guarantor_id,
            e.name as guarantor_name,
            g.guarantee_type
        FROM guarantees g
        JOIN entities e ON g.guarantor_id = e.id
        JOIN debt_instruments d ON g.debt_instrument_id = d.id
        WHERE d.company_id = :company_id
    """)
    guarantees_result = await session.execute(guarantees_query, {"company_id": company_id})
    guarantees = guarantees_result.fetchall()

    return {
        "company": company,
        "entities": entities,
        "debt_instruments": debt_instruments,
        "guarantees": guarantees
    }


async def get_structural_sub_analysis(session: AsyncSession):
    """Calculate structural subordination metrics for all companies."""
    query = text("""
        WITH entity_debt AS (
            SELECT
                c.id as company_id,
                c.ticker,
                c.name,
                c.sector,
                e.entity_type,
                COALESCE(SUM(d.outstanding), 0) as debt_at_level
            FROM companies c
            LEFT JOIN entities e ON c.id = e.company_id
            LEFT JOIN debt_instruments d ON e.id = d.issuer_id
            GROUP BY c.id, c.ticker, c.name, c.sector, e.entity_type
        ),
        company_totals AS (
            SELECT
                company_id,
                ticker,
                name,
                sector,
                SUM(debt_at_level) as total_debt,
                SUM(CASE WHEN entity_type = 'holdco' THEN debt_at_level ELSE 0 END) as holdco_debt,
                SUM(CASE WHEN entity_type IN ('opco', 'subsidiary') THEN debt_at_level ELSE 0 END) as opco_debt
            FROM entity_debt
            GROUP BY company_id, ticker, name, sector
            HAVING SUM(debt_at_level) > 0
        ),
        guarantee_stats AS (
            SELECT
                c.id as company_id,
                COUNT(DISTINCT g.debt_instrument_id) as guaranteed_instruments,
                COUNT(DISTINCT d.id) as total_instruments
            FROM companies c
            LEFT JOIN debt_instruments d ON c.id = d.company_id
            LEFT JOIN guarantees g ON d.id = g.debt_instrument_id
            GROUP BY c.id
        ),
        entity_counts AS (
            SELECT
                c.id as company_id,
                COUNT(DISTINCT e.id) as total_entities,
                COUNT(DISTINCT CASE WHEN e.is_guarantor THEN e.id END) as guarantor_count
            FROM companies c
            LEFT JOIN entities e ON c.id = e.company_id
            GROUP BY c.id
        )
        SELECT
            ct.ticker,
            ct.name,
            ct.sector,
            ec.total_entities,
            ec.guarantor_count,
            ct.total_debt,
            ct.holdco_debt,
            ct.opco_debt,
            ROUND(100.0 * ct.holdco_debt / NULLIF(ct.total_debt, 0), 1) as holdco_debt_pct,
            CASE
                WHEN gs.total_instruments > 0
                THEN ROUND(100.0 * gs.guaranteed_instruments / gs.total_instruments, 1)
                ELSE 0
            END as guarantee_coverage_pct
        FROM company_totals ct
        JOIN guarantee_stats gs ON ct.company_id = gs.company_id
        JOIN entity_counts ec ON ct.company_id = ec.company_id
        ORDER BY
            ROUND(100.0 * ct.holdco_debt / NULLIF(ct.total_debt, 0), 1) DESC NULLS LAST
    """)
    result = await session.execute(query)
    return result.fetchall()


async def get_companies_with_best_hierarchy(session: AsyncSession, min_tiers: int = 3):
    """Find companies with the deepest/most interesting hierarchies."""
    # Returns: ticker, name, sector, entity_count, tier_count, max_tier, holdco_count, opco_count, sub_count, debt_count
    query = text("""
        WITH hierarchy_depth AS (
            SELECT
                c.ticker,
                c.name,
                c.sector,
                COUNT(DISTINCT e.id)::int as entity_count,
                COUNT(DISTINCT e.structure_tier)::int as tier_count,
                MAX(e.structure_tier)::int as max_tier,
                COUNT(DISTINCT CASE WHEN e.entity_type = 'holdco' THEN e.id END)::int as holdco_count,
                COUNT(DISTINCT CASE WHEN e.entity_type = 'opco' THEN e.id END)::int as opco_count,
                COUNT(DISTINCT CASE WHEN e.entity_type = 'subsidiary' THEN e.id END)::int as sub_count,
                COUNT(DISTINCT d.id)::int as debt_count
            FROM companies c
            JOIN entities e ON c.id = e.company_id
            LEFT JOIN debt_instruments d ON c.id = d.company_id
            GROUP BY c.ticker, c.name, c.sector
        )
        SELECT ticker, name, entity_count, tier_count, max_tier, holdco_count, opco_count, sub_count, debt_count
        FROM hierarchy_depth
        WHERE entity_count >= 3 AND debt_count > 0
        ORDER BY tier_count DESC, entity_count DESC
        LIMIT 20
    """)
    result = await session.execute(query)
    return result.fetchall()


async def check_covenant_data(session: AsyncSession):
    """Check if we have any covenant data in the database."""
    # Check in debt_instruments attributes
    query = text("""
        SELECT
            c.ticker,
            c.name,
            d.name as debt_name,
            d.attributes
        FROM debt_instruments d
        JOIN companies c ON d.company_id = c.id
        WHERE d.attributes IS NOT NULL
          AND d.attributes != '{}'::jsonb
        LIMIT 20
    """)
    result = await session.execute(query)
    return result.fetchall()


async def main():
    print("=" * 60)
    print("DebtStack Demo Data Extraction")
    print("=" * 60)

    session, engine = await get_session()

    try:
        # 1. Get all companies overview
        print("\n1. ALL COMPANIES OVERVIEW")
        print("-" * 40)
        companies = await get_all_companies(session)
        print(f"Total companies with debt: {len(companies)}")
        print("\nTop 10 by total debt:")
        for c in companies[:10]:
            debt_b = (c[5] or 0) / 100_000_000_000  # Convert cents to billions
            print(f"  {str(c[0]):6s} | {c[1][:30]:30s} | {str(c[2] or 'N/A'):15s} | {int(c[3]):3d} entities | {int(c[4]):3d} debt | ${debt_b:,.1f}B")

        # 2. Find companies with best hierarchies for Demo 1
        print("\n\n2. COMPANIES WITH BEST HIERARCHIES (Demo 1 candidates)")
        print("-" * 40)
        best_hierarchies = await get_companies_with_best_hierarchy(session)
        print("Top candidates by hierarchy depth:")
        # Columns: ticker, name, entity_count, tier_count, max_tier, holdco_count, opco_count, sub_count, debt_count
        for h in best_hierarchies[:10]:
            print(f"  {h[0]:6s} | {h[1][:25]:25s} | {h[2]:3d} entities | {h[3]:2d} tiers | max_tier={h[4]} | {h[8]:2d} debt")

        # Pick the best candidate for Demo 1
        demo1_ticker = best_hierarchies[0][0] if best_hierarchies else "AAPL"
        print(f"\nSelected for Demo 1: {demo1_ticker}")

        # 3. Get detailed structure for Demo 1 candidate
        print(f"\n\n3. DETAILED STRUCTURE FOR {demo1_ticker} (Demo 1)")
        print("-" * 40)
        structure = await get_company_structure(session, demo1_ticker)

        if structure:
            company = structure["company"]
            print(f"Company: {company[1]} ({company[2]})")
            print(f"Sector: {company[3]}")

            print(f"\nEntities ({len(structure['entities'])}):")
            entity_map = {}
            for e in structure["entities"]:
                entity_map[str(e[0])] = e[1]
                parent_name = entity_map.get(str(e[4]), "None") if e[4] else "None"
                print(f"  [{e[2]:10s}] {e[1][:40]:40s} | parent={parent_name[:20]:20s} | guarantor={e[5]}")

            print(f"\nDebt Instruments ({len(structure['debt_instruments'])}):")
            for d in structure["debt_instruments"]:
                issuer_name = entity_map.get(str(d[2]), "Unknown")[:20]
                amount_m = (d[6] or 0) / 100_000_000  # Convert cents to millions
                rate = (d[9] or 0) / 100  # Convert bps to %
                print(f"  ${amount_m:>8,.0f}M | {d[4]:15s} | {d[1][:35]:35s} | issuer={issuer_name} | {rate:.2f}%")

            print(f"\nGuarantees ({len(structure['guarantees'])}):")
            for g in structure["guarantees"][:10]:
                print(f"  Debt {str(g[0])[:8]}... guaranteed by {g[2]}")

        # 4. Structural subordination analysis for Demo 2
        print("\n\n4. STRUCTURAL SUBORDINATION ANALYSIS (Demo 2)")
        print("-" * 40)
        sub_analysis = await get_structural_sub_analysis(session)

        print("All companies by holdco debt %:")
        high_risk = []
        medium_risk = []
        low_risk = []

        for s in sub_analysis:
            holdco_pct = float(s[8] or 0)
            guarantee_pct = float(s[9] or 0)
            total_debt = s[5] or 0

            # Skip companies with very little debt
            if total_debt < 100_000_000_000:  # Less than $1B
                continue

            debt_b = total_debt / 100_000_000_000
            print(f"  {s[0]:6s} | {s[1][:25]:25s} | holdco={holdco_pct:5.1f}% | guarantee={guarantee_pct:5.1f}% | ${debt_b:,.1f}B")

            # Classify risk
            if holdco_pct > 50 and guarantee_pct < 50:
                high_risk.append(s)
            elif holdco_pct < 20 or guarantee_pct > 70:
                low_risk.append(s)
            else:
                medium_risk.append(s)

        print(f"\nHigh risk companies: {len(high_risk)}")
        print(f"Medium risk companies: {len(medium_risk)}")
        print(f"Low risk companies: {len(low_risk)}")

        # 5. Check for covenant data (Demo 3)
        print("\n\n5. COVENANT DATA CHECK (Demo 3)")
        print("-" * 40)
        covenant_data = await check_covenant_data(session)
        print(f"Debt instruments with attributes: {len(covenant_data)}")
        for c in covenant_data[:5]:
            print(f"  {c[0]:6s} | {c[2][:40]:40s}")
            if c[3]:
                print(f"    Attributes: {json.dumps(c[3], default=str)[:100]}...")

        # 6. Generate JSON output for demos
        print("\n\n6. GENERATING DEMO JSON FILES")
        print("-" * 40)

        # Demo 1: Structure visualization
        if structure:
            demo1_data = format_demo1_data(structure)
            with open("results/demo1_structure.json", "w") as f:
                json.dump(demo1_data, f, indent=2, default=decimal_default)
            print(f"  [OK] Demo 1 data saved to results/demo1_structure.json")

        # Demo 2: Structural subordination
        demo2_data = format_demo2_data(high_risk, medium_risk, low_risk)
        with open("results/demo2_subordination.json", "w") as f:
            json.dump(demo2_data, f, indent=2, default=decimal_default)
        print(f"  [OK] Demo 2 data saved to results/demo2_subordination.json")

        # Also try to get detailed data for the 3 selected demo 2 companies
        demo2_tickers = []
        if high_risk:
            demo2_tickers.append(high_risk[0][0])
        if medium_risk:
            demo2_tickers.append(medium_risk[0][0])
        if low_risk:
            demo2_tickers.append(low_risk[0][0])

        print(f"\n  Demo 2 selected companies: {demo2_tickers}")

        for ticker in demo2_tickers:
            struct = await get_company_structure(session, ticker)
            if struct:
                filename = f"results/demo2_{ticker}_structure.json"
                with open(filename, "w") as f:
                    json.dump(format_demo1_data(struct), f, indent=2, default=decimal_default)
                print(f"  [OK] {ticker} structure saved to {filename}")

        print("\n" + "=" * 60)
        print("RECOMMENDATIONS")
        print("=" * 60)

        print(f"""
Demo 1 (Corporate Structure):
  - Best candidate: {demo1_ticker}
  - Has {len(structure['entities']) if structure else 0} entities and {len(structure['debt_instruments']) if structure else 0} debt instruments
  - Shows clear hierarchy with debt placement

Demo 2 (Structural Subordination):
  - Found {len(high_risk)} high-risk, {len(medium_risk)} medium-risk, {len(low_risk)} low-risk companies
  - Selected: {demo2_tickers if demo2_tickers else 'Need more data'}

Demo 3 (Covenant Monitoring):
  - Covenant data available: {'Yes' if covenant_data else 'Limited/No'}
  - Recommendation: {'Build with real thresholds + illustrative current ratios' if covenant_data else 'Skip or use illustrative data'}
""")

    finally:
        await session.close()
        await engine.dispose()


def format_demo1_data(structure):
    """Format structure data for Demo 1 JSON."""
    company = structure["company"]
    entities = structure["entities"]
    debt = structure["debt_instruments"]
    guarantees = structure["guarantees"]

    # Build entity lookup
    entity_lookup = {}
    for e in entities:
        entity_lookup[str(e[0])] = {
            "id": str(e[0]),
            "name": e[1],
            "type": e[2],
            "jurisdiction": e[3],
            "parent_id": str(e[4]) if e[4] else None,
            "is_guarantor": e[5],
            "is_borrower": e[6],
            "structure_tier": e[8],
            "debt_instruments": [],
            "guarantees_given": []
        }

    # Add debt to entities
    for d in debt:
        issuer_id = str(d[2])
        if issuer_id in entity_lookup:
            entity_lookup[issuer_id]["debt_instruments"].append({
                "id": str(d[0]),
                "name": d[1],
                "instrument_type": d[3],
                "seniority": d[4],
                "security_type": d[5],
                "outstanding_cents": d[6],
                "outstanding_formatted": f"${(d[6] or 0) / 100_000_000:,.0f}M",
                "commitment_cents": d[7],
                "currency": d[8],
                "interest_rate_bps": d[9],
                "interest_rate_formatted": f"{(d[9] or 0) / 100:.2f}%",
                "rate_type": d[10],
                "spread_bps": d[11],
                "benchmark": d[12],
                "maturity_date": d[13].isoformat() if d[13] else None,
                "guarantors": []
            })

    # Add guarantee info
    for g in guarantees:
        debt_id = str(g[0])
        guarantor_name = g[2]
        # Find the debt instrument and add guarantor
        for entity in entity_lookup.values():
            for debt_inst in entity["debt_instruments"]:
                if debt_inst["id"] == debt_id:
                    debt_inst["guarantors"].append(guarantor_name)

    return {
        "company": {
            "ticker": company[1],
            "name": company[2],
            "sector": company[3],
            "cik": company[4]
        },
        "entities": list(entity_lookup.values()),
        "summary": {
            "total_entities": len(entities),
            "total_debt_instruments": len(debt),
            "total_guarantees": len(guarantees)
        }
    }


def format_demo2_data(high_risk, medium_risk, low_risk):
    """Format structural subordination data for Demo 2 JSON."""
    companies = []

    def add_company(data, risk_level):
        if not data:
            return
        s = data[0]
        total_debt = s[5] or 0
        holdco_debt = s[6] or 0

        companies.append({
            "ticker": s[0],
            "name": s[1],
            "sector": s[2],
            "risk_level": risk_level,
            "total_entities": s[3],
            "guarantor_count": s[4],
            "total_debt_cents": total_debt,
            "total_debt_formatted": f"${total_debt / 100_000_000_000:,.1f}B",
            "holdco_debt_cents": holdco_debt,
            "holdco_debt_formatted": f"${holdco_debt / 100_000_000_000:,.1f}B",
            "holdco_debt_pct": float(s[8] or 0),
            "guarantee_coverage_pct": float(s[9] or 0),
            "key_findings": generate_findings(s, risk_level)
        })

    add_company(high_risk, "HIGH")
    add_company(medium_risk, "MEDIUM")
    add_company(low_risk, "LOW")

    return {"companies": companies}


def generate_findings(data, risk_level):
    """Generate key findings based on metrics."""
    holdco_pct = float(data[8] or 0)
    guarantee_pct = float(data[9] or 0)

    findings = []

    if risk_level == "HIGH":
        findings.append(f"{holdco_pct:.0f}% of debt at HoldCo level")
        findings.append(f"Only {guarantee_pct:.0f}% guarantee coverage")
        findings.append("High structural subordination risk")
    elif risk_level == "MEDIUM":
        findings.append(f"Mixed debt placement ({holdco_pct:.0f}% at HoldCo)")
        findings.append(f"Moderate {guarantee_pct:.0f}% guarantee coverage")
        findings.append("Some structural subordination exposure")
    else:  # LOW
        if holdco_pct < 20:
            findings.append("Most debt at operating company level")
        else:
            findings.append(f"Only {holdco_pct:.0f}% debt at HoldCo")
        if guarantee_pct > 70:
            findings.append(f"Strong {guarantee_pct:.0f}% guarantee coverage")
        findings.append("Limited structural subordination risk")

    return findings


if __name__ == "__main__":
    asyncio.run(main())

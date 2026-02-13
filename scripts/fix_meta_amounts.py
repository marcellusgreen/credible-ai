"""Fix META debt instrument amounts from 8-K offering announcements.

Adds 14 missing bonds from 3 earlier offerings (Aug 2022, May 2023, Aug 2024)
that were not in the database. The Nov 2025 offering (6 bonds) was already added.

Sources:
- Aug 2022 8-K: 4 notes, $10.0B total
- May 2023 8-K: 5 notes, $8.5B total
- Aug 2024 8-K: 5 notes, $10.5B total
- Nov 2025 8-K: 6 notes, $30.0B total (already in DB)
"""
import asyncio
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text


async def fix():
    engine = create_async_engine(os.getenv("DATABASE_URL"))
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Get META company_id and issuer_id
        result = await session.execute(text("""
            SELECT c.id,
                   (SELECT e.id FROM entities e WHERE e.company_id = c.id AND e.is_root = true LIMIT 1)
            FROM companies c WHERE c.ticker = 'META'
        """))
        row = result.fetchone()
        company_id = row[0]
        issuer_id = row[1]
        print(f"META company_id: {company_id}")

        # ============================================================
        # Add missing bonds from 3 earlier offerings
        # ============================================================

        # (name, rate_bps, maturity_date, issue_date, outstanding_cents)
        new_instruments = [
            # --- Aug 2022 offering (8-K filed 2022-08-09): 4 notes, $10.0B ---
            ("3.500% Senior Notes due 2027", 350, date(2027, 8, 15), date(2022, 8, 15), 275_000_000_000),   # $2.75B
            ("3.850% Senior Notes due 2032", 385, date(2032, 8, 15), date(2022, 8, 15), 300_000_000_000),   # $3.0B
            ("4.450% Senior Notes due 2052", 445, date(2052, 8, 15), date(2022, 8, 15), 275_000_000_000),   # $2.75B
            ("4.650% Senior Notes due 2062", 465, date(2062, 8, 15), date(2022, 8, 15), 150_000_000_000),   # $1.5B

            # --- May 2023 offering (8-K filed 2023-05-09): 5 notes, $8.5B ---
            ("4.600% Senior Notes due 2028", 460, date(2028, 5, 15), date(2023, 5, 9), 150_000_000_000),    # $1.5B
            ("4.800% Senior Notes due 2030", 480, date(2030, 5, 15), date(2023, 5, 9), 100_000_000_000),    # $1.0B
            ("4.950% Senior Notes due 2033", 495, date(2033, 5, 15), date(2023, 5, 9), 175_000_000_000),    # $1.75B
            ("5.600% Senior Notes due 2053", 560, date(2053, 5, 15), date(2023, 5, 9), 250_000_000_000),    # $2.5B
            ("5.750% Senior Notes due 2063", 575, date(2063, 5, 15), date(2023, 5, 9), 175_000_000_000),    # $1.75B

            # --- Aug 2024 offering (8-K filed 2024-08-22): 5 notes, $10.5B ---
            ("4.300% Senior Notes due 2029", 430, date(2029, 8, 15), date(2024, 8, 22), 100_000_000_000),   # $1.0B
            ("4.550% Senior Notes due 2031", 455, date(2031, 8, 15), date(2024, 8, 22), 100_000_000_000),   # $1.0B
            ("4.750% Senior Notes due 2034", 475, date(2034, 8, 15), date(2024, 8, 22), 250_000_000_000),   # $2.5B
            ("5.400% Senior Notes due 2054", 540, date(2054, 8, 15), date(2024, 8, 22), 325_000_000_000),   # $3.25B
            ("5.550% Senior Notes due 2064", 555, date(2064, 8, 15), date(2024, 8, 22), 275_000_000_000),   # $2.75B
        ]

        created = 0
        skipped = 0
        for name, rate, mat, issue, amt in new_instruments:
            # Check if already exists (avoid duplicates)
            result = await session.execute(text("""
                SELECT id FROM debt_instruments
                WHERE company_id = :cid AND name = :name AND is_active = true
            """), {"cid": str(company_id), "name": name})
            if result.fetchone():
                print(f"  SKIP (exists): {name}")
                skipped += 1
                continue

            new_id = str(uuid.uuid4())
            await session.execute(text("""
                INSERT INTO debt_instruments (id, company_id, issuer_id, name, instrument_type, seniority,
                    interest_rate, maturity_date, issue_date, outstanding, is_active)
                VALUES (:id, :cid, :iid, :name, 'senior_notes', 'senior_unsecured',
                    :rate, :mat, :issue_date, :amt, true)
            """), {
                "id": new_id, "cid": str(company_id), "iid": str(issuer_id),
                "name": name, "rate": rate, "mat": mat, "amt": amt,
                "issue_date": issue,
            })
            print(f"  Created: {name} -> ${amt / 100_000_000_000:.2f}B (issued {issue})")
            created += 1

        print(f"\nCreated {created}, skipped {skipped} (already exist)")
        await session.commit()

        # Verify all META instruments
        result = await session.execute(text("""
            SELECT name, outstanding, interest_rate, maturity_date, issue_date
            FROM debt_instruments
            WHERE company_id = :cid AND is_active = true
            ORDER BY maturity_date
        """), {"cid": str(company_id)})
        rows = result.fetchall()
        total = 0
        print(f"\nMETA instruments after fix ({len(rows)} total):")
        for r in rows:
            amt_b = r[1] / 100_000_000_000 if r[1] else 0
            total += r[1] or 0
            rate = f"{r[2]/100:.2f}%" if r[2] else "N/A"
            print(f"  {r[0]:45s} | ${amt_b:5.2f}B | {rate:6s} | mat {r[3]} | issued {r[4]}")
        print(f"\nTotal instruments outstanding: ${total / 100_000_000_000:.1f}B")
        print(f"Q3 2025 total_debt (financials): $28.83B")
        print(f"Expected total (all 4 offerings): ~$59B (includes Nov 2025 post-quarter)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(fix())

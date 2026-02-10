#!/usr/bin/env python3
"""Insert VAL debt instruments manually from 10-K data."""

import asyncio
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.core.database import engine


# VAL (Valaris) debt instruments data (from 10-K Note 6: Debt)
# Second Lien Notes issued April 2023: $700M + $400M = $1,100M total
# 8.375% Senior Secured Second Lien Notes due April 30, 2030
# Also has $375M revolving credit facility (undrawn as of Dec 31, 2024)
VAL_DEBT_DATA = [
    # (name, coupon_pct, maturity, amount_millions, currency, instrument_type, seniority, is_secured)
    ('8.375% Senior Secured Second Lien Notes due 2030', 8.375, date(2030, 4, 30), 1100.0, 'USD', 'bond', 'senior_secured', True),
    # Credit facility is undrawn, so we don't add it as outstanding debt
]


async def main():
    async with engine.begin() as conn:
        # Get VAL company ID
        result = await conn.execute(text("SELECT id FROM companies WHERE ticker = 'VAL'"))
        row = result.fetchone()
        if not row:
            print('VAL not found in database')
            return

        company_id = row[0]
        print(f'VAL company_id: {company_id}')

        # Get the parent entity (Valaris Limited) as issuer
        result = await conn.execute(text("""
            SELECT id FROM entities
            WHERE company_id = :cid AND entity_type = 'holdco'
            ORDER BY id LIMIT 1
        """), {'cid': company_id})
        row = result.fetchone()

        if row:
            issuer_id = row[0]
            print(f'Issuer entity ID: {issuer_id}')
        else:
            # Try to find any parent entity
            result = await conn.execute(text("""
                SELECT id FROM entities
                WHERE company_id = :cid AND parent_id IS NULL
                ORDER BY id LIMIT 1
            """), {'cid': company_id})
            row = result.fetchone()
            if row:
                issuer_id = row[0]
                print(f'Using parent entity ID: {issuer_id}')
            else:
                print('No parent entity found, will set issuer_id to NULL')
                issuer_id = None

        # Delete existing debt for VAL
        result = await conn.execute(
            text('DELETE FROM debt_instruments WHERE company_id = :cid'),
            {'cid': company_id}
        )
        print(f'Deleted existing debt instruments')

        # Insert each debt instrument
        inserted = 0
        for name, coupon, maturity, amount_m, currency, inst_type, seniority, is_secured in VAL_DEBT_DATA:
            # Convert millions to cents (1M = 100,000,000 cents)
            amount_cents = int(amount_m * 100_000_000)
            coupon_bps = int(coupon * 100)
            debt_id = uuid4()

            # Determine security type based on seniority
            security_type = 'second_lien' if 'second lien' in name.lower() else ('first_lien' if is_secured else 'unsecured')

            await conn.execute(text("""
                INSERT INTO debt_instruments (
                    id, company_id, issuer_id, name, instrument_type,
                    seniority, security_type, rate_type, interest_rate,
                    maturity_date, outstanding, currency, created_at
                ) VALUES (
                    :id, :company_id, :issuer_id, :name, :inst_type,
                    :seniority, :security_type, 'fixed', :coupon,
                    :maturity, :amount, :currency, NOW()
                )
            """), {
                'id': debt_id,
                'company_id': company_id,
                'issuer_id': issuer_id,
                'name': name,
                'inst_type': inst_type,
                'seniority': seniority,
                'security_type': security_type,
                'coupon': coupon_bps,
                'maturity': maturity,
                'amount': amount_cents,
                'currency': currency
            })
            inserted += 1

        print(f'Inserted {inserted} debt instruments for VAL')

        # Verify
        result = await conn.execute(
            text('SELECT COUNT(*) FROM debt_instruments WHERE company_id = :cid'),
            {'cid': company_id}
        )
        count = result.fetchone()[0]

        result = await conn.execute(
            text('SELECT SUM(outstanding) FROM debt_instruments WHERE company_id = :cid'),
            {'cid': company_id}
        )
        total = result.fetchone()[0]

        print(f'Verification: {count} instruments, total: ${total/100_000_000_000:.2f}B')


if __name__ == '__main__':
    asyncio.run(main())

#!/usr/bin/env python3
"""Insert LLY debt instruments manually from 10-K data."""

import asyncio
import sys
from datetime import date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.core.database import engine


# LLY debt instruments data (from 10-K Note 11: Borrowings)
# Amounts in millions USD, need to convert to cents (multiply by 100,000,000)
LLY_DEBT_DATA = [
    # (name, coupon_pct, maturity, amount_millions, currency)
    ('7.125% Notes due 2025', 7.125, date(2025, 6, 1), 217.5, 'USD'),
    ('2.75% Notes due 2025', 2.75, date(2025, 9, 15), 560.6, 'USD'),
    ('5.0% Notes due 2026', 5.0, date(2026, 3, 15), 750.0, 'USD'),
    ('1.625% Euro Notes due 2026', 1.625, date(2026, 6, 2), 779.1, 'EUR'),
    ('4.500% Notes due 2027', 4.5, date(2027, 5, 15), 1000.0, 'USD'),
    ('5.5% Notes due 2027', 5.5, date(2027, 3, 15), 364.3, 'USD'),
    ('3.1% Notes due 2027', 3.1, date(2027, 5, 15), 401.5, 'USD'),
    ('4.150% Notes due 2027', 4.15, date(2027, 8, 14), 750.0, 'USD'),
    ('0.45% Swiss Franc Notes due 2028', 0.45, date(2028, 9, 25), 441.6, 'CHF'),
    ('4.500% Notes due 2029', 4.5, date(2029, 5, 15), 1000.0, 'USD'),
    ('3.375% Notes due 2029', 3.375, date(2029, 3, 15), 930.6, 'USD'),
    ('4.200% Notes due 2029', 4.2, date(2029, 8, 14), 1000.0, 'USD'),
    ('0.42% Japanese Yen Notes due 2029', 0.42, date(2029, 3, 14), 145.6, 'JPY'),
    ('2.125% Euro Notes due 2030', 2.125, date(2030, 6, 3), 779.1, 'EUR'),
    ('0.625% Euro Notes due 2031', 0.625, date(2031, 1, 19), 623.2, 'EUR'),
    ('4.7% Notes due 2033', 4.7, date(2033, 3, 15), 1000.0, 'USD'),
    ('0.50% Euro Notes due 2033', 0.5, date(2033, 9, 14), 623.2, 'EUR'),
    ('4.700% Notes due 2034', 4.7, date(2034, 5, 15), 1500.0, 'USD'),
    ('4.600% Notes due 2034', 4.6, date(2034, 8, 14), 1250.0, 'USD'),
    ('0.56% Japanese Yen Notes due 2034', 0.56, date(2034, 3, 14), 58.9, 'JPY'),
    ('6.77% Notes due 2036', 6.77, date(2036, 1, 1), 158.6, 'USD'),
    ('5.55% Notes due 2037', 5.55, date(2037, 10, 1), 444.7, 'USD'),
    ('5.95% Notes due 2037', 5.95, date(2037, 11, 15), 266.8, 'USD'),
    ('3.875% Notes due 2039', 3.875, date(2039, 9, 15), 240.3, 'USD'),
    ('1.625% British Pound Notes due 2043', 1.625, date(2043, 6, 14), 313.8, 'GBP'),
    ('4.65% Notes due 2044', 4.65, date(2044, 2, 15), 38.3, 'USD'),
    ('3.7% Notes due 2045', 3.7, date(2045, 3, 1), 386.8, 'USD'),
    ('3.95% Notes due 2047', 3.95, date(2047, 9, 15), 347.0, 'USD'),
    ('3.95% Notes due 2049', 3.95, date(2049, 5, 15), 958.2, 'USD'),
    ('1.70% Euro Notes due 2049', 1.7, date(2049, 6, 14), 1038.7, 'EUR'),
    ('0.97% Japanese Yen Notes due 2049', 0.97, date(2049, 3, 14), 48.5, 'JPY'),
    ('2.25% Notes due 2050', 2.25, date(2050, 5, 15), 1250.0, 'USD'),
    ('1.125% Euro Notes due 2051', 1.125, date(2051, 6, 1), 519.4, 'EUR'),
    ('4.875% Notes due 2053', 4.875, date(2053, 3, 1), 1250.0, 'USD'),
    ('5.000% Notes due 2054', 5.0, date(2054, 5, 15), 1500.0, 'USD'),
    ('5.050% Notes due 2054', 5.05, date(2054, 8, 14), 1250.0, 'USD'),
    ('4.15% Notes due 2059', 4.15, date(2059, 5, 15), 591.3, 'USD'),
    ('2.50% Notes due 2060', 2.5, date(2060, 9, 15), 850.0, 'USD'),
    ('1.375% Euro Notes due 2061', 1.375, date(2061, 2, 9), 727.1, 'EUR'),
    ('4.95% Notes due 2063', 4.95, date(2063, 3, 15), 1000.0, 'USD'),
    ('5.100% Notes due 2064', 5.1, date(2064, 5, 15), 1500.0, 'USD'),
    ('5.200% Notes due 2064', 5.2, date(2064, 8, 14), 750.0, 'USD'),
]


async def main():
    async with engine.begin() as conn:
        # Get LLY company ID
        result = await conn.execute(text("SELECT id FROM companies WHERE ticker = 'LLY'"))
        row = result.fetchone()
        if not row:
            print('LLY not found in database')
            return

        company_id = row[0]
        print(f'LLY company_id: {company_id}')

        # Get the parent entity (Eli Lilly and Company) as issuer
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
            print('No holdco found, will set issuer_id to NULL')
            issuer_id = None

        # Delete existing debt for LLY
        result = await conn.execute(
            text('DELETE FROM debt_instruments WHERE company_id = :cid'),
            {'cid': company_id}
        )
        print(f'Deleted existing debt instruments')

        # Insert each debt instrument
        from uuid import uuid4
        inserted = 0
        for name, coupon, maturity, amount_m, currency in LLY_DEBT_DATA:
            # Convert millions to cents (1M = 100,000,000 cents)
            amount_cents = int(amount_m * 100_000_000)
            coupon_bps = int(coupon * 100)
            debt_id = uuid4()

            await conn.execute(text("""
                INSERT INTO debt_instruments (
                    id, company_id, issuer_id, name, instrument_type,
                    seniority, rate_type, interest_rate, maturity_date, outstanding,
                    currency, created_at
                ) VALUES (
                    :id, :company_id, :issuer_id, :name, 'bond',
                    'senior_unsecured', 'fixed', :coupon, :maturity, :amount,
                    :currency, NOW()
                )
            """), {
                'id': debt_id,
                'company_id': company_id,
                'issuer_id': issuer_id,
                'name': name,
                'coupon': coupon_bps,
                'maturity': maturity,
                'amount': amount_cents,
                'currency': currency
            })
            inserted += 1

        print(f'Inserted {inserted} debt instruments for LLY')

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

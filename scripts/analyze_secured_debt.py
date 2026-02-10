#!/usr/bin/env python3
"""Analyze secured/subordinated debt missing guarantee and collateral data."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.core.database import engine


async def main():
    async with engine.begin() as conn:
        # Get breakdown by seniority
        result = await conn.execute(text('''
            SELECT seniority, COUNT(*) as count
            FROM debt_instruments
            GROUP BY seniority
            ORDER BY count DESC
        '''))
        seniority_breakdown = result.fetchall()

        print('DEBT INSTRUMENTS BY SENIORITY:')
        print('='*60)
        total = 0
        for seniority, count in seniority_breakdown:
            print(f'  {seniority}: {count}')
            total += count
        print(f'  TOTAL: {total}')

        # Get secured debt (not senior_unsecured) without guarantee data
        result = await conn.execute(text("""
            SELECT c.ticker, c.name, d.name as debt_name, d.seniority, d.security_type,
                   d.outstanding, d.guarantee_data_confidence
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            LEFT JOIN guarantees g ON g.debt_instrument_id = d.id
            WHERE d.seniority <> 'senior_unsecured'
              AND g.id IS NULL
            ORDER BY d.outstanding DESC NULLS LAST
        """))
        secured_no_guarantees = result.fetchall()

        print(f'\n\nSECURED/SUBORDINATED DEBT WITHOUT GUARANTEE DATA ({len(secured_no_guarantees)} instruments):')
        print('='*100)
        for ticker, company, debt_name, seniority, security_type, outstanding, confidence in secured_no_guarantees[:40]:
            amt = f'${outstanding/100_000_000_000:.2f}B' if outstanding else 'N/A'
            print(f'  {ticker:<6} {debt_name[:50]:<50} {seniority:<18} {amt}')

        if len(secured_no_guarantees) > 40:
            print(f'  ... and {len(secured_no_guarantees) - 40} more')

        # Get unique companies
        companies_no_guarantees = set(row[0] for row in secured_no_guarantees)
        print(f'\n  Unique companies: {len(companies_no_guarantees)}')
        print(f'  Companies: {", ".join(sorted(companies_no_guarantees))}')

        # Get secured debt without collateral data
        result = await conn.execute(text("""
            SELECT c.ticker, c.name, d.name as debt_name, d.seniority, d.security_type,
                   d.outstanding, d.collateral_data_confidence
            FROM debt_instruments d
            JOIN companies c ON c.id = d.company_id
            LEFT JOIN collateral col ON col.debt_instrument_id = d.id
            WHERE d.seniority <> 'senior_unsecured'
              AND col.id IS NULL
            ORDER BY d.outstanding DESC NULLS LAST
        """))
        secured_no_collateral = result.fetchall()

        print(f'\n\nSECURED/SUBORDINATED DEBT WITHOUT COLLATERAL DATA ({len(secured_no_collateral)} instruments):')
        print('='*100)
        for ticker, company, debt_name, seniority, security_type, outstanding, confidence in secured_no_collateral[:40]:
            amt = f'${outstanding/100_000_000_000:.2f}B' if outstanding else 'N/A'
            print(f'  {ticker:<6} {debt_name[:50]:<50} {seniority:<18} {amt}')

        if len(secured_no_collateral) > 40:
            print(f'  ... and {len(secured_no_collateral) - 40} more')

        # Get unique companies
        companies_no_collateral = set(row[0] for row in secured_no_collateral)
        print(f'\n  Unique companies: {len(companies_no_collateral)}')
        print(f'  Companies: {", ".join(sorted(companies_no_collateral))}')


if __name__ == '__main__':
    asyncio.run(main())

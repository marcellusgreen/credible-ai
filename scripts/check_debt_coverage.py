#!/usr/bin/env python3
"""Check debt coverage for a company."""
import sys

from sqlalchemy import select

from script_utils import get_db_session, run_async
from app.models import Company, DebtInstrument


async def check(ticker: str = 'AAPL'):
    async with get_db_session() as db:
        # Get company
        result = await db.execute(select(Company).where(Company.ticker == ticker))
        company = result.scalar_one_or_none()
        if not company:
            print(f'Company {ticker} not found')
            return

        # Get debt instruments
        result = await db.execute(
            select(DebtInstrument)
            .where(DebtInstrument.company_id == company.id)
            .where(DebtInstrument.is_active == True)
            .order_by(DebtInstrument.maturity_date)
        )
        instruments = list(result.scalars().all())

        # Sum up outstanding amounts
        total_outstanding = 0
        with_amount = 0
        without_amount = 0
        for inst in instruments:
            if inst.outstanding:
                total_outstanding += inst.outstanding
                with_amount += 1
            else:
                without_amount += 1

        print(f'{ticker} debt instruments: {len(instruments)}')
        print(f'  - With outstanding amount: {with_amount}')
        print(f'  - Without outstanding amount: {without_amount}')
        print()
        total_outstanding_dollars = total_outstanding / 100
        print(f'Sum of outstanding (from FWPs): ${total_outstanding_dollars:,.0f}')
        print(f'Total debt from 10-K: $91,300,000,000')
        print()
        coverage = total_outstanding_dollars / 91_300_000_000 * 100
        print(f'Coverage: {coverage:.1f}%')
        print()
        print('Sample bonds with amounts:')
        for inst in instruments[:15]:
            if inst.outstanding:
                amt = f'${inst.outstanding/100:,.0f}'
            else:
                amt = 'N/A'
            name = inst.name[:45] if inst.name else 'Unknown'
            print(f'  {name:<45} | Outstanding: {amt}')


if __name__ == '__main__':
    ticker = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'
    run_async(check(ticker))

#!/usr/bin/env python3
"""Check Finnhub vs SEC source breakdown for missing amounts."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

async def check():
    engine = create_async_engine(os.getenv('DATABASE_URL'), echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                CASE WHEN di.attributes->>'source' = 'finnhub_discovery' THEN 'finnhub' ELSE 'sec_extracted' END as source,
                CASE
                    WHEN di.outstanding > 0 THEN 'has_amount'
                    WHEN di.outstanding = 0 THEN 'zero'
                    ELSE 'null'
                END as amount_status,
                COUNT(*) as cnt
            FROM debt_instruments di
            WHERE di.is_active = true
            GROUP BY 1, 2
            ORDER BY 1, 2
        """))
        print('Source breakdown:')
        for row in result.fetchall():
            print(f'  {row[0]:15s} {row[1]:12s}: {row[2]}')

        # Also check: how many zero/null instruments have matching names in cache but with amounts?
        print()
        print('Instruments missing amounts by source:')
        result = await session.execute(text("""
            SELECT
                CASE WHEN di.attributes->>'source' = 'finnhub_discovery' THEN 'finnhub' ELSE 'sec_extracted' END as source,
                COUNT(*) as total_missing
            FROM debt_instruments di
            WHERE di.is_active = true
            AND (di.outstanding IS NULL OR di.outstanding = 0)
            GROUP BY 1
        """))
        for row in result.fetchall():
            print(f'  {row[0]:15s}: {row[1]} missing')

    await engine.dispose()

asyncio.run(check())

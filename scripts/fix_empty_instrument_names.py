#!/usr/bin/env python3
"""
Fix instruments with empty names by extracting names from debt footnotes.

Uses DeepSeek to match instruments (by rate/maturity) to their proper names
from the debt footnote content.

Usage:
    python scripts/fix_empty_instrument_names.py --ticker SYK --dry-run
    python scripts/fix_empty_instrument_names.py --ticker SYK --save
    python scripts/fix_empty_instrument_names.py --all --save
"""

import argparse
import asyncio
import io
import json
import os
import sys
from typing import Optional

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from sqlalchemy import text
from app.core.database import async_session_maker


DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

MAX_CONCURRENT_CALLS = 3
api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)


async def call_deepseek(prompt: str, max_tokens: int = 1500) -> Optional[str]:
    """Call DeepSeek API with a prompt."""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY not set")

    async with api_semaphore:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


def build_name_extraction_prompt(instruments: list[dict], footnote_content: str) -> str:
    """Build a prompt to extract instrument names from debt footnote."""

    instruments_desc = "INSTRUMENTS NEEDING NAMES:\n"
    for i, inst in enumerate(instruments, 1):
        rate = f"{inst['rate']:.3f}%" if inst['rate'] else "Unknown rate"
        maturity = str(inst['maturity'])[:10] if inst['maturity'] else "Unknown maturity"
        instruments_desc += f"{i}. Type: {inst['type']}, Rate: {rate}, Maturity: {maturity}\n"

    # Truncate footnote to reasonable size
    footnote_preview = footnote_content[:15000] if footnote_content else "No content"

    prompt = f"""Given the debt footnote below, identify the proper names for these debt instruments.

{instruments_desc}

DEBT FOOTNOTE CONTENT:
{footnote_preview}

TASK: For each instrument above, find its proper name from the footnote content.
Match by coupon rate AND maturity year. The footnote typically lists bonds like:
"4.500% Senior Notes due 2027" or "5.750% Senior Secured Notes due 2029"

Return JSON only:
{{
    "matches": [
        {{
            "instrument_index": 1,
            "name": "4.500% Senior Notes due 2027",
            "confidence": 0.95
        }}
    ]
}}

Rules:
- Only match if both rate AND maturity year match
- Use the exact name format from the footnote
- Confidence should be 0.9+ for exact matches, 0.7-0.9 for close matches
- If no match found, omit that instrument from the response
"""
    return prompt


def parse_llm_response(response: str) -> dict:
    """Parse the LLM's JSON response."""
    try:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            json_str = response[start:end]
            return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    return {"matches": []}


async def fix_names_for_company(ticker: str, dry_run: bool = True) -> dict:
    """Fix empty instrument names for a single company."""

    print(f"\n{'='*60}")
    print(f"Processing {ticker}")
    print(f"{'='*60}")

    async with async_session_maker() as session:
        # Get company ID
        result = await session.execute(text('''
            SELECT id FROM companies WHERE ticker = :ticker
        '''), {'ticker': ticker})
        company_row = result.fetchone()
        if not company_row:
            return {"error": f"Company not found: {ticker}"}
        company_id = company_row[0]

        # Get instruments with empty names
        result = await session.execute(text('''
            SELECT id, instrument_type, interest_rate, maturity_date
            FROM debt_instruments
            WHERE company_id = :cid
              AND is_active = true
              AND (name IS NULL OR name = :empty)
            ORDER BY maturity_date
        '''), {'cid': company_id, 'empty': ''})
        instruments = result.fetchall()

        if not instruments:
            print(f"  No instruments with empty names")
            return {"ticker": ticker, "fixed": 0, "total": 0}

        print(f"  Found {len(instruments)} instruments with empty names")

        # Get most recent debt footnote
        result = await session.execute(text('''
            SELECT content
            FROM document_sections
            WHERE company_id = :cid AND section_type = 'debt_footnote'
            ORDER BY filing_date DESC
            LIMIT 1
        '''), {'cid': company_id})
        footnote_row = result.fetchone()

        if not footnote_row or not footnote_row[0]:
            print(f"  No debt footnote found")
            return {"ticker": ticker, "fixed": 0, "total": len(instruments), "error": "No debt footnote"}

        footnote_content = footnote_row[0]
        print(f"  Debt footnote: {len(footnote_content):,} chars")

        # Prepare instruments for LLM
        instruments_data = []
        for inst in instruments:
            instruments_data.append({
                'id': inst[0],
                'type': inst[1] or 'unknown',
                'rate': inst[2] / 100.0 if inst[2] else None,  # Convert from basis points
                'maturity': inst[3]
            })

        # Call LLM to extract names
        print(f"  Calling LLM to extract names...")
        prompt = build_name_extraction_prompt(instruments_data, footnote_content)

        try:
            response = await call_deepseek(prompt)
            result = parse_llm_response(response)
        except Exception as e:
            print(f"  LLM error: {e}")
            return {"ticker": ticker, "fixed": 0, "total": len(instruments), "error": str(e)}

        matches = result.get("matches", [])
        print(f"  LLM found {len(matches)} name matches")

        # Update instruments
        fixed = 0
        for match in matches:
            idx = match.get("instrument_index", 0) - 1
            name = match.get("name", "")
            confidence = match.get("confidence", 0)

            if 0 <= idx < len(instruments_data) and name and confidence >= 0.7:
                inst_id = instruments_data[idx]['id']
                rate = instruments_data[idx]['rate']
                rate_str = f"{rate:.3f}%" if rate else "NULL"

                print(f"  ✓ {rate_str} -> {name} (conf: {confidence:.2f})")

                if not dry_run:
                    await session.execute(text('''
                        UPDATE debt_instruments
                        SET name = :name
                        WHERE id = :id
                    '''), {'name': name, 'id': inst_id})
                    fixed += 1

        if not dry_run and fixed > 0:
            await session.commit()
            print(f"  Updated {fixed} instrument names")

        return {
            "ticker": ticker,
            "fixed": fixed if not dry_run else len(matches),
            "total": len(instruments),
            "matches": len(matches)
        }


async def main():
    parser = argparse.ArgumentParser(description="Fix empty instrument names using debt footnotes")
    parser.add_argument("--ticker", help="Single company ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies with empty names")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    parser.add_argument("--save", action="store_true", help="Save changes to database")
    parser.add_argument("--limit", type=int, default=10, help="Max companies to process (with --all)")
    args = parser.parse_args()

    if not args.ticker and not args.all:
        parser.error("Either --ticker or --all is required")

    if not DEEPSEEK_API_KEY:
        print("ERROR: DEEPSEEK_API_KEY not set")
        sys.exit(1)

    dry_run = not args.save

    if args.ticker:
        result = await fix_names_for_company(args.ticker.upper(), dry_run=dry_run)
        print(f"\nResult: {result}")
        return

    # Batch mode - get companies with empty names
    async with async_session_maker() as session:
        result = await session.execute(text('''
            SELECT c.ticker, COUNT(*) as cnt
            FROM debt_instruments di
            JOIN companies c ON di.company_id = c.id
            WHERE di.is_active = true AND (di.name IS NULL OR di.name = :empty)
            GROUP BY c.ticker
            ORDER BY cnt DESC
            LIMIT :limit
        '''), {'empty': '', 'limit': args.limit})
        tickers = [row[0] for row in result.fetchall()]

    print(f"Processing {len(tickers)} companies: {tickers}")

    results = []
    for ticker in tickers:
        try:
            result = await fix_names_for_company(ticker, dry_run=dry_run)
            results.append(result)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
            results.append({"ticker": ticker, "error": str(e)})

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    total_fixed = sum(r.get("fixed", 0) for r in results)
    total_instruments = sum(r.get("total", 0) for r in results)
    print(f"Fixed: {total_fixed} / {total_instruments} instruments")


if __name__ == "__main__":
    asyncio.run(main())

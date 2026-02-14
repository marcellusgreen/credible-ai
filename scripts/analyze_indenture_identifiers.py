#!/usr/bin/env python3
"""Analyze what identifiers appear in indentures for matching."""

import re
import sys
from collections import Counter

from sqlalchemy import select

from script_utils import get_db_session, print_header, run_async
from app.models import DocumentSection


async def main():
    print_header("INDENTURE IDENTIFIER ANALYSIS")

    async with get_db_session() as session:
        # Sample indentures
        result = await session.execute(
            select(DocumentSection.section_title, DocumentSection.content)
            .where(DocumentSection.section_type == 'indenture')
            .limit(100)
        )

        # Track what we find
        found_patterns = Counter()
        examples = {}

        for row in result.fetchall():
            title = row[0] or ''
            content = (row[1] or '')[:100000]
            full_text = title + ' ' + content

            # CUSIP patterns
            cusips = re.findall(r'CUSIP[:\s#No.]+([A-Z0-9]{6}\s*[A-Z0-9]{2,3})', full_text, re.IGNORECASE)
            if cusips:
                found_patterns['CUSIP'] += 1
                if 'CUSIP' not in examples:
                    examples['CUSIP'] = cusips[:3]

            # ISIN patterns
            isins = re.findall(r'ISIN[:\s#No.]+([A-Z]{2}[A-Z0-9]{10})', full_text, re.IGNORECASE)
            if isins:
                found_patterns['ISIN'] += 1
                if 'ISIN' not in examples:
                    examples['ISIN'] = isins[:3]

            # Dollar amounts (principal)
            amounts = re.findall(r'\$([0-9,]+(?:\.[0-9]+)?)\s*(million|billion)?', full_text, re.IGNORECASE)
            if amounts:
                found_patterns['Dollar Amount'] += 1
                if 'Dollar Amount' not in examples:
                    examples['Dollar Amount'] = [f"${a[0]} {a[1]}" for a in amounts[:3]]

            # Interest rate patterns
            rates = re.findall(r'(\d+\.\d+)\s*(?:%|percent)', full_text, re.IGNORECASE)
            if rates:
                found_patterns['Interest Rate'] += 1
                if 'Interest Rate' not in examples:
                    examples['Interest Rate'] = [f"{r}%" for r in rates[:3]]

            # Maturity date patterns (more specific)
            mat_dates = re.findall(r'(?:due|matur\w*)\s+(?:on\s+)?(\w+\s+\d{1,2},?\s+\d{4})', full_text, re.IGNORECASE)
            if mat_dates:
                found_patterns['Maturity Date'] += 1
                if 'Maturity Date' not in examples:
                    examples['Maturity Date'] = mat_dates[:3]

            # Maturity year only
            mat_years = re.findall(r'due\s+(?:in\s+)?(\d{4})', full_text, re.IGNORECASE)
            if mat_years:
                found_patterns['Maturity Year'] += 1
                if 'Maturity Year' not in examples:
                    examples['Maturity Year'] = mat_years[:3]

            # Series identifiers
            series = re.findall(r'(?:series|tranche)\s+([A-Z0-9-]+)', full_text, re.IGNORECASE)
            if series:
                found_patterns['Series/Tranche'] += 1
                if 'Series/Tranche' not in examples:
                    examples['Series/Tranche'] = series[:3]

            # Note description in title (e.g., "5.25% Senior Notes due 2030")
            note_desc = re.findall(r'(\d+\.\d+%?\s*(?:Senior\s+)?Notes?\s+due\s+\d{4})', full_text[:2000], re.IGNORECASE)
            if note_desc:
                found_patterns['Note Description'] += 1
                if 'Note Description' not in examples:
                    examples['Note Description'] = note_desc[:3]

        total = 100
        print(f"Identifier coverage in {total} sample indentures:\n")
        for pattern, count in found_patterns.most_common():
            print(f"  {pattern:20} {count:3} ({count}%)")
            if pattern in examples:
                print(f"    Examples: {examples[pattern]}")

        # What we could use for matching
        print("\n\nPOTENTIAL MATCHING STRATEGIES:")
        print("=" * 60)
        print("""
1. CUSIP/ISIN (already implemented)
   - High confidence (0.95) but low coverage (~28%)

2. Issue Date = Filing Date (already implemented)
   - Good confidence (0.65-0.85) when exact match

3. Coupon Rate + Maturity Year (already implemented)
   - Moderate confidence when both match

4. NOTE DESCRIPTION MATCHING (new)
   - Extract "X.XX% Senior Notes due YYYY" from both instrument name and indenture
   - Match on normalized description
   - Could be high confidence when full description matches

5. PRINCIPAL AMOUNT MATCHING (new)
   - Match outstanding/principal from debt instrument to amounts in indenture
   - Lower confidence (many indentures cover multiple issuances)

6. SERIES/TRANCHE MATCHING (new)
   - Match series identifiers between instrument and indenture
   - Useful for multi-tranche facilities
""")


if __name__ == "__main__":
    run_async(main())

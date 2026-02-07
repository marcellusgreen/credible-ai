#!/usr/bin/env python3
"""
Diagnose why specific instruments aren't matching to available documents.

This script analyzes each unmatched note with available indentures to determine
if the issue is:
1. Document missing - the specific indenture for this note isn't in the DB
2. Algorithm failure - the indenture exists but matching logic failed
3. Generic/aggregate name - instrument name too vague to match

Output:
- For each unmatched note, checks if its coupon+maturity combo exists in any indenture
- Identifies specific algorithm improvements needed
"""

import re
from collections import defaultdict

from sqlalchemy import text

from script_utils import get_db_session, print_header, run_async


def extract_coupons_from_text(text: str) -> set[float]:
    """Extract coupon rates from text."""
    coupons = set()

    # Decimal pattern: 5.750%, 5.75%
    for match in re.finditer(r'(\d+\.?\d*)\s*%', text):
        try:
            rate = float(match.group(1))
            if 0 < rate < 25:  # Reasonable coupon range
                coupons.add(round(rate, 3))
        except ValueError:
            pass

    # Fraction pattern: 5 3/4%
    for match in re.finditer(r'(\d+)\s+(\d)/(\d)\s*%', text):
        try:
            whole = int(match.group(1))
            num = int(match.group(2))
            denom = int(match.group(3))
            rate = whole + (num / denom)
            if 0 < rate < 25:
                coupons.add(round(rate, 3))
        except (ValueError, ZeroDivisionError):
            pass

    return coupons


def extract_years_from_text(text: str) -> set[int]:
    """Extract maturity years from text."""
    years = set()

    # Pattern: "due YYYY" or "due Month YYYY" or "YYYY Notes" or "maturing YYYY"
    patterns = [
        r'due\s+(?:\w+\s+)?(\d{4})',
        r'matur(?:ing|ity)[^0-9]*(\d{4})',
        r'(\d{4})\s+(?:senior\s+)?notes',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            year = int(match.group(1))
            if 2000 < year < 2100:
                years.add(year)

    return years


def extract_note_descriptions(text: str) -> list[tuple[float, int]]:
    """Extract (coupon, year) tuples from text like '5.25% Notes due 2030'."""
    descriptions = []

    # Pattern: rate% [qualifier] notes due [month] [day,] year
    pattern = r'(\d+\.?\d*)\s*%\s*(?:senior\s+secured\s+|senior\s+unsecured\s+|senior\s+|subordinated\s+)?notes?\s+due\s+(?:\w+\s+)?(?:\d{1,2},?\s+)?(\d{4})'

    for match in re.finditer(pattern, text, re.IGNORECASE):
        try:
            rate = float(match.group(1))
            year = int(match.group(2))
            if 0 < rate < 25 and 2000 < year < 2100:
                descriptions.append((round(rate, 3), year))
        except ValueError:
            pass

    return descriptions


async def diagnose():
    print_header("DOCUMENT MATCHING DIAGNOSIS")

    async with get_db_session() as session:

        # Get unlinked notes with their company's indentures
        result = await session.execute(text('''
            WITH unlinked_notes AS (
                SELECT
                    c.id as company_id,
                    c.ticker,
                    di.id as instrument_id,
                    di.name,
                    di.interest_rate,
                    di.maturity_date,
                    di.principal / 100.0 / 1e9 as principal_bn
                FROM debt_instruments di
                JOIN entities e ON e.id = di.issuer_id
                JOIN companies c ON c.id = e.company_id
                LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
                WHERE di.is_active = true AND did.id IS NULL
                AND di.instrument_type IN (
                    'senior_notes', 'senior_secured_notes', 'senior_unsecured_notes',
                    'convertible_notes', 'subordinated_notes', 'debentures'
                )
            ),
            company_indentures AS (
                SELECT
                    company_id,
                    COUNT(*) as indenture_count
                FROM document_sections
                WHERE section_type = 'indenture'
                GROUP BY company_id
            )
            SELECT
                un.ticker,
                un.instrument_id,
                un.name,
                un.interest_rate,
                un.maturity_date,
                un.principal_bn,
                un.company_id,
                COALESCE(ci.indenture_count, 0) as indentures_available
            FROM unlinked_notes un
            LEFT JOIN company_indentures ci ON ci.company_id = un.company_id
            WHERE COALESCE(ci.indenture_count, 0) > 0
            ORDER BY un.principal_bn DESC NULLS LAST
        '''))
        unlinked = result.fetchall()

        print(f"\nAnalyzing {len(unlinked)} unlinked notes that have indentures available...")

        # Categorize failures
        categories = {
            'document_missing': [],  # Coupon/maturity not in any indenture
            'algorithm_failure': [],  # Coupon+maturity exists but didn't match
            'generic_name': [],  # No usable coupon/maturity in instrument
            'rate_format_mismatch': [],  # Rate exists with different precision
        }

        for row in unlinked:
            ticker = row.ticker
            name = row.name or ''
            interest_rate = row.interest_rate  # In basis points (e.g., 450 = 4.50%)
            maturity_date = row.maturity_date
            company_id = row.company_id

            # Extract coupon and maturity from instrument
            inst_coupon = interest_rate / 100 if interest_rate else None
            inst_year = maturity_date.year if maturity_date else None

            # If not available from fields, try to extract from name
            if not inst_coupon:
                name_coupons = extract_coupons_from_text(name)
                inst_coupon = list(name_coupons)[0] if name_coupons else None

            if not inst_year:
                name_years = extract_years_from_text(name)
                inst_year = list(name_years)[0] if name_years else None

            # Skip if we can't identify the note
            if not inst_coupon or not inst_year:
                categories['generic_name'].append({
                    'ticker': ticker,
                    'name': name,
                    'coupon': inst_coupon,
                    'year': inst_year,
                    'principal_bn': row.principal_bn,
                })
                continue

            # Check if this coupon+year combo exists in any indenture
            indenture_result = await session.execute(text('''
                SELECT
                    id,
                    section_title,
                    LEFT(content, 100000) as content_preview
                FROM document_sections
                WHERE company_id = :company_id
                AND section_type = 'indenture'
            '''), {'company_id': str(company_id)})
            indentures = indenture_result.fetchall()

            # Search for this specific note in indentures
            found_exact = False
            found_fuzzy = False
            best_match_info = None

            for ind in indentures:
                title = ind.section_title or ''
                content = ind.content_preview or ''
                full_text = title + '\n' + content

                # Extract all note descriptions from this indenture
                note_descs = extract_note_descriptions(full_text)

                # Check for exact match (within 0.01% tolerance)
                for (doc_coupon, doc_year) in note_descs:
                    if abs(doc_coupon - inst_coupon) < 0.01 and doc_year == inst_year:
                        found_exact = True
                        best_match_info = {
                            'title': title[:100],
                            'matched': f"{doc_coupon}% due {doc_year}",
                        }
                        break

                if found_exact:
                    break

                # Check for fuzzy match (rate with different precision)
                for (doc_coupon, doc_year) in note_descs:
                    if abs(doc_coupon - inst_coupon) < 0.05 and doc_year == inst_year:
                        found_fuzzy = True
                        best_match_info = {
                            'title': title[:100],
                            'matched': f"{doc_coupon}% due {doc_year}",
                            'inst': f"{inst_coupon}% due {inst_year}",
                        }

            if found_exact:
                # The indenture exists and contains this exact note - algorithm failure
                categories['algorithm_failure'].append({
                    'ticker': ticker,
                    'name': name[:60],
                    'coupon': inst_coupon,
                    'year': inst_year,
                    'principal_bn': row.principal_bn,
                    'indenture_title': best_match_info['title'],
                })
            elif found_fuzzy:
                # Indenture has similar rate but not exact - rate format mismatch
                categories['rate_format_mismatch'].append({
                    'ticker': ticker,
                    'name': name[:60],
                    'coupon': inst_coupon,
                    'year': inst_year,
                    'principal_bn': row.principal_bn,
                    'indenture_info': best_match_info,
                })
            else:
                # No indenture contains this note - document missing
                categories['document_missing'].append({
                    'ticker': ticker,
                    'name': name[:60],
                    'coupon': inst_coupon,
                    'year': inst_year,
                    'principal_bn': row.principal_bn,
                    'indentures_searched': len(indentures),
                })

        # Print results
        print('\n' + '=' * 100)
        print(f"RESULTS: {len(unlinked)} notes analyzed")
        print('=' * 100)

        print(f"\n1. ALGORITHM FAILURES ({len(categories['algorithm_failure'])} notes)")
        print("   These notes have matching indentures but the algorithm failed to link them")
        print("-" * 100)
        total_principal = sum(n.get('principal_bn') or 0 for n in categories['algorithm_failure'])
        print(f"   Total principal: ${total_principal:.1f}B")
        print()
        for n in categories['algorithm_failure'][:20]:
            p = f"${n['principal_bn']:.1f}B" if n['principal_bn'] else "N/A"
            name = n['name'].encode('ascii', 'replace').decode('ascii')
            title = n['indenture_title'].encode('ascii', 'replace').decode('ascii')
            print(f"   {n['ticker']:<6} {n['coupon']:.3f}% due {n['year']} ({p})")
            print(f"          Name: {name}")
            print(f"          Indenture: {title}")

        print(f"\n2. RATE FORMAT MISMATCHES ({len(categories['rate_format_mismatch'])} notes)")
        print("   Indenture has similar but not exact coupon rate")
        print("-" * 100)
        total_principal = sum(n.get('principal_bn') or 0 for n in categories['rate_format_mismatch'])
        print(f"   Total principal: ${total_principal:.1f}B")
        print()
        for n in categories['rate_format_mismatch'][:15]:
            p = f"${n['principal_bn']:.1f}B" if n['principal_bn'] else "N/A"
            info = n['indenture_info']
            print(f"   {n['ticker']:<6} Instrument: {n['coupon']:.3f}% due {n['year']} ({p})")
            print(f"          Indenture: {info.get('matched', 'N/A')}")

        print(f"\n3. DOCUMENT MISSING ({len(categories['document_missing'])} notes)")
        print("   The specific indenture for this note isn't in the database")
        print("-" * 100)
        total_principal = sum(n.get('principal_bn') or 0 for n in categories['document_missing'])
        print(f"   Total principal: ${total_principal:.1f}B")
        print()
        # Group by company
        by_company = defaultdict(list)
        for n in categories['document_missing']:
            by_company[n['ticker']].append(n)

        for ticker, notes in sorted(by_company.items(), key=lambda x: -len(x[1]))[:15]:
            p_total = sum(n.get('principal_bn') or 0 for n in notes)
            print(f"   {ticker}: {len(notes)} notes (${p_total:.1f}B)")
            for n in notes[:3]:
                print(f"      - {n['coupon']:.3f}% due {n['year']}")

        print(f"\n4. GENERIC/AGGREGATE NAMES ({len(categories['generic_name'])} notes)")
        print("   Cannot determine coupon or maturity from instrument data")
        print("-" * 100)
        total_principal = sum(n.get('principal_bn') or 0 for n in categories['generic_name'])
        print(f"   Total principal: ${total_principal:.1f}B")
        print()
        for n in categories['generic_name'][:15]:
            p = f"${n['principal_bn']:.1f}B" if n['principal_bn'] else "N/A"
            name = (n['name'] or 'N/A')[:50].encode('ascii', 'replace').decode('ascii')
            print(f"   {n['ticker']:<6} {name} ({p})")

        # Summary
        print('\n' + '=' * 100)
        print("SUMMARY & RECOMMENDATIONS")
        print('=' * 100)

        algo_failures = len(categories['algorithm_failure'])
        rate_mismatches = len(categories['rate_format_mismatch'])
        doc_missing = len(categories['document_missing'])
        generic = len(categories['generic_name'])

        print(f"\n  Algorithm failures: {algo_failures} notes")
        print(f"  Rate format mismatches: {rate_mismatches} notes")
        print(f"  Documents missing: {doc_missing} notes")
        print(f"  Generic/aggregate names: {generic} notes")

        fixable = algo_failures + rate_mismatches
        unfixable = doc_missing + generic

        print(f"\n  FIXABLE with algorithm improvements: {fixable} notes ({100*fixable/(fixable+unfixable):.0f}%)")
        print(f"  UNFIXABLE (need more documents): {unfixable} notes ({100*unfixable/(fixable+unfixable):.0f}%)")

        if algo_failures > 0:
            print("\n  Recommendation 1: Debug algorithm - indentures exist but matching failed")
            print("  Likely cause: matching logic not finding note descriptions in document content")

        if rate_mismatches > 0:
            print(f"\n  Recommendation 2: Increase coupon tolerance from 0.01 to 0.05")
            print(f"  Would fix {rate_mismatches} additional notes")


if __name__ == "__main__":
    run_async(diagnose())

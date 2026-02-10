#!/usr/bin/env python3
"""QC the LLM-created document matches."""

import asyncio
import sys
import io
import os

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker


async def qc_matches():
    async with async_session_maker() as session:
        # Get all LLM-created matches
        result = await session.execute(text("""
            SELECT
                c.ticker,
                di.name as instrument_name,
                di.instrument_type,
                di.interest_rate,
                di.maturity_date,
                ds.section_title as doc_title,
                ds.section_type as doc_type,
                ds.filing_date,
                did.match_confidence,
                did.match_evidence,
                did.created_at,
                di.id as inst_id,
                ds.id as doc_id
            FROM debt_instrument_documents did
            JOIN debt_instruments di ON di.id = did.debt_instrument_id
            JOIN document_sections ds ON ds.id = did.document_section_id
            JOIN companies c ON c.id = di.company_id
            WHERE did.match_method = 'llm_deepseek'
            ORDER BY did.created_at DESC, c.ticker
        """))

        matches = result.fetchall()

        print("=" * 100)
        print(f"QC REPORT: LLM-CREATED MATCHES ({len(matches)} total)")
        print("=" * 100)

        # Group by company
        by_company = {}
        for row in matches:
            ticker = row[0]
            if ticker not in by_company:
                by_company[ticker] = []
            by_company[ticker].append(row)

        print(f"\nCompanies with LLM matches: {len(by_company)}")
        print()

        # Analyze each match
        suspicious = []
        good = []

        for ticker, company_matches in sorted(by_company.items()):
            print(f"\n{'='*80}")
            print(f"{ticker} ({len(company_matches)} matches)")
            print(f"{'='*80}")

            for row in company_matches:
                (ticker, inst_name, inst_type, rate, maturity, doc_title,
                 doc_type, filing_date, confidence, evidence, created_at,
                 inst_id, doc_id) = row

                # Format rate
                rate_str = f"{rate/100:.3f}%" if rate else "N/A"

                print(f"\n  Instrument: {inst_name[:60]}")
                print(f"    Type: {inst_type}, Rate: {rate_str}, Maturity: {maturity}")
                print(f"  -> Document: {doc_title[:60] if doc_title else 'N/A'}")
                print(f"    Doc Type: {doc_type}, Filed: {filing_date}, Confidence: {confidence}")

                # Extract reasoning from evidence
                if evidence and isinstance(evidence, dict):
                    reasoning = evidence.get('reasoning', 'No reasoning')
                    print(f"    Reasoning: {reasoning[:100]}")

                # Flag suspicious matches
                is_suspicious = False
                reasons = []

                # Check if doc type matches instrument type
                if inst_type in ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan', 'abl'):
                    if doc_type != 'credit_agreement':
                        is_suspicious = True
                        reasons.append(f"Loan matched to {doc_type} instead of credit_agreement")
                elif 'note' in inst_type.lower() or 'bond' in inst_type.lower():
                    if doc_type != 'indenture':
                        is_suspicious = True
                        reasons.append(f"Note/bond matched to {doc_type} instead of indenture")

                # Check confidence
                if confidence and float(confidence) < 0.5:
                    is_suspicious = True
                    reasons.append(f"Low confidence: {confidence}")

                if is_suspicious:
                    suspicious.append((ticker, inst_name, doc_title, reasons))
                    print(f"    ⚠️  SUSPICIOUS: {', '.join(reasons)}")
                else:
                    good.append((ticker, inst_name, doc_title))
                    print(f"    ✓ Looks good")

        # Summary
        print("\n" + "=" * 100)
        print("SUMMARY")
        print("=" * 100)
        print(f"Total LLM matches: {len(matches)}")
        print(f"Good matches: {len(good)}")
        print(f"Suspicious matches: {len(suspicious)}")

        if suspicious:
            print(f"\nSUSPICIOUS MATCHES TO REVIEW:")
            for ticker, inst, doc, reasons in suspicious:
                print(f"  {ticker}: {inst[:40]} -> {doc[:40] if doc else 'N/A'}")
                for r in reasons:
                    print(f"    - {r}")

        # Sample verification - check if document content actually mentions the instrument
        print("\n" + "=" * 100)
        print("CONTENT VERIFICATION (sample of 10)")
        print("=" * 100)

        import random
        sample = random.sample(matches, min(10, len(matches)))

        for row in sample:
            (ticker, inst_name, inst_type, rate, maturity, doc_title,
             doc_type, filing_date, confidence, evidence, created_at,
             inst_id, doc_id) = row

            # Get document content
            result2 = await session.execute(text("""
                SELECT content FROM document_sections WHERE id = :doc_id
            """), {"doc_id": str(doc_id)})
            content_row = result2.fetchone()
            content = content_row[0] if content_row else ""

            print(f"\n{ticker}: {inst_name[:50]}")

            # Check if key identifiers appear in content
            checks = []

            # Check for rate
            if rate:
                rate_str = f"{rate/100:.2f}".rstrip('0').rstrip('.')
                if rate_str in content:
                    checks.append(f"✓ Rate {rate_str}% found")
                else:
                    checks.append(f"✗ Rate {rate_str}% NOT found")

            # Check for year
            if maturity:
                year = str(maturity)[:4]
                if year in content:
                    checks.append(f"✓ Year {year} found")
                else:
                    checks.append(f"✗ Year {year} NOT found")

            # Check for instrument type keywords
            if inst_type in ('revolver', 'term_loan_a', 'term_loan_b', 'term_loan'):
                if 'revolv' in content.lower() or 'term loan' in content.lower():
                    checks.append("✓ Facility keywords found")
                else:
                    checks.append("✗ Facility keywords NOT found")

            # Check for instrument name keywords
            name_words = inst_name.lower().split()
            name_matches = sum(1 for w in name_words if len(w) > 3 and w in content.lower())
            if name_matches >= 2:
                checks.append(f"✓ Name keywords found ({name_matches})")
            else:
                checks.append(f"? Name keywords: {name_matches}")

            for check in checks:
                print(f"  {check}")


async def main():
    await qc_matches()


if __name__ == "__main__":
    asyncio.run(main())

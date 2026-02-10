#!/usr/bin/env python3
"""
Link Finnhub-discovered bonds to existing indenture documents,
then extract guarantees and collateral for the linked bonds.

Phase 4 bond discovery creates DebtInstrument records with CUSIP/ISIN,
coupon, maturity, seniority - but no document links, no guarantees, and
no collateral. This script fills those gaps by reusing existing linking
and extraction infrastructure.

Steps:
  1. Backfill source tags on existing Finnhub bonds
  2. Pattern-based document matching (rate + maturity in doc content)
  3. Heuristic document matching (score-based fallback)
  4. Base indenture fallback (lowest confidence)
  5. Mark unmatchable bonds (no indenture documents available)
  6. Extract guarantees for newly-linked bonds
  7. Extract collateral for secured bonds with links

Usage:
    python scripts/link_finnhub_bonds.py --all
    python scripts/link_finnhub_bonds.py --ticker CHTR
    python scripts/link_finnhub_bonds.py --all --dry-run
    python scripts/link_finnhub_bonds.py --step backfill
    python scripts/link_finnhub_bonds.py --step match
    python scripts/link_finnhub_bonds.py --step heuristic
    python scripts/link_finnhub_bonds.py --step base-indenture
    python scripts/link_finnhub_bonds.py --step mark-no-doc
    python scripts/link_finnhub_bonds.py --step guarantees
    python scripts/link_finnhub_bonds.py --step collateral
"""

import argparse
import asyncio
import io
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.core.database import async_session_maker
from app.models import DebtInstrumentDocument


# =============================================================================
# Helper functions (inlined to avoid module-level side effects from imports)
# =============================================================================

def extract_rate_maturity(instrument: dict) -> tuple:
    """Extract rate and maturity year patterns from instrument."""
    rate_pattern = None
    maturity_pattern = None

    if instrument.get('interest_rate'):
        rate = instrument['interest_rate'] / 100  # Convert from bps
        rate_pattern = f"{rate:.2f}".rstrip('0').rstrip('.')

    if instrument.get('maturity_date'):
        maturity_str = str(instrument['maturity_date'])
        if len(maturity_str) >= 4:
            maturity_pattern = maturity_str[:4]

    return rate_pattern, maturity_pattern


def find_instrument_in_document(instrument: dict, doc_content: str) -> list:
    """Search document for mentions of the instrument using rate + maturity patterns."""
    matches = []
    name = instrument.get('name', '')
    rate_pattern, maturity_year = extract_rate_maturity(instrument)

    if rate_pattern and maturity_year:
        rate_escaped = re.escape(rate_pattern)
        patterns_to_try = [
            rf'{rate_escaped}%?\s*[^%]*?(?:due|matur)[^%]*?{maturity_year}',
            rf'{rate_escaped}%?\s*(?:Senior\s*)?Notes?\s*(?:due\s*)?{maturity_year}',
            rf'{maturity_year}[^%]*?{rate_escaped}%',
        ]

        for pattern in patterns_to_try:
            found = re.search(pattern, doc_content, re.IGNORECASE)
            if found:
                match_pos = found.start()
                start = max(0, match_pos - 100)
                end = min(len(doc_content), found.end() + 300)
                context = doc_content[start:end]
                matches.append({
                    'type': 'rate_maturity_match',
                    'pattern': f'{rate_pattern}% due {maturity_year}',
                    'context': context,
                    'confidence': 0.80,
                })
                break

    if name and len(name) > 10 and 'note' in name.lower():
        if re.search(re.escape(name[:30]), doc_content, re.IGNORECASE):
            match_pos = doc_content.lower().find(name[:30].lower())
            if match_pos >= 0:
                start = max(0, match_pos - 100)
                end = min(len(doc_content), match_pos + 500)
                context = doc_content[start:end]
                matches.append({
                    'type': 'name_match',
                    'pattern': name[:30],
                    'context': context,
                    'confidence': 0.75,
                })

    return matches


def is_officer_certificate(title: str, content: str) -> bool:
    """Check if this is an officer certificate (should skip)."""
    title_lower = title.lower() if title else ''
    content_lower = content[:2000].lower() if content else ''
    if any(x in title_lower for x in ['officer', 'pricing', 'certificate']):
        return True
    if any(x in content_lower for x in ['action of authorized pricing officers', 'officer certificate']):
        return True
    return False


def is_base_indenture(title: str, content: str) -> bool:
    """Check if this is a base indenture (not supplemental or officer cert)."""
    title_lower = title.lower() if title else ''
    content_lower = content[:2000].lower() if content else ''

    if is_officer_certificate(title, content):
        return False
    if 'supplemental' in title_lower:
        return False
    if 'indenture' in title_lower:
        if any(x in content_lower for x in ['unlimited as to aggregate principal', 'base indenture',
                                            'original indenture', 'may from time to time']):
            return True
        if len(content) > 20000:
            return True
    return False


def is_any_indenture(title: str, content: str) -> bool:
    """Check if this is any valid indenture (base or supplemental, not officer cert)."""
    title_lower = title.lower() if title else ''
    if is_officer_certificate(title, content):
        return False
    if 'indenture' in title_lower:
        return True
    return False

# Phase 4 start date - bonds created after this are Finnhub-discovered
PHASE4_START_DATE = datetime(2026, 2, 3, tzinfo=timezone.utc)


# =============================================================================
# STEP 1: Backfill source tags
# =============================================================================

async def step_backfill_source_tags(session, ticker=None, dry_run=False):
    """Tag existing Finnhub bonds that lack the source attribute."""
    print()
    print("STEP 1: Backfill source tags")
    print("-" * 70)

    where_clause = """
        WHERE instrument_type = 'bond'
          AND cusip IS NOT NULL
          AND isin IS NOT NULL
          AND id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
          AND created_at >= :start_date
          AND (attributes IS NULL OR NOT (attributes ? 'source'))
    """
    params = {"start_date": PHASE4_START_DATE}

    if ticker:
        where_clause += " AND company_id = (SELECT id FROM companies WHERE ticker = :ticker)"
        params["ticker"] = ticker

    # Count affected bonds
    result = await session.execute(text(f"""
        SELECT COUNT(*) FROM debt_instruments {where_clause}
    """), params)
    count = result.scalar()

    print(f"  Bonds to tag: {count}")

    if count == 0:
        print("  Nothing to do.")
        return 0

    if not dry_run:
        await session.execute(text(f"""
            UPDATE debt_instruments
            SET attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"source": "finnhub_discovery"}}'::jsonb
            {where_clause}
        """), params)
        await session.commit()
        print(f"  Tagged {count} bonds with source=finnhub_discovery")

    return count


# =============================================================================
# STEP 2: Pattern-based document matching
# =============================================================================

async def step_pattern_match(session, ticker=None, dry_run=False):
    """Match Finnhub bonds to documents using rate + maturity patterns."""
    print()
    print("STEP 2: Pattern-based document matching")
    print("-" * 70)

    # Get unlinked Finnhub bonds
    where_clause = """
        WHERE di.instrument_type = 'bond'
          AND di.is_active = true
          AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
          AND (di.attributes->>'source' = 'finnhub_discovery'
               OR (di.cusip IS NOT NULL AND di.isin IS NOT NULL
                   AND di.created_at >= :start_date))
          AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
               OR di.attributes->>'no_document_expected' != 'true')
    """
    params = {"start_date": PHASE4_START_DATE}

    if ticker:
        where_clause += " AND di.company_id = (SELECT id FROM companies WHERE ticker = :ticker)"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT di.id, di.company_id, di.name, di.interest_rate, di.maturity_date,
               di.instrument_type, c.ticker
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        {where_clause}
        ORDER BY c.ticker, di.name
    """), params)
    instruments = result.fetchall()

    print(f"  Unlinked Finnhub bonds: {len(instruments)}", flush=True)

    if not instruments:
        return 0

    # Group by company for efficient doc fetching
    by_company = {}
    for row in instruments:
        inst_id, company_id, name, rate, maturity, inst_type, tick = row
        if company_id not in by_company:
            by_company[company_id] = {"ticker": tick, "instruments": []}
        by_company[company_id]["instruments"].append({
            "id": inst_id,
            "name": name,
            "interest_rate": rate,
            "maturity_date": str(maturity) if maturity else None,
            "instrument_type": inst_type,
        })

    total_linked = 0
    company_count = len(by_company)

    for idx, (company_id, info) in enumerate(by_company.items(), 1):
        print(f"  [{idx}/{company_count}] {info['ticker']}...", end="", flush=True)

        try:
            # Get company's indenture documents
            result = await session.execute(text("""
                SELECT id, section_title, content
                FROM document_sections
                WHERE company_id = :cid
                  AND section_type IN ('indenture', 'credit_agreement')
                ORDER BY filing_date DESC
            """), {"cid": company_id})
            docs = result.fetchall()

            if not docs:
                print(f" no docs", flush=True)
                continue

            company_linked = 0
            for inst in info["instruments"]:
                best_match = None
                best_confidence = 0

                for doc_id, doc_title, doc_content in docs:
                    if not doc_content:
                        continue

                    matches = find_instrument_in_document(inst, doc_content)
                    if matches:
                        # Take the best match
                        top = max(matches, key=lambda m: m["confidence"])
                        if top["confidence"] > best_confidence:
                            best_confidence = top["confidence"]
                            best_match = {
                                "doc_id": doc_id,
                                "doc_title": doc_title,
                                "match_type": top["type"],
                                "pattern": top["pattern"],
                                "confidence": top["confidence"],
                            }

                if best_match:
                    company_linked += 1

                    if not dry_run:
                        # Check for existing link
                        existing = await session.execute(text("""
                            SELECT id FROM debt_instrument_documents
                            WHERE debt_instrument_id = :inst_id
                              AND document_section_id = :doc_id
                        """), {"inst_id": inst["id"], "doc_id": best_match["doc_id"]})

                        if not existing.fetchone():
                            new_link = DebtInstrumentDocument(
                                debt_instrument_id=inst["id"],
                                document_section_id=best_match["doc_id"],
                                relationship_type='governs',
                                match_confidence=Decimal(str(best_match["confidence"])),
                                match_method='smart_matching',
                                match_evidence={
                                    'match_type': best_match["match_type"],
                                    'pattern': best_match["pattern"],
                                    'source': 'finnhub_linker',
                                },
                                is_verified=False,
                                created_by='finnhub_linker',
                            )
                            session.add(new_link)
                            total_linked += 1

            print(f" {company_linked}/{len(info['instruments'])} matched", flush=True)

        except Exception as e:
            print(f" error: {e}", flush=True)
            await session.rollback()

    if not dry_run and total_linked > 0:
        await session.commit()

    print(f"  Pattern-matched: {total_linked}", flush=True)
    return total_linked


# =============================================================================
# STEP 3: Heuristic document matching
# =============================================================================

async def step_heuristic_match(session, ticker=None, dry_run=False):
    """Match remaining unlinked Finnhub bonds using heuristic scoring."""
    print()
    print("STEP 3: Heuristic document matching")
    print("-" * 70)

    # Get still-unlinked Finnhub bonds
    where_clause = """
        WHERE di.instrument_type = 'bond'
          AND di.is_active = true
          AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
          AND (di.attributes->>'source' = 'finnhub_discovery'
               OR (di.cusip IS NOT NULL AND di.isin IS NOT NULL
                   AND di.created_at >= :start_date))
          AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
               OR di.attributes->>'no_document_expected' != 'true')
    """
    params = {"start_date": PHASE4_START_DATE}

    if ticker:
        where_clause += " AND di.company_id = (SELECT id FROM companies WHERE ticker = :ticker)"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT di.id, di.company_id, di.name, di.interest_rate, di.maturity_date,
               di.instrument_type, c.ticker
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        {where_clause}
        ORDER BY c.ticker, di.name
    """), params)
    instruments = result.fetchall()

    print(f"  Still unlinked: {len(instruments)}", flush=True)

    if not instruments:
        return 0

    by_company = {}
    for row in instruments:
        inst_id, company_id, name, rate, maturity, inst_type, tick = row
        if company_id not in by_company:
            by_company[company_id] = {"ticker": tick, "instruments": []}
        by_company[company_id]["instruments"].append({
            "id": inst_id,
            "name": name or "",
            "interest_rate": rate,
            "maturity_date": maturity,
            "instrument_type": inst_type or "",
        })

    total_linked = 0
    company_count = len(by_company)

    for idx, (company_id, info) in enumerate(by_company.items(), 1):
        print(f"  [{idx}/{company_count}] {info['ticker']}...", end="", flush=True)

        try:
            # Get company's documents
            result = await session.execute(text("""
                SELECT id, section_title, section_type, content
                FROM document_sections
                WHERE company_id = :cid
                  AND section_type IN ('indenture', 'credit_agreement')
                ORDER BY filing_date DESC
            """), {"cid": company_id})
            docs = result.fetchall()

            if not docs:
                print(f" no docs", flush=True)
                continue

            company_linked = 0
            for inst in info["instruments"]:
                # Extract rate and year from instrument fields
                rate_str = None
                if inst["interest_rate"]:
                    rate_val = inst["interest_rate"] / 100  # bps to percent
                    rate_str = f"{rate_val:.2f}".rstrip('0').rstrip('.')

                year_str = None
                if inst["maturity_date"]:
                    year_str = str(inst["maturity_date"])[:4]

                # Also try extracting from name
                if not rate_str and inst["name"]:
                    rate_match = re.search(r'(\d+\.?\d*)\s*%', inst["name"])
                    if rate_match:
                        rate_str = rate_match.group(1)

                if not year_str and inst["name"]:
                    year_match = re.search(r'(?:due|20)(\d{2})\b', inst["name"])
                    if year_match:
                        year_str = "20" + year_match.group(1) if len(year_match.group(1)) == 2 else year_match.group(1)

                best_match = None
                best_score = 0

                for doc_id, doc_title, doc_section_type, doc_content in docs:
                    if not doc_content:
                        continue

                    score = 0
                    content_lower = doc_content[:5000].lower()

                    # Rate match (+0.4)
                    if rate_str and rate_str in content_lower:
                        score += 0.4

                    # Year match (+0.3)
                    if year_str and year_str in content_lower:
                        score += 0.3

                    # Bond-to-indenture type match (+0.2)
                    if 'bond' in inst["instrument_type"].lower() and doc_section_type == 'indenture':
                        score += 0.2

                    # Name similarity
                    name_lower = inst["name"].lower().replace(",", "").replace(".", "").replace("-", " ").strip()
                    if len(name_lower) > 10:
                        from difflib import SequenceMatcher
                        ratio = SequenceMatcher(None, name_lower, content_lower[:2000]).ratio()
                        if ratio > 0.3:
                            score += ratio * 0.3

                    if score > best_score and score >= 0.5:
                        best_score = score
                        best_match = {
                            "doc_id": doc_id,
                            "doc_title": doc_title,
                            "score": score,
                        }

                if best_match:
                    company_linked += 1

                    if not dry_run:
                        existing = await session.execute(text("""
                            SELECT id FROM debt_instrument_documents
                            WHERE debt_instrument_id = :inst_id
                              AND document_section_id = :doc_id
                        """), {"inst_id": inst["id"], "doc_id": best_match["doc_id"]})

                        if not existing.fetchone():
                            new_link = DebtInstrumentDocument(
                                debt_instrument_id=inst["id"],
                                document_section_id=best_match["doc_id"],
                                relationship_type='governs',
                                match_confidence=Decimal(str(round(best_match["score"], 3))),
                                match_method='heuristic',
                                match_evidence={
                                    'rate': rate_str,
                                    'year': year_str,
                                    'source': 'finnhub_linker',
                                },
                                is_verified=False,
                                created_by='finnhub_linker',
                            )
                            session.add(new_link)
                            total_linked += 1

            print(f" {company_linked}/{len(info['instruments'])} matched", flush=True)

        except Exception as e:
            print(f" error: {e}", flush=True)
            await session.rollback()

    if not dry_run and total_linked > 0:
        await session.commit()

    print(f"  Heuristic-matched: {total_linked}", flush=True)
    return total_linked


# =============================================================================
# STEP 4: Base indenture fallback
# =============================================================================

async def step_base_indenture_fallback(session, ticker=None, dry_run=False):
    """Link remaining unlinked Finnhub bonds to company's base indenture."""
    print()
    print("STEP 4: Base indenture fallback")
    print("-" * 70)

    # Get still-unlinked Finnhub bonds
    where_clause = """
        WHERE di.instrument_type = 'bond'
          AND di.is_active = true
          AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
          AND (di.attributes->>'source' = 'finnhub_discovery'
               OR (di.cusip IS NOT NULL AND di.isin IS NOT NULL
                   AND di.created_at >= :start_date))
          AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
               OR di.attributes->>'no_document_expected' != 'true')
    """
    params = {"start_date": PHASE4_START_DATE}

    if ticker:
        where_clause += " AND di.company_id = (SELECT id FROM companies WHERE ticker = :ticker)"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT di.id, di.company_id, di.name, c.ticker
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        {where_clause}
        ORDER BY c.ticker, di.name
    """), params)
    instruments = result.fetchall()

    print(f"  Still unlinked: {len(instruments)}", flush=True)

    if not instruments:
        return 0

    # Group by company
    by_company = {}
    for inst_id, company_id, name, tick in instruments:
        if company_id not in by_company:
            by_company[company_id] = {"ticker": tick, "instruments": []}
        by_company[company_id]["instruments"].append({"id": inst_id, "name": name})

    total_linked = 0

    for company_id, info in by_company.items():
        try:
            # Get indenture documents
            result = await session.execute(text("""
                SELECT id, section_title, content
                FROM document_sections
                WHERE company_id = :cid AND section_type = 'indenture'
                ORDER BY filing_date DESC
            """), {"cid": company_id})
            docs = result.fetchall()

            if not docs:
                continue

            # Find best indenture (base > supplemental > any)
            best_indenture = None
            any_indenture = None

            for doc_id, title, content in docs:
                if is_base_indenture(title, content or ''):
                    best_indenture = {
                        'id': doc_id,
                        'title': title,
                        'type': 'base',
                    }
                    break
                elif any_indenture is None and is_any_indenture(title, content or ''):
                    any_indenture = {
                        'id': doc_id,
                        'title': title,
                        'type': 'supplemental',
                    }

            if not best_indenture:
                best_indenture = any_indenture

            if not best_indenture:
                continue

            confidence = Decimal('0.60') if best_indenture['type'] == 'base' else Decimal('0.55')
            method = 'base_indenture_fallback' if best_indenture['type'] == 'base' else 'suppl_indenture_fallback'

            doc_display = (best_indenture['title'] or 'Unknown')[:40]
            print(f"  [{info['ticker']}] {len(info['instruments'])} bonds -> {doc_display} ({best_indenture['type']}, {confidence})", flush=True)

            for inst in info["instruments"]:
                if not dry_run:
                    existing = await session.execute(text("""
                        SELECT id FROM debt_instrument_documents
                        WHERE debt_instrument_id = :inst_id
                          AND document_section_id = :doc_id
                    """), {"inst_id": inst["id"], "doc_id": best_indenture["id"]})

                    if not existing.fetchone():
                        new_link = DebtInstrumentDocument(
                            debt_instrument_id=inst["id"],
                            document_section_id=best_indenture["id"],
                            relationship_type='governs',
                            match_confidence=confidence,
                            match_method=method,
                            match_evidence={
                                'note': f"Linked to {best_indenture['type']} indenture; specific supplemental not found",
                                'source': 'finnhub_linker',
                            },
                            is_verified=False,
                            created_by='finnhub_linker',
                        )
                        session.add(new_link)
                        total_linked += 1

        except Exception as e:
            print(f"  [{info['ticker']}] error: {e}", flush=True)
            await session.rollback()

    if not dry_run and total_linked > 0:
        await session.commit()

    print(f"  Base-indenture linked: {total_linked}", flush=True)
    return total_linked


# =============================================================================
# STEP 5: Mark unmatchable bonds
# =============================================================================

async def step_mark_no_doc(session, ticker=None, dry_run=False):
    """Mark Finnhub bonds that can't be linked (no indenture docs available)."""
    print()
    print("STEP 5: Mark unmatchable bonds")
    print("-" * 70)

    # Get still-unlinked Finnhub bonds
    where_clause = """
        WHERE di.instrument_type = 'bond'
          AND di.is_active = true
          AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
          AND (di.attributes->>'source' = 'finnhub_discovery'
               OR (di.cusip IS NOT NULL AND di.isin IS NOT NULL
                   AND di.created_at >= :start_date))
          AND (di.attributes IS NULL OR di.attributes->>'no_document_expected' IS NULL
               OR di.attributes->>'no_document_expected' != 'true')
    """
    params = {"start_date": PHASE4_START_DATE}

    if ticker:
        where_clause += " AND di.company_id = (SELECT id FROM companies WHERE ticker = :ticker)"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT di.id, di.company_id, di.name, c.ticker
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        {where_clause}
        ORDER BY c.ticker, di.name
    """), params)
    instruments = result.fetchall()

    print(f"  Still unlinked after all matching: {len(instruments)}")

    if not instruments:
        return 0

    # Check which companies genuinely have no indenture docs
    company_ids = list(set(str(row[1]) for row in instruments))

    # Get companies that DO have indenture documents
    result = await session.execute(text("""
        SELECT DISTINCT company_id
        FROM document_sections
        WHERE section_type = 'indenture'
    """))
    companies_with_indentures = set(row[0] for row in result.fetchall())

    to_mark = []
    for inst_id, company_id, name, tick in instruments:
        if company_id not in companies_with_indentures:
            to_mark.append(inst_id)
            inst_display = (name or "Unknown")[:50]
            print(f"  [{tick}] {inst_display} - no indenture documents available")

    print(f"  Bonds to mark as no-doc-expected: {len(to_mark)}")

    if not dry_run and to_mark:
        for i in range(0, len(to_mark), 100):
            batch = to_mark[i:i+100]
            placeholders = ", ".join([f"'{id}'::uuid" for id in batch])
            await session.execute(text(f"""
                UPDATE debt_instruments
                SET attributes = COALESCE(attributes, '{{}}'::jsonb)
                    || '{{"no_document_expected": true, "no_doc_reason": "no_indenture_documents_available"}}'::jsonb
                WHERE id IN ({placeholders})
            """))

        await session.commit()
        print(f"  Marked {len(to_mark)} bonds")

    # Report remaining truly unlinked (have indentures but didn't match)
    remaining = len(instruments) - len(to_mark)
    if remaining > 0:
        print(f"  Note: {remaining} bonds remain unlinked despite available indentures (manual review needed)")

    return len(to_mark)


# =============================================================================
# STEP 6: Extract guarantees
# =============================================================================

async def step_extract_guarantees(session, ticker=None, dry_run=False):
    """Extract guarantees for companies with newly-linked Finnhub bonds."""
    print()
    print("STEP 6: Extract guarantees for linked Finnhub bonds")
    print("-" * 70)

    # Find companies that have Finnhub bonds with document links
    where_clause = """
        WHERE did.created_by = 'finnhub_linker'
    """
    params = {}

    if ticker:
        where_clause += " AND c.ticker = :ticker"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT DISTINCT c.id, c.ticker, c.name
        FROM companies c
        JOIN debt_instruments di ON di.company_id = c.id
        JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
        {where_clause}
        ORDER BY c.ticker
    """), params)
    companies = result.fetchall()

    print(f"  Companies with linked Finnhub bonds: {len(companies)}")

    if not companies or dry_run:
        if dry_run:
            print("  (dry run - skipping extraction)")
        return 0

    # Import guarantee extraction
    from app.services.guarantee_extraction import extract_guarantees

    total = 0
    for company_id, tick, name in companies:
        print(f"  [{tick}] {name}...", end="", flush=True)
        try:
            count = await extract_guarantees(session, company_id, tick, {})
            print(f" {count} guarantees")
            total += count
        except Exception as e:
            print(f" error: {e}")

        await asyncio.sleep(1)  # Rate limiting

    print(f"  Total guarantees created: {total}")
    return total


# =============================================================================
# STEP 7: Extract collateral
# =============================================================================

async def step_extract_collateral(session, ticker=None, dry_run=False):
    """Extract collateral for secured Finnhub bonds with document links."""
    print()
    print("STEP 7: Extract collateral for secured Finnhub bonds")
    print("-" * 70)

    # Find companies with secured Finnhub bonds that have document links
    where_clause = """
        WHERE did.created_by = 'finnhub_linker'
          AND di.seniority = 'senior_secured'
    """
    params = {}

    if ticker:
        where_clause += " AND c.ticker = :ticker"
        params["ticker"] = ticker

    result = await session.execute(text(f"""
        SELECT DISTINCT c.id, c.ticker, c.name
        FROM companies c
        JOIN debt_instruments di ON di.company_id = c.id
        JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
        {where_clause}
        ORDER BY c.ticker
    """), params)
    companies = result.fetchall()

    print(f"  Companies with linked secured Finnhub bonds: {len(companies)}")

    if not companies or dry_run:
        if dry_run:
            print("  (dry run - skipping extraction)")
        return 0

    # Import collateral extraction
    from app.services.collateral_extraction import extract_collateral

    total = 0
    for company_id, tick, name in companies:
        print(f"  [{tick}] {name}...", end="", flush=True)
        try:
            count = await extract_collateral(session, company_id, tick, {})
            print(f" {count} collateral records")
            total += count
        except Exception as e:
            print(f" error: {e}")

        await asyncio.sleep(1)  # Rate limiting

    print(f"  Total collateral records created: {total}")
    return total


# =============================================================================
# Summary stats
# =============================================================================

async def print_coverage_stats(session):
    """Print coverage metrics before and after linking."""
    print()
    print("=" * 70)
    print("COVERAGE METRICS")
    print("=" * 70)

    result = await session.execute(text("""
        SELECT
            COUNT(DISTINCT di.id) as total,
            COUNT(DISTINCT did.debt_instrument_id) as linked,
            COUNT(DISTINCT CASE WHEN di.attributes->>'no_document_expected' = 'true' THEN di.id END) as no_doc_expected,
            COUNT(DISTINCT CASE WHEN di.attributes->>'source' = 'finnhub_discovery' THEN di.id END) as finnhub_bonds,
            COUNT(DISTINCT CASE
                WHEN di.attributes->>'source' = 'finnhub_discovery'
                 AND did.debt_instrument_id IS NOT NULL THEN di.id END) as finnhub_linked
        FROM debt_instruments di
        LEFT JOIN debt_instrument_documents did ON did.debt_instrument_id = di.id
        WHERE di.is_active = true
    """))
    row = result.fetchone()

    total, linked, no_doc, finnhub_total, finnhub_linked = row
    linkable = total - no_doc

    print(f"  Total active instruments:     {total}")
    print(f"  No document expected:         {no_doc}")
    print(f"  Linkable instruments:         {linkable}")
    print(f"  Actually linked:              {linked}")
    if linkable > 0:
        print(f"  Adjusted coverage:            {linked/linkable*100:.1f}%")
    print()
    print(f"  Finnhub bonds total:          {finnhub_total}")
    print(f"  Finnhub bonds linked:         {finnhub_linked}")
    if finnhub_total > 0:
        print(f"  Finnhub link rate:            {finnhub_linked/finnhub_total*100:.1f}%")

    # Breakdown by match method for Finnhub bonds
    result = await session.execute(text("""
        SELECT did.match_method, COUNT(*)
        FROM debt_instrument_documents did
        WHERE did.created_by = 'finnhub_linker'
        GROUP BY did.match_method
        ORDER BY COUNT(*) DESC
    """))
    methods = result.fetchall()

    if methods:
        print()
        print("  Finnhub link methods:")
        for method, count in methods:
            print(f"    {method or 'unknown'}: {count}")


# =============================================================================
# Main
# =============================================================================

ALL_STEPS = {
    'backfill': step_backfill_source_tags,
    'match': step_pattern_match,
    'heuristic': step_heuristic_match,
    'base-indenture': step_base_indenture_fallback,
    'mark-no-doc': step_mark_no_doc,
    'guarantees': step_extract_guarantees,
    'collateral': step_extract_collateral,
}


async def run_pipeline(args):
    """Run the full Finnhub bond linking pipeline."""
    print("=" * 70)
    print("FINNHUB BOND LINKING PIPELINE")
    print("=" * 70)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    if args.ticker:
        print(f"Ticker: {args.ticker}")
    if args.step:
        print(f"Step: {args.step}")

    # Determine which steps to run
    if args.step:
        steps_to_run = {args.step: ALL_STEPS[args.step]}
    else:
        steps_to_run = ALL_STEPS

    stats = {}

    # Use a fresh session per step to prevent cascade failures
    # (Neon free tier can drop connections on large queries)
    for step_name, step_func in steps_to_run.items():
        try:
            async with async_session_maker() as session:
                count = await step_func(
                    session,
                    ticker=args.ticker,
                    dry_run=args.dry_run,
                )
                stats[step_name] = count
        except Exception as e:
            print(f"\n  ERROR in {step_name}: {e}")
            stats[step_name] = f"error: {e}"

    # Print summary with fresh session
    try:
        async with async_session_maker() as session:
            await print_coverage_stats(session)
    except Exception as e:
        print(f"\n  ERROR getting coverage stats: {e}")

    print()
    print("=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    for step_name, count in stats.items():
        print(f"  {step_name}: {count}")


async def main():
    parser = argparse.ArgumentParser(
        description="Link Finnhub-discovered bonds to indenture documents"
    )
    parser.add_argument("--ticker", type=str, help="Process single company by ticker")
    parser.add_argument("--all", action="store_true", help="Process all companies")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument(
        "--step",
        choices=list(ALL_STEPS.keys()),
        help="Run a specific step only"
    )
    args = parser.parse_args()

    if not args.ticker and not args.all and not args.step:
        parser.error("Specify --ticker, --all, or --step")

    await run_pipeline(args)


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

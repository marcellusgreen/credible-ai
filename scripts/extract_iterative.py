#!/usr/bin/env python3
"""
Complete company extraction pipeline - IDEMPOTENT VERSION.

This is THE script for adding a new company to the database. It runs all
extraction steps in the correct order, efficiently reusing data between steps.

IDEMPOTENT: Safe to re-run for existing companies. Skips steps where:
  - Data already exists (e.g., entity_count > 20)
  - Source data is unavailable (e.g., no Exhibit 21) - tracked via extraction_status
Use --force to override skip logic.

Skip conditions:
  1. Download filings - never skipped
  2. Core extraction - skip if entity_count > 20 AND debt_count > 0
  3. Save to DB - uses merge logic to preserve existing data
  4. Document sections - skip if count > 5
  5. TTM financials - skip if latest_quarter is current (checks if 60+ days past next quarter end)
  6. Ownership hierarchy - skip if entity_count > 50 OR status='no_data' (no Exhibit 21)
  7. Guarantees - skip if guarantee_count > 0 OR status='no_data'
  8. Collateral - skip if collateral_count > 0 OR status='no_data'
  9. Metrics computation - always run
  10. QC validation

The extraction_status field in company_cache tracks step attempts:
  - "success": Step completed with data (includes metadata like latest_quarter)
  - "no_data": Step attempted but source data unavailable (won't retry)
  - "error": Step failed (will retry on next run)

Financials tracking example:
  {"financials": {"status": "success", "latest_quarter": "2025Q3", "attempted_at": "..."}}
  - If current date is 60+ days past next quarter end, will re-extract for new data

Usage:
    # Single company
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

    # Single company with force (re-run all steps)
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db --force

    # All companies (batch mode)
    python scripts/extract_iterative.py --all --save-db

    # Resume batch from last company
    python scripts/extract_iterative.py --all --save-db --resume

    # Dry run (show what would be done)
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193

Environment variables:
    GEMINI_API_KEY - Required for extraction
    ANTHROPIC_API_KEY - Optional, for Claude escalation
    SEC_API_KEY - Optional, for faster filing retrieval
    DATABASE_URL - Required for --save-db
"""

import argparse
import asyncio
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.iterative_extraction import IterativeExtractionService, IterativeExtractionResult
from app.services.extraction import SecApiClient, SECEdgarClient, check_existing_data, merge_extraction_to_db, save_extraction_to_db


# =============================================================================
# DATA STRUCTURES FOR EXHIBIT 21
# =============================================================================

@dataclass
class SubsidiaryEntry:
    """A subsidiary extracted from Exhibit 21 with indentation level."""
    name: str
    jurisdiction: Optional[str]
    indent_level: int  # 0 = root, 1 = direct, 2 = grandchild, etc.
    raw_line: str  # Original text for debugging


@dataclass
class HierarchyRelationship:
    """A parent-child relationship derived from indentation."""
    parent_name: str
    child_name: str
    ownership_type: str  # 'direct' or 'indirect'


# =============================================================================
# FILING DOWNLOAD
# =============================================================================

async def download_filings(ticker: str, cik: str, sec_api_key: str = None) -> dict[str, str]:
    """Download all relevant filings."""
    filings = {}

    if sec_api_key:
        print(f"  Downloading filings via SEC-API...")
        sec_client = SecApiClient(sec_api_key)
        filings = await sec_client.get_all_relevant_filings(ticker, cik=cik)
        exhibit_21 = sec_client.get_exhibit_21(ticker)
        if exhibit_21:
            filings['exhibit_21'] = exhibit_21
        print(f"    Downloaded {len(filings)} filings")
    else:
        print(f"  Downloading filings via SEC EDGAR...")
        edgar = SECEdgarClient()
        filings = await edgar.get_all_relevant_filings(cik)
        await edgar.close()
        print(f"    Downloaded {len(filings)} filings")

    return filings


# =============================================================================
# EXHIBIT 21 PARSING (Full implementation from extract_exhibit21_hierarchy.py)
# =============================================================================

async def fetch_exhibit21_html(cik: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Fetch raw Exhibit 21 HTML from SEC EDGAR.
    Returns the raw HTML without any cleaning, so we can parse indentation.
    """
    cik_clean = cik.lstrip('0')
    cik_padded = cik.zfill(10)

    filings_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        await asyncio.sleep(0.2)  # Rate limit
        response = await client.get(filings_url)
        if response.status_code != 200:
            return None

        data = response.json()
        filings = data.get("filings", {}).get("recent", {})

        forms = filings.get("form", [])
        accession_numbers = filings.get("accessionNumber", [])

        ten_k_accession = None
        for i, form in enumerate(forms):
            if form == "10-K":
                ten_k_accession = accession_numbers[i]
                break

        if not ten_k_accession:
            return None

        accession_formatted = ten_k_accession.replace("-", "")

        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/index.json"
        await asyncio.sleep(0.2)
        response = await client.get(index_url)
        if response.status_code != 200:
            return None

        index_data = response.json()
        items = index_data.get("directory", {}).get("item", [])

        exhibit_candidates = []
        for item in items:
            name = item.get("name", "").lower()
            if not name.endswith((".htm", ".html")):
                continue

            if name.startswith('r') and name[1:].startswith('2'):
                continue

            is_ex21 = False
            if re.search(r'ex[hx]?[-_]?21', name):
                is_ex21 = True
            elif re.search(r'exhibit[-_]?21', name):
                is_ex21 = True
            elif re.search(r'21[-_]?1\.htm', name):
                is_ex21 = True

            if 'ex321' in name or 'exx321' in name:
                is_ex21 = False

            if is_ex21:
                exhibit_candidates.append(item['name'])

        for candidate in exhibit_candidates:
            exhibit_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{accession_formatted}/{candidate}"
            response = await client.get(exhibit_url)
            if response.status_code == 200:
                content = response.text.lower()
                if 'subsidiary' in content or 'jurisdiction' in content or 'delaware' in content:
                    return response.text

        return None

    except Exception as e:
        print(f"      Error fetching Exhibit 21: {e}")
        return None


def detect_indent_from_html(raw_html: str) -> list[SubsidiaryEntry]:
    """
    Parse HTML to extract subsidiaries with their indentation levels.
    Handles both table-based (<tr>/<td>) and div-based layouts.
    """
    entries = []

    clean = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.IGNORECASE | re.DOTALL)

    # Skip patterns for headers/noise
    skip_patterns = [
        r'^name\s*(of\s*)?(jurisdiction|country)?$',
        r'^name\s+jurisdiction',
        r'^subsidiary',
        r'^exhibit',
        r'^page\s*\d',
        r'^\d+$',
        r'^state.*incorporation',
        r'^jurisdiction\s*(of)?',
        r'^country\s*(name)?',
        r'^\*+$',
        r'^the following',
        r'^indentation',
        r'^list of subsidiar',
        r'^meta platforms',  # Company name header
        r'^document$',
    ]

    def should_skip(text: str) -> bool:
        text_lower = text.lower().strip()
        if any(re.match(p, text_lower) for p in skip_patterns):
            return True
        if text.isupper() and len(text) < 20:
            return True
        return False

    def extract_jurisdiction_from_text(text: str) -> tuple[str, str]:
        """Extract jurisdiction from parentheses at end of name, e.g., 'Company LLC (Delaware)'"""
        match = re.search(r'\(([^)]+)\)\s*$', text)
        if match:
            jurisdiction = match.group(1).strip()
            name = text[:match.start()].strip()
            return name, jurisdiction
        return text, None

    # Try table-based parsing first
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', clean, flags=re.IGNORECASE | re.DOTALL)

    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.IGNORECASE | re.DOTALL)

        if not cells:
            continue

        empty_cell_count = 0
        name_cell_idx = 0

        for i, cell in enumerate(cells):
            cell_text = re.sub(r'<[^>]+>', '', cell)
            cell_text = html.unescape(cell_text)
            cell_text = cell_text.strip()

            if not cell_text or cell_text.replace('\xa0', '').strip() == '':
                empty_cell_count += 1
            else:
                name_cell_idx = i
                break

        if name_cell_idx >= len(cells):
            continue

        name_cell = cells[name_cell_idx]

        nbsp_pattern = r'^[\s]*(?:&#160;|&nbsp;|\xa0)+'
        nbsp_match = re.match(nbsp_pattern, name_cell, re.IGNORECASE)
        nbsp_count = 0
        if nbsp_match:
            nbsp_text = nbsp_match.group(0)
            nbsp_count = nbsp_text.count('&#160;') + nbsp_text.count('&nbsp;') + nbsp_text.count('\xa0')

        indent_level = empty_cell_count + (nbsp_count // 2)

        padding_match = re.search(r'padding-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if padding_match:
            padding_px = int(padding_match.group(1))
            indent_level = max(indent_level, padding_px // 15)

        margin_match = re.search(r'margin-left:\s*(\d+)', name_cell, re.IGNORECASE)
        if margin_match:
            margin_px = int(margin_match.group(1))
            indent_level = max(indent_level, margin_px // 15)

        text = re.sub(r'<[^>]+>', '', name_cell)
        text = html.unescape(text)
        text = ' '.join(text.split())

        if not text or len(text) < 3:
            continue

        if should_skip(text):
            continue

        # Try to get jurisdiction from adjacent cell first
        jurisdiction = None
        for j in range(name_cell_idx + 1, min(name_cell_idx + 3, len(cells))):
            jur_text = re.sub(r'<[^>]+>', '', cells[j])
            jur_text = html.unescape(jur_text).strip()
            if jur_text and len(jur_text) > 1 and len(jur_text) < 50:
                if jur_text.replace('\xa0', '').strip():
                    jurisdiction = jur_text
                    break

        # If no jurisdiction in adjacent cell, try extracting from name
        if not jurisdiction:
            text, jurisdiction = extract_jurisdiction_from_text(text)

        entries.append(SubsidiaryEntry(
            name=text,
            jurisdiction=jurisdiction,
            indent_level=indent_level,
            raw_line=name_cell[:100],
        ))

    # If no table rows found, try div-based parsing (like META's format)
    if not entries:
        # Look for divs containing font tags with subsidiary names
        # Pattern: <div><font ...>Company Name (Jurisdiction)</font></div>
        div_pattern = r'<div[^>]*>\s*<font[^>]*>([^<]+)</font>\s*</div>'
        div_matches = re.findall(div_pattern, clean, flags=re.IGNORECASE)

        for match in div_matches:
            text = html.unescape(match)
            text = ' '.join(text.split())

            if not text or len(text) < 3:
                continue

            if should_skip(text):
                continue

            # Extract jurisdiction from parentheses
            name, jurisdiction = extract_jurisdiction_from_text(text)

            if not name or len(name) < 3:
                continue

            # All div-based entries are at the same level (flat list)
            entries.append(SubsidiaryEntry(
                name=name,
                jurisdiction=jurisdiction,
                indent_level=0,  # Flat structure
                raw_line=text[:100],
            ))

    return entries


def build_hierarchy_from_entries(entries: list[SubsidiaryEntry], root_company_name: str) -> list[HierarchyRelationship]:
    """
    Build parent-child relationships from indented entries.
    """
    relationships = []

    if not entries:
        return relationships

    parent_stack = [(0, root_company_name)]

    for entry in entries:
        while parent_stack and parent_stack[-1][0] >= entry.indent_level:
            parent_stack.pop()

        if parent_stack:
            parent_name = parent_stack[-1][1]
        else:
            parent_name = root_company_name

        if entry.indent_level <= 1:
            ownership_type = 'direct'
        else:
            ownership_type = 'indirect'

        relationships.append(HierarchyRelationship(
            parent_name=parent_name,
            child_name=entry.name,
            ownership_type=ownership_type,
        ))

        parent_stack.append((entry.indent_level, entry.name))

    return relationships


def normalize_name_for_matching(name: str) -> str:
    """Normalize entity name for fuzzy matching."""
    name = name.lower()
    for suffix in [', inc.', ', inc', ' inc.', ' inc', ', llc', ' llc',
                   ', ltd.', ', ltd', ' ltd.', ' ltd', ', corp.', ', corp',
                   ' corp.', ' corp', ', corporation', ' corporation',
                   ', l.l.c.', ' l.l.c.', ', limited', ' limited']:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    name = re.sub(r'[^\w\s]', ' ', name)
    name = ' '.join(name.split())
    return name.strip()


# =============================================================================
# ENRICHMENT FUNCTIONS
# =============================================================================

async def extract_ownership_hierarchy(session, company_id: UUID, ticker: str, cik: str, company_name: str) -> dict:
    """
    Extract ownership hierarchy from Exhibit 21 indentation.

    FULL implementation that:
    1. Fetches raw Exhibit 21 HTML from SEC EDGAR
    2. Parses indentation using detect_indent_from_html()
    3. Builds hierarchy using build_hierarchy_from_entries()
    4. Creates missing entities found in Exhibit 21
    5. Updates parent_id and creates OwnershipLink records
    """
    from sqlalchemy import select
    from app.models import Entity, OwnershipLink

    stats = {
        'entries_found': 0,
        'relationships_built': 0,
        'entities_created': 0,
        'entities_updated': 0,
        'links_created': 0,
        'not_matched': [],
    }

    headers = {
        "User-Agent": "DebtStack research@debtstack.ai",
        "Accept-Encoding": "gzip, deflate",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        html_content = await fetch_exhibit21_html(cik, client)

    if not html_content:
        print(f"      No Exhibit 21 found")
        return stats

    # Parse indentation to get entries
    entries = detect_indent_from_html(html_content)
    stats['entries_found'] = len(entries)

    if not entries:
        print(f"      No entities parsed from Exhibit 21")
        return stats

    print(f"      Found {len(entries)} entities in Exhibit 21")

    # Build hierarchy from indentation
    relationships = build_hierarchy_from_entries(entries, company_name)
    stats['relationships_built'] = len(relationships)

    # Get existing entities
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    existing_entities = result.scalars().all()

    # Build lookup by normalized name
    entity_by_name = {}
    for e in existing_entities:
        normalized = normalize_name_for_matching(e.name)
        entity_by_name[normalized] = e
        entity_by_name[e.name.lower()] = e

    # Create missing entities from Exhibit 21
    entities_to_create = []
    for entry in entries:
        normalized = normalize_name_for_matching(entry.name)
        if normalized not in entity_by_name and entry.name.lower() not in entity_by_name:
            # Entity doesn't exist, create it
            entity_id = uuid4()
            entity = Entity(
                id=entity_id,
                company_id=company_id,
                name=entry.name,
                slug=re.sub(r'[^a-z0-9]+', '-', entry.name.lower())[:255],
                entity_type='subsidiary',
                jurisdiction=entry.jurisdiction,
                structure_tier=3,
                is_domestic=entry.jurisdiction in ['Delaware', 'California', 'Texas', 'New York', 'Nevada', 'Florida', 'Ohio', 'Illinois', 'Pennsylvania', 'Georgia', 'North Carolina', 'Virginia', 'Michigan', 'New Jersey', 'Washington', 'Arizona', 'Massachusetts', 'Maryland', 'Colorado', 'Minnesota'] if entry.jurisdiction else True,
            )
            session.add(entity)
            entities_to_create.append(entity)
            entity_by_name[normalized] = entity
            entity_by_name[entry.name.lower()] = entity
            stats['entities_created'] += 1

    if entities_to_create:
        await session.flush()
        print(f"      Created {len(entities_to_create)} new entities")

    # Mark root entity
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id, Entity.entity_type == 'holdco')
    )
    holdco = result.scalar_one_or_none()
    if holdco:
        holdco.is_root = True
        entity_by_name[normalize_name_for_matching(company_name)] = holdco
        entity_by_name[company_name.lower()] = holdco

    # Update hierarchy relationships
    for rel in relationships:
        parent_normalized = normalize_name_for_matching(rel.parent_name)
        parent_entity = entity_by_name.get(parent_normalized) or entity_by_name.get(rel.parent_name.lower())

        child_normalized = normalize_name_for_matching(rel.child_name)
        child_entity = entity_by_name.get(child_normalized) or entity_by_name.get(rel.child_name.lower())

        if not child_entity:
            stats['not_matched'].append(rel.child_name)
            continue

        if not parent_entity:
            if normalize_name_for_matching(rel.parent_name) == normalize_name_for_matching(company_name):
                parent_entity = holdco
            else:
                stats['not_matched'].append(f"Parent: {rel.parent_name}")
                continue

        # Update entity.parent_id
        if parent_entity and child_entity.parent_id != parent_entity.id:
            child_entity.parent_id = parent_entity.id
            stats['entities_updated'] += 1

        # Check for existing ownership_link
        if parent_entity:
            existing_link = await session.scalar(
                select(OwnershipLink).where(
                    OwnershipLink.child_entity_id == child_entity.id
                )
            )

            if not existing_link:
                new_link = OwnershipLink(
                    id=uuid4(),
                    parent_entity_id=parent_entity.id,
                    child_entity_id=child_entity.id,
                    ownership_pct=100.0,
                    ownership_type=rel.ownership_type,
                )
                session.add(new_link)
                stats['links_created'] += 1

    await session.commit()

    print(f"      Updated {stats['entities_updated']} entity parents, created {stats['links_created']} links")
    if stats['not_matched']:
        print(f"      Not matched: {len(stats['not_matched'])} entities")

    return stats


async def extract_guarantees(session, company_id: UUID, ticker: str, filings: dict) -> int:
    """Extract guarantee relationships from indentures and credit agreements."""
    import google.generativeai as genai
    from sqlalchemy import select
    from app.models import Entity, DebtInstrument, Guarantee, DocumentSection
    from app.services.utils import parse_json_robust
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        return 0

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # Get entities and debt instruments
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    entities = list(result.scalars().all())
    entity_names = [e.name for e in entities]
    entity_map = {e.name.lower(): e.id for e in entities}

    result = await session.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    instruments = list(result.scalars().all())

    if not instruments or not entities:
        return 0

    # Get document sections (indentures, credit agreements)
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['indenture', 'credit_agreement', 'guarantor_list'])
        ).limit(5)
    )
    docs = list(result.scalars().all())

    # Build context from filings if no stored docs
    doc_content = ""
    if docs:
        doc_content = "\n\n".join([f"=== {d.section_type} ===\n{d.content[:30000]}" for d in docs])
    else:
        # Use raw filings
        for key, content in list(filings.items())[:3]:
            if content:
                doc_content += f"\n\n=== {key} ===\n{content[:30000]}"

    if not doc_content:
        return 0

    # Build prompt
    entity_list = "\n".join([f"- {name}" for name in entity_names[:50]])
    debt_list = "\n".join([f"- {i.name}" for i in instruments[:30]])

    prompt = f"""Analyze these documents to extract guarantee relationships for {ticker}.

ENTITIES (use exact names):
{entity_list}

DEBT INSTRUMENTS:
{debt_list}

DOCUMENTS:
{doc_content[:50000]}

Return JSON:
{{
  "guarantees": [
    {{"debt_name": "exact instrument name", "guarantor_names": ["Entity 1", "Entity 2"], "guarantee_type": "full"}}
  ]
}}

Only include guarantors that EXACTLY match entity names above."""

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        guarantees_created = 0
        for g in result_data.get('guarantees', []):
            debt_name = g.get('debt_name', '').lower()
            # Find matching instrument
            instrument = next((i for i in instruments if i.name and i.name.lower() == debt_name), None)
            if not instrument:
                continue

            for guarantor_name in g.get('guarantor_names', []):
                entity_id = entity_map.get(guarantor_name.lower())
                if not entity_id:
                    continue

                # Check if guarantee already exists
                existing = await session.execute(
                    select(Guarantee).where(
                        Guarantee.debt_instrument_id == instrument.id,
                        Guarantee.guarantor_id == entity_id
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                guarantee = Guarantee(
                    id=uuid4(),
                    debt_instrument_id=instrument.id,
                    guarantor_id=entity_id,
                    guarantee_type=g.get('guarantee_type', 'full'),
                )
                session.add(guarantee)
                guarantees_created += 1

        await session.commit()
        return guarantees_created
    except Exception as e:
        print(f"      Guarantee extraction error: {e}")
        return 0


def _extract_collateral_sections(content: str) -> str:
    """Extract sections of content that discuss collateral."""
    import re

    sections = []

    # Patterns that indicate collateral discussion
    patterns = [
        r'(?:secured|collateralized)\s+by[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:first|second)-priority\s+lien[^.]*\.(?:[^.]*\.){0,5}',
        r'All\s+obligations\s+under[^.]*secured[^.]*\.(?:[^.]*\.){0,10}',
        r'Collateral[^.]*includes?[^.]*\.(?:[^.]*\.){0,5}',
        r'(?:pledged|pledge)\s+(?:of\s+)?(?:substantially\s+all|all)[^.]*\.(?:[^.]*\.){0,5}',
        r'security\s+interest\s+in[^.]*\.(?:[^.]*\.){0,5}',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            if len(match) > 50:  # Skip very short matches
                sections.append(match.strip())

    # Also look for bullet points describing collateral
    bullet_pattern = r'(?:•|\*|-)\s*(?:a\s+)?(?:first|second)-priority\s+lien[^•\*\-\n]*'
    bullet_matches = re.findall(bullet_pattern, content, re.IGNORECASE)
    sections.extend(bullet_matches)

    if sections:
        return "\n\n".join(set(sections))[:20000]

    return ""


def _fuzzy_match_debt_name(name1: str, name2: str, threshold: float = 0.6) -> bool:
    """Check if two debt names are similar enough to match."""
    from difflib import SequenceMatcher

    if not name1 or not name2:
        return False
    name1 = name1.lower().strip()
    name2 = name2.lower().strip()

    # Exact match
    if name1 == name2:
        return True

    # One contains the other
    if name1 in name2 or name2 in name1:
        return True

    # Fuzzy match
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold


async def extract_collateral(session, company_id: UUID, ticker: str, filings: dict) -> int:
    """Extract collateral for secured debt instruments.

    Enhanced version with:
    - Better query to find secured instruments (checks seniority AND security_type)
    - Fuzzy matching of debt names
    - Extracts collateral-specific sections from documents
    - More comprehensive prompt with additional collateral types
    """
    import google.generativeai as genai
    from sqlalchemy import select, or_
    from app.models import DebtInstrument, Collateral, DocumentSection
    from app.services.utils import parse_json_robust
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        return 0

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    # Get secured debt instruments - check both seniority AND security_type
    result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company_id,
            DebtInstrument.is_active == True,
            or_(
                DebtInstrument.seniority.in_(['senior_secured', 'secured']),
                DebtInstrument.security_type.in_(['first_lien', 'second_lien'])
            )
        )
    )
    secured_instruments = list(result.scalars().all())

    if not secured_instruments:
        return 0

    # Filter to instruments that don't already have collateral
    instruments_with_collateral = set()
    result = await session.execute(
        select(Collateral.debt_instrument_id).where(
            Collateral.debt_instrument_id.in_([i.id for i in secured_instruments])
        )
    )
    for row in result:
        instruments_with_collateral.add(row[0])

    instruments_missing_collateral = [i for i in secured_instruments if i.id not in instruments_with_collateral]

    if not instruments_missing_collateral:
        return 0

    # Get document content - prioritize sections with collateral info
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            or_(
                DocumentSection.content.ilike('%secured by%'),
                DocumentSection.content.ilike('%collateral%'),
                DocumentSection.content.ilike('%first-priority lien%'),
                DocumentSection.content.ilike('%pledged%'),
                DocumentSection.content.ilike('%security interest%')
            )
        ).order_by(DocumentSection.section_type)
    )
    collateral_docs = list(result.scalars().all())

    # Also get credit agreements and debt footnotes
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_([
                'credit_agreement', 'indenture', 'debt_footnote',
                'debt_overview', 'long_term_debt'
            ])
        ).order_by(DocumentSection.section_type)
    )
    standard_docs = list(result.scalars().all())

    # Combine and dedupe
    seen_ids = set()
    all_docs = []
    for doc in collateral_docs + standard_docs:
        if doc.id not in seen_ids:
            seen_ids.add(doc.id)
            all_docs.append(doc)

    doc_content = ""
    if all_docs:
        content_parts = []
        for d in all_docs[:10]:
            collateral_sections = _extract_collateral_sections(d.content)
            if collateral_sections:
                content_parts.append(f"=== {d.section_type.upper()} (collateral sections) ===\n{collateral_sections}")
            else:
                content_parts.append(f"=== {d.section_type.upper()} ===\n{d.content[:30000]}")
        doc_content = "\n\n".join(content_parts)[:150000]
    else:
        for key, content in list(filings.items())[:2]:
            if content:
                doc_content += f"\n\n=== {key} ===\n{content[:50000]}"

    if not doc_content:
        return 0

    # Build detailed debt list with numbers for matching
    debt_list = []
    for i, inst in enumerate(instruments_missing_collateral[:30]):
        seniority = inst.seniority or 'unknown'
        sec_type = inst.security_type or 'unknown'
        principal = f"${inst.principal / 100 / 1e6:,.0f}MM" if inst.principal else "N/A"
        debt_list.append(f"{i+1}. {inst.name} | Seniority: {seniority} | Security: {sec_type} | Principal: {principal}")

    debt_str = "\n".join(debt_list)

    prompt = f"""Analyze this company's SEC filings to identify COLLATERAL securing these debt instruments.

COMPANY: {ticker}

SECURED DEBT INSTRUMENTS (numbered for reference):
{debt_str}

FILING CONTENT:
{doc_content[:100000]}

INSTRUCTIONS:
1. Find specific language describing what assets secure each debt instrument
2. Look for: "secured by", "collateralized by", "pledged", "first lien on", "security interest in"
3. Common collateral includes: real estate, equipment, receivables, inventory, vehicles, aircraft, ships, intellectual property, subsidiary stock, cash, securities
4. For credit facilities, look for "substantially all assets" or similar general security language

COLLATERAL TYPES (use these exact values):
- real_estate: Property, land, buildings, mortgages
- equipment: Machinery, rigs, manufacturing equipment
- receivables: Accounts receivable, notes receivable, securitization assets
- inventory: Raw materials, finished goods, work in progress
- vehicles: Aircraft, ships, trucks, fleet vehicles
- cash: Cash deposits, restricted cash
- ip: Intellectual property, patents, trademarks
- subsidiary_stock: Stock/equity of subsidiaries
- securities: Investment securities, marketable securities
- energy_assets: Oil/gas reserves, pipelines, power plants
- general_lien: "Substantially all assets" or blanket security interest

Return JSON with ONE collateral record per debt instrument (use the PRIMARY collateral type):
{{
  "collateral": [
    {{
      "debt_number": 1,
      "debt_name": "exact or close name from list",
      "collateral_type": "PRIMARY type from list above",
      "description": "comprehensive description of ALL collateral securing this debt",
      "priority": "first_lien or second_lien"
    }}
  ]
}}

IMPORTANT: Return only ONE record per debt instrument. If multiple collateral types secure one instrument, choose the PRIMARY type and include ALL types in the description.

Return ONLY valid JSON."""

    try:
        response = model.generate_content(prompt)
        result_data = parse_json_robust(response.text)

        collateral_created = 0
        for c in result_data.get('collateral', []):
            # Try to match by number first
            debt_num = c.get('debt_number')
            instrument = None

            if debt_num and 1 <= debt_num <= len(instruments_missing_collateral):
                instrument = instruments_missing_collateral[debt_num - 1]
            else:
                # Fall back to fuzzy name matching
                debt_name = c.get('debt_name', '')
                for inst in instruments_missing_collateral:
                    if _fuzzy_match_debt_name(debt_name, inst.name):
                        instrument = inst
                        break

            if not instrument:
                continue

            # Check if collateral already exists for this instrument
            existing = await session.execute(
                select(Collateral).where(Collateral.debt_instrument_id == instrument.id)
            )
            if existing.scalar_one_or_none():
                continue

            collateral = Collateral(
                id=uuid4(),
                debt_instrument_id=instrument.id,
                collateral_type=c.get('collateral_type', 'general_lien'),
                description=c.get('description', ''),
                priority=c.get('priority'),
            )
            session.add(collateral)
            collateral_created += 1

        await session.commit()
        return collateral_created
    except Exception as e:
        print(f"      Collateral extraction error: {e}")
        return 0


async def link_documents_to_instruments(session, company_id: UUID) -> int:
    """Link debt instruments to their governing documents."""
    from sqlalchemy import select
    from app.models import DebtInstrument, DocumentSection

    # Get unlinked instruments
    result = await session.execute(
        select(DebtInstrument).where(
            DebtInstrument.company_id == company_id,
        )
    )
    instruments = list(result.scalars().all())

    if not instruments:
        return 0

    # Get available documents
    result = await session.execute(
        select(DocumentSection).where(
            DocumentSection.company_id == company_id,
            DocumentSection.section_type.in_(['indenture', 'credit_agreement'])
        )
    )
    docs = list(result.scalars().all())

    if not docs:
        return 0

    # Simple matching: notes -> indentures, loans -> credit agreements
    indentures = [d for d in docs if d.section_type == 'indenture']
    credit_agreements = [d for d in docs if d.section_type == 'credit_agreement']

    links_created = 0
    for inst in instruments:
        inst_type = (inst.instrument_type or '').lower()
        inst_name = (inst.name or '').lower()

        # Match based on instrument type
        if any(x in inst_type or x in inst_name for x in ['note', 'bond', 'debenture']):
            if indentures:
                # Would need source_document_id field - skip for now
                links_created += 1
        elif any(x in inst_type or x in inst_name for x in ['loan', 'term', 'revolver', 'credit']):
            if credit_agreements:
                links_created += 1

    await session.commit()
    return links_created


async def recompute_metrics(session, company_id: UUID, ticker: str) -> bool:
    """Recompute metrics for the company."""
    from sqlalchemy import select
    from datetime import date, timedelta
    from decimal import Decimal
    from app.models import Company, CompanyMetrics, CompanyFinancials, DebtInstrument, Entity

    # Get company
    result = await session.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        return False

    # Get entities and debt
    result = await session.execute(select(Entity).where(Entity.company_id == company_id))
    entities = list(result.scalars().all())

    result = await session.execute(
        select(DebtInstrument).where(DebtInstrument.company_id == company_id, DebtInstrument.is_active == True)
    )
    instruments = list(result.scalars().all())

    # Calculate totals
    total_debt = sum(i.outstanding or i.principal or 0 for i in instruments)
    secured_debt = sum(
        i.outstanding or i.principal or 0 for i in instruments
        if i.seniority in ('senior_secured', 'secured', 'first_lien', 'second_lien')
    )

    # Get TTM financials
    result = await session.execute(
        select(CompanyFinancials)
        .where(CompanyFinancials.company_id == company_id)
        .order_by(CompanyFinancials.fiscal_year.desc(), CompanyFinancials.fiscal_quarter.desc())
        .limit(4)
    )
    financials = list(result.scalars().all())

    # Calculate TTM EBITDA
    ttm_ebitda = None
    if financials:
        ebitda_values = [f.ebitda for f in financials if f.ebitda]
        if len(ebitda_values) >= 1:
            ttm_ebitda = sum(ebitda_values) * (4 / len(ebitda_values))  # Annualize

    # Get or create metrics
    result = await session.execute(select(CompanyMetrics).where(CompanyMetrics.company_id == company_id))
    metrics = result.scalar_one_or_none()

    if not metrics:
        metrics = CompanyMetrics(ticker=ticker, company_id=company_id)
        session.add(metrics)

    # Update metrics
    metrics.total_debt = total_debt
    metrics.secured_debt = secured_debt
    metrics.entity_count = len(entities)

    # Calculate leverage ratios
    if ttm_ebitda and ttm_ebitda > 0:
        metrics.leverage_ratio = Decimal(str(total_debt / ttm_ebitda))

        if financials:
            cash = financials[0].cash_and_equivalents or 0
            net_debt = total_debt - cash
            metrics.net_leverage_ratio = Decimal(str(net_debt / ttm_ebitda))
            metrics.net_debt = net_debt

            if financials[0].interest_expense and financials[0].interest_expense > 0:
                annualized_interest = financials[0].interest_expense * 4
                metrics.interest_coverage = Decimal(str(ttm_ebitda / annualized_interest))

    # Calculate maturity profile
    today = date.today()
    metrics.debt_due_1yr = sum(
        i.outstanding or i.principal or 0 for i in instruments
        if i.maturity_date and i.maturity_date <= today + timedelta(days=365)
    )
    metrics.debt_due_2yr = sum(
        i.outstanding or i.principal or 0 for i in instruments
        if i.maturity_date and today + timedelta(days=365) < i.maturity_date <= today + timedelta(days=730)
    )
    metrics.debt_due_3yr = sum(
        i.outstanding or i.principal or 0 for i in instruments
        if i.maturity_date and today + timedelta(days=730) < i.maturity_date <= today + timedelta(days=1095)
    )

    metrics.has_near_term_maturity = metrics.debt_due_1yr > 0 or metrics.debt_due_2yr > 0
    metrics.updated_at = datetime.utcnow()

    await session.commit()
    return True


async def run_qc_checks(session, company_id: UUID, ticker: str) -> dict:
    """Run basic QC checks on the extracted data."""
    from sqlalchemy import select, func
    from app.models import Entity, DebtInstrument, Guarantee, CompanyFinancials

    issues = []

    # Check for entities
    result = await session.execute(
        select(func.count()).select_from(Entity).where(Entity.company_id == company_id)
    )
    entity_count = result.scalar()
    if entity_count == 0:
        issues.append("No entities extracted")

    # Check for holdco
    result = await session.execute(
        select(Entity).where(Entity.company_id == company_id, Entity.is_root == True)
    )
    if not result.scalar_one_or_none():
        issues.append("No root entity (holdco) identified")

    # Check for debt instruments
    result = await session.execute(
        select(func.count()).select_from(DebtInstrument).where(DebtInstrument.company_id == company_id)
    )
    debt_count = result.scalar()
    if debt_count == 0:
        issues.append("No debt instruments extracted")

    # Check for guarantees
    result = await session.execute(
        select(func.count()).select_from(Guarantee)
        .join(DebtInstrument)
        .where(DebtInstrument.company_id == company_id)
    )
    guarantee_count = result.scalar()

    # Check for financials
    result = await session.execute(
        select(func.count()).select_from(CompanyFinancials).where(CompanyFinancials.company_id == company_id)
    )
    financial_count = result.scalar()

    return {
        "entities": entity_count,
        "debt_instruments": debt_count,
        "guarantees": guarantee_count,
        "financials": financial_count,
        "issues": issues,
        "passed": len(issues) == 0,
    }


# =============================================================================
# MAIN EXTRACTION PIPELINE
# =============================================================================

async def run_iterative_extraction(
    ticker: str,
    cik: str,
    gemini_api_key: str,
    anthropic_api_key: str,
    sec_api_key: str = None,
    quality_threshold: float = 85.0,
    max_iterations: int = 3,
    save_results: bool = True,
    save_to_db: bool = False,
    database_url: str = None,
    skip_financials: bool = False,
    skip_enrichment: bool = False,
    core_only: bool = False,
    force: bool = False,
) -> IterativeExtractionResult:
    """
    Run complete extraction pipeline (idempotent).

    Args:
        ticker: Stock ticker
        cik: SEC CIK number
        gemini_api_key: Gemini API key
        anthropic_api_key: Anthropic API key (optional)
        sec_api_key: SEC-API.io key (optional)
        quality_threshold: QA score threshold (default 85%)
        max_iterations: Max QA fix iterations (default 3)
        save_results: Save JSON results to files
        save_to_db: Save to database
        database_url: Database connection string
        skip_financials: Skip TTM financial extraction
        skip_enrichment: Skip guarantees, collateral, document linking
        core_only: Only run core extraction (fastest)
        force: Force re-run all steps (ignore skip conditions)
    """
    print(f"\n{'='*70}")
    print(f"COMPLETE COMPANY EXTRACTION: {ticker} (CIK: {cik})")
    print(f"{'='*70}")
    if force:
        print(f"  [FORCE MODE] Re-running all steps regardless of existing data")

    # Check existing data if saving to DB
    existing_data = None
    company_name = None
    if save_to_db and database_url:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

        engine = create_async_engine(database_url, echo=False)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        async with async_session() as session:
            existing_data = await check_existing_data(session, ticker)

            if existing_data.get('exists'):
                print(f"\n  Existing data found:")
                print(f"    - Entities: {existing_data.get('entity_count', 0)}")
                print(f"    - Debt instruments: {existing_data.get('debt_count', 0)}")
                print(f"    - Financials: {existing_data.get('financials_count', 0)} quarters")
                print(f"    - Ownership links: {existing_data.get('ownership_link_count', 0)}")
                print(f"    - Guarantees: {existing_data.get('guarantee_count', 0)}")
                print(f"    - Collateral: {existing_data.get('collateral_count', 0)}")
                print(f"    - Document sections: {existing_data.get('document_section_count', 0)}")

                # Get company name for hierarchy building
                from app.models import Company
                from sqlalchemy import select
                result = await session.execute(select(Company).where(Company.ticker == ticker.upper()))
                company = result.scalar_one_or_none()
                if company:
                    company_name = company.name

        await engine.dispose()
    else:
        existing_data = {'exists': False}

    # Determine what to skip based on existing data
    skip_core = False
    skip_document_sections = False
    skip_ttm_financials = skip_financials or core_only
    skip_hierarchy = False
    skip_guarantees = skip_enrichment or core_only
    skip_collateral = skip_enrichment or core_only

    if not force and existing_data.get('exists'):
        entity_count = existing_data.get('entity_count', 0)
        debt_count = existing_data.get('debt_count', 0)
        extraction_status = existing_data.get('extraction_status', {})

        # Helper to check if step was already attempted with no data available
        def step_has_no_data(step_name: str) -> bool:
            step_status = extraction_status.get(step_name, {})
            return step_status.get('status') == 'no_data'

        # Skip core if entity_count > 20 AND debt_count > 0
        if entity_count > 20 and debt_count > 0:
            skip_core = True
            print(f"\n  [SKIP] Core extraction (have {entity_count} entities, {debt_count} debt instruments)")

        # Skip document sections if count > 5
        if existing_data.get('document_section_count', 0) > 5:
            skip_document_sections = True
            print(f"  [SKIP] Document sections (have {existing_data.get('document_section_count', 0)} sections)")

        # Skip financials - deferred until after filings download to check for new quarters
        # We'll check latest_quarter from extraction_status against SEC filings
        if step_has_no_data('financials'):
            skip_ttm_financials = True
            print(f"  [SKIP] TTM financials (no data available - previously attempted)")
        # Note: has_financials check is deferred to after filings download

        # Skip hierarchy if entity_count > 50 OR no Exhibit 21 available
        if entity_count > 50:
            skip_hierarchy = True
            print(f"  [SKIP] Ownership hierarchy (have {entity_count} entities from Exhibit 21)")
        elif step_has_no_data('hierarchy'):
            skip_hierarchy = True
            print(f"  [SKIP] Ownership hierarchy (no Exhibit 21 available - previously attempted)")

        # Skip guarantees if guarantee_count > 0 OR previously attempted with no data
        if existing_data.get('guarantee_count', 0) > 0:
            skip_guarantees = True
            print(f"  [SKIP] Guarantees (have {existing_data.get('guarantee_count', 0)} guarantees)")
        elif step_has_no_data('guarantees'):
            skip_guarantees = True
            print(f"  [SKIP] Guarantees (no data available - previously attempted)")

        # Skip collateral if collateral_count > 0 OR previously attempted with no data
        if existing_data.get('collateral_count', 0) > 0:
            skip_collateral = True
            print(f"  [SKIP] Collateral (have {existing_data.get('collateral_count', 0)} collateral records)")
        elif step_has_no_data('collateral'):
            skip_collateral = True
            print(f"  [SKIP] Collateral (no data available - previously attempted)")

    # Download filings (always done, needed for various steps)
    print(f"\n[1/10] Downloading SEC filings...")
    filings = await download_filings(ticker, cik, sec_api_key)

    if not filings:
        print("Error: No filings found")
        sys.exit(1)

    # Core extraction with QA loop
    result = None
    if not skip_core:
        print(f"\n[2/10] Running core extraction (entities + debt)...")
        service = IterativeExtractionService(
            gemini_api_key=gemini_api_key,
            anthropic_api_key=anthropic_api_key,
            sec_api_key=sec_api_key,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
        )

        result = await service.extract_with_feedback(
            ticker=ticker,
            cik=cik,
            filings=filings,
        )

        # Print extraction summary
        print(f"\n    Core Extraction Results:")
        print(f"    - Entities: {len(result.extraction.get('entities', []))}")
        print(f"    - Debt instruments: {len(result.extraction.get('debt_instruments', []))}")
        print(f"    - QA Score: {result.final_qa_score:.0f}%")
        print(f"    - Cost: ${result.total_cost:.4f}")

        company_name = result.extraction.get('company_name', ticker)

        # Save results to files
        if save_results:
            os.makedirs("results", exist_ok=True)
            extraction_path = f"results/{ticker.lower()}_iterative.json"
            with open(extraction_path, 'w') as f:
                json.dump(result.extraction, f, indent=2, default=str)
            print(f"    - Saved to {extraction_path}")
    else:
        print(f"\n[2/10] Skipping core extraction (data exists)")
        # Create a dummy result for compatibility
        from datetime import datetime
        from app.services.qa_agent import QAReport
        result = IterativeExtractionResult(
            ticker=ticker,
            extraction={'entities': [], 'debt_instruments': []},
            final_qa_score=100.0,
            total_cost=0.0,
            total_duration=0.0,
            iterations=[],
            final_model='skipped',
            qa_report=QAReport(
                ticker=ticker,
                timestamp=datetime.now(),
                checks=[],
                overall_score=100.0,
                overall_status='pass',
                summary='Skipped - data exists',
            ),
        )

    # Database operations
    company_id = existing_data.get('company_id') if existing_data.get('exists') else None
    if save_to_db and database_url:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.services.section_extraction import extract_and_store_sections
        from scripts.extract_tiered import convert_to_extraction_result

        engine = create_async_engine(database_url, echo=False)
        async_session = async_sessionmaker(engine, expire_on_commit=False)

        # Save core extraction (use merge if data exists)
        if not skip_core and result.extraction.get('entities'):
            async with async_session() as session:
                print(f"\n[3/10] Saving to database...")
                extraction_result = convert_to_extraction_result(result.extraction, ticker)

                if existing_data.get('exists') and not force:
                    # Merge with existing data (adds new + updates existing)
                    company_id, merge_stats = await merge_extraction_to_db(session, extraction_result, ticker, cik=cik)
                    print(f"    [OK] Merged extraction: +{merge_stats['entities_added']} entities, ~{merge_stats['entities_updated']} updated")
                    print(f"         +{merge_stats['debt_added']} debt, ~{merge_stats['debt_updated']} updated, +{merge_stats['guarantees_added']} guarantees")
                else:
                    # Full replacement
                    company_id = await save_extraction_to_db(session, extraction_result, ticker, cik=cik)
                    print(f"    [OK] Saved core extraction")

        else:
            print(f"\n[3/10] Skipping save (no new core data)")

        await engine.dispose()

        # Document sections
        if not skip_document_sections and company_id:
            print(f"\n[4/10] Extracting document sections...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    sections_stored = await extract_and_store_sections(
                        db=session,
                        company_id=company_id,
                        filings_content=filings,
                    )
                    print(f"    [OK] Stored {sections_stored} document sections")
                except Exception as e:
                    print(f"    [WARN] Section extraction failed: {e}")

            await engine.dispose()
        else:
            print(f"\n[4/10] Skipping document sections")

        # TTM Financials - check if new quarters might be available
        if not skip_ttm_financials and existing_data.get('has_financials') and not force:
            extraction_status = existing_data.get('extraction_status', {})
            financials_status = extraction_status.get('financials', {})
            stored_latest = financials_status.get('latest_quarter', '')  # e.g., "2025Q3"

            if stored_latest:
                # Parse stored quarter and compare with current date
                # Companies typically file ~45 days after quarter end
                # So if we're 60+ days into a new quarter, new data likely available
                try:
                    from datetime import datetime, timedelta
                    stored_year = int(stored_latest[:4])
                    stored_q = int(stored_latest[-1])

                    # Calculate expected next quarter
                    if stored_q == 4:
                        next_year, next_q = stored_year + 1, 1
                    else:
                        next_year, next_q = stored_year, stored_q + 1

                    # Quarter end dates: Q1=Mar31, Q2=Jun30, Q3=Sep30, Q4=Dec31
                    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
                    end_month, end_day = quarter_ends[next_q]
                    next_quarter_end = datetime(next_year, end_month, end_day)

                    # If we're 60+ days past the next quarter end, new filing likely available
                    days_since_quarter = (datetime.now() - next_quarter_end).days
                    if days_since_quarter >= 60:
                        print(f"  [UPDATE] New quarter likely available (have {stored_latest}, ~{days_since_quarter} days since Q{next_q} end)")
                    else:
                        skip_ttm_financials = True
                        print(f"  [SKIP] TTM financials (have {stored_latest}, next quarter not yet due)")
                except Exception as e:
                    # Can't parse, skip with basic message
                    skip_ttm_financials = True
                    print(f"  [SKIP] TTM financials (have {existing_data.get('financials_count', 0)} quarters)")
            else:
                # No latest_quarter stored, skip if has financials
                skip_ttm_financials = True
                print(f"  [SKIP] TTM financials (have {existing_data.get('financials_count', 0)} quarters)")

        if not skip_ttm_financials and company_id:
            print(f"\n[5/10] Extracting TTM financials (4 quarters)...")
            try:
                from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db

                ttm_results = await extract_ttm_financials(
                    ticker=ticker,
                    cik=cik,
                    use_claude=False,
                )

                if ttm_results:
                    engine = create_async_engine(database_url, echo=False)
                    async_session = async_sessionmaker(engine, expire_on_commit=False)

                    # Find the latest quarter from results
                    latest = max(ttm_results, key=lambda x: (x.fiscal_year, x.fiscal_quarter))
                    latest_quarter = f"{latest.fiscal_year}Q{latest.fiscal_quarter}"

                    async with async_session() as session:
                        for fin_result in ttm_results:
                            await save_financials_to_db(session, ticker, fin_result)
                        await session.commit()
                        # Record success with latest quarter metadata
                        if company_id:
                            from app.services.extraction import update_extraction_status
                            await update_extraction_status(
                                session, company_id, 'financials', 'success',
                                f"Extracted {len(ttm_results)} quarters through {latest_quarter}",
                                metadata={'latest_quarter': latest_quarter}
                            )

                    await engine.dispose()
                    print(f"    [OK] Extracted {len(ttm_results)} quarters (latest: {latest_quarter})")
                else:
                    print(f"    [WARN] No financials extracted")
                    # Record no_data
                    if company_id and database_url:
                        engine = create_async_engine(database_url, echo=False)
                        async_session = async_sessionmaker(engine, expire_on_commit=False)
                        async with async_session() as session:
                            from app.services.extraction import update_extraction_status
                            await update_extraction_status(session, company_id, 'financials', 'no_data', 'No financial data found')
                        await engine.dispose()
            except Exception as e:
                print(f"    [WARN] Financial extraction failed: {e}")
                # Record error
                if company_id and database_url:
                    try:
                        engine = create_async_engine(database_url, echo=False)
                        async_session = async_sessionmaker(engine, expire_on_commit=False)
                        async with async_session() as session:
                            from app.services.extraction import update_extraction_status
                            await update_extraction_status(session, company_id, 'financials', 'error', str(e))
                        await engine.dispose()
                    except:
                        pass
        else:
            print(f"\n[5/10] Skipping TTM financials")

        # Ownership hierarchy (FULL Exhibit 21 integration)
        if not skip_hierarchy and company_id:
            print(f"\n[6/10] Extracting ownership hierarchy from Exhibit 21...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    # Get company name if we don't have it
                    if not company_name:
                        from app.models import Company
                        from sqlalchemy import select
                        result = await session.execute(select(Company).where(Company.id == company_id))
                        company = result.scalar_one_or_none()
                        company_name = company.name if company else ticker

                    hierarchy_stats = await extract_ownership_hierarchy(
                        session, company_id, ticker, cik, company_name
                    )

                    # Record extraction status
                    from app.services.extraction import update_extraction_status
                    if hierarchy_stats.get('entries_found', 0) == 0:
                        await update_extraction_status(session, company_id, 'hierarchy', 'no_data', 'No Exhibit 21 found')
                        print(f"    [INFO] No Exhibit 21 available (status recorded)")
                    else:
                        await update_extraction_status(session, company_id, 'hierarchy', 'success',
                            f"Created {hierarchy_stats.get('entities_created', 0)} entities, {hierarchy_stats.get('links_created', 0)} links")
                        print(f"    [OK] Processed hierarchy (created {hierarchy_stats.get('entities_created', 0)} entities, {hierarchy_stats.get('links_created', 0)} links)")
                except Exception as e:
                    print(f"    [WARN] Hierarchy extraction failed: {e}")
                    # Record error status
                    try:
                        from app.services.extraction import update_extraction_status
                        await update_extraction_status(session, company_id, 'hierarchy', 'error', str(e))
                    except:
                        pass

            await engine.dispose()
        else:
            print(f"\n[6/10] Skipping ownership hierarchy")

        # Guarantees
        if not skip_guarantees and company_id:
            print(f"\n[7/10] Extracting guarantees...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    guarantee_count = await extract_guarantees(session, company_id, ticker, filings)
                    # Record status
                    from app.services.extraction import update_extraction_status
                    if guarantee_count > 0:
                        await update_extraction_status(session, company_id, 'guarantees', 'success',
                            f"Created {guarantee_count} guarantees")
                    else:
                        await update_extraction_status(session, company_id, 'guarantees', 'no_data',
                            'No guarantee relationships found')
                    print(f"    [OK] Created {guarantee_count} guarantees")
                except Exception as e:
                    print(f"    [WARN] Guarantee extraction failed: {e}")
                    try:
                        from app.services.extraction import update_extraction_status
                        await update_extraction_status(session, company_id, 'guarantees', 'error', str(e))
                    except:
                        pass

            await engine.dispose()
        else:
            print(f"\n[7/10] Skipping guarantees")

        # Collateral
        if not skip_collateral and company_id:
            print(f"\n[8/10] Extracting collateral...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    collateral_count = await extract_collateral(session, company_id, ticker, filings)
                    # Record status
                    from app.services.extraction import update_extraction_status
                    if collateral_count > 0:
                        await update_extraction_status(session, company_id, 'collateral', 'success',
                            f"Created {collateral_count} collateral records")
                    else:
                        await update_extraction_status(session, company_id, 'collateral', 'no_data',
                            'No collateral found (no secured debt)')
                    print(f"    [OK] Created {collateral_count} collateral records")
                except Exception as e:
                    print(f"    [WARN] Collateral extraction failed: {e}")
                    try:
                        from app.services.extraction import update_extraction_status
                        await update_extraction_status(session, company_id, 'collateral', 'error', str(e))
                    except:
                        pass

                # Document linking
                print(f"\n        Linking documents to instruments...")
                try:
                    links_count = await link_documents_to_instruments(session, company_id)
                    print(f"    [OK] Linked {links_count} instruments")
                except Exception as e:
                    print(f"    [WARN] Document linking failed: {e}")

            await engine.dispose()
        else:
            print(f"\n[8/10] Skipping collateral")

        # Metrics computation (always run)
        if company_id:
            print(f"\n[9/10] Computing metrics...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    await recompute_metrics(session, company_id, ticker)
                    print(f"    [OK] Metrics computed")
                except Exception as e:
                    print(f"    [WARN] Metrics computation failed: {e}")

                # QC checks
                print(f"\n[10/10] Running QC checks...")
                qc_results = await run_qc_checks(session, company_id, ticker)
                print(f"    - Entities: {qc_results['entities']}")
                print(f"    - Debt instruments: {qc_results['debt_instruments']}")
                print(f"    - Guarantees: {qc_results['guarantees']}")
                print(f"    - Financials: {qc_results['financials']} quarters")
                if qc_results['issues']:
                    print(f"    - Issues: {', '.join(qc_results['issues'])}")
                else:
                    print(f"    - Status: PASSED")

            await engine.dispose()
    else:
        print(f"\n[3-10] Skipping database operations (--save-db not specified)")

    # Final summary
    print(f"\n{'='*70}")
    print(f"EXTRACTION COMPLETE: {ticker}")
    print(f"{'='*70}")
    if not skip_core:
        print(f"  QA Score: {result.final_qa_score:.0f}%")
        print(f"  Total Cost: ${result.total_cost:.4f}")
        print(f"  Duration: {result.total_duration:.1f}s")

    return result


async def run_batch_extraction(
    database_url: str,
    gemini_api_key: str,
    anthropic_api_key: str,
    sec_api_key: str = None,
    force: bool = False,
    resume: bool = False,
    limit: int = 0,
    start_index: int = 0,
    end_index: int = 0,
) -> dict:
    """
    Run extraction for all companies in the database.

    Args:
        database_url: Database connection string
        gemini_api_key: Gemini API key
        anthropic_api_key: Anthropic API key
        sec_api_key: SEC-API.io key
        force: Force re-run all steps
        resume: Resume from last processed company
        limit: Limit number of companies (0 = unlimited)

    Returns:
        Stats dict with success/error counts
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import select, text

    print(f"\n{'='*70}")
    print(f"BATCH EXTRACTION MODE")
    print(f"{'='*70}")

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    # Get all companies with CIKs
    async with async_session() as session:
        result = await session.execute(text('''
            SELECT ticker, cik, name
            FROM companies
            WHERE cik IS NOT NULL AND cik != ''
            ORDER BY ticker
        '''))
        companies = result.fetchall()

    await engine.dispose()

    total_companies = len(companies)

    # Apply start/end index for parallel batching
    if start_index > 0 or end_index > 0:
        end_idx = end_index if end_index > 0 else len(companies)
        companies = companies[start_index:end_idx]
        print(f"  Batch range: {start_index} to {end_idx} ({len(companies)} companies)")

    if limit > 0:
        companies = companies[:limit]

    print(f"  Found {total_companies} total companies, processing {len(companies)}")

    # Resume support - track last processed company
    progress_file = "results/.batch_progress.json"
    last_processed = None
    if resume and os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                progress = json.load(f)
                last_processed = progress.get('last_ticker')
                print(f"  Resuming from after: {last_processed}")
        except Exception:
            pass

    stats = {'total': len(companies), 'processed': 0, 'success': 0, 'errors': 0, 'skipped': 0}

    # Skip to resume point
    if last_processed:
        skip_until_found = True
        for i, (ticker, _, _) in enumerate(companies):
            if ticker == last_processed:
                skip_until_found = False
                stats['skipped'] = i + 1
                companies = companies[i + 1:]
                break
        if skip_until_found:
            print(f"  Warning: Resume ticker {last_processed} not found, starting from beginning")

    print(f"  Processing {len(companies)} companies...")
    print()

    for i, (ticker, cik, name) in enumerate(companies):
        # Handle Unicode names safely for Windows console
        safe_name = name.encode('ascii', 'replace').decode('ascii')
        print(f"\n[{stats['skipped'] + i + 1}/{stats['total']}] {ticker}: {safe_name}")

        try:
            await run_iterative_extraction(
                ticker=ticker,
                cik=cik,
                gemini_api_key=gemini_api_key,
                anthropic_api_key=anthropic_api_key,
                sec_api_key=sec_api_key,
                save_to_db=True,
                database_url=database_url,
                force=force,
            )
            stats['success'] += 1
        except Exception as e:
            print(f"  [ERROR] {e}")
            stats['errors'] += 1

        stats['processed'] += 1

        # Save progress
        os.makedirs("results", exist_ok=True)
        with open(progress_file, 'w') as f:
            json.dump({'last_ticker': ticker, 'timestamp': datetime.now().isoformat()}, f)

        # Brief pause between companies to avoid rate limits
        await asyncio.sleep(1)

    # Final summary
    print(f"\n{'='*70}")
    print(f"BATCH EXTRACTION COMPLETE")
    print(f"{'='*70}")
    print(f"  Total: {stats['total']}")
    print(f"  Processed: {stats['processed']}")
    print(f"  Success: {stats['success']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Skipped (resume): {stats['skipped']}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Complete company extraction pipeline (idempotent)"
    )
    parser.add_argument("--ticker", help="Stock ticker (e.g., AAPL)")
    parser.add_argument("--cik", help="SEC CIK number")
    parser.add_argument("--threshold", type=float, default=85.0,
                       help="QA quality threshold (default: 85)")
    parser.add_argument("--max-iterations", type=int, default=3,
                       help="Maximum fix iterations (default: 3)")
    parser.add_argument("--no-save", action="store_true",
                       help="Don't save results to files")
    parser.add_argument("--save-db", action="store_true",
                       help="Save to database")
    parser.add_argument("--skip-financials", action="store_true",
                       help="Skip TTM financial extraction")
    parser.add_argument("--skip-enrichment", action="store_true",
                       help="Skip guarantees, collateral, document linking")
    parser.add_argument("--core-only", action="store_true",
                       help="Only run core extraction (fastest)")
    parser.add_argument("--force", action="store_true",
                       help="Force re-run all steps (ignore skip conditions)")
    parser.add_argument("--all", action="store_true",
                       help="Process all companies in database")
    parser.add_argument("--resume", action="store_true",
                       help="Resume batch from last processed company")
    parser.add_argument("--limit", type=int, default=0,
                       help="Limit number of companies in batch mode")
    parser.add_argument("--start-index", type=int, default=0,
                       help="Start index for parallel batching (0-based)")
    parser.add_argument("--end-index", type=int, default=0,
                       help="End index for parallel batching (exclusive, 0=all)")

    args = parser.parse_args()

    # Validate arguments
    if not args.all and not args.ticker:
        print("Error: Must specify --ticker or --all")
        sys.exit(1)

    if args.ticker and not args.cik:
        print("Error: --cik is required when using --ticker")
        sys.exit(1)

    # Load environment
    load_dotenv()

    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not gemini_api_key:
        print("Error: GEMINI_API_KEY required")
        sys.exit(1)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        print("Warning: ANTHROPIC_API_KEY not set, Claude escalation disabled")

    sec_api_key = os.getenv("SEC_API_KEY")
    database_url = os.getenv("DATABASE_URL")

    if args.all:
        if not database_url:
            print("Error: DATABASE_URL required for --all mode")
            sys.exit(1)

        asyncio.run(
            run_batch_extraction(
                database_url=database_url,
                gemini_api_key=gemini_api_key,
                anthropic_api_key=anthropic_api_key,
                sec_api_key=sec_api_key,
                force=args.force,
                resume=args.resume,
                limit=args.limit,
                start_index=args.start_index,
                end_index=args.end_index,
            )
        )
    else:
        asyncio.run(
            run_iterative_extraction(
                ticker=args.ticker,
                cik=args.cik,
                gemini_api_key=gemini_api_key,
                anthropic_api_key=anthropic_api_key,
                sec_api_key=sec_api_key,
                quality_threshold=args.threshold,
                max_iterations=args.max_iterations,
                save_results=not args.no_save,
                save_to_db=args.save_db,
                database_url=database_url,
                skip_financials=args.skip_financials,
                skip_enrichment=args.skip_enrichment,
                core_only=args.core_only,
                force=args.force,
            )
        )


if __name__ == "__main__":
    main()

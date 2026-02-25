#!/usr/bin/env python3
"""
Enrich UK entity ownership from Companies House PSC data.

Queries the UK Companies House API for orphan entities with UK jurisdiction,
finds their parent company via PSC (Persons with Significant Control) data,
and updates parent_id relationships in the database.

Usage:
    # Analyze UK orphan count (no API calls)
    python scripts/enrich_uk_ownership.py --analyze

    # Single company dry run
    python scripts/enrich_uk_ownership.py --ticker GSK

    # Single company with verbose output
    python scripts/enrich_uk_ownership.py --ticker GSK --verbose

    # Single company, persist to DB
    python scripts/enrich_uk_ownership.py --ticker GSK --save

    # All companies, dry run
    python scripts/enrich_uk_ownership.py --all

    # All companies, persist
    python scripts/enrich_uk_ownership.py --all --save

    # Skip entities that already have a cached CH number
    python scripts/enrich_uk_ownership.py --all --skip-cached

    # Custom confidence threshold
    python scripts/enrich_uk_ownership.py --all --confidence 0.70
"""

import asyncio
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

import httpx
from rapidfuzz import fuzz
from sqlalchemy import select, func, and_

from script_utils import (
    create_fix_parser,
    get_db_session,
    print_header,
    print_subheader,
    print_summary,
    run_async,
)
from app.core.config import get_settings
from app.models import Company, Entity, OwnershipLink


# =============================================================================
# CONSTANTS
# =============================================================================

CH_BASE_URL = "https://api.company-information.service.gov.uk"
CH_RATE_LIMIT_DELAY = 0.5  # seconds between requests
CH_MAX_RETRIES = 3
CH_RETRY_DELAY = 60  # seconds on 429

# UK jurisdiction keywords for entity matching
UK_JURISDICTION_KEYWORDS = [
    "england", "wales", "scotland", "northern ireland",
    "united kingdom", "uk", "great britain", "gb",
    "england and wales", "england & wales",
]

# Suffixes to normalize for UK company name matching
UK_SUFFIX_MAP = {
    "limited": "ltd",
    "public limited company": "plc",
    "limited liability partnership": "llp",
    "community interest company": "cic",
    "incorporated": "inc",
    "corporation": "corp",
}

# PSC natures_of_control to ownership percentage mapping
PSC_OWNERSHIP_MAP = {
    "ownership-of-shares-75-to-100-percent": Decimal("100.00"),
    "ownership-of-shares-50-to-75-percent": Decimal("62.50"),
    "ownership-of-shares-25-to-50-percent": Decimal("37.50"),
    "voting-rights-75-to-100-percent": Decimal("100.00"),
    "voting-rights-50-to-75-percent": Decimal("62.50"),
    "voting-rights-25-to-50-percent": Decimal("37.50"),
    "right-to-appoint-and-remove-directors": Decimal("100.00"),
    "significant-influence-or-control": Decimal("100.00"),
}


# =============================================================================
# UK COMPANIES HOUSE API CLIENT
# =============================================================================

class CompaniesHouseClient:
    """Client for UK Companies House REST API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._last_request_time = 0.0
        self.request_count = 0

    def _get_auth(self) -> tuple[str, str]:
        """HTTP Basic auth: API key as username, empty password."""
        return (self.api_key, "")

    async def _rate_limit(self):
        """Enforce rate limit between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < CH_RATE_LIMIT_DELAY:
            await asyncio.sleep(CH_RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.monotonic()

    async def _request(self, client: httpx.AsyncClient, path: str, params: dict = None) -> Optional[dict]:
        """Make a rate-limited request with 429 retry."""
        await self._rate_limit()

        url = f"{CH_BASE_URL}{path}"
        for attempt in range(CH_MAX_RETRIES):
            try:
                resp = await client.get(url, params=params, auth=self._get_auth(), timeout=30.0)
                self.request_count += 1

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = CH_RETRY_DELAY * (attempt + 1)
                    print(f"    Rate limited (429), waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code == 404:
                    return None
                else:
                    print(f"    CH API error {resp.status_code}: {resp.text[:200]}")
                    return None
            except httpx.TimeoutException:
                print(f"    CH API timeout (attempt {attempt + 1})")
                if attempt < CH_MAX_RETRIES - 1:
                    await asyncio.sleep(5)
                    continue
                return None
            except Exception as e:
                print(f"    CH API error: {e}")
                return None

        return None

    async def search_companies(self, client: httpx.AsyncClient, name: str) -> list[dict]:
        """Search Companies House by company name."""
        data = await self._request(client, "/search/companies", {"q": name, "items_per_page": 5})
        if not data:
            return []
        return data.get("items", [])

    async def get_psc_list(self, client: httpx.AsyncClient, company_number: str) -> list[dict]:
        """Get Persons with Significant Control for a company."""
        data = await self._request(client, f"/company/{company_number}/persons-with-significant-control")
        if not data:
            return []
        return data.get("items", [])


# =============================================================================
# NAME MATCHING UTILITIES
# =============================================================================

def normalize_uk_name(name: str) -> str:
    """Normalize a UK company name for matching.

    Removes common suffixes, lowercases, strips punctuation.
    """
    if not name:
        return ""

    n = name.lower().strip()

    # Remove common punctuation
    n = n.replace(",", "").replace(".", "").replace("'", "").replace('"', '')

    # Normalize UK-specific suffixes
    for long_form, short_form in UK_SUFFIX_MAP.items():
        n = n.replace(long_form, short_form)

    # Remove trailing suffix entirely for base comparison
    n = re.sub(r'\s+(ltd|plc|llp|lp|llc|inc|corp|cic)\s*$', '', n)

    # Collapse whitespace
    n = re.sub(r'\s+', ' ', n).strip()

    return n


def score_ch_match(entity_name: str, ch_result: dict, entity_jurisdiction: str = None, entity_formation_type: str = None) -> float:
    """Score a Companies House search result against an entity.

    Returns 0.0-1.0 combining:
    - Fuzzy name similarity (60% weight)
    - Company status bonus (active = +0.1)
    - Formation type match (+0.15)
    - Jurisdiction match (+0.15)
    """
    ch_name = ch_result.get("title", "")
    if not ch_name:
        return 0.0

    # Fuzzy name similarity (60% weight)
    norm_entity = normalize_uk_name(entity_name)
    norm_ch = normalize_uk_name(ch_name)

    if not norm_entity or not norm_ch:
        return 0.0

    name_score = fuzz.token_sort_ratio(norm_entity, norm_ch) / 100.0
    score = name_score * 0.60

    # Company status bonus
    status = ch_result.get("company_status", "")
    if status == "active":
        score += 0.10

    # Formation type match
    ch_type = ch_result.get("company_type", "")
    if entity_formation_type and ch_type:
        type_matches = {
            ("ltd", "ltd"): True,
            ("llc", "ltd"): True,
            ("corp", "plc"): True,
            ("plc", "plc"): True,
            ("llp", "llp"): True,
            ("lp", "limited-partnership"): True,
        }
        eft = entity_formation_type.lower()
        if (eft, ch_type) in type_matches or eft in ch_type or ch_type in eft:
            score += 0.15

    # Jurisdiction match
    if entity_jurisdiction:
        ej = entity_jurisdiction.lower()
        ch_address = ch_result.get("address", {}) or {}
        ch_locality = (ch_address.get("locality") or "").lower()
        ch_region = (ch_address.get("region") or "").lower()
        ch_country = (ch_address.get("country") or "").lower()

        location_text = f"{ch_locality} {ch_region} {ch_country}"
        if any(kw in ej for kw in UK_JURISDICTION_KEYWORDS):
            if any(kw in location_text for kw in ["england", "wales", "london", "scotland", "uk", "united kingdom"]):
                score += 0.15

    return min(score, 1.0)


def psc_ownership_pct(natures: list[str]) -> Decimal:
    """Map PSC natures_of_control to approximate ownership percentage."""
    if not natures:
        return Decimal("100.00")

    for nature in natures:
        if nature in PSC_OWNERSHIP_MAP:
            return PSC_OWNERSHIP_MAP[nature]

    # Default to 100% for unrecognized control types
    return Decimal("100.00")


# =============================================================================
# ENTITY ANALYSIS
# =============================================================================

def is_uk_jurisdiction(jurisdiction: str) -> bool:
    """Check if a jurisdiction string indicates UK."""
    if not jurisdiction:
        return False
    j = jurisdiction.lower()
    return any(kw in j for kw in UK_JURISDICTION_KEYWORDS)


async def get_uk_orphans(db, company_id: UUID = None) -> list:
    """Get UK-jurisdictioned orphan entities (parent_id IS NULL, is_root=false).

    Returns list of (entity_id, entity_name, jurisdiction, formation_type, company_id, ticker, attributes).
    """
    query = (
        select(
            Entity.id,
            Entity.name,
            Entity.jurisdiction,
            Entity.formation_type,
            Entity.company_id,
            Company.ticker,
            Entity.attributes,
        )
        .join(Company, Company.id == Entity.company_id)
        .where(
            and_(
                Entity.parent_id.is_(None),
                Entity.is_root.is_(False),
            )
        )
        .order_by(Company.ticker, Entity.name)
    )

    if company_id:
        query = query.where(Entity.company_id == company_id)

    result = await db.execute(query)
    rows = result.fetchall()

    # Filter to UK jurisdictions in Python (more flexible than SQL LIKE)
    uk_rows = [r for r in rows if is_uk_jurisdiction(r.jurisdiction)]
    return uk_rows


async def get_company_entities(db, company_id: UUID) -> list[Entity]:
    """Get all entities for a company (for parent matching)."""
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    return list(result.scalars())


# =============================================================================
# ANALYSIS MODE
# =============================================================================

async def run_analyze(args):
    """Show statistics about UK orphan entities without making API calls."""
    print_header("UK OWNERSHIP ENRICHMENT - ANALYSIS")

    async with get_db_session() as db:
        # Total orphans
        total_orphans = await db.scalar(
            select(func.count(Entity.id)).where(
                and_(Entity.parent_id.is_(None), Entity.is_root.is_(False))
            )
        )

        # Total entities
        total_entities = await db.scalar(select(func.count(Entity.id)))

        # Get UK orphans
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company not found: {args.ticker}")
                return
            uk_orphans = await get_uk_orphans(db, company.id)
            print(f"Company: {company.ticker} - {company.name}")
        else:
            uk_orphans = await get_uk_orphans(db)

        # Count entities with cached CH numbers
        cached_count = sum(
            1 for r in uk_orphans
            if (r.attributes or {}).get("companies_house_number")
        )

        # Count entities already enriched
        enriched_count = sum(
            1 for r in uk_orphans
            if (r.attributes or {}).get("ch_enrichment_date")
        )

        # Group by company
        by_company = {}
        for r in uk_orphans:
            by_company.setdefault(r.ticker, []).append(r)

        print(f"\nTotal entities: {total_entities:,}")
        print(f"Total orphans (parent_id=NULL, is_root=false): {total_orphans:,}")
        print(f"UK orphans: {len(uk_orphans):,}")
        print(f"  With cached CH number: {cached_count}")
        print(f"  Already enriched: {enriched_count}")
        print(f"  Companies with UK orphans: {len(by_company)}")

        # Estimated API calls
        search_calls = len(uk_orphans) - cached_count
        psc_calls = len(uk_orphans)
        total_calls = search_calls + psc_calls
        est_time = total_calls * CH_RATE_LIMIT_DELAY
        print(f"\nEstimated API calls: {total_calls:,} ({search_calls} search + {psc_calls} PSC)")
        print(f"Estimated time: {est_time / 60:.1f} minutes")

        if args.verbose:
            print_subheader("UK ORPHANS BY COMPANY")
            for ticker in sorted(by_company.keys()):
                entities = by_company[ticker]
                print(f"\n  {ticker} ({len(entities)} UK orphans):")
                for e in entities[:10]:
                    cached = " [CH cached]" if (e.attributes or {}).get("companies_house_number") else ""
                    print(f"    - {e.name} ({e.jurisdiction}){cached}")
                if len(entities) > 10:
                    print(f"    ... and {len(entities) - 10} more")


# =============================================================================
# MAIN ENRICHMENT
# =============================================================================

async def process_entity(
    ch_client: CompaniesHouseClient,
    http_client: httpx.AsyncClient,
    entity_id: UUID,
    entity_name: str,
    entity_jurisdiction: str,
    entity_formation_type: str,
    entity_attributes: dict,
    company_entities: list[Entity],
    root_entity: Optional[Entity],
    confidence_threshold: float,
    skip_cached: bool,
    verbose: bool,
) -> Optional[dict]:
    """Process a single entity: search CH, get PSC, find parent.

    Returns dict with match info if a parent was found, None otherwise.
    """
    attrs = entity_attributes or {}
    ch_number = attrs.get("companies_house_number")

    # Step 1: Get or find CH company number
    if ch_number and skip_cached:
        if verbose:
            print(f"    Skipping (cached CH: {ch_number})")
        return None

    if not ch_number:
        # Search Companies House
        results = await ch_client.search_companies(http_client, entity_name)
        if not results:
            if verbose:
                print(f"    No CH results for '{entity_name}'")
            return None

        # Score and pick best match
        best_score = 0.0
        best_result = None
        for r in results:
            s = score_ch_match(entity_name, r, entity_jurisdiction, entity_formation_type)
            if s > best_score:
                best_score = s
                best_result = r

        if best_score < confidence_threshold or not best_result:
            if verbose:
                top_name = results[0].get("title", "?") if results else "?"
                print(f"    No match above threshold ({best_score:.2f} < {confidence_threshold}): '{top_name}'")
            return None

        ch_number = best_result.get("company_number")
        ch_name = best_result.get("title", "")
        if verbose:
            print(f"    CH match: {ch_name} ({ch_number}) score={best_score:.2f}")
    else:
        best_score = 1.0  # Cached number = high confidence
        if verbose:
            print(f"    Using cached CH number: {ch_number}")

    if not ch_number:
        return None

    # Step 2: Get PSC list
    psc_list = await ch_client.get_psc_list(http_client, ch_number)
    if not psc_list:
        if verbose:
            print(f"    No PSC data for {ch_number}")
        return {"ch_number": ch_number, "ch_match_score": best_score, "parent": None}

    # Step 3: Filter for corporate entity PSCs
    corporate_pscs = [
        p for p in psc_list
        if p.get("kind") == "corporate-entity-person-with-significant-control"
    ]

    if not corporate_pscs:
        if verbose:
            psc_kinds = [p.get("kind", "?") for p in psc_list]
            print(f"    No corporate PSCs (found: {psc_kinds})")
        return {"ch_number": ch_number, "ch_match_score": best_score, "parent": None}

    # Step 4: Match PSC parent name to entities in same company
    for psc in corporate_pscs:
        psc_name = psc.get("name", "")
        if not psc_name:
            continue

        ownership = psc_ownership_pct(psc.get("natures_of_control", []))

        matched_parent = _match_psc_to_entity(
            psc_name, psc, company_entities, root_entity, verbose
        )

        if matched_parent:
            return {
                "ch_number": ch_number,
                "ch_match_score": best_score,
                "parent": matched_parent,
                "psc_name": psc_name,
                "ownership_pct": ownership,
                "match_confidence": matched_parent["confidence"],
            }

    if verbose:
        psc_names = [p.get("name", "?") for p in corporate_pscs]
        print(f"    Corporate PSCs not matched: {psc_names}")

    return {"ch_number": ch_number, "ch_match_score": best_score, "parent": None}


def _match_psc_to_entity(
    psc_name: str,
    psc: dict,
    company_entities: list[Entity],
    root_entity: Optional[Entity],
    verbose: bool,
) -> Optional[dict]:
    """Match a PSC parent name to an entity in the company.

    Tries in order:
    a. Exact CH number match (if PSC has identification.registration_number)
    b. Exact normalized name match
    c. Root entity name match
    d. Fuzzy name match (token_sort_ratio >= 80)
    """
    psc_identification = psc.get("identification", {}) or {}
    psc_reg_number = psc_identification.get("registration_number", "")

    norm_psc = normalize_uk_name(psc_name)

    # a. Exact CH number match
    if psc_reg_number:
        for entity in company_entities:
            entity_ch = (entity.attributes or {}).get("companies_house_number", "")
            if entity_ch and entity_ch == psc_reg_number:
                if verbose:
                    print(f"    PSC parent match (CH number): {entity.name}")
                return {"entity": entity, "confidence": "high", "method": "ch_number"}

    # b. Exact normalized name match
    for entity in company_entities:
        norm_entity = normalize_uk_name(entity.name)
        if norm_entity and norm_entity == norm_psc:
            if verbose:
                print(f"    PSC parent match (exact name): {entity.name}")
            return {"entity": entity, "confidence": "high", "method": "exact_name"}

        if entity.legal_name:
            norm_legal = normalize_uk_name(entity.legal_name)
            if norm_legal and norm_legal == norm_psc:
                if verbose:
                    print(f"    PSC parent match (exact legal name): {entity.name}")
                return {"entity": entity, "confidence": "high", "method": "exact_legal_name"}

    # c. Root entity name match — most common case
    if root_entity:
        norm_root = normalize_uk_name(root_entity.name)
        if norm_root and norm_psc:
            root_score = fuzz.token_sort_ratio(norm_root, norm_psc)
            if root_score >= 70:
                if verbose:
                    print(f"    PSC parent match (root entity, score={root_score}): {root_entity.name}")
                return {"entity": root_entity, "confidence": "high", "method": "root_entity"}

    # d. Fuzzy name match
    best_match = None
    best_ratio = 0
    for entity in company_entities:
        norm_entity = normalize_uk_name(entity.name)
        if not norm_entity:
            continue

        ratio = fuzz.token_sort_ratio(norm_psc, norm_entity)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = entity

        # Also check legal_name
        if entity.legal_name:
            norm_legal = normalize_uk_name(entity.legal_name)
            if norm_legal:
                ratio_legal = fuzz.token_sort_ratio(norm_psc, norm_legal)
                if ratio_legal > best_ratio:
                    best_ratio = ratio_legal
                    best_match = entity

    if best_match and best_ratio >= 80:
        if verbose:
            print(f"    PSC parent match (fuzzy, score={best_ratio}): {best_match.name}")
        return {"entity": best_match, "confidence": "medium", "method": f"fuzzy_{best_ratio}"}

    return None


async def process_company_entities(
    ch_client: CompaniesHouseClient,
    http_client: httpx.AsyncClient,
    db,
    company_id: UUID,
    ticker: str,
    uk_orphans: list,
    confidence_threshold: float,
    skip_cached: bool,
    save: bool,
    verbose: bool,
) -> dict:
    """Process all UK orphan entities for a single company.

    Returns stats dict.
    """
    stats = {
        "entities_processed": 0,
        "ch_found": 0,
        "psc_found": 0,
        "parents_matched": 0,
        "parents_saved": 0,
    }

    # Get all entities for parent matching
    company_entities = await get_company_entities(db, company_id)

    # Find root entity
    root_entity = next((e for e in company_entities if e.is_root), None)

    for orphan in uk_orphans:
        entity_id = orphan.id
        entity_name = orphan.name
        entity_jurisdiction = orphan.jurisdiction
        entity_formation_type = orphan.formation_type
        entity_attributes = orphan.attributes

        if verbose:
            print(f"  [{ticker}] {entity_name}")

        result = await process_entity(
            ch_client=ch_client,
            http_client=http_client,
            entity_id=entity_id,
            entity_name=entity_name,
            entity_jurisdiction=entity_jurisdiction,
            entity_formation_type=entity_formation_type,
            entity_attributes=entity_attributes,
            company_entities=company_entities,
            root_entity=root_entity,
            confidence_threshold=confidence_threshold,
            skip_cached=skip_cached,
            verbose=verbose,
        )

        stats["entities_processed"] += 1

        if result is None:
            continue

        ch_number = result.get("ch_number")
        if ch_number:
            stats["ch_found"] += 1

        parent_info = result.get("parent")
        if parent_info:
            stats["psc_found"] += 1
            parent_entity = parent_info["entity"]
            stats["parents_matched"] += 1

            psc_name = result.get("psc_name", "")
            ownership_pct = result.get("ownership_pct", Decimal("100.00"))
            match_confidence = result.get("match_confidence", "medium")

            if not verbose:
                print(f"  [{ticker}] {entity_name} -> {parent_entity.name} ({match_confidence}, via {parent_info['method']})")

            if save:
                # Fetch the actual entity to update
                entity = await db.get(Entity, entity_id)
                if entity:
                    entity.parent_id = parent_entity.id

                    # Store metadata in attributes
                    attrs = dict(entity.attributes or {})
                    attrs["companies_house_number"] = ch_number
                    attrs["ch_psc_parent_name"] = psc_name
                    attrs["ch_enrichment_date"] = datetime.now(timezone.utc).isoformat()
                    attrs["ch_psc_match_confidence"] = match_confidence
                    attrs["ch_psc_match_method"] = parent_info["method"]
                    entity.attributes = attrs

                    # Create or update OwnershipLink
                    existing_link = await db.scalar(
                        select(OwnershipLink).where(
                            OwnershipLink.child_entity_id == entity_id
                        )
                    )

                    if existing_link:
                        existing_link.parent_entity_id = parent_entity.id
                        existing_link.ownership_pct = ownership_pct
                        existing_link.ownership_type = "direct"
                        existing_link.attributes = {
                            **(existing_link.attributes or {}),
                            "source": "companies_house_psc",
                            "ch_company_number": ch_number,
                            "psc_name": psc_name,
                        }
                    else:
                        link = OwnershipLink(
                            parent_entity_id=parent_entity.id,
                            child_entity_id=entity_id,
                            ownership_pct=ownership_pct,
                            ownership_type="direct",
                            attributes={
                                "source": "companies_house_psc",
                                "ch_company_number": ch_number,
                                "psc_name": psc_name,
                            },
                        )
                        db.add(link)

                    stats["parents_saved"] += 1

            elif ch_number:
                # Even in dry run, cache the CH number if found (no --save needed)
                pass
        else:
            # Cache CH number even when no parent found
            if save and ch_number:
                entity = await db.get(Entity, entity_id)
                if entity:
                    attrs = dict(entity.attributes or {})
                    if not attrs.get("companies_house_number"):
                        attrs["companies_house_number"] = ch_number
                        entity.attributes = attrs

    if save:
        await db.commit()

    return stats


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = create_fix_parser("Enrich UK entity ownership from Companies House PSC data")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show statistics only, no API calls",
    )
    parser.add_argument(
        "--skip-cached",
        action="store_true",
        help="Skip entities that already have a cached CH number",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.60,
        help="Minimum CH search match confidence threshold (default: 0.60)",
    )

    args = parser.parse_args()

    # Analysis mode
    if args.analyze:
        await run_analyze(args)
        return

    if not args.ticker and not getattr(args, "all", False):
        print("Error: Must specify --ticker, --all, or --analyze")
        return

    settings = get_settings()
    api_key = settings.companies_house_api_key
    if not api_key:
        print("Error: COMPANIES_HOUSE_API_KEY not set")
        print("Get a free key at https://developer.company-information.service.gov.uk/")
        return

    ch_client = CompaniesHouseClient(api_key)

    print_header("UK OWNERSHIP ENRICHMENT - COMPANIES HOUSE PSC")
    print(f"Mode: {'SAVE TO DB' if args.save else 'DRY RUN'}")
    print(f"Confidence threshold: {args.confidence}")
    if args.skip_cached:
        print("Skipping entities with cached CH numbers")
    print()

    # Collect UK orphans
    async with get_db_session() as db:
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company not found: {args.ticker}")
                return
            all_uk_orphans = await get_uk_orphans(db, company.id)
        else:
            all_uk_orphans = await get_uk_orphans(db)
            if args.limit and args.limit > 0:
                # Limit by number of unique companies
                seen_companies = set()
                limited = []
                for r in all_uk_orphans:
                    seen_companies.add(r.company_id)
                    if len(seen_companies) <= args.limit:
                        limited.append(r)
                all_uk_orphans = limited

    if not all_uk_orphans:
        print("No UK orphan entities found.")
        return

    # Group by company
    by_company = {}
    for r in all_uk_orphans:
        by_company.setdefault((r.company_id, r.ticker), []).append(r)

    print(f"Found {len(all_uk_orphans)} UK orphans across {len(by_company)} companies")
    print()

    total_stats = {
        "companies_processed": 0,
        "entities_processed": 0,
        "ch_found": 0,
        "psc_found": 0,
        "parents_matched": 0,
        "parents_saved": 0,
    }

    async with httpx.AsyncClient() as http_client:
        for (company_id, ticker), orphans in sorted(by_company.items(), key=lambda x: x[0][1]):
            print_subheader(f"{ticker} ({len(orphans)} UK orphans)")

            try:
                async with get_db_session() as db:
                    stats = await process_company_entities(
                        ch_client=ch_client,
                        http_client=http_client,
                        db=db,
                        company_id=company_id,
                        ticker=ticker,
                        uk_orphans=orphans,
                        confidence_threshold=args.confidence,
                        skip_cached=args.skip_cached,
                        save=args.save,
                        verbose=args.verbose,
                    )

                    total_stats["companies_processed"] += 1
                    for key in ["entities_processed", "ch_found", "psc_found", "parents_matched", "parents_saved"]:
                        total_stats[key] += stats.get(key, 0)

                    # Print per-company summary
                    saved_str = f", {stats['parents_saved']} saved" if args.save else ""
                    print(f"  Results: {stats['ch_found']} CH matched, "
                          f"{stats['psc_found']} with PSC, "
                          f"{stats['parents_matched']} parents found{saved_str}")

            except Exception as e:
                print(f"  Error processing {ticker}: {e}")
                import traceback
                traceback.print_exc()

    # Final summary
    print_summary({
        "Companies processed": total_stats["companies_processed"],
        "Entities processed": total_stats["entities_processed"],
        "CH numbers found": total_stats["ch_found"],
        "With corporate PSC": total_stats["psc_found"],
        "Parents matched": total_stats["parents_matched"],
        "Parents saved": total_stats["parents_saved"] if args.save else "N/A (dry run)",
        "API requests": ch_client.request_count,
    })


if __name__ == "__main__":
    run_async(main())

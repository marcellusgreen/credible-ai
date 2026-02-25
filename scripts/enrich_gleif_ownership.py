#!/usr/bin/env python3
"""
Enrich entity ownership from GLEIF LEI Level 2 "Who Owns Whom" data.

Downloads bulk GLEIF CSV files (Level 1 entities + Level 2 relationships),
builds in-memory indexes, and matches orphan entities to LEI records to
discover parent-child relationships.

GLEIF API is free and open — no API key required.

Usage:
    # Analyze orphan count and LEI match potential
    python scripts/enrich_gleif_ownership.py --analyze

    # Single company dry run
    python scripts/enrich_gleif_ownership.py --ticker TMO

    # Single company with verbose output
    python scripts/enrich_gleif_ownership.py --ticker TMO --verbose

    # Single company, persist to DB
    python scripts/enrich_gleif_ownership.py --ticker TMO --save

    # All companies, dry run
    python scripts/enrich_gleif_ownership.py --all

    # All companies, persist
    python scripts/enrich_gleif_ownership.py --all --save

    # Skip entities that already have a cached LEI
    python scripts/enrich_gleif_ownership.py --all --skip-cached

    # Custom confidence threshold
    python scripts/enrich_gleif_ownership.py --all --confidence 0.75

    # Force re-download of GLEIF files
    python scripts/enrich_gleif_ownership.py --all --force-download
"""

import csv
import io
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
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
from app.models import Company, Entity, OwnershipLink


# =============================================================================
# CONSTANTS
# =============================================================================

# GLEIF bulk download URLs
GLEIF_LEI_URL = "https://leidata-preview.gleif.org/api/v2/golden-copies/publishes/lei2/latest"
GLEIF_RR_URL = "https://leidata-preview.gleif.org/api/v2/golden-copies/publishes/rr/latest"

# Cache directory and file paths
GLEIF_DATA_DIR = Path(__file__).parent.parent / "data" / "gleif"
LEI_CACHE_FILE = GLEIF_DATA_DIR / "lei_records.csv"
RR_CACHE_FILE = GLEIF_DATA_DIR / "relationships.csv"

# Cache duration: 24 hours
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60

# Global corporate suffixes to strip for name matching
CORPORATE_SUFFIXES = [
    # English
    "limited", "ltd", "public limited company", "plc",
    "limited liability company", "llc",
    "limited liability partnership", "llp",
    "limited partnership", "lp",
    "incorporated", "inc", "corporation", "corp",
    "company", "co",
    # German
    "gesellschaft mit beschraenkter haftung", "gesellschaft mit beschrankter haftung", "gmbh",
    "aktiengesellschaft", "ag",
    "kommanditgesellschaft", "kg",
    "gmbh & co kg", "gmbh and co kg",
    # French
    "societe anonyme", "sa",
    "societe a responsabilite limitee", "sarl",
    "societe par actions simplifiee", "sas",
    "societe en commandite par actions", "sca",
    # Dutch/Belgian
    "besloten vennootschap", "bv",
    "naamloze vennootschap", "nv",
    # Nordic
    "aktiebolag", "ab",
    "aktieselskab", "as",
    "allmennaksjeselskap", "asa",
    "oyj",
    # Italian
    "societa per azioni", "spa",
    "societa a responsabilita limitata", "srl",
    # Spanish/Portuguese
    "sociedad anonima",
    "sociedad limitada", "sl",
    "sociedad de responsabilidad limitada",
    # Japanese
    "kabushiki kaisha", "kk",
    # Other
    "proprietary limited", "pty ltd", "pty",
    "private limited", "pvt ltd", "pvt",
    "holdings", "holding",
    "group",
]

# Short suffixes to strip as trailing words (regex pattern)
SHORT_SUFFIX_PATTERN = re.compile(
    r'\s+('
    r'ltd|plc|llc|llp|lp|inc|corp|co|'
    r'gmbh|ag|kg|'
    r'sa|sarl|sas|sca|'
    r'bv|nv|'
    r'ab|as|asa|oyj|'
    r'spa|srl|sl|'
    r'kk|'
    r'pty|pvt'
    r')\s*$'
)

# GLEIF Level 2 relationship types we care about
DIRECT_PARENT_RELATIONSHIP = "IS_DIRECTLY_CONSOLIDATED_BY"
ULTIMATE_PARENT_RELATIONSHIP = "IS_ULTIMATELY_CONSOLIDATED_BY"


# =============================================================================
# GLEIF DATA DOWNLOAD & CACHING
# =============================================================================

def is_cache_fresh(filepath: Path) -> bool:
    """Check if a cached file exists and is less than 24 hours old."""
    if not filepath.exists():
        return False
    age = time.time() - filepath.stat().st_mtime
    return age < CACHE_MAX_AGE_SECONDS


async def download_gleif_file(url: str, dest: Path, description: str) -> bool:
    """Download a GLEIF file with progress indication.

    The GLEIF API returns a JSON response with a download URL. We first
    fetch the metadata, then download the actual CSV/ZIP file.
    """
    print(f"  Downloading {description}...")

    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            # First, get the download metadata
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                print(f"    Error fetching metadata: {resp.status_code}")
                return False

            metadata = resp.json()
            # The actual CSV download URL is in the response
            csv_url = metadata.get("data", {}).get("full_file", {}).get("csv", {}).get("url")
            if not csv_url:
                # Try delta or other formats
                csv_url = metadata.get("data", {}).get("delta_file", {}).get("csv", {}).get("url")
            if not csv_url:
                print(f"    Could not find CSV download URL in metadata")
                print(f"    Metadata keys: {list(metadata.get('data', {}).keys())}")
                return False

            print(f"    URL: {csv_url[:80]}...")

            # Download the actual file (may be ZIP)
            async with client.stream("GET", csv_url) as stream:
                if stream.status_code != 200:
                    print(f"    Download error: {stream.status_code}")
                    return False

                content_length = int(stream.headers.get("content-length", 0))
                downloaded = 0

                # Determine if it's a ZIP file
                is_zip = csv_url.endswith(".zip") or "zip" in stream.headers.get("content-type", "")

                if is_zip:
                    # Download to memory, then extract
                    chunks = []
                    async for chunk in stream.aiter_bytes(chunk_size=1024 * 1024):
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        if content_length:
                            pct = downloaded / content_length * 100
                            print(f"    Downloaded: {downloaded / 1024 / 1024:.1f} MB ({pct:.0f}%)", end="\r")
                    print()

                    # Extract CSV from ZIP
                    zip_data = b"".join(chunks)
                    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
                        if not csv_names:
                            print(f"    No CSV file found in ZIP archive")
                            return False
                        csv_name = csv_names[0]
                        print(f"    Extracting {csv_name}...")
                        with zf.open(csv_name) as csv_in, open(dest, "wb") as csv_out:
                            while True:
                                data = csv_in.read(1024 * 1024)
                                if not data:
                                    break
                                csv_out.write(data)
                else:
                    # Direct CSV download
                    with open(dest, "wb") as f:
                        async for chunk in stream.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if content_length:
                                pct = downloaded / content_length * 100
                                print(f"    Downloaded: {downloaded / 1024 / 1024:.1f} MB ({pct:.0f}%)", end="\r")
                    print()

            size_mb = dest.stat().st_size / 1024 / 1024
            print(f"    Saved: {dest.name} ({size_mb:.1f} MB)")
            return True

    except Exception as e:
        print(f"    Download error: {e}")
        return False


async def ensure_gleif_data(force_download: bool = False) -> bool:
    """Ensure GLEIF Level 1 and Level 2 data files are downloaded and cached."""
    GLEIF_DATA_DIR.mkdir(parents=True, exist_ok=True)

    lei_fresh = is_cache_fresh(LEI_CACHE_FILE) and not force_download
    rr_fresh = is_cache_fresh(RR_CACHE_FILE) and not force_download

    if lei_fresh and rr_fresh:
        lei_size = LEI_CACHE_FILE.stat().st_size / 1024 / 1024
        rr_size = RR_CACHE_FILE.stat().st_size / 1024 / 1024
        print(f"Using cached GLEIF data (LEI: {lei_size:.1f} MB, RR: {rr_size:.1f} MB)")
        return True

    print_subheader("DOWNLOADING GLEIF DATA")

    if not lei_fresh:
        if not await download_gleif_file(GLEIF_LEI_URL, LEI_CACHE_FILE, "Level 1 Entity Data"):
            print("Failed to download Level 1 entity data")
            return False

    if not rr_fresh:
        if not await download_gleif_file(GLEIF_RR_URL, RR_CACHE_FILE, "Level 2 Relationship Data"):
            print("Failed to download Level 2 relationship data")
            return False

    return True


# =============================================================================
# IN-MEMORY INDEXES
# =============================================================================

def build_lei_index(verbose: bool = False) -> tuple[dict, dict]:
    """Build in-memory indexes from Level 1 CSV.

    Returns:
        name_to_leis: dict mapping normalized name → list of LEIs
        lei_to_entity: dict mapping LEI → {name, jurisdiction, status, legal_form}
    """
    print("  Building LEI entity index...")
    start = time.monotonic()

    name_to_leis: dict[str, list[str]] = {}
    lei_to_entity: dict[str, dict] = {}

    with open(LEI_CACHE_FILE, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            lei = row.get("LEI", "").strip()
            name = row.get("Entity.LegalName", "").strip()
            status = row.get("Registration.RegistrationStatus", "").strip()
            jurisdiction = row.get("Entity.LegalJurisdiction", "").strip()
            legal_form = row.get("Entity.LegalForm.EntityLegalFormCode", "").strip()
            country = row.get("Entity.LegalAddress.Country", "").strip()

            if not lei or not name:
                continue

            lei_to_entity[lei] = {
                "name": name,
                "jurisdiction": jurisdiction,
                "status": status,
                "legal_form": legal_form,
                "country": country,
            }

            norm = normalize_gleif_name(name)
            if norm:
                name_to_leis.setdefault(norm, []).append(lei)

            count += 1
            if count % 500000 == 0:
                print(f"    Processed {count:,} LEI records...")

    elapsed = time.monotonic() - start
    print(f"    Indexed {len(lei_to_entity):,} LEI entities ({len(name_to_leis):,} unique names) in {elapsed:.1f}s")

    return name_to_leis, lei_to_entity


def build_relationship_index(verbose: bool = False) -> tuple[dict, dict]:
    """Build in-memory indexes from Level 2 CSV.

    Returns:
        child_to_parent: dict mapping child LEI → direct parent LEI (ACTIVE only)
        child_to_ultimate: dict mapping child LEI → ultimate parent LEI (ACTIVE only)
    """
    print("  Building relationship index...")
    start = time.monotonic()

    child_to_parent: dict[str, str] = {}
    child_to_ultimate: dict[str, str] = {}

    with open(RR_CACHE_FILE, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            rel_type = row.get("Relationship.RelationshipType", "").strip()
            rel_status = row.get("Relationship.RelationshipStatus", "").strip()
            child_lei = row.get("Relationship.StartNode.NodeID", "").strip()
            parent_lei = row.get("Relationship.EndNode.NodeID", "").strip()

            if not child_lei or not parent_lei:
                continue

            # Only use ACTIVE relationships
            if rel_status != "ACTIVE":
                continue

            if rel_type == DIRECT_PARENT_RELATIONSHIP:
                child_to_parent[child_lei] = parent_lei
            elif rel_type == ULTIMATE_PARENT_RELATIONSHIP:
                child_to_ultimate[child_lei] = parent_lei

            count += 1

    elapsed = time.monotonic() - start
    print(f"    Indexed {len(child_to_parent):,} direct parent + {len(child_to_ultimate):,} ultimate parent relationships in {elapsed:.1f}s")

    return child_to_parent, child_to_ultimate


# =============================================================================
# NAME MATCHING UTILITIES
# =============================================================================

def normalize_gleif_name(name: str) -> str:
    """Normalize a company name for GLEIF matching.

    Strips corporate suffixes globally (international), lowercases, removes punctuation.
    """
    if not name:
        return ""

    n = name.lower().strip()

    # Remove common punctuation
    n = n.replace(",", "").replace(".", "").replace("'", "").replace('"', '')
    n = n.replace("(", "").replace(")", "").replace("-", " ")

    # Normalize long-form suffixes to short form, then strip
    for suffix in CORPORATE_SUFFIXES:
        # Only replace at end of string or followed by space
        if n.endswith(suffix):
            n = n[: -len(suffix)]
        elif f" {suffix} " in n:
            n = n.replace(f" {suffix} ", " ")

    # Remove trailing short suffixes
    n = SHORT_SUFFIX_PATTERN.sub("", n)

    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()

    return n


def score_lei_match(
    entity_name: str,
    entity_jurisdiction: str,
    lei: str,
    lei_entity: dict,
    confidence_threshold: float,
) -> float:
    """Score a LEI entity match against an orphan entity.

    Returns 0.0-1.0 combining:
    - Fuzzy name similarity via token_sort_ratio (70% weight)
    - Jurisdiction/country match (+0.20)
    - Entity status ACTIVE in GLEIF (+0.10)
    """
    lei_name = lei_entity.get("name", "")
    if not lei_name:
        return 0.0

    norm_entity = normalize_gleif_name(entity_name)
    norm_lei = normalize_gleif_name(lei_name)

    if not norm_entity or not norm_lei:
        return 0.0

    # Name similarity (70% weight)
    name_score = fuzz.token_sort_ratio(norm_entity, norm_lei) / 100.0
    score = name_score * 0.70

    # Quick reject: if name score alone can't meet threshold, skip
    if score + 0.30 < confidence_threshold:
        return score

    # Jurisdiction match (+0.20)
    if entity_jurisdiction:
        ej = entity_jurisdiction.lower()
        lei_country = lei_entity.get("country", "").lower()
        lei_jurisdiction = lei_entity.get("jurisdiction", "").lower()

        # Map common jurisdiction descriptions to country codes
        jurisdiction_map = {
            "us": ["united states", "us", "usa", "delaware", "new york", "california", "texas", "nevada"],
            "gb": ["united kingdom", "uk", "england", "wales", "scotland", "england and wales"],
            "de": ["germany", "deutschland"],
            "fr": ["france"],
            "nl": ["netherlands", "nederland"],
            "ie": ["ireland"],
            "lu": ["luxembourg"],
            "ch": ["switzerland"],
            "jp": ["japan"],
            "ca": ["canada"],
            "au": ["australia"],
            "sg": ["singapore"],
            "hk": ["hong kong"],
            "cn": ["china"],
            "br": ["brazil"],
            "in": ["india"],
            "se": ["sweden"],
            "dk": ["denmark"],
            "no": ["norway"],
            "fi": ["finland"],
            "be": ["belgium"],
            "it": ["italy"],
            "es": ["spain"],
            "pt": ["portugal"],
            "kr": ["south korea", "korea"],
            "tw": ["taiwan"],
            "mx": ["mexico"],
            "bm": ["bermuda"],
            "ky": ["cayman islands", "cayman"],
            "vg": ["british virgin islands", "bvi"],
            "je": ["jersey"],
            "gg": ["guernsey"],
        }

        matched = False
        for code, keywords in jurisdiction_map.items():
            if any(kw in ej for kw in keywords):
                if lei_country == code or code in lei_jurisdiction.lower():
                    matched = True
                    break
            if any(kw in lei_jurisdiction.lower() for kw in keywords):
                if any(kw in ej for kw in keywords):
                    matched = True
                    break

        if matched:
            score += 0.20

    # Entity status bonus (+0.10)
    if lei_entity.get("status", "").upper() == "ISSUED":
        score += 0.10

    return min(score, 1.0)


def match_parent_to_entity(
    parent_name: str,
    parent_lei: str,
    company_entities: list,
    root_entity,
    verbose: bool,
) -> Optional[dict]:
    """Match a GLEIF parent name/LEI to an entity in the company.

    Tries in order:
    a. Exact LEI match (if entity has cached LEI in attributes)
    b. Exact normalized name match
    c. Root entity name match (fuzzy >= 70)
    d. Fuzzy name match (token_sort_ratio >= 80)
    """
    norm_parent = normalize_gleif_name(parent_name)

    # a. Exact LEI match
    if parent_lei:
        for entity in company_entities:
            entity_lei = (entity.attributes or {}).get("gleif_lei", "")
            if entity_lei and entity_lei == parent_lei:
                if verbose:
                    print(f"      Parent match (LEI): {entity.name}")
                return {"entity": entity, "confidence": "high", "method": "lei_match"}

    # b. Exact normalized name match
    for entity in company_entities:
        norm_entity = normalize_gleif_name(entity.name)
        if norm_entity and norm_entity == norm_parent:
            if verbose:
                print(f"      Parent match (exact name): {entity.name}")
            return {"entity": entity, "confidence": "high", "method": "exact_name"}

        if entity.legal_name:
            norm_legal = normalize_gleif_name(entity.legal_name)
            if norm_legal and norm_legal == norm_parent:
                if verbose:
                    print(f"      Parent match (exact legal name): {entity.name}")
                return {"entity": entity, "confidence": "high", "method": "exact_legal_name"}

    # c. Root entity name match
    if root_entity:
        norm_root = normalize_gleif_name(root_entity.name)
        if norm_root and norm_parent:
            root_score = fuzz.token_sort_ratio(norm_root, norm_parent)
            if root_score >= 70:
                if verbose:
                    print(f"      Parent match (root entity, score={root_score}): {root_entity.name}")
                return {"entity": root_entity, "confidence": "high", "method": "root_entity"}

    # d. Fuzzy name match
    best_match = None
    best_ratio = 0
    for entity in company_entities:
        norm_entity = normalize_gleif_name(entity.name)
        if not norm_entity:
            continue

        ratio = fuzz.token_sort_ratio(norm_parent, norm_entity)
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = entity

        if entity.legal_name:
            norm_legal = normalize_gleif_name(entity.legal_name)
            if norm_legal:
                ratio_legal = fuzz.token_sort_ratio(norm_parent, norm_legal)
                if ratio_legal > best_ratio:
                    best_ratio = ratio_legal
                    best_match = entity

    if best_match and best_ratio >= 80:
        if verbose:
            print(f"      Parent match (fuzzy, score={best_ratio}): {best_match.name}")
        return {"entity": best_match, "confidence": "medium", "method": f"fuzzy_{best_ratio}"}

    return None


# =============================================================================
# ENTITY QUERIES
# =============================================================================

async def get_orphan_entities(db, company_id: UUID = None) -> list:
    """Get orphan entities (parent_id IS NULL, is_root=false).

    Returns list of rows with entity info + company ticker.
    """
    query = (
        select(
            Entity.id,
            Entity.name,
            Entity.legal_name,
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
    return result.fetchall()


async def get_company_entities(db, company_id: UUID) -> list[Entity]:
    """Get all entities for a company (for parent matching)."""
    result = await db.execute(
        select(Entity).where(Entity.company_id == company_id)
    )
    return list(result.scalars())


# =============================================================================
# ANALYSIS MODE
# =============================================================================

async def run_analyze(args, name_to_leis: dict = None):
    """Show statistics about orphan entities and LEI match potential."""
    print_header("GLEIF LEI OWNERSHIP ENRICHMENT - ANALYSIS")

    async with get_db_session() as db:
        # Total orphans
        total_orphans = await db.scalar(
            select(func.count(Entity.id)).where(
                and_(Entity.parent_id.is_(None), Entity.is_root.is_(False))
            )
        )

        total_entities = await db.scalar(select(func.count(Entity.id)))

        # Get orphans
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company not found: {args.ticker}")
                return
            orphans = await get_orphan_entities(db, company.id)
            print(f"Company: {company.ticker} - {company.name}")
        else:
            orphans = await get_orphan_entities(db)

        # Count entities with cached LEIs
        cached_lei_count = sum(
            1 for r in orphans
            if (r.attributes or {}).get("gleif_lei")
        )

        # Count already enriched
        enriched_count = sum(
            1 for r in orphans
            if (r.attributes or {}).get("gleif_enrichment_date")
        )

        # Group by company
        by_company = {}
        for r in orphans:
            by_company.setdefault(r.ticker, []).append(r)

        # Count unique companies
        total_companies = await db.scalar(select(func.count(Company.id)))

        print(f"\nTotal entities: {total_entities:,}")
        print(f"Total orphans (parent_id=NULL, is_root=false): {total_orphans:,}")
        print(f"Companies with orphans: {len(by_company)}")
        print(f"  With cached GLEIF LEI: {cached_lei_count}")
        print(f"  Already GLEIF-enriched: {enriched_count}")

        # If we have the LEI index, show match potential
        if name_to_leis:
            potential_matches = 0
            for r in orphans:
                norm = normalize_gleif_name(r.name)
                if norm and norm in name_to_leis:
                    potential_matches += 1
            print(f"\nLEI match potential (exact normalized name):")
            print(f"  Orphans with name in GLEIF index: {potential_matches:,} / {len(orphans):,}")
            print(f"  Match rate: {potential_matches / len(orphans) * 100:.1f}%" if orphans else "  No orphans")

        if args.verbose:
            print_subheader("ORPHANS BY COMPANY")
            for ticker in sorted(by_company.keys()):
                entities = by_company[ticker]
                print(f"\n  {ticker} ({len(entities)} orphans):")
                for e in entities[:10]:
                    cached = " [LEI cached]" if (e.attributes or {}).get("gleif_lei") else ""
                    print(f"    - {e.name} ({e.jurisdiction}){cached}")
                if len(entities) > 10:
                    print(f"    ... and {len(entities) - 10} more")


# =============================================================================
# MAIN ENRICHMENT
# =============================================================================

def process_orphan_entity(
    entity_name: str,
    entity_legal_name: str,
    entity_jurisdiction: str,
    entity_attributes: dict,
    name_to_leis: dict,
    lei_to_entity: dict,
    child_to_parent: dict,
    child_to_ultimate: dict,
    confidence_threshold: float,
    skip_cached: bool,
    verbose: bool,
) -> Optional[dict]:
    """Process a single orphan entity: find LEI, look up parent.

    Returns dict with LEI match info and parent LEI info, or None.
    """
    attrs = entity_attributes or {}
    cached_lei = attrs.get("gleif_lei")

    # Step 1: Check for cached LEI
    if cached_lei and skip_cached:
        if verbose:
            print(f"      Skipping (cached LEI: {cached_lei})")
        return None

    # Step 2: Find LEI for this entity
    matched_lei = None
    match_score = 0.0

    if cached_lei and cached_lei in lei_to_entity:
        matched_lei = cached_lei
        match_score = 1.0
        if verbose:
            print(f"      Using cached LEI: {cached_lei}")
    else:
        # Try entity name
        candidates = []
        for name in [entity_name, entity_legal_name]:
            if not name:
                continue
            norm = normalize_gleif_name(name)
            if norm and norm in name_to_leis:
                for lei in name_to_leis[norm]:
                    candidates.append(lei)

        if not candidates:
            if verbose:
                print(f"      No LEI candidates for '{entity_name}'")
            return None

        # Score and pick best match
        best_score = 0.0
        best_lei = None
        seen = set()
        for lei in candidates:
            if lei in seen:
                continue
            seen.add(lei)
            lei_ent = lei_to_entity.get(lei, {})
            s = score_lei_match(entity_name, entity_jurisdiction, lei, lei_ent, confidence_threshold)
            if s > best_score:
                best_score = s
                best_lei = lei

        if best_score < confidence_threshold or not best_lei:
            if verbose:
                print(f"      No LEI match above threshold ({best_score:.2f} < {confidence_threshold})")
            return None

        matched_lei = best_lei
        match_score = best_score
        lei_ent = lei_to_entity.get(matched_lei, {})
        if verbose:
            print(f"      LEI match: {lei_ent.get('name', '?')} ({matched_lei}) score={match_score:.2f}")

    # Step 3: Look up parent LEI
    parent_lei = child_to_parent.get(matched_lei)
    ultimate_parent_lei = child_to_ultimate.get(matched_lei)

    if not parent_lei:
        if verbose:
            print(f"      No parent relationship in GLEIF for LEI {matched_lei}")
        return {
            "lei": matched_lei,
            "lei_match_score": match_score,
            "parent_lei": None,
            "parent_name": None,
        }

    parent_entity_info = lei_to_entity.get(parent_lei, {})
    parent_name = parent_entity_info.get("name", "")

    if verbose:
        print(f"      GLEIF parent: {parent_name} ({parent_lei})")

    return {
        "lei": matched_lei,
        "lei_match_score": match_score,
        "parent_lei": parent_lei,
        "parent_name": parent_name,
        "ultimate_parent_lei": ultimate_parent_lei,
    }


async def process_company(
    db,
    company_id: UUID,
    ticker: str,
    orphans: list,
    name_to_leis: dict,
    lei_to_entity: dict,
    child_to_parent: dict,
    child_to_ultimate: dict,
    confidence_threshold: float,
    skip_cached: bool,
    save: bool,
    verbose: bool,
) -> dict:
    """Process all orphan entities for a single company."""
    stats = {
        "entities_processed": 0,
        "lei_found": 0,
        "parent_lei_found": 0,
        "parents_matched": 0,
        "parents_saved": 0,
    }

    # Get all entities for parent matching
    company_entities = await get_company_entities(db, company_id)
    root_entity = next((e for e in company_entities if e.is_root), None)

    for orphan in orphans:
        entity_id = orphan.id
        entity_name = orphan.name
        entity_legal_name = orphan.legal_name
        entity_jurisdiction = orphan.jurisdiction
        entity_attributes = orphan.attributes

        if verbose:
            print(f"    [{ticker}] {entity_name}")

        result = process_orphan_entity(
            entity_name=entity_name,
            entity_legal_name=entity_legal_name,
            entity_jurisdiction=entity_jurisdiction or "",
            entity_attributes=entity_attributes,
            name_to_leis=name_to_leis,
            lei_to_entity=lei_to_entity,
            child_to_parent=child_to_parent,
            child_to_ultimate=child_to_ultimate,
            confidence_threshold=confidence_threshold,
            skip_cached=skip_cached,
            verbose=verbose,
        )

        stats["entities_processed"] += 1

        if result is None:
            continue

        matched_lei = result.get("lei")
        if matched_lei:
            stats["lei_found"] += 1

        parent_lei = result.get("parent_lei")
        parent_name = result.get("parent_name")

        if not parent_lei or not parent_name:
            # Cache LEI even if no parent found
            if save and matched_lei:
                entity = await db.get(Entity, entity_id)
                if entity:
                    attrs = dict(entity.attributes or {})
                    if not attrs.get("gleif_lei"):
                        attrs["gleif_lei"] = matched_lei
                        entity.attributes = attrs
            continue

        stats["parent_lei_found"] += 1

        # Match parent to entity in same company
        matched_parent = match_parent_to_entity(
            parent_name=parent_name,
            parent_lei=parent_lei,
            company_entities=company_entities,
            root_entity=root_entity,
            verbose=verbose,
        )

        if matched_parent:
            parent_entity = matched_parent["entity"]
            stats["parents_matched"] += 1

            if not verbose:
                print(f"    [{ticker}] {entity_name} -> {parent_entity.name} ({matched_parent['confidence']}, via {matched_parent['method']})")

            if save:
                entity = await db.get(Entity, entity_id)
                if entity:
                    entity.parent_id = parent_entity.id

                    # Store metadata in attributes
                    attrs = dict(entity.attributes or {})
                    attrs["gleif_lei"] = matched_lei
                    attrs["gleif_parent_lei"] = parent_lei
                    attrs["gleif_parent_name"] = parent_name
                    attrs["gleif_enrichment_date"] = datetime.now(timezone.utc).isoformat()
                    attrs["gleif_match_confidence"] = result.get("lei_match_score", 0)
                    attrs["gleif_match_method"] = matched_parent["method"]
                    if result.get("ultimate_parent_lei"):
                        attrs["gleif_ultimate_parent_lei"] = result["ultimate_parent_lei"]
                    entity.attributes = attrs

                    # Create or update OwnershipLink
                    existing_link = await db.scalar(
                        select(OwnershipLink).where(
                            OwnershipLink.child_entity_id == entity_id
                        )
                    )

                    if existing_link:
                        existing_link.parent_entity_id = parent_entity.id
                        existing_link.ownership_type = "direct"
                        existing_link.attributes = {
                            **(existing_link.attributes or {}),
                            "source": "gleif_lei",
                            "gleif_child_lei": matched_lei,
                            "gleif_parent_lei": parent_lei,
                        }
                    else:
                        link = OwnershipLink(
                            parent_entity_id=parent_entity.id,
                            child_entity_id=entity_id,
                            ownership_type="direct",
                            attributes={
                                "source": "gleif_lei",
                                "gleif_child_lei": matched_lei,
                                "gleif_parent_lei": parent_lei,
                            },
                        )
                        db.add(link)

                    stats["parents_saved"] += 1
        else:
            if verbose:
                print(f"      Parent '{parent_name}' not matched to any entity in {ticker}")

            # Cache LEI even if parent not matched to entity
            if save and matched_lei:
                entity = await db.get(Entity, entity_id)
                if entity:
                    attrs = dict(entity.attributes or {})
                    if not attrs.get("gleif_lei"):
                        attrs["gleif_lei"] = matched_lei
                        attrs["gleif_parent_lei"] = parent_lei
                        attrs["gleif_parent_name"] = parent_name
                        entity.attributes = attrs

    if save:
        await db.commit()

    return stats


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = create_fix_parser("Enrich entity ownership from GLEIF LEI data")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show statistics only, no processing",
    )
    parser.add_argument(
        "--skip-cached",
        action="store_true",
        help="Skip entities that already have a cached GLEIF LEI",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.70,
        help="Minimum LEI match confidence threshold (default: 0.70)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download GLEIF files even if cached",
    )

    args = parser.parse_args()

    # Analysis mode — can run without downloading data
    if args.analyze:
        # Try to load LEI index if data is available
        name_to_leis = None
        if LEI_CACHE_FILE.exists():
            name_to_leis, _ = build_lei_index()
        await run_analyze(args, name_to_leis)
        return

    if not args.ticker and not getattr(args, "all", False):
        print("Error: Must specify --ticker, --all, or --analyze")
        return

    # Download GLEIF data
    if not await ensure_gleif_data(args.force_download):
        print("Error: Failed to download GLEIF data")
        return

    # Build in-memory indexes
    print_header("GLEIF LEI OWNERSHIP ENRICHMENT")
    print(f"Mode: {'SAVE TO DB' if args.save else 'DRY RUN'}")
    print(f"Confidence threshold: {args.confidence}")
    if args.skip_cached:
        print("Skipping entities with cached LEIs")
    print()

    print_subheader("BUILDING INDEXES")
    name_to_leis, lei_to_entity = build_lei_index(args.verbose)
    child_to_parent, child_to_ultimate = build_relationship_index(args.verbose)
    print()

    # Collect orphan entities
    async with get_db_session() as db:
        if args.ticker:
            company = await db.scalar(
                select(Company).where(Company.ticker == args.ticker.upper())
            )
            if not company:
                print(f"Company not found: {args.ticker}")
                return
            all_orphans = await get_orphan_entities(db, company.id)
        else:
            all_orphans = await get_orphan_entities(db)
            if args.limit and args.limit > 0:
                seen_companies = set()
                limited = []
                for r in all_orphans:
                    seen_companies.add(r.company_id)
                    if len(seen_companies) <= args.limit:
                        limited.append(r)
                all_orphans = limited

    if not all_orphans:
        print("No orphan entities found.")
        return

    # Group by company
    by_company = {}
    for r in all_orphans:
        by_company.setdefault((r.company_id, r.ticker), []).append(r)

    print(f"Found {len(all_orphans):,} orphans across {len(by_company)} companies")
    print()

    total_stats = {
        "companies_processed": 0,
        "entities_processed": 0,
        "lei_found": 0,
        "parent_lei_found": 0,
        "parents_matched": 0,
        "parents_saved": 0,
    }

    for (company_id, ticker), orphans in sorted(by_company.items(), key=lambda x: x[0][1]):
        print_subheader(f"{ticker} ({len(orphans)} orphans)")

        try:
            # Fresh session per company (Neon serverless pattern)
            async with get_db_session() as db:
                stats = await process_company(
                    db=db,
                    company_id=company_id,
                    ticker=ticker,
                    orphans=orphans,
                    name_to_leis=name_to_leis,
                    lei_to_entity=lei_to_entity,
                    child_to_parent=child_to_parent,
                    child_to_ultimate=child_to_ultimate,
                    confidence_threshold=args.confidence,
                    skip_cached=args.skip_cached,
                    save=args.save,
                    verbose=args.verbose,
                )

                total_stats["companies_processed"] += 1
                for key in ["entities_processed", "lei_found", "parent_lei_found", "parents_matched", "parents_saved"]:
                    total_stats[key] += stats.get(key, 0)

                saved_str = f", {stats['parents_saved']} saved" if args.save else ""
                print(f"  Results: {stats['lei_found']} LEI matched, "
                      f"{stats['parent_lei_found']} with parent LEI, "
                      f"{stats['parents_matched']} parents found{saved_str}")

        except Exception as e:
            print(f"  Error processing {ticker}: {e}")
            import traceback
            traceback.print_exc()

    # Final summary
    print_summary({
        "Companies processed": total_stats["companies_processed"],
        "Entities processed": total_stats["entities_processed"],
        "LEI matches found": total_stats["lei_found"],
        "With parent LEI in GLEIF": total_stats["parent_lei_found"],
        "Parents matched to entity": total_stats["parents_matched"],
        "Parents saved": total_stats["parents_saved"] if args.save else "N/A (dry run)",
        "GLEIF index size": f"{len(lei_to_entity):,} LEIs, {len(child_to_parent):,} relationships",
    })


if __name__ == "__main__":
    run_async(main())

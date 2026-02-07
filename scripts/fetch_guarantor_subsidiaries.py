#!/usr/bin/env python3
"""
Fetch and parse Exhibit 22.1 (List of Guarantor Subsidiaries) from SEC EDGAR.

This script fetches the official guarantor subsidiary list from SEC filings,
creates missing entities, and links them as guarantors to the appropriate debt.

Usage:
    python scripts/fetch_guarantor_subsidiaries.py --ticker TMUS [--dry-run]
"""

import argparse
import os
import re
from typing import Optional

import google.generativeai as genai
from pydantic import BaseModel, Field
from sqlalchemy import select

from script_utils import get_db_session, print_header, run_async
from app.core.config import get_settings
from app.models import Company, Entity, DebtInstrument, Guarantee, DocumentSection
from app.services.extraction import SecApiClient
from app.services.utils import clean_filing_html, parse_json_robust


# =============================================================================
# SEC API HELPERS
# =============================================================================

def fetch_exhibit_22(ticker: str, sec_client: SecApiClient) -> Optional[str]:
    """
    Fetch Exhibit 22.1 (List of Guarantor Subsidiaries) from latest 10-K.

    Exhibit 22 was introduced by SEC in 2021 to require explicit listing
    of all subsidiary guarantors for registered debt.
    """
    if not sec_client.query_api:
        return None

    # Query for 10-K filings with Exhibit 22
    query = {
        "query": {
            "query_string": {
                "query": f'ticker:{ticker} AND formType:"10-K"'
            }
        },
        "from": "0",
        "size": "1",
        "sort": [{"filedAt": {"order": "desc"}}]
    }

    try:
        response = sec_client.query_api.get_filings(query)
        filings = response.get("filings", [])

        if not filings:
            print(f"  No 10-K filings found for {ticker}")
            return None

        # Find Exhibit 22 URL in the filing
        for doc in filings[0].get("documentFormatFiles", []):
            doc_type = doc.get("type", "").upper()
            description = doc.get("description", "").upper()

            # Look for EX-22 or description mentioning guarantor subsidiaries
            if "22" in doc_type or "GUARANTOR" in description or "SUBSIDIARY GUARANTOR" in description:
                exhibit_url = doc.get("documentUrl", "")
                if exhibit_url:
                    print(f"  Found Exhibit 22: {doc_type} - {description}")
                    content = sec_client.get_filing_content(exhibit_url)
                    if content:
                        return clean_filing_html(content)

        # If no Exhibit 22, look in the main 10-K for guarantor subsidiary section
        print(f"  No Exhibit 22 found, checking main 10-K filing...")
        main_url = filings[0].get("linkToFilingDetails", "")
        if main_url:
            content = sec_client.get_filing_content(main_url)
            if content:
                content = clean_filing_html(content)
                # Look for guarantor subsidiary section
                lower = content.lower()
                if "guarantor subsidiaries" in lower or "subsidiary guarantors" in lower:
                    # Extract relevant section
                    idx = lower.find("guarantor subsidiar")
                    if idx == -1:
                        idx = lower.find("subsidiary guarantor")
                    if idx >= 0:
                        return content[max(0, idx-500):idx+15000]

        return None
    except Exception as e:
        print(f"  [FAIL] SEC-API Exhibit 22 fetch failed: {e}")
        return None


def fetch_exhibit_21_full(ticker: str, sec_client: SecApiClient) -> Optional[str]:
    """
    Fetch full Exhibit 21 (Subsidiaries of the Registrant).
    """
    content = sec_client.get_exhibit_21(ticker)
    if content:
        return clean_filing_html(content)
    return None


# =============================================================================
# PROMPTS
# =============================================================================

PARSE_GUARANTOR_LIST_PROMPT = """Extract the list of guarantor subsidiary names from this SEC filing content.

CONTENT:
{content}

Return JSON with:
{{
  "guarantor_subsidiaries": [
    {{
      "name": "Full Legal Name of Entity",
      "jurisdiction": "State/Country of incorporation if mentioned",
      "entity_type": "LLC|Corporation|LP|etc if mentioned"
    }}
  ],
  "debt_description": "Description of which debt these entities guarantee, if mentioned",
  "uncertainties": []
}}

EXTRACTION TIPS:
1. Look for tables listing subsidiary names
2. Include ALL listed subsidiaries - don't skip any
3. Clean up names (remove asterisks, footnote markers, extra spaces)
4. If jurisdiction is shown in a separate column, capture it
5. Entity type might be embedded in name (e.g., "ABC Holdings, LLC")

Return only valid JSON."""


# =============================================================================
# MODELS
# =============================================================================

class GuarantorSubsidiary(BaseModel):
    """A guarantor subsidiary from Exhibit 22."""
    name: str
    jurisdiction: Optional[str] = None
    entity_type: Optional[str] = None


class GuarantorListResult(BaseModel):
    """Result of parsing guarantor list."""
    guarantor_subsidiaries: list[GuarantorSubsidiary] = Field(default_factory=list)
    debt_description: Optional[str] = None
    uncertainties: list[str] = Field(default_factory=list)


# =============================================================================
# MAIN FUNCTION
# =============================================================================

async def fetch_and_create_guarantors(
    ticker: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Fetch Exhibit 22.1 and create missing guarantor entities.
    """
    settings = get_settings()

    stats = {
        "ticker": ticker,
        "exhibit_22_found": False,
        "exhibit_21_found": False,
        "exhibit_stored": False,
        "guarantors_parsed": 0,
        "entities_created": 0,
        "entities_updated": 0,
        "guarantees_created": 0,
    }

    # Track which exhibit type we found for storage
    exhibit_type = None
    exhibit_content_raw = None

    # Initialize SEC client
    sec_api_key = os.getenv("SEC_API_KEY") or settings.sec_api_key
    if not sec_api_key:
        print("SEC_API_KEY not set")
        return stats

    sec_client = SecApiClient(api_key=sec_api_key)

    # Set up Gemini API key
    gemini_key = os.getenv("GEMINI_API_KEY") or settings.gemini_api_key
    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key

    # Fetch Exhibit 22.1
    print(f"Fetching Exhibit 22.1 for {ticker}...")
    exhibit_22_content = fetch_exhibit_22(ticker, sec_client)

    if exhibit_22_content:
        stats["exhibit_22_found"] = True
        exhibit_type = "exhibit_22"
        exhibit_content_raw = exhibit_22_content
        print(f"  Found Exhibit 22 ({len(exhibit_22_content)} chars)")
    else:
        print("  No Exhibit 22 found, trying Exhibit 21...")
        exhibit_21_content = fetch_exhibit_21_full(ticker, sec_client)
        if exhibit_21_content:
            stats["exhibit_21_found"] = True
            exhibit_type = "exhibit_21"
            exhibit_content_raw = exhibit_21_content
            print(f"  Found Exhibit 21 ({len(exhibit_21_content)} chars)")
            exhibit_22_content = exhibit_21_content  # Use Exhibit 21 as fallback
        else:
            print("  No subsidiary exhibits found")
            return stats

    # Parse guarantor list using LLM
    print("Parsing guarantor list...")

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "max_output_tokens": 8000,
        },
    )

    # Truncate content if needed
    content_to_parse = exhibit_22_content[:80000] if len(exhibit_22_content) > 80000 else exhibit_22_content

    prompt = PARSE_GUARANTOR_LIST_PROMPT.format(content=content_to_parse)
    response = model.generate_content(prompt)

    data = parse_json_robust(response.text)
    if not data:
        print("  Failed to parse LLM response")
        return stats

    result = GuarantorListResult(**data)
    stats["guarantors_parsed"] = len(result.guarantor_subsidiaries)

    print(f"  Parsed {len(result.guarantor_subsidiaries)} guarantor subsidiaries")
    if result.debt_description:
        print(f"  Debt: {result.debt_description[:100]}...")

    if verbose:
        for g in result.guarantor_subsidiaries[:10]:
            print(f"    - {g.name} ({g.jurisdiction or 'N/A'})")
        if len(result.guarantor_subsidiaries) > 10:
            print(f"    ... and {len(result.guarantor_subsidiaries) - 10} more")

    # Connect to database
    async with get_db_session() as db:
        # Get company
        company_result = await db.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        company = company_result.scalar_one_or_none()
        if not company:
            print(f"Company not found: {ticker}")
            return stats

        # Get existing entities
        entities_result = await db.execute(
            select(Entity).where(Entity.company_id == company.id)
        )
        existing_entities = {e.name.lower(): e for e in entities_result.scalars().all()}

        print(f"\nExisting entities: {len(existing_entities)}")

        # Get debt instruments that need guarantors (secured debt)
        debt_result = await db.execute(
            select(DebtInstrument)
            .where(DebtInstrument.company_id == company.id)
            .where(DebtInstrument.is_active == True)
            .where(DebtInstrument.seniority == 'senior_secured')
        )
        secured_debt = debt_result.scalars().all()

        print(f"Secured debt instruments: {len(secured_debt)}")

        # Get existing guarantees
        existing_guarantees = set()
        for debt in secured_debt:
            guar_result = await db.execute(
                select(Guarantee).where(Guarantee.debt_instrument_id == debt.id)
            )
            for g in guar_result.scalars().all():
                existing_guarantees.add((debt.id, g.guarantor_id))

        # Process each guarantor subsidiary
        new_entities = []
        updated_entities = []
        new_guarantees = []

        for guarantor in result.guarantor_subsidiaries:
            name_lower = guarantor.name.lower().strip()

            # Check if entity exists
            if name_lower in existing_entities:
                entity = existing_entities[name_lower]
                if not entity.is_guarantor:
                    entity.is_guarantor = True
                    updated_entities.append(entity)
                    if verbose:
                        print(f"  [UPDATE] Marked as guarantor: {entity.name}")
            else:
                # Check for partial match
                matched = False
                for existing_name, entity in existing_entities.items():
                    if name_lower in existing_name or existing_name in name_lower:
                        if not entity.is_guarantor:
                            entity.is_guarantor = True
                            updated_entities.append(entity)
                            if verbose:
                                print(f"  [UPDATE] Marked as guarantor (partial match): {entity.name}")
                        matched = True
                        break

                if not matched:
                    # Create new entity
                    # Determine entity type from name
                    entity_type = "subsidiary"
                    name_upper = guarantor.name.upper()
                    if "HOLDING" in name_upper:
                        entity_type = "holdco"
                    elif "CAPITAL" in name_upper or "FINANCE" in name_upper:
                        entity_type = "finco"
                    elif "OPERATING" in name_upper or "OPERATIONS" in name_upper:
                        entity_type = "opco"

                    new_entity = Entity(
                        company_id=company.id,
                        name=guarantor.name,
                        entity_type=entity_type,
                        jurisdiction=guarantor.jurisdiction,
                        is_guarantor=True,
                    )
                    new_entities.append(new_entity)
                    if verbose:
                        print(f"  [NEW] Entity: {guarantor.name}")

        stats["entities_created"] = len(new_entities)
        stats["entities_updated"] = len(updated_entities)

        if not dry_run:
            # Add new entities
            for entity in new_entities:
                db.add(entity)

            await db.flush()  # Get IDs for new entities

            # Create guarantees for secured debt
            all_guarantor_entities = updated_entities + new_entities
            for debt in secured_debt:
                for entity in all_guarantor_entities:
                    if (debt.id, entity.id) not in existing_guarantees:
                        guarantee = Guarantee(
                            debt_instrument_id=debt.id,
                            guarantor_id=entity.id,
                            guarantee_type="full",
                        )
                        db.add(guarantee)
                        new_guarantees.append((entity.name, debt.name))

            # Store the exhibit content as a document section
            if exhibit_content_raw and exhibit_type:
                from datetime import date as date_type
                # Check if we already have this exhibit stored
                existing_doc = await db.execute(
                    select(DocumentSection)
                    .where(DocumentSection.company_id == company.id)
                    .where(DocumentSection.section_type == exhibit_type)
                )
                if not existing_doc.scalars().first():
                    doc_section = DocumentSection(
                        company_id=company.id,
                        doc_type="10-K",
                        filing_date=date_type.today(),
                        section_type=exhibit_type,
                        section_title=f"Exhibit 22.1 - List of Guarantor Subsidiaries" if exhibit_type == "exhibit_22" else "Exhibit 21 - Subsidiaries",
                        content=exhibit_content_raw,
                        content_length=len(exhibit_content_raw),
                    )
                    db.add(doc_section)
                    stats["exhibit_stored"] = True
                    if verbose:
                        print(f"  [STORED] {exhibit_type} ({len(exhibit_content_raw)} chars)")

            await db.commit()
            stats["guarantees_created"] = len(new_guarantees)

            print(f"\nCommitted: {len(new_entities)} new entities, {len(updated_entities)} updated, {len(new_guarantees)} new guarantees")
        else:
            print(f"\nDRY RUN - Would create: {len(new_entities)} entities, update: {len(updated_entities)}")

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Fetch and process Exhibit 22.1 Guarantor Subsidiaries")
    parser.add_argument("--ticker", required=True, help="Company ticker")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    print_header(f"FETCH GUARANTOR SUBSIDIARIES - {args.ticker.upper()}")
    if args.dry_run:
        print("DRY RUN - no changes will be saved")
    print()

    stats = await fetch_and_create_guarantors(
        ticker=args.ticker,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Ticker:             {stats['ticker']}")
    print(f"Exhibit 22 found:   {stats['exhibit_22_found']}")
    print(f"Exhibit 21 found:   {stats['exhibit_21_found']}")
    print(f"Exhibit stored:     {stats['exhibit_stored']}")
    print(f"Guarantors parsed:  {stats['guarantors_parsed']}")
    print(f"Entities created:   {stats['entities_created']}")
    print(f"Entities updated:   {stats['entities_updated']}")
    print(f"Guarantees created: {stats['guarantees_created']}")


if __name__ == "__main__":
    run_async(main())

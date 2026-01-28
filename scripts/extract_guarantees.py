#!/usr/bin/env python3
"""
Extract and link guarantees for debt instruments.

This script uses LLM to analyze indentures, credit agreements, and SEC filings
to identify which entities guarantee which debt instruments, then creates
the guarantee relationships in the database.

Usage:
    python scripts/extract_guarantees.py --ticker CHTR [--dry-run]
"""

import argparse
import asyncio
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings

# Set up API keys before importing genai
_settings = get_settings()
if _settings.gemini_api_key:
    os.environ["GEMINI_API_KEY"] = _settings.gemini_api_key

import google.generativeai as genai
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import Company, Entity, DebtInstrument, Guarantee
from app.services.utils import parse_json_robust


# =============================================================================
# PROMPTS
# =============================================================================

SYSTEM_PROMPT = """You are a credit analyst extracting guarantee relationships from SEC filings and credit documents.

Your job is to identify:
1. Which entities are guarantors for each debt instrument
2. Match guarantor names to the provided list of entities
3. Determine the guarantee type (full, limited, etc.)

CRITICAL RULES:
- Only output guarantor names that EXACTLY match one of the provided entity names
- If a guarantor is mentioned but doesn't match any entity, note it in uncertainties
- Consider both indentures (for bonds/notes) and credit agreements (for loans)
- Parent guarantees are common - the ultimate parent often guarantees subsidiary debt
- Subsidiary guarantors are typically "Subsidiary Guarantors" or listed in schedules
"""

GUARANTEE_EXTRACTION_PROMPT = """Analyze the following documents to extract guarantee relationships for {ticker}'s debt instruments.

AVAILABLE ENTITIES (use these names exactly):
{entity_list}

DEBT INSTRUMENTS TO ANALYZE:
{debt_list}

DOCUMENT CONTENT:
{documents}

For each debt instrument, identify which entities guarantee it. Return JSON:

{{
  "guarantees": [
    {{
      "debt_instrument_name": "exact name from debt list above",
      "guarantor_names": ["Entity Name 1", "Entity Name 2"],
      "guarantee_type": "full|limited|conditional",
      "notes": "optional notes about the guarantee"
    }}
  ],
  "uncertainties": [
    "any guarantors mentioned that don't match entity list",
    "any debt instruments with unclear guarantee status"
  ]
}}

EXTRACTION TIPS:
1. Senior secured bank debt (Term Loans, Revolvers) is typically guaranteed by operating subsidiaries
2. Senior unsecured notes may be guaranteed by the parent company
3. Look for phrases like:
   - "guaranteed by [entity]"
   - "Subsidiary Guarantors"
   - "fully and unconditionally guaranteed"
   - "jointly and severally guaranteed"
4. Credit agreements often have "Guarantee Agreement" as a related document
5. If debt is explicitly "not guaranteed", don't include any guarantors

Return only the JSON object."""


# =============================================================================
# MODELS
# =============================================================================

class GuaranteeMapping(BaseModel):
    """A single guarantee relationship."""
    debt_instrument_name: str
    guarantor_names: list[str] = Field(default_factory=list)
    guarantee_type: Optional[str] = "full"
    notes: Optional[str] = None


class GuaranteeExtractionResult(BaseModel):
    """Result of guarantee extraction."""
    guarantees: list[GuaranteeMapping] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

async def extract_guarantees_for_company(
    ticker: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Extract and save guarantee relationships for a company.

    Returns dict with stats about what was found/created.
    """
    settings = get_settings()
    url = settings.database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    engine = create_async_engine(url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    stats = {
        "ticker": ticker,
        "entities_found": 0,
        "debt_instruments": 0,
        "documents_analyzed": 0,
        "new_guarantees_created": 0,
        "existing_guarantees": 0,
        "uncertainties": [],
    }

    async with async_session() as db:
        # Get company
        result = await db.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        company = result.scalar_one_or_none()
        if not company:
            print(f"Company not found: {ticker}")
            return stats

        # Get all entities
        result = await db.execute(
            select(Entity).where(Entity.company_id == company.id)
        )
        entities = result.scalars().all()
        entity_names = [e.name for e in entities]
        entity_by_name = {e.name.lower(): e for e in entities}
        stats["entities_found"] = len(entities)

        if verbose:
            print(f"\nFound {len(entities)} entities for {ticker}")

        # Get debt instruments
        result = await db.execute(
            select(DebtInstrument)
            .where(DebtInstrument.company_id == company.id)
            .where(DebtInstrument.is_active == True)
        )
        debt_instruments = result.scalars().all()
        debt_by_name = {d.name.lower(): d for d in debt_instruments}
        stats["debt_instruments"] = len(debt_instruments)

        if verbose:
            print(f"Found {len(debt_instruments)} debt instruments")

        # Get existing guarantees
        existing_guarantees = set()
        for debt in debt_instruments:
            result = await db.execute(
                select(Guarantee).where(Guarantee.debt_instrument_id == debt.id)
            )
            for g in result.scalars().all():
                existing_guarantees.add((debt.id, g.guarantor_id))
        stats["existing_guarantees"] = len(existing_guarantees)

        # Get documents (indentures, credit agreements, guarantor lists)
        result = await db.execute(text('''
            SELECT section_type, content
            FROM document_sections
            WHERE company_id = :company_id
            AND section_type IN ('indenture', 'credit_agreement', 'guarantor_list', 'debt_footnote')
            ORDER BY
                CASE section_type
                    WHEN 'indenture' THEN 1
                    WHEN 'credit_agreement' THEN 2
                    WHEN 'guarantor_list' THEN 3
                    ELSE 4
                END
        '''), {"company_id": str(company.id)})

        documents = []
        for row in result.fetchall():
            # Truncate each document to avoid token limits
            content = row[1][:50000] if len(row[1]) > 50000 else row[1]
            documents.append(f"=== {row[0].upper()} ===\n{content}")

        stats["documents_analyzed"] = len(documents)

        if not documents:
            print(f"No documents found for {ticker}")
            return stats

        if verbose:
            print(f"Found {len(documents)} documents to analyze")

        # Prepare entity list for prompt
        entity_list = "\n".join([f"- {name}" for name in sorted(entity_names)])

        # Prepare debt list for prompt
        debt_list = "\n".join([
            f"- {d.name} ({d.instrument_type}, {d.seniority})"
            for d in debt_instruments
        ])

        # Combine documents (limit total size)
        combined_docs = "\n\n".join(documents)
        if len(combined_docs) > 150000:
            combined_docs = combined_docs[:150000] + "\n\n[TRUNCATED]"

        # Build prompt
        prompt = GUARANTEE_EXTRACTION_PROMPT.format(
            ticker=ticker,
            entity_list=entity_list,
            debt_list=debt_list,
            documents=combined_docs,
        )

        # Call LLM
        if verbose:
            print("Calling Gemini for guarantee extraction...")

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "max_output_tokens": 8000,
            },
            system_instruction=SYSTEM_PROMPT,
        )

        response = model.generate_content(prompt)
        response_text = response.text

        # Parse response
        data = parse_json_robust(response_text)
        if not data:
            print(f"Failed to parse LLM response for {ticker}")
            if verbose:
                print(f"Response: {response_text[:500]}...")
            return stats

        result = GuaranteeExtractionResult(**data)
        stats["uncertainties"] = result.uncertainties

        if verbose:
            print(f"Extracted {len(result.guarantees)} guarantee mappings")
            if result.uncertainties:
                print(f"Uncertainties: {result.uncertainties}")

        # Process guarantee mappings
        new_guarantees_to_create = set()  # Track (debt_id, entity_id) to avoid duplicates

        for mapping in result.guarantees:
            # Find debt instrument
            debt = debt_by_name.get(mapping.debt_instrument_name.lower())
            if not debt:
                # Try partial match
                for d in debt_instruments:
                    if mapping.debt_instrument_name.lower() in d.name.lower():
                        debt = d
                        break

            if not debt:
                if verbose:
                    print(f"  [SKIP] Debt not found: {mapping.debt_instrument_name}")
                continue

            # Process each guarantor
            for guarantor_name in mapping.guarantor_names:
                entity = entity_by_name.get(guarantor_name.lower())
                if not entity:
                    # Try partial match
                    for e in entities:
                        if guarantor_name.lower() in e.name.lower() or e.name.lower() in guarantor_name.lower():
                            entity = e
                            break

                if not entity:
                    if verbose:
                        print(f"  [SKIP] Entity not found: {guarantor_name}")
                    stats["uncertainties"].append(f"Entity not found: {guarantor_name}")
                    continue

                # Check if guarantee already exists
                if (debt.id, entity.id) in existing_guarantees:
                    if verbose:
                        print(f"  [EXISTS] {entity.name} -> {debt.name[:40]}")
                    continue

                # Check if we've already queued this guarantee
                if (debt.id, entity.id) in new_guarantees_to_create:
                    continue

                new_guarantees_to_create.add((debt.id, entity.id))
                stats["new_guarantees_created"] += 1
                print(f"  [NEW] {entity.name} -> {debt.name[:40]}")

                # Mark entity as guarantor
                entity.is_guarantor = True

        # Create all new guarantees
        if not dry_run and new_guarantees_to_create:
            for debt_id, entity_id in new_guarantees_to_create:
                guarantee = Guarantee(
                    debt_instrument_id=debt_id,
                    guarantor_id=entity_id,
                    guarantee_type="full",
                )
                db.add(guarantee)

        if not dry_run and stats["new_guarantees_created"] > 0:
            await db.commit()
            print(f"\nCommitted {stats['new_guarantees_created']} new guarantees")

    await engine.dispose()
    return stats


async def main():
    parser = argparse.ArgumentParser(description="Extract guarantee relationships")
    parser.add_argument("--ticker", required=True, help="Company ticker")
    parser.add_argument("--dry-run", action="store_true", help="Don't save changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    print(f"Extracting guarantees for {args.ticker.upper()}")
    if args.dry_run:
        print("DRY RUN - no changes will be saved")
    print()

    stats = await extract_guarantees_for_company(
        ticker=args.ticker,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Ticker:              {stats['ticker']}")
    print(f"Entities found:      {stats['entities_found']}")
    print(f"Debt instruments:    {stats['debt_instruments']}")
    print(f"Documents analyzed:  {stats['documents_analyzed']}")
    print(f"Existing guarantees: {stats['existing_guarantees']}")
    print(f"New guarantees:      {stats['new_guarantees_created']}")

    if stats["uncertainties"]:
        print(f"\nUncertainties:")
        for u in stats["uncertainties"][:10]:
            print(f"  - {u}")


if __name__ == "__main__":
    asyncio.run(main())

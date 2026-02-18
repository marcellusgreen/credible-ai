#!/usr/bin/env python3
"""
Tiered extraction script - cost-optimized extraction using DeepSeek + Claude.

Usage:
    python scripts/extract_tiered.py --ticker AAPL --cik 0000320193
    python scripts/extract_tiered.py --ticker AAPL --cik 0000320193 --skip-tier1
    python scripts/extract_tiered.py --ticker AAPL --cik 0000320193 --skip-db

Environment variables:
    ANTHROPIC_API_KEY - Required for Claude (Tier 2/3)
    DEEPSEEK_API_KEY - Required for DeepSeek (Tier 1)
    SEC_API_KEY - Optional, for faster SEC filing retrieval
    DATABASE_URL - Required unless --skip-db
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base
from app.services.tiered_extraction import (
    TieredExtractionService,
    ExtractionTracker,
    ExtractionMetrics,
    Complexity,
)
from app.services.extraction import (
    ExtractionResult,
    ExtractedEntity,
    ExtractedDebtInstrument,
    save_extraction_to_db,
)


def convert_to_extraction_result(raw: dict, ticker: str) -> ExtractionResult:
    """Convert raw extraction dict to ExtractionResult for database save."""

    entities = []
    for e in raw.get('entities', []):
        # Convert owners format
        owners = e.get('owners', [])
        if not owners and e.get('parent_name'):
            # Legacy format
            owners = [{'parent_name': e['parent_name'], 'ownership_pct': e.get('ownership_pct', 100)}]
        # Clean None values in owner dicts (Pydantic rejects None for str fields with defaults)
        for o in owners:
            if o.get('ownership_type') is None:
                o['ownership_type'] = 'direct'

        entities.append(ExtractedEntity(
            name=e.get('name', ''),
            entity_type=e.get('entity_type', 'subsidiary'),
            jurisdiction=e.get('jurisdiction'),
            formation_type=e.get('formation_type'),
            owners=owners,
            consolidation_method=e.get('consolidation_method', 'full'),
            is_guarantor=e.get('is_guarantor') or False,
            is_borrower=e.get('is_borrower') or False,
            is_restricted=e.get('is_restricted') if e.get('is_restricted') is not None else True,
            is_unrestricted=e.get('is_unrestricted') or False,
            is_material=e.get('is_material') if e.get('is_material') is not None else True,
            is_domestic=e.get('is_domestic') if e.get('is_domestic') is not None else True,
            is_vie=e.get('is_vie') or False,
            vie_primary_beneficiary=e.get('vie_primary_beneficiary') or False,
        ))

    debt_instruments = []
    for d in raw.get('debt_instruments', []):
        debt_instruments.append(ExtractedDebtInstrument(
            name=d.get('name', ''),
            instrument_type=d.get('instrument_type', 'senior_notes'),
            seniority=d.get('seniority', 'senior_unsecured'),
            security_type=d.get('security_type'),
            issuer_name=d.get('issuer_name', raw.get('company_name', '')),
            commitment=d.get('commitment'),
            principal=d.get('principal'),
            outstanding=d.get('outstanding'),
            currency=d.get('currency', 'USD'),
            rate_type=d.get('rate_type'),
            interest_rate=d.get('interest_rate'),
            spread_bps=d.get('spread_bps'),
            benchmark=d.get('benchmark'),
            floor_bps=d.get('floor_bps'),
            issue_date=d.get('issue_date'),
            maturity_date=d.get('maturity_date'),
            guarantor_names=d.get('guarantor_names', []),
            attributes=d.get('attributes', {}),
        ))

    return ExtractionResult(
        company_name=raw.get('company_name', ''),
        ticker=raw.get('ticker', ticker),
        sector=raw.get('sector'),
        entities=entities,
        debt_instruments=debt_instruments,
        uncertainties=raw.get('uncertainties', []),
    )


async def run_tiered_extraction(
    database_url: str,
    anthropic_api_key: str,
    deepseek_api_key: str,
    gemini_api_key: str,
    sec_api_key: str,
    ticker: str,
    cik: str,
    skip_db: bool = False,
    skip_tier1: bool = False,
    tier1_model: str = "gemini",
):
    """Run the tiered extraction pipeline."""

    print(f"\n{'='*60}")
    print(f"TIERED EXTRACTION: {ticker} (CIK: {cik})")
    print(f"{'='*60}")

    if not deepseek_api_key and not gemini_api_key:
        print("\n[WARN] No Tier 1 API key set - will skip Tier 1 and start at Tier 2")
        skip_tier1 = True
    elif tier1_model == "gemini" and gemini_api_key:
        print(f"\n  Using Gemini 2.0 Flash for Tier 1 (cheapest option)")
    elif tier1_model == "deepseek" and deepseek_api_key:
        print(f"\n  Using DeepSeek V3 for Tier 1")

    # Initialize service
    service = TieredExtractionService(
        anthropic_api_key=anthropic_api_key,
        deepseek_api_key=deepseek_api_key,
        gemini_api_key=gemini_api_key,
        sec_api_key=sec_api_key,
        tier1_model=tier1_model,
    )

    tracker = ExtractionTracker()

    try:
        # Run extraction
        print("\nStep 1: Tiered extraction...")
        start_time = datetime.now()

        result, metrics = await service.extract_company(
            ticker=ticker,
            cik=cik,
            skip_tier1=skip_tier1,
        )

        elapsed = (datetime.now() - start_time).total_seconds()

        # Record metrics
        tracker.record(metrics)

        # Print results
        print(f"\n{'='*60}")
        print("EXTRACTION RESULTS")
        print(f"{'='*60}")
        print(f"Company: {result.get('company_name')}")
        print(f"Ticker: {result.get('ticker')}")
        print(f"Entities: {len(result.get('entities', []))}")
        print(f"Debt instruments: {len(result.get('debt_instruments', []))}")
        print(f"Uncertainties: {len(result.get('uncertainties', []))}")

        print(f"\nExtraction metadata:")
        meta = result.get('_extraction', {})
        print(f"  Attempts: {meta.get('attempts')}")
        print(f"  Models used: {meta.get('models_used')}")
        print(f"  Final model: {meta.get('final_model')}")
        print(f"  Total cost: ${meta.get('total_cost', 0):.4f}")
        print(f"  Complexity: {meta.get('complexity')}")
        print(f"  Validation score: {meta.get('validation_score', 0):.0%}")
        print(f"  Duration: {elapsed:.1f}s")

        # Print entities
        print(f"\nEntities:")
        for i, entity in enumerate(result.get('entities', [])[:10]):
            parent_info = ""
            owners = entity.get('owners', [])
            if owners:
                parent_info = f" (parent: {owners[0].get('parent_name', 'N/A')})"
            elif entity.get('entity_type') == 'holdco':
                parent_info = " (root)"
            print(f"  {i+1}. {entity.get('name')} ({entity.get('entity_type')}){parent_info}")
        if len(result.get('entities', [])) > 10:
            print(f"  ... and {len(result.get('entities', [])) - 10} more")

        # Print debt
        print(f"\nDebt instruments:")
        for i, debt in enumerate(result.get('debt_instruments', [])[:5]):
            outstanding = debt.get('outstanding') or debt.get('principal')
            amount_str = f"${outstanding/100:,.0f}" if outstanding else "N/A"
            print(f"  {i+1}. {debt.get('name')}")
            print(f"     {debt.get('seniority')}, {debt.get('security_type', 'N/A')}, {amount_str}")
        if len(result.get('debt_instruments', [])) > 5:
            print(f"  ... and {len(result.get('debt_instruments', [])) - 5} more")

        # Print uncertainties
        if result.get('uncertainties'):
            print(f"\nUncertainties:")
            for u in result.get('uncertainties', [])[:3]:
                print(f"  - {u}")

        # Save to database
        if not skip_db and database_url:
            print(f"\nStep 2: Saving to database...")

            # Convert to ExtractionResult
            extraction_result = convert_to_extraction_result(result, ticker)

            # Save
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                await save_extraction_to_db(session, extraction_result, cik)
                await session.commit()
                print(f"  [OK] Saved to database")

            await engine.dispose()

        # Print summary
        tracker.print_summary()

        print(f"\n{'='*60}")
        print("EXTRACTION COMPLETE")
        print(f"{'='*60}")

        return result, metrics

    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(
        description="Tiered extraction - cost-optimized corporate structure extraction"
    )
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g., AAPL)")
    parser.add_argument("--cik", required=True, help="SEC CIK number (e.g., 0000320193)")
    parser.add_argument("--skip-db", action="store_true", help="Skip database save")
    parser.add_argument("--skip-tier1", action="store_true", help="Skip Tier 1, start at Tier 2 (Claude)")
    parser.add_argument("--tier1", choices=["gemini", "deepseek"], default="gemini",
                       help="Tier 1 model to use (default: gemini)")
    parser.add_argument("--database-url", help="Database URL (or set DATABASE_URL env var)")

    args = parser.parse_args()

    # Load environment
    load_dotenv()

    # Get API keys
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        print("Error: ANTHROPIC_API_KEY environment variable is required")
        sys.exit(1)

    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not deepseek_api_key and not gemini_api_key:
        print("Note: No Tier 1 API key set (GEMINI_API_KEY or DEEPSEEK_API_KEY).")
        print("      Will use Claude only (more expensive).")
    elif args.tier1 == "gemini" and not gemini_api_key:
        print("Note: GEMINI_API_KEY not set, falling back to DeepSeek for Tier 1")
    elif args.tier1 == "deepseek" and not deepseek_api_key:
        print("Note: DEEPSEEK_API_KEY not set, falling back to Gemini for Tier 1")

    sec_api_key = os.getenv("SEC_API_KEY")
    if not sec_api_key:
        print("Note: SEC_API_KEY not set. Using direct SEC EDGAR (may be rate-limited).")

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url and not args.skip_db:
        print("Error: DATABASE_URL required (or use --skip-db)")
        sys.exit(1)

    # Run extraction
    asyncio.run(
        run_tiered_extraction(
            database_url=database_url or "",
            anthropic_api_key=anthropic_api_key,
            deepseek_api_key=deepseek_api_key or "",
            gemini_api_key=gemini_api_key or "",
            sec_api_key=sec_api_key or "",
            ticker=args.ticker,
            cik=args.cik,
            skip_db=args.skip_db,
            skip_tier1=args.skip_tier1,
            tier1_model=args.tier1,
        )
    )


if __name__ == "__main__":
    main()

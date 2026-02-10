#!/usr/bin/env python3
"""
Complete company extraction pipeline - IDEMPOTENT VERSION.

This is THE script for adding a new company to the database. It runs all
extraction steps in the correct order, efficiently reusing data between steps.

IDEMPOTENT: Safe to re-run for existing companies. Skips steps where:
  - Data already exists (e.g., entity_count > 20)
  - Source data is unavailable (e.g., no Exhibit 21) - tracked via extraction_status
Use --force to override skip logic.

STEPS (16 total):
  1. Download filings - never skipped
  2. Core extraction - skip if entity_count > 20 AND debt_count > 0
  3. Save to DB - uses merge logic to preserve existing data
  4. Document sections - skip if count > 5
  5. Document linking - skip if 50%+ of instruments already linked
  6. TTM financials - skip if latest_quarter is current
  7. Ownership hierarchy - skip if status='no_data' (no Exhibit 21)
  8. Guarantees - skip if guarantee_count > 0 OR status='no_data'
  9. Collateral - skip if collateral_count > 0 OR status='no_data'
  10. Metrics computation - always run
  11. Covenants - skip if covenant_count > 0
  12. Finnhub bond discovery - skip unless --full (slow: ~5 min)
  13. Link Finnhub bonds - ALWAYS runs if unlinked Finnhub bonds exist
  14. Current bond pricing - skip unless --full
  15. Historical pricing - skip unless --full
  16. Completeness check - always run

The extraction_status field in company_cache tracks step attempts:
  - "success": Step completed with data (includes metadata like latest_quarter)
  - "no_data": Step attempted but source data unavailable (won't retry)
  - "error": Step failed (will retry on next run)

Financials tracking example:
  {"financials": {"status": "success", "latest_quarter": "2025Q3", "attempted_at": "..."}}
  - If current date is 60+ days past next quarter end, will re-extract for new data

Usage:
    # Single company (steps 1-11, fast ~3-5 min)
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db

    # Single company FULL (steps 1-16, includes Finnhub/pricing ~10 min)
    python scripts/extract_iterative.py --ticker AAPL --cik 0000320193 --save-db --full

    # Force re-run all steps
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
    FINNHUB_API_KEY - Required for --full (bond discovery/pricing)
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional
from uuid import UUID

from dotenv import load_dotenv

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.iterative_extraction import IterativeExtractionService, IterativeExtractionResult
from app.services.extraction import SecApiClient, SECEdgarClient, check_existing_data, merge_extraction_to_db, save_extraction_to_db

# Import extraction services
from app.services.hierarchy_extraction import extract_ownership_hierarchy
from app.services.guarantee_extraction import extract_guarantees
from app.services.collateral_extraction import extract_collateral
from app.services.document_linking import link_documents, link_documents_heuristic
from app.services.metrics import recompute_metrics_for_company
from app.services.qc import run_qc_checks
from app.services.covenant_extraction import extract_covenants


# =============================================================================
# FILING DOWNLOAD
# =============================================================================

async def download_filings(ticker: str, cik: str, sec_api_key: str = None) -> tuple[dict[str, str], dict[str, str]]:
    """Download all relevant filings.

    Returns:
        Tuple of (filings_content, filing_urls) where:
        - filings_content: Filing content keyed by type and date
        - filing_urls: SEC filing URLs keyed by same keys
    """
    filings = {}
    filing_urls = {}

    if sec_api_key:
        print(f"  Downloading filings via SEC-API...")
        sec_client = SecApiClient(sec_api_key)
        filings, filing_urls = await sec_client.get_all_relevant_filings(ticker, cik=cik)
        exhibit_21 = sec_client.get_exhibit_21(ticker)
        if exhibit_21:
            filings['exhibit_21'] = exhibit_21
            # exhibit_21 URL is not available from get_exhibit_21, but it's okay
        print(f"    Downloaded {len(filings)} filings")
    else:
        print(f"  Downloading filings via SEC EDGAR...")
        edgar = SECEdgarClient()
        filings, filing_urls = await edgar.get_all_relevant_filings(cik)
        await edgar.close()
        print(f"    Downloaded {len(filings)} filings")

    return filings, filing_urls


# =============================================================================
# DOCUMENT LINKING (delegated to document_linking service)
# =============================================================================


async def link_documents_to_instruments(session, company_id: UUID, ticker: str, use_llm: bool = False) -> int:
    """
    Link debt instruments to their governing documents.

    Uses the document_linking service which creates DebtInstrumentDocument records
    that guarantee/collateral extraction can use.

    Args:
        session: Database session
        company_id: Company UUID
        ticker: Stock ticker
        use_llm: If True, use LLM-based matching (slower but more accurate).
                 If False, use heuristic matching (faster).
    """
    if use_llm:
        return await link_documents(session, company_id, ticker)
    else:
        return await link_documents_heuristic(session, company_id)


async def recompute_metrics(session, company_id: UUID, ticker: str) -> bool:
    """Recompute metrics for the company.

    Delegates to the metrics service which handles:
    - Balance sheet debt as primary source (more accurate than instrument sum)
    - TTM EBITDA from 4 quarters with annualization
    - Source filings provenance tracking
    - Full set of derived metrics and flags
    """
    from sqlalchemy import select
    from app.models import Company
    from app.services.metrics import recompute_metrics_for_company

    # Get company object needed by recompute_metrics_for_company
    result = await session.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        return False

    try:
        await recompute_metrics_for_company(session, company, dry_run=False)
        return True
    except Exception as e:
        print(f"    [WARN] Metrics computation failed: {e}")
        return False


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
    full: bool = False,
    finnhub_api_key: str = None,
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
        full: Run ALL steps including Finnhub discovery and pricing (slow)
        finnhub_api_key: Finnhub API key (required for --full)
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
                print(f"    - Document links: {existing_data.get('document_link_count', 0)}")
                print(f"    - Guarantees: {existing_data.get('guarantee_count', 0)}")
                print(f"    - Collateral: {existing_data.get('collateral_count', 0)}")
                print(f"    - Document sections: {existing_data.get('document_section_count', 0)}")

                # Get company name and check if financial institution
                from app.models import Company
                from sqlalchemy import select
                result = await session.execute(select(Company).where(Company.ticker == ticker.upper()))
                company = result.scalar_one_or_none()
                if company:
                    company_name = company.name
                    existing_data['is_financial_institution'] = company.is_financial_institution

        await engine.dispose()
    else:
        existing_data = {'exists': False}

    # Determine what to skip based on existing data
    skip_core = False
    skip_document_sections = False
    skip_document_linking = skip_enrichment or core_only
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

        # Skip document linking if most debt instruments are already linked
        debt_count = existing_data.get('debt_count', 0)
        document_link_count = existing_data.get('document_link_count', 0)
        if debt_count > 0 and document_link_count >= debt_count * 0.5:
            # At least 50% of instruments are linked
            skip_document_linking = True
            print(f"  [SKIP] Document linking (have {document_link_count}/{debt_count} instrument links)")
        elif step_has_no_data('document_linking'):
            skip_document_linking = True
            print(f"  [SKIP] Document linking (no linkable documents - previously attempted)")

    # Download filings (always done, needed for various steps)
    print(f"\n[1/16] Downloading SEC filings...")
    filings, filing_urls = await download_filings(ticker, cik, sec_api_key)

    if not filings:
        print("Error: No filings found")
        sys.exit(1)

    # Core extraction with QA loop
    result = None
    if not skip_core:
        print(f"\n[2/16] Running core extraction (entities + debt)...")
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
        print(f"\n[2/16] Skipping core extraction (data exists)")
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
                print(f"\n[3/16] Saving to database...")
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
            print(f"\n[3/16] Skipping save (no new core data)")

        await engine.dispose()

        # Document sections
        if not skip_document_sections and company_id:
            print(f"\n[4/16] Extracting document sections...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    sections_stored = await extract_and_store_sections(
                        db=session,
                        company_id=company_id,
                        filings_content=filings,
                        filing_urls=filing_urls,
                    )
                    print(f"    [OK] Stored {sections_stored} document sections")
                except Exception as e:
                    print(f"    [WARN] Section extraction failed: {e}")

            await engine.dispose()
        else:
            print(f"\n[4/16] Skipping document sections")

        # Document linking - link debt instruments to their source documents
        # This MUST run before guarantees/collateral so they can use linked docs
        if not skip_document_linking and company_id:
            print(f"\n[5/16] Linking documents to debt instruments...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    # Use heuristic matching (fast, no LLM needed)
                    links_count = await link_documents_to_instruments(
                        session, company_id, ticker, use_llm=False
                    )
                    # Record status
                    from app.services.extraction import update_extraction_status
                    if links_count > 0:
                        await update_extraction_status(
                            session, company_id, 'document_linking', 'success',
                            f"Created {links_count} document links"
                        )
                    else:
                        await update_extraction_status(
                            session, company_id, 'document_linking', 'no_data',
                            'No linkable documents or all instruments already linked'
                        )
                    print(f"    [OK] Created {links_count} document links")
                except Exception as e:
                    print(f"    [WARN] Document linking failed: {e}")
                    try:
                        from app.services.extraction import update_extraction_status
                        await update_extraction_status(session, company_id, 'document_linking', 'error', str(e))
                    except:
                        pass

            await engine.dispose()
        else:
            print(f"\n[5/16] Skipping document linking")

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
            print(f"\n[6/16] Extracting TTM financials (4 quarters)...")
            try:
                from app.services.financial_extraction import extract_ttm_financials, save_financials_to_db

                # Check if this is a financial institution (bank, insurance, etc.)
                is_financial_institution = existing_data.get('is_financial_institution', False)
                if is_financial_institution:
                    print(f"  [INFO] Financial institution - using bank-specific extraction")

                ttm_results = await extract_ttm_financials(
                    ticker=ticker,
                    cik=cik,
                    use_claude=False,
                    is_financial_institution=is_financial_institution,
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
            print(f"\n[6/16] Skipping TTM financials")

        # Ownership hierarchy (FULL Exhibit 21 integration)
        if not skip_hierarchy and company_id:
            print(f"\n[7/16] Extracting ownership hierarchy from Exhibit 21...")
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
            print(f"\n[7/16] Skipping ownership hierarchy")

        # Guarantees
        if not skip_guarantees and company_id:
            print(f"\n[8/16] Extracting guarantees...")
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
            print(f"\n[8/16] Skipping guarantees")

        # Collateral
        if not skip_collateral and company_id:
            print(f"\n[9/16] Extracting collateral...")
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

            await engine.dispose()
        else:
            print(f"\n[9/16] Skipping collateral")

        # Metrics computation (always run)
        if company_id:
            print(f"\n[10/16] Computing metrics...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                try:
                    await recompute_metrics(session, company_id, ticker)
                    print(f"    [OK] Metrics computed")
                except Exception as e:
                    print(f"    [WARN] Metrics computation failed: {e}")

            await engine.dispose()

        # Step 11: Covenants
        if company_id and not skip_enrichment and not core_only:
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                # Check existing covenants
                from sqlalchemy import text
                result_cov = await session.execute(
                    text("SELECT COUNT(*) FROM covenants WHERE company_id = :id"),
                    {"id": company_id}
                )
                covenant_count = result_cov.scalar()

                if covenant_count > 0 and not force:
                    print(f"\n[11/16] Skipping covenants (have {covenant_count} existing)")
                else:
                    print(f"\n[11/16] Extracting covenants...")
                    try:
                        count = await extract_covenants(session, company_id, ticker, force=force)
                        print(f"    [OK] Extracted {count} covenants")
                    except Exception as e:
                        print(f"    [WARN] Covenant extraction failed: {e}")

            await engine.dispose()

        # Step 12: Finnhub bond discovery (only with --full, slow ~5 min)
        if full and company_id:
            if not finnhub_api_key:
                print(f"\n[12/16] Skipping Finnhub discovery (FINNHUB_API_KEY not set)")
            else:
                print(f"\n[12/16] Discovering bonds from Finnhub (this takes ~5 minutes)...")
                try:
                    from scripts.expand_bond_pricing import phase4_discover_from_finnhub
                    os.environ['FINNHUB_API_KEY'] = finnhub_api_key

                    engine = create_async_engine(database_url, echo=False)
                    async_session = async_sessionmaker(engine, expire_on_commit=False)

                    await phase4_discover_from_finnhub(async_session, ticker=ticker)
                    await engine.dispose()
                    print(f"    [OK] Finnhub discovery complete")
                except Exception as e:
                    print(f"    [WARN] Finnhub discovery failed: {e}")
        else:
            print(f"\n[12/16] Skipping Finnhub discovery (use --full to enable)")

        # Step 13: Link Finnhub bonds to documents (ALWAYS runs if unlinked bonds exist)
        # This ensures bonds discovered in previous --full runs get linked
        if company_id and not skip_enrichment and not core_only:
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                # Check for unlinked Finnhub bonds
                from sqlalchemy import text as sa_text
                result = await session.execute(sa_text('''
                    SELECT COUNT(*) FROM debt_instruments di
                    WHERE di.company_id = :cid
                      AND di.instrument_type = 'bond'
                      AND di.is_active = true
                      AND di.id NOT IN (SELECT DISTINCT debt_instrument_id FROM debt_instrument_documents)
                      AND (di.attributes->>'source' = 'finnhub_discovery'
                           OR (di.cusip IS NOT NULL AND di.isin IS NOT NULL))
                '''), {'cid': company_id})
                unlinked_count = result.scalar()

                if unlinked_count > 0:
                    print(f"\n[13/16] Linking {unlinked_count} Finnhub bonds to documents...")
                    try:
                        from scripts.link_finnhub_bonds import link_finnhub_bonds_for_company
                        link_stats = await link_finnhub_bonds_for_company(session, company_id, ticker)
                        total = link_stats['total_linked']
                        print(f"    [OK] Linked {total} Finnhub bonds "
                              f"(pattern: {link_stats['pattern_matched']}, "
                              f"heuristic: {link_stats['heuristic_matched']}, "
                              f"fallback: {link_stats['fallback_linked']})")
                    except Exception as e:
                        print(f"    [WARN] Finnhub bond linking failed: {e}")
                else:
                    print(f"\n[13/16] Skipping Finnhub bond linking (no unlinked bonds)")

            await engine.dispose()
        else:
            print(f"\n[13/16] Skipping Finnhub bond linking")

        # Steps 14-15: Bond pricing (only with --full)
        if full and company_id and finnhub_api_key:
            # Step 14: Current bond pricing
            print(f"\n[14/16] Fetching current bond pricing...")
            try:
                from app.services.bond_pricing import update_company_pricing
                engine = create_async_engine(database_url, echo=False)
                async_session = async_sessionmaker(engine, expire_on_commit=False)

                async with async_session() as session:
                    results = await update_company_pricing(session, ticker)
                    print(f"    [OK] Priced {results.get('prices_found', 0)} bonds")

                await engine.dispose()
            except Exception as e:
                print(f"    [WARN] Bond pricing failed: {e}")

            # Step 15: Historical pricing backfill
            print(f"\n[15/16] Backfilling historical pricing...")
            try:
                from app.services.pricing_history import backfill_company_history
                engine = create_async_engine(database_url, echo=False)
                async_session = async_sessionmaker(engine, expire_on_commit=False)

                async with async_session() as session:
                    stats = await backfill_company_history(session, company_id, days=365)
                    print(f"    [OK] Backfilled {stats.get('prices_saved', 0)} historical prices")

                await engine.dispose()
            except Exception as e:
                print(f"    [WARN] Historical pricing backfill failed: {e}")
        else:
            print(f"\n[14-15/16] Skipping bond pricing (use --full to enable)")

        # Step 16: Completeness check
        if company_id:
            print(f"\n[16/16] Running completeness check...")
            engine = create_async_engine(database_url, echo=False)
            async_session = async_sessionmaker(engine, expire_on_commit=False)

            async with async_session() as session:
                completeness = await check_company_completeness(session, company_id, ticker)
                print(f"    - Entities: {completeness['entities']}")
                print(f"    - Debt instruments: {completeness['debt_instruments']}")
                print(f"    - Document sections: {completeness['document_sections']}")
                print(f"    - Document links: {completeness['document_links']}")
                print(f"    - Financials: {completeness['financials_quarters']} quarters")
                print(f"    - Guarantees: {completeness['guarantees']}")
                print(f"    - Collateral: {completeness['collateral']}")
                print(f"    - Metrics: {'Yes' if completeness['metrics'] else 'No'}")
                print(f"    - Covenants: {completeness['covenants']}")
                print(f"    - Bonds with pricing: {completeness['bonds_with_pricing']}")
                print(f"    - Historical pricing: {completeness['historical_pricing_records']}")

                if completeness['issues']:
                    print(f"    - Issues: {', '.join(completeness['issues'])}")
                else:
                    print(f"    - Status: COMPLETE")

            await engine.dispose()
    else:
        print(f"\n[3-16] Skipping database operations (--save-db not specified)")

    # Final summary
    print(f"\n{'='*70}")
    print(f"EXTRACTION COMPLETE: {ticker}")
    print(f"{'='*70}")
    if not skip_core:
        print(f"  QA Score: {result.final_qa_score:.0f}%")
        print(f"  Total Cost: ${result.total_cost:.4f}")
        print(f"  Duration: {result.total_duration:.1f}s")

    return result


# =============================================================================
# COMPLETENESS CHECK
# =============================================================================

async def check_company_completeness(session, company_id, ticker: str) -> dict:
    """Check data completeness for a company and return status dict."""
    from sqlalchemy import text

    checks = {}
    issues = []

    # 1. Entities
    result = await session.execute(
        text("SELECT COUNT(*) FROM entities WHERE company_id = :id"),
        {"id": company_id}
    )
    checks['entities'] = result.scalar()
    if checks['entities'] == 0:
        issues.append('no_entities')

    # 2. Debt instruments
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM debt_instruments di
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id AND di.is_active = true
        """),
        {"id": company_id}
    )
    checks['debt_instruments'] = result.scalar()

    # 3. Document sections
    result = await session.execute(
        text("SELECT COUNT(*) FROM document_sections WHERE company_id = :id"),
        {"id": company_id}
    )
    checks['document_sections'] = result.scalar()
    if checks['document_sections'] == 0:
        issues.append('no_documents')

    # 4. Document links
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM debt_instrument_documents did
            JOIN debt_instruments di ON did.debt_instrument_id = di.id
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id
        """),
        {"id": company_id}
    )
    checks['document_links'] = result.scalar()

    # 5. Financials
    result = await session.execute(
        text("SELECT COUNT(*) FROM company_financials WHERE company_id = :id"),
        {"id": company_id}
    )
    checks['financials_quarters'] = result.scalar()
    if checks['financials_quarters'] < 4:
        issues.append('incomplete_financials')

    # 6. Guarantees
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM guarantees g
            JOIN debt_instruments di ON g.debt_instrument_id = di.id
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id
        """),
        {"id": company_id}
    )
    checks['guarantees'] = result.scalar()

    # 7. Collateral
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM collateral col
            JOIN debt_instruments di ON col.debt_instrument_id = di.id
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id
        """),
        {"id": company_id}
    )
    checks['collateral'] = result.scalar()

    # 8. Metrics
    result = await session.execute(
        text("SELECT COUNT(*) FROM company_metrics WHERE company_id = :id"),
        {"id": company_id}
    )
    checks['metrics'] = result.scalar() > 0
    if not checks['metrics']:
        issues.append('no_metrics')

    # 9. Covenants
    result = await session.execute(
        text("SELECT COUNT(*) FROM covenants WHERE company_id = :id"),
        {"id": company_id}
    )
    checks['covenants'] = result.scalar()

    # 10. Bond pricing
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM bond_pricing bp
            JOIN debt_instruments di ON bp.debt_instrument_id = di.id
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id
        """),
        {"id": company_id}
    )
    checks['bonds_with_pricing'] = result.scalar()

    # 11. Historical pricing
    result = await session.execute(
        text("""
            SELECT COUNT(*) FROM bond_pricing_history bph
            JOIN debt_instruments di ON bph.debt_instrument_id = di.id
            JOIN entities e ON di.issuer_id = e.id
            WHERE e.company_id = :id
        """),
        {"id": company_id}
    )
    checks['historical_pricing_records'] = result.scalar()
    if checks['historical_pricing_records'] == 0 and checks['bonds_with_pricing'] > 0:
        issues.append('no_historical_pricing')

    checks['issues'] = issues
    return checks


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
    parser.add_argument("--full", action="store_true",
                       help="Run ALL steps including Finnhub discovery and pricing (slow, ~10 min)")

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
        finnhub_api_key = os.getenv("FINNHUB_API_KEY")
        if args.full and not finnhub_api_key:
            print("Warning: --full specified but FINNHUB_API_KEY not set. Steps 12-15 will be skipped.")

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
                full=args.full,
                finnhub_api_key=finnhub_api_key,
            )
        )


if __name__ == "__main__":
    main()

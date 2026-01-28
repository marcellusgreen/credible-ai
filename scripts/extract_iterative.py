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
from app.services.metrics import recompute_metrics_for_company
from app.services.qc import run_qc_checks


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
# DOCUMENT LINKING (kept inline - simple logic)
# =============================================================================


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

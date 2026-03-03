"""
Filing Refresh Orchestrator
============================

Takes a NewFiling and runs the appropriate extraction steps using existing
services. Each filing type (10-K, 10-Q, 8-K) triggers a different set of
steps per the refresh matrix.

USAGE
-----
    from app.services.filing_refresh import FilingRefreshService
    from app.services.filing_monitor import NewFiling

    service = FilingRefreshService()
    result = await service.refresh_for_filing(filing)
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker
from app.models import Company, CompanyCache, DebtInstrument
from app.services.filing_monitor import NewFiling

logger = structlog.get_logger()

# Ordered steps to run per filing type
STEPS_BY_FILING_TYPE: dict[str, list[str]] = {
    "10-Q": [
        "financials",
        "documents",
        "amounts",
        "metrics",
        "cache",
    ],
    "10-K": [
        "financials",
        "hierarchy",
        "guarantees",
        "collateral",
        "covenants",
        "documents",
        "amounts",
        "metrics",
        "cache",
    ],
    "8-K": [
        "documents",
        "amounts",
        "metrics",
        "cache",
    ],
}


@dataclass
class RefreshResult:
    """Tracks the outcome of a filing refresh."""
    filing: NewFiling
    steps_run: list[str] = field(default_factory=list)
    steps_failed: list[str] = field(default_factory=list)
    steps_skipped: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return len(self.steps_failed) == 0


class FilingRefreshService:
    """
    Orchestrates data refresh when new SEC filings are detected.

    Design decisions:
    - Fresh DB session per step (Neon idle connection pattern)
    - Steps continue on failure (failed hierarchy doesn't prevent metrics)
    - Filing content downloaded once, passed to steps that need it
    - Uses existing update_extraction_status() to track what ran
    """

    async def refresh_for_filing(self, filing: NewFiling) -> RefreshResult:
        """
        Run all appropriate refresh steps for a new filing.

        Parameters
        ----------
        filing : NewFiling
            The new filing to process

        Returns
        -------
        RefreshResult
            Summary of steps run, failed, and duration
        """
        result = RefreshResult(filing=filing)
        start_time = time.time()

        steps = STEPS_BY_FILING_TYPE.get(filing.form_type, [])
        if not steps:
            logger.warning(
                "filing_refresh.unknown_form_type",
                ticker=filing.ticker,
                form_type=filing.form_type,
            )
            result.error = f"Unknown form type: {filing.form_type}"
            return result

        logger.info(
            "filing_refresh.start",
            ticker=filing.ticker,
            form_type=filing.form_type,
            filing_date=str(filing.filing_date),
            steps=steps,
        )

        # Download filing content once for steps that need it
        filings_content = {}
        filing_urls = {}
        if any(s in steps for s in ["documents", "guarantees", "collateral"]):
            filings_content, filing_urls = await self._download_filings(filing)

        # Run each step, continuing on failure
        for step in steps:
            try:
                logger.info(
                    "filing_refresh.step_start",
                    ticker=filing.ticker,
                    step=step,
                )
                await self._run_step(step, filing, filings_content, filing_urls)
                result.steps_run.append(step)
                logger.info(
                    "filing_refresh.step_done",
                    ticker=filing.ticker,
                    step=step,
                )
            except Exception as e:
                result.steps_failed.append(step)
                logger.error(
                    "filing_refresh.step_failed",
                    ticker=filing.ticker,
                    step=step,
                    error=str(e),
                )
                # Update extraction status with error
                try:
                    await self._update_status(
                        filing.company_id, step, "error", str(e)
                    )
                except Exception:
                    pass

        # Update source_filing_date (conditional: only if newer)
        await self._update_source_filing_date(filing)

        result.duration_seconds = time.time() - start_time

        logger.info(
            "filing_refresh.done",
            ticker=filing.ticker,
            form_type=filing.form_type,
            steps_run=result.steps_run,
            steps_failed=result.steps_failed,
            duration=f"{result.duration_seconds:.1f}s",
        )

        return result

    async def _run_step(
        self,
        step: str,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Dispatch to the correct step handler."""
        handlers = {
            "financials": self._step_financials,
            "documents": self._step_documents,
            "amounts": self._step_amounts,
            "hierarchy": self._step_hierarchy,
            "guarantees": self._step_guarantees,
            "collateral": self._step_collateral,
            "covenants": self._step_covenants,
            "metrics": self._step_metrics,
            "cache": self._step_cache,
        }
        handler = handlers[step]
        await handler(filing, filings_content, filing_urls)

    async def _download_filings(
        self, filing: NewFiling
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Download filing content from SEC EDGAR."""
        from app.services.sec_client import SECEdgarClient

        edgar = SECEdgarClient()
        try:
            filings_content, filing_urls = await edgar.get_all_relevant_filings(
                cik=filing.cik
            )
            return filings_content, filing_urls
        finally:
            await edgar.close()

    async def _step_financials(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract TTM financial statements."""
        from app.services.financial_extraction import (
            extract_ttm_financials,
            save_financials_to_db,
        )

        financials = await extract_ttm_financials(
            ticker=filing.ticker,
            cik=filing.cik,
            use_claude=False,
            is_financial_institution=filing.is_financial_institution,
        )

        if financials:
            saved_count = 0
            for fin in financials:
                try:
                    async with async_session_maker() as session:
                        result = await save_financials_to_db(
                            session, filing.ticker, fin
                        )
                        if result:
                            saved_count += 1
                except Exception as e:
                    logger.warning(
                        "filing_refresh.financials.save_failed",
                        ticker=filing.ticker,
                        error=str(e),
                    )

            await self._update_status(
                filing.company_id,
                "financials",
                "success",
                f"Saved {saved_count} quarters",
            )
        else:
            await self._update_status(
                filing.company_id, "financials", "no_data", "No financials extracted"
            )

    async def _step_documents(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract and store document sections, then link to instruments."""
        from app.services.section_extraction import extract_and_store_sections
        from app.services.document_linking import link_documents_heuristic

        if not filings_content:
            await self._update_status(
                filing.company_id,
                "document_sections",
                "no_data",
                "No filing content downloaded",
            )
            return

        # Extract sections
        async with async_session_maker() as session:
            sections_stored = await extract_and_store_sections(
                db=session,
                company_id=filing.company_id,
                filings_content=filings_content,
                filing_urls=filing_urls,
            )

        # Link documents to instruments
        async with async_session_maker() as session:
            links_created = await link_documents_heuristic(
                session=session,
                company_id=filing.company_id,
            )

        await self._update_status(
            filing.company_id,
            "document_sections",
            "success",
            f"Stored {sections_stored} sections, created {links_created} links",
        )

    async def _step_amounts(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Backfill outstanding amounts from indentures via regex ($0 cost)."""
        # Import the amounts extraction functions
        import sys
        import os
        scripts_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts",
        )
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        from extract_amounts_from_indentures import (
            get_bullet_instruments_needing_amounts,
            get_indenture_docs,
            get_linked_doc_ids,
            run_phase_a,
        )

        async with async_session_maker() as session:
            instruments = await get_bullet_instruments_needing_amounts(
                session, filing.company_id
            )
            indenture_docs = await get_indenture_docs(session, filing.company_id)

            if not instruments or not indenture_docs:
                return

            instrument_data = [
                {
                    "id": inst.id,
                    "name": inst.name,
                    "cusip": inst.cusip,
                    "interest_rate": inst.interest_rate,
                    "maturity_date": inst.maturity_date,
                    "instrument_type": inst.instrument_type,
                    "attributes": dict(inst.attributes) if inst.attributes else {},
                }
                for inst in instruments
            ]

            doc_data = [
                {
                    "id": doc.id,
                    "filing_date": doc.filing_date,
                    "content": doc.content,
                    "content_length": doc.content_length,
                    "section_title": doc.section_title,
                }
                for doc in indenture_docs
            ]

            inst_ids = [inst.id for inst in instruments]
            raw_links = await get_linked_doc_ids(session, inst_ids)
            linked_doc_ids_by_idx = {}
            for i, inst_d in enumerate(instrument_data):
                if inst_d["id"] in raw_links:
                    linked_doc_ids_by_idx[i] = raw_links[inst_d["id"]]

            matches = run_phase_a(instrument_data, doc_data, linked_doc_ids_by_idx)

            if matches:
                for inst_idx, match_info in matches.items():
                    inst_id = instrument_data[inst_idx]["id"]
                    result = await session.execute(
                        select(DebtInstrument).where(DebtInstrument.id == inst_id)
                    )
                    db_inst = result.scalar_one_or_none()
                    if db_inst:
                        db_inst.outstanding = match_info["amount_cents"]
                        if not db_inst.principal or db_inst.principal <= 0:
                            db_inst.principal = match_info["amount_cents"]
                        attrs = dict(db_inst.attributes) if db_inst.attributes else {}
                        attrs.update(
                            {
                                "amount_source": "indenture_principal",
                                "amount_method": match_info.get("source", "regex"),
                                "amount_doc_date": match_info.get("doc_date", ""),
                                "amount_confidence": "high",
                                "amount_updated_at": datetime.now().strftime(
                                    "%Y-%m-%d"
                                ),
                            }
                        )
                        if match_info.get("tap_count", 1) > 1:
                            attrs["amount_tap_count"] = match_info["tap_count"]
                        db_inst.attributes = attrs
                await session.commit()

    async def _step_hierarchy(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract ownership hierarchy from Exhibit 21 (10-K only)."""
        from app.services.hierarchy_extraction import extract_ownership_hierarchy

        async with async_session_maker() as session:
            result = await extract_ownership_hierarchy(
                session=session,
                company_id=filing.company_id,
                ticker=filing.ticker,
                cik=filing.cik,
                company_name=filing.name,
            )

        status = "success" if result else "no_data"
        await self._update_status(
            filing.company_id, "hierarchy", status, str(result) if result else None
        )

    async def _step_guarantees(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract guarantee relationships."""
        from app.services.guarantee_extraction import extract_guarantees

        async with async_session_maker() as session:
            count = await extract_guarantees(
                session=session,
                company_id=filing.company_id,
                ticker=filing.ticker,
                filings=filings_content,
            )

        await self._update_status(
            filing.company_id,
            "guarantees",
            "success" if count > 0 else "no_data",
            f"Created {count} guarantees",
        )

    async def _step_collateral(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract collateral for secured debt."""
        from app.services.collateral_extraction import extract_collateral

        async with async_session_maker() as session:
            count = await extract_collateral(
                session=session,
                company_id=filing.company_id,
                ticker=filing.ticker,
                filings=filings_content,
            )

        await self._update_status(
            filing.company_id,
            "collateral",
            "success" if count > 0 else "no_data",
            f"Created {count} collateral records",
        )

    async def _step_covenants(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Extract structured covenant data."""
        from app.services.covenant_extraction import extract_covenants

        async with async_session_maker() as session:
            count = await extract_covenants(
                session=session,
                company_id=filing.company_id,
                ticker=filing.ticker,
                filings=filings_content,
                force=True,
            )

        await self._update_status(
            filing.company_id,
            "covenants",
            "success" if count > 0 else "no_data",
            f"Created {count} covenants",
        )

    async def _step_metrics(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Recompute credit metrics."""
        from app.services.metrics import recompute_metrics_for_company

        async with async_session_maker() as session:
            result = await session.execute(
                select(Company).where(Company.id == filing.company_id)
            )
            company = result.scalar_one_or_none()

            if company:
                metrics = await recompute_metrics_for_company(
                    db=session, company=company, dry_run=False
                )

        await self._update_status(
            filing.company_id, "metrics", "success", "Metrics recomputed"
        )

    async def _step_cache(
        self,
        filing: NewFiling,
        filings_content: dict[str, str],
        filing_urls: dict[str, str],
    ) -> None:
        """Refresh pre-computed API cache."""
        from app.services.extraction import refresh_company_cache

        async with async_session_maker() as session:
            await refresh_company_cache(
                db=session,
                company_id=filing.company_id,
                ticker=filing.ticker,
                filing_date=filing.filing_date,
            )

    async def _update_status(
        self,
        company_id: UUID,
        step: str,
        status: str,
        details: Optional[str] = None,
    ) -> None:
        """Update extraction status for a step."""
        from app.services.extraction import update_extraction_status

        async with async_session_maker() as session:
            await update_extraction_status(
                db=session,
                company_id=company_id,
                step=step,
                status=status,
                details=details,
            )

    async def _update_source_filing_date(self, filing: NewFiling) -> None:
        """Conditionally update source_filing_date (only if newer)."""
        async with async_session_maker() as session:
            await session.execute(
                text('''
                    UPDATE company_cache
                    SET source_filing_date = :filing_date
                    WHERE company_id = :company_id
                      AND (source_filing_date IS NULL OR source_filing_date < :filing_date)
                '''),
                {
                    "company_id": str(filing.company_id),
                    "filing_date": filing.filing_date,
                },
            )
            await session.commit()

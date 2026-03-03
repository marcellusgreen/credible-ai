"""
SEC Filing Monitor
==================

Detects new SEC filings by polling EDGAR's submissions JSON endpoint.
Used by the automated refresh system to trigger data updates when
companies file new 10-K, 10-Q, or 8-K reports.

USAGE
-----
    from app.services.filing_monitor import FilingMonitor, NewFiling

    monitor = FilingMonitor()
    new_filings = await monitor.check_all_companies(db)
    await monitor.close()
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from uuid import UUID

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# SEC EDGAR rate limit: 10 req/sec. We use ~8 req/sec for safety margin.
REQUEST_DELAY = 0.12

# Retry settings for 429/503 errors
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


@dataclass
class NewFiling:
    """A newly detected SEC filing that needs processing."""
    company_id: UUID
    ticker: str
    cik: str
    name: str
    form_type: str
    filing_date: date
    accession_number: str
    is_financial_institution: bool = False


class FilingMonitor:
    """
    Monitors SEC EDGAR for new filings across tracked companies.

    Polls the submissions JSON endpoint (same as SECEdgarClient) and
    compares filing dates against stored source_filing_date in company_cache.
    """

    BASE_URL = "https://data.sec.gov"
    USER_AGENT = "DebtStack.ai contact@debtstack.ai"
    FORM_TYPES = ["10-K", "10-Q", "8-K"]

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_latest_filings(
        self,
        cik: str,
        form_types: list[str] = None,
        lookback_days: int = 90,
    ) -> list[dict]:
        """
        Poll EDGAR for recent filings of specified types.

        Parameters
        ----------
        cik : str
            Company CIK number
        form_types : list[str]
            Form types to look for (default: 10-K, 10-Q, 8-K)
        lookback_days : int
            Only include filings from last N days

        Returns
        -------
        list[dict]
            Filing metadata dicts with form_type, filing_date, accession_number
        """
        if form_types is None:
            form_types = self.FORM_TYPES

        cik_padded = cik.zfill(10)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        cutoff_date = datetime.now().date() - timedelta(days=lookback_days)

        # Retry with exponential backoff on 429/503
        for attempt in range(MAX_RETRIES):
            try:
                response = await self.client.get(url)

                if response.status_code in (429, 503):
                    backoff = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning(
                        "filing_monitor.rate_limited",
                        cik=cik,
                        status=response.status_code,
                        retry_in=backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError:
                if attempt < MAX_RETRIES - 1:
                    backoff = INITIAL_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue
                raise
        else:
            return []

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])

        filings = []
        for i, form in enumerate(forms):
            if form not in form_types:
                continue

            try:
                fdate = datetime.strptime(filing_dates[i], "%Y-%m-%d").date()
            except (ValueError, IndexError):
                continue

            if fdate < cutoff_date:
                continue

            filings.append({
                "form_type": form,
                "filing_date": fdate,
                "accession_number": accession_numbers[i],
            })

        return filings

    def check_company(
        self,
        company_id: UUID,
        ticker: str,
        cik: str,
        name: str,
        is_financial_institution: bool,
        last_filing_date: Optional[date],
        filings: list[dict],
    ) -> list[NewFiling]:
        """
        Compare EDGAR filings against stored source_filing_date.

        A filing is "new" if filing_date > source_filing_date (or if
        source_filing_date is NULL, meaning never processed).

        Parameters
        ----------
        company_id : UUID
            Company database ID
        ticker : str
            Stock ticker
        cik : str
            SEC CIK number
        name : str
            Company name
        is_financial_institution : bool
            Whether company is a bank/financial
        last_filing_date : date or None
            Current source_filing_date from company_cache
        filings : list[dict]
            Recent filings from get_latest_filings()

        Returns
        -------
        list[NewFiling]
            New filings that need processing
        """
        new_filings = []

        for filing in filings:
            fdate = filing["filing_date"]

            # New if no previous filing date recorded, or if this filing is newer
            if last_filing_date is None or fdate > last_filing_date:
                new_filings.append(NewFiling(
                    company_id=company_id,
                    ticker=ticker,
                    cik=cik,
                    name=name,
                    form_type=filing["form_type"],
                    filing_date=fdate,
                    accession_number=filing["accession_number"],
                    is_financial_institution=is_financial_institution,
                ))

        return new_filings

    async def check_all_companies(
        self,
        db: AsyncSession,
        ticker_filter: Optional[str] = None,
    ) -> list[NewFiling]:
        """
        Check all tracked companies for new filings.

        Loads companies from DB, polls EDGAR for each, compares against
        stored source_filing_date.

        Parameters
        ----------
        db : AsyncSession
            Database session
        ticker_filter : str, optional
            Only check this specific ticker

        Returns
        -------
        list[NewFiling]
            All new filings detected across all companies
        """
        # Load companies with their last known filing date
        if ticker_filter:
            query = text('''
                SELECT c.id, c.ticker, c.cik, c.name, c.is_financial_institution,
                       cc.source_filing_date
                FROM companies c
                LEFT JOIN company_cache cc ON c.id = cc.company_id
                WHERE c.cik IS NOT NULL AND c.ticker = :ticker
                ORDER BY c.ticker
            ''')
            result = await db.execute(query, {"ticker": ticker_filter.upper()})
        else:
            query = text('''
                SELECT c.id, c.ticker, c.cik, c.name, c.is_financial_institution,
                       cc.source_filing_date
                FROM companies c
                LEFT JOIN company_cache cc ON c.id = cc.company_id
                WHERE c.cik IS NOT NULL
                ORDER BY c.ticker
            ''')
            result = await db.execute(query)

        companies = result.fetchall()
        all_new_filings = []

        logger.info(
            "filing_monitor.scan_start",
            company_count=len(companies),
            ticker_filter=ticker_filter,
        )

        for company in companies:
            company_id, ticker, cik, name, is_financial, last_filing_date = company

            try:
                filings = await self.get_latest_filings(cik)
                new = self.check_company(
                    company_id=company_id,
                    ticker=ticker,
                    cik=cik,
                    name=name,
                    is_financial_institution=is_financial,
                    last_filing_date=last_filing_date,
                    filings=filings,
                )

                if new:
                    logger.info(
                        "filing_monitor.new_filings",
                        ticker=ticker,
                        count=len(new),
                        types=[f.form_type for f in new],
                    )
                    all_new_filings.extend(new)

                # Rate limiting between companies
                await asyncio.sleep(REQUEST_DELAY)

            except Exception as e:
                logger.warning(
                    "filing_monitor.check_failed",
                    ticker=ticker,
                    cik=cik,
                    error=str(e),
                )
                continue

        logger.info(
            "filing_monitor.scan_done",
            companies_checked=len(companies),
            new_filings_found=len(all_new_filings),
        )

        return all_new_filings

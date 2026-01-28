"""
SEC Filing Clients
==================

Clients for fetching SEC filings from EDGAR and SEC-API.io.

CONTENTS
--------
- SecApiClient: Fast commercial API (SEC-API.io)
- SECEdgarClient: Direct SEC EDGAR access (rate-limited)
- FilingInfo: Pydantic model for filing metadata

USAGE
-----
    from app.services.sec_client import SecApiClient, SECEdgarClient

    # SEC-API.io (faster, no rate limits)
    client = SecApiClient(api_key="...")
    filings = await client.get_all_relevant_filings("AAPL")

    # Direct EDGAR (free, rate-limited)
    edgar = SECEdgarClient()
    filings = await edgar.get_all_relevant_filings(cik="0000320193")
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel

from app.services.utils import clean_filing_html


class FilingInfo(BaseModel):
    """Metadata for a single SEC filing."""
    form_type: str
    filing_date: str
    accession_number: str
    primary_document: str
    description: str = ""


class SecApiClient:
    """
    Client for SEC-API.io - faster alternative to direct SEC EDGAR access.

    USAGE
    -----
        client = SecApiClient(api_key="your-key")
        filings = await client.get_all_relevant_filings("AAPL")

    Get your free API key at: https://sec-api.io/
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.query_api = None
        self.render_api = None
        self._init_apis()

    def _init_apis(self):
        """Initialize SEC-API clients."""
        try:
            from sec_api import QueryApi, RenderApi
            self.query_api = QueryApi(api_key=self.api_key)
            self.render_api = RenderApi(api_key=self.api_key)
        except ImportError:
            print("  [WARN] sec-api package not installed. Run: pip install sec-api")

    def get_filings_by_ticker(
        self,
        ticker: str,
        form_types: list[str] = None,
        max_filings: int = 15,
        cik: str = None
    ) -> list[dict]:
        """
        Get recent filings for a company by ticker or CIK.

        PARAMETERS
        ----------
        ticker : str
            Stock ticker symbol
        form_types : list[str]
            Form types to fetch (default: 10-K, 10-Q, 8-K)
        max_filings : int
            Maximum filings to return
        cik : str
            Fallback CIK if ticker search fails

        RETURNS
        -------
        list[dict]
            Filing metadata with URLs
        """
        if not self.query_api:
            return []

        if form_types is None:
            form_types = ["10-K", "10-Q", "8-K"]

        form_query = " OR ".join([f'formType:"{ft}"' for ft in form_types])

        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:{ticker} AND ({form_query})'
                }
            },
            "from": "0",
            "size": str(max_filings),
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        try:
            response = self.query_api.get_filings(query)
            filings = response.get("filings", [])

            if not filings and cik:
                cik_num = cik.lstrip("0")
                query["query"]["query_string"]["query"] = f'cik:{cik_num} AND ({form_query})'
                response = self.query_api.get_filings(query)
                filings = response.get("filings", [])

            return filings
        except Exception as e:
            print(f"  [FAIL] SEC-API query failed: {e}")
            return []

    def get_filing_content(self, filing_url: str) -> str:
        """Download filing content as text."""
        if not self.render_api:
            return ""

        try:
            content = self.render_api.get_filing(filing_url)
            if content and (content.strip().startswith('<') or content.strip().startswith('<?xml')):
                content = clean_filing_html(content)
            return content
        except Exception as e:
            print(f"  [FAIL] SEC-API render failed: {e}")
            return ""

    def get_exhibit_21(self, ticker: str) -> str:
        """
        Fetch Exhibit 21 (subsidiaries list) from latest 10-K.

        PARAMETERS
        ----------
        ticker : str
            Stock ticker

        RETURNS
        -------
        str
            Exhibit 21 content or empty string
        """
        if not self.query_api:
            return ""

        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:{ticker} AND formType:"10-K" AND documentFormatFiles.type:"EX-21"'
                }
            },
            "from": "0",
            "size": "1",
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        try:
            response = self.query_api.get_filings(query)
            filings = response.get("filings", [])

            if not filings:
                return ""

            for doc in filings[0].get("documentFormatFiles", []):
                doc_type = doc.get("type", "").upper()
                if "21" in doc_type or "SUBSIDIARIES" in doc.get("description", "").upper():
                    exhibit_url = doc.get("documentUrl", "")
                    if exhibit_url:
                        return self.get_filing_content(exhibit_url)

            return ""
        except Exception as e:
            print(f"  [FAIL] SEC-API Exhibit 21 fetch failed: {e}")
            return ""

    def get_historical_indentures(
        self,
        ticker: str,
        cik: str = None,
        max_filings: int = 100
    ) -> list[dict]:
        """
        Fetch historical filings containing EX-4 exhibits (indentures).

        PARAMETERS
        ----------
        ticker : str
            Stock ticker
        cik : str
            Fallback CIK
        max_filings : int
            Maximum filings to return

        RETURNS
        -------
        list[dict]
            Filing metadata
        """
        if not self.query_api:
            return []

        ex4_types = ' OR '.join([f'documentFormatFiles.type:"EX-4.{i}"' for i in range(1, 11)])
        query = {
            "query": {
                "query_string": {
                    "query": f'ticker:{ticker} AND ({ex4_types})'
                }
            },
            "from": "0",
            "size": str(max_filings),
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        try:
            response = self.query_api.get_filings(query)
            filings = response.get("filings", [])

            if not filings and cik:
                cik_num = cik.lstrip("0")
                query["query"]["query_string"]["query"] = f'cik:{cik_num} AND ({ex4_types})'
                response = self.query_api.get_filings(query)
                filings = response.get("filings", [])

            if filings:
                print(f"    Found {len(filings)} historical filings with EX-4 exhibits")

            return filings
        except Exception as e:
            print(f"  [FAIL] SEC-API indenture query failed: {e}")
            return []

    async def get_all_relevant_filings(
        self,
        ticker: str,
        include_exhibits: bool = True,
        cik: str = None
    ) -> dict[str, str]:
        """
        Get all relevant filings for comprehensive extraction.

        PARAMETERS
        ----------
        ticker : str
            Stock ticker
        include_exhibits : bool
            Whether to download exhibits (default: True)
        cik : str
            Fallback CIK

        RETURNS
        -------
        dict[str, str]
            Filing content keyed by type and date
        """
        filings_content = {}

        ten_k_filings = self.get_filings_by_ticker(ticker, form_types=["10-K"], max_filings=1, cik=cik)
        other_filings = self.get_filings_by_ticker(ticker, form_types=["10-Q", "8-K"], max_filings=30, cik=cik)
        indenture_filings = self.get_historical_indentures(ticker, cik=cik, max_filings=100)

        # Deduplicate
        seen = set()
        filings = []
        for f in ten_k_filings + other_filings + indenture_filings:
            acc = f.get("accessionNo", f.get("filedAt"))
            if acc not in seen:
                seen.add(acc)
                filings.append(f)

        print(f"  Found {len(filings)} filings via SEC-API")

        download_tasks = []

        for filing in filings:
            form_type = filing.get("formType", "")
            filed_at = filing.get("filedAt", "")[:10]
            key = f"{form_type}_{filed_at}"

            filing_url = filing.get("linkToFilingDetails", "")
            if filing_url:
                download_tasks.append((key, filing_url, False))

            if include_exhibits:
                for doc in filing.get("documentFormatFiles", []):
                    doc_type = doc.get("type", "").upper()
                    description = doc.get("description", "").upper()
                    exhibit_url = doc.get("documentUrl", "")

                    if not exhibit_url:
                        continue

                    if form_type == "10-K" and "21" in doc_type:
                        download_tasks.append((f"exhibit_21_{filed_at}", exhibit_url, True))
                    elif "EX-10" in doc_type or (doc_type.startswith("10") and "." in doc_type):
                        exclude = ["EMPLOYMENT", "COMPENSATION", "BONUS", "INCENTIVE",
                                   "SEPARATION", "SEVERANCE", "LEASE", "SUBLEASE",
                                   "CONSULTING", "SERVICES AGREEMENT", "LICENSE"]
                        if not any(kw in description for kw in exclude):
                            download_tasks.append((f"credit_agreement_{filed_at}_{doc_type.replace('.', '_')}", exhibit_url, True))
                    elif "EX-4" in doc_type or (doc_type.startswith("4") and "." in doc_type):
                        exclude = ["FORM OF CERTIFICATE", "SPECIMEN", "RIGHTS AGREEMENT"]
                        if not any(kw in description for kw in exclude):
                            download_tasks.append((f"indenture_{filed_at}_{doc_type.replace('.', '_')}", exhibit_url, True))

        async def download_file(key: str, url: str, is_exhibit: bool):
            try:
                loop = asyncio.get_event_loop()
                content = await loop.run_in_executor(None, self.get_filing_content, url)
                if content:
                    return (key, content, is_exhibit)
            except Exception:
                pass
            return None

        semaphore = asyncio.Semaphore(5)

        async def bounded_download(key, url, is_exhibit):
            async with semaphore:
                return await download_file(key, url, is_exhibit)

        results = await asyncio.gather(
            *[bounded_download(key, url, is_ex) for key, url, is_ex in download_tasks],
            return_exceptions=True
        )

        for result in results:
            if result and not isinstance(result, Exception):
                key, content, is_exhibit = result
                filings_content[key] = content
                if is_exhibit:
                    print(f"      [OK] Downloaded {key}")
                else:
                    print(f"    [OK] Downloaded {key}")

        return filings_content


class SECEdgarClient:
    """
    Client for fetching filings directly from SEC EDGAR.

    USAGE
    -----
        edgar = SECEdgarClient()
        filings = await edgar.get_all_relevant_filings(cik="0000320193")
        await edgar.close()

    Note: SEC EDGAR has rate limits (10 requests/second).
    """

    BASE_URL = "https://data.sec.gov"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
    USER_AGENT = "DebtStack.ai contact@debtstack.ai"

    def __init__(self):
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.USER_AGENT},
            timeout=60.0,
            follow_redirects=True,
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def get_company_filings(self, cik: str) -> dict:
        """Get list of filings for a company by CIK."""
        cik_padded = cik.zfill(10)
        url = f"{self.BASE_URL}/submissions/CIK{cik_padded}.json"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_recent_filings(
        self,
        cik: str,
        form_types: list[str] = None,
        max_filings: int = 20,
        lookback_days: int = 365
    ) -> list[FilingInfo]:
        """
        Get recent filings of specified types.

        PARAMETERS
        ----------
        cik : str
            Company CIK number
        form_types : list[str]
            Form types to include
        max_filings : int
            Maximum filings to return
        lookback_days : int
            Only include filings from last N days

        RETURNS
        -------
        list[FilingInfo]
            Filing metadata
        """
        if form_types is None:
            form_types = ["10-K", "10-Q", "8-K"]

        filings_data = await self.get_company_filings(cik)
        recent = filings_data["filings"]["recent"]
        cutoff_date = datetime.now().date() - timedelta(days=lookback_days)

        filings = []
        for i, form in enumerate(recent["form"]):
            if form in form_types:
                filing_date = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").date()

                if filing_date >= cutoff_date:
                    filings.append(FilingInfo(
                        form_type=form,
                        filing_date=recent["filingDate"][i],
                        accession_number=recent["accessionNumber"][i],
                        primary_document=recent["primaryDocument"][i],
                        description=recent.get("primaryDocDescription", [""])[i] if "primaryDocDescription" in recent else "",
                    ))

                if len(filings) >= max_filings:
                    break

        return filings

    async def download_filing(self, cik: str, filing: FilingInfo) -> str:
        """Download a single filing's content."""
        accession_no_dashes = filing.accession_number.replace("-", "")
        doc_url = f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/{filing.primary_document}"

        response = await self.client.get(doc_url)
        response.raise_for_status()
        return response.text

    async def get_filing_exhibits(self, cik: str, filing: FilingInfo) -> list[dict]:
        """Get list of exhibits for a filing."""
        accession_no_dashes = filing.accession_number.replace("-", "")
        index_url = f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/index.json"

        try:
            response = await self.client.get(index_url)
            response.raise_for_status()
            data = response.json()

            exhibits = []
            for item in data.get("directory", {}).get("item", []):
                name = item.get("name", "")
                if any(x in name.lower() for x in ["ex10", "ex21", "ex99", "exhibit"]):
                    exhibits.append({
                        "name": name,
                        "url": f"{self.ARCHIVES_URL}/{cik}/{accession_no_dashes}/{name}",
                        "type": item.get("type", ""),
                    })
            return exhibits
        except Exception:
            return []

    async def download_exhibit(self, url: str) -> str:
        """Download an exhibit by URL."""
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    async def get_latest_10k(self, cik: str) -> tuple[str, str, str]:
        """
        Get the latest 10-K filing content.

        RETURNS
        -------
        tuple
            (filing_content, accession_number, filing_date)
        """
        filings = await self.get_recent_filings(cik, form_types=["10-K"], max_filings=1, lookback_days=400)

        if not filings:
            raise ValueError(f"No 10-K filing found for CIK {cik}")

        filing = filings[0]
        content = await self.download_filing(cik, filing)

        return content, filing.accession_number, filing.filing_date

    async def get_all_relevant_filings(
        self,
        cik: str,
        include_exhibits: bool = True
    ) -> dict[str, str]:
        """
        Get all relevant filings for comprehensive extraction.

        PARAMETERS
        ----------
        cik : str
            Company CIK
        include_exhibits : bool
            Whether to download exhibits

        RETURNS
        -------
        dict[str, str]
            Filing content keyed by type and date
        """
        filings_content = {}

        filings = await self.get_recent_filings(
            cik,
            form_types=["10-K", "10-Q", "8-K"],
            max_filings=15,
            lookback_days=400
        )

        print(f"  Found {len(filings)} relevant filings")

        for filing in filings:
            key = f"{filing.form_type}_{filing.filing_date}"
            try:
                await asyncio.sleep(0.15)  # Rate limiting
                content = await self.download_filing(cik, filing)
                filings_content[key] = content
                print(f"    [OK] Downloaded {key}")

                if include_exhibits and filing.form_type == "8-K":
                    exhibits = await self.get_filing_exhibits(cik, filing)
                    for exhibit in exhibits[:3]:
                        try:
                            await asyncio.sleep(0.15)
                            ex_content = await self.download_exhibit(exhibit["url"])
                            ex_key = f"exhibit_{filing.filing_date}_{exhibit['name']}"
                            filings_content[ex_key] = ex_content
                            print(f"      [OK] Downloaded exhibit: {exhibit['name']}")
                        except Exception:
                            print(f"      [FAIL] Failed to download exhibit: {exhibit['name']}")

            except Exception as e:
                print(f"    [FAIL] Failed to download {key}: {e}")

        return filings_content

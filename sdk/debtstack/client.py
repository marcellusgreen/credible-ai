"""
DebtStack.ai Python SDK

Official Python SDK for DebtStack.ai's credit data API.
Provides access to all 8 primitives optimized for AI agents.

Usage:
    from debtstack import DebtStackClient

    async with DebtStackClient(api_key="your_api_key") as client:
        companies = await client.search_companies(ticker="AAPL,MSFT")
        print(companies)
"""

import httpx
from typing import Optional, Dict, Any, List, Union
from datetime import date


class DebtStackClient:
    """
    Async client for DebtStack.ai API.

    Provides access to all 8 primitives:
    1. search_companies - Horizontal company search with filtering
    2. search_bonds - Horizontal bond search with yield/spread filters
    3. resolve_bond - Bond identifier resolution (CUSIP, ISIN, fuzzy)
    4. traverse_entities - Graph traversal for guarantors, structure
    5. search_pricing - Bond pricing data from FINRA TRACE
    6. search_documents - Full-text search across SEC filings
    7. batch - Execute multiple operations in parallel
    8. get_changes - Diff/changelog since a date
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.debtstack.ai/v1",
        timeout: int = 30
    ):
        """
        Initialize DebtStack client.

        Args:
            api_key: Your DebtStack API key (get one at debtstack.ai/signup)
            base_url: API base URL (default: https://api.debtstack.ai/v1)
            timeout: Request timeout in seconds (default: 30)
        """
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "debtstack-python/0.1.0"
                }
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # =========================================================================
    # Primitive 1: search.companies
    # =========================================================================

    async def search_companies(
        self,
        ticker: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        rating_bucket: Optional[str] = None,
        min_leverage: Optional[float] = None,
        max_leverage: Optional[float] = None,
        min_net_leverage: Optional[float] = None,
        max_net_leverage: Optional[float] = None,
        min_debt: Optional[int] = None,
        max_debt: Optional[int] = None,
        has_structural_sub: Optional[bool] = None,
        has_floating_rate: Optional[bool] = None,
        has_near_term_maturity: Optional[bool] = None,
        has_holdco_debt: Optional[bool] = None,
        has_opco_debt: Optional[bool] = None,
        fields: Optional[str] = None,
        sort: str = "ticker",
        limit: int = 50,
        offset: int = 0,
        include_metadata: bool = False,
    ) -> Dict[str, Any]:
        """
        Search companies with powerful filtering and field selection.

        Args:
            ticker: Comma-separated tickers (e.g., "AAPL,MSFT,GOOGL")
            sector: Filter by sector (e.g., "Technology")
            industry: Filter by industry (e.g., "Software")
            rating_bucket: Rating bucket: IG, HY-BB, HY-B, HY-CCC, NR
            min_leverage: Minimum leverage ratio
            max_leverage: Maximum leverage ratio
            min_net_leverage: Minimum net leverage ratio
            max_net_leverage: Maximum net leverage ratio
            min_debt: Minimum total debt (in cents)
            max_debt: Maximum total debt (in cents)
            has_structural_sub: Filter for structural subordination
            has_floating_rate: Filter for floating rate debt
            has_near_term_maturity: Filter for debt maturing within 24 months
            has_holdco_debt: Filter for holdco-level debt
            has_opco_debt: Filter for opco-level debt
            fields: Comma-separated fields to return
            sort: Sort field, prefix with - for descending (e.g., "-net_leverage_ratio")
            limit: Results per page (max 100)
            offset: Pagination offset
            include_metadata: Include extraction quality metadata

        Returns:
            Dictionary with "data" (list of companies) and "meta" (pagination info)

        Example:
            # Find MAG7 company with highest leverage
            result = await client.search_companies(
                ticker="AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
                fields="ticker,name,net_leverage_ratio",
                sort="-net_leverage_ratio",
                limit=1
            )
        """
        params = {
            "sort": sort,
            "limit": limit,
            "offset": offset,
            "include_metadata": include_metadata,
        }

        # Add optional filters
        if ticker:
            params["ticker"] = ticker
        if sector:
            params["sector"] = sector
        if industry:
            params["industry"] = industry
        if rating_bucket:
            params["rating_bucket"] = rating_bucket
        if min_leverage is not None:
            params["min_leverage"] = min_leverage
        if max_leverage is not None:
            params["max_leverage"] = max_leverage
        if min_net_leverage is not None:
            params["min_net_leverage"] = min_net_leverage
        if max_net_leverage is not None:
            params["max_net_leverage"] = max_net_leverage
        if min_debt is not None:
            params["min_debt"] = min_debt
        if max_debt is not None:
            params["max_debt"] = max_debt
        if has_structural_sub is not None:
            params["has_structural_sub"] = has_structural_sub
        if has_floating_rate is not None:
            params["has_floating_rate"] = has_floating_rate
        if has_near_term_maturity is not None:
            params["has_near_term_maturity"] = has_near_term_maturity
        if has_holdco_debt is not None:
            params["has_holdco_debt"] = has_holdco_debt
        if has_opco_debt is not None:
            params["has_opco_debt"] = has_opco_debt
        if fields:
            params["fields"] = fields

        client = await self._get_client()
        response = await client.get("/companies", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 2: search.bonds
    # =========================================================================

    async def search_bonds(
        self,
        ticker: Optional[str] = None,
        cusip: Optional[str] = None,
        sector: Optional[str] = None,
        seniority: Optional[str] = None,
        security_type: Optional[str] = None,
        instrument_type: Optional[str] = None,
        issuer_type: Optional[str] = None,
        rate_type: Optional[str] = None,
        min_coupon: Optional[float] = None,
        max_coupon: Optional[float] = None,
        min_ytm: Optional[float] = None,
        max_ytm: Optional[float] = None,
        min_spread: Optional[int] = None,
        max_spread: Optional[int] = None,
        maturity_before: Optional[Union[str, date]] = None,
        maturity_after: Optional[Union[str, date]] = None,
        min_outstanding: Optional[int] = None,
        has_pricing: Optional[bool] = None,
        has_guarantors: Optional[bool] = None,
        has_cusip: Optional[bool] = None,
        currency: Optional[str] = None,
        fields: Optional[str] = None,
        sort: str = "name",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search bonds with powerful filtering for yield hunting and screening.

        Args:
            ticker: Filter by company ticker(s)
            cusip: Filter by CUSIP(s)
            sector: Filter by company sector
            seniority: senior_secured, senior_unsecured, subordinated
            security_type: first_lien, second_lien, unsecured
            instrument_type: term_loan_b, senior_notes, revolver, etc.
            issuer_type: holdco, opco, subsidiary
            rate_type: fixed, floating
            min_coupon: Minimum coupon rate (%)
            max_coupon: Maximum coupon rate (%)
            min_ytm: Minimum yield to maturity (%)
            max_ytm: Maximum yield to maturity (%)
            min_spread: Minimum spread to treasury (bps)
            max_spread: Maximum spread to treasury (bps)
            maturity_before: Maturity before date
            maturity_after: Maturity after date
            min_outstanding: Minimum outstanding (cents)
            has_pricing: Has pricing data
            has_guarantors: Has guarantor entities
            has_cusip: Has CUSIP (tradeable)
            currency: Currency code (e.g., "USD")
            fields: Comma-separated fields to return
            sort: Sort field, prefix with - for descending
            limit: Results per page (max 100)
            offset: Pagination offset

        Returns:
            Dictionary with "data" (list of bonds) and "meta" (pagination info)

        Example:
            # Find high-yield bonds yielding >8%
            result = await client.search_bonds(
                seniority="senior_unsecured",
                min_ytm=8.0,
                has_pricing=True,
                fields="name,cusip,company_ticker,coupon_rate,maturity_date,pricing",
                sort="-pricing.ytm"
            )
        """
        params = {
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }

        if ticker:
            params["ticker"] = ticker
        if cusip:
            params["cusip"] = cusip
        if sector:
            params["sector"] = sector
        if seniority:
            params["seniority"] = seniority
        if security_type:
            params["security_type"] = security_type
        if instrument_type:
            params["instrument_type"] = instrument_type
        if issuer_type:
            params["issuer_type"] = issuer_type
        if rate_type:
            params["rate_type"] = rate_type
        if min_coupon is not None:
            params["min_coupon"] = min_coupon
        if max_coupon is not None:
            params["max_coupon"] = max_coupon
        if min_ytm is not None:
            params["min_ytm"] = min_ytm
        if max_ytm is not None:
            params["max_ytm"] = max_ytm
        if min_spread is not None:
            params["min_spread"] = min_spread
        if max_spread is not None:
            params["max_spread"] = max_spread
        if maturity_before:
            params["maturity_before"] = str(maturity_before)
        if maturity_after:
            params["maturity_after"] = str(maturity_after)
        if min_outstanding is not None:
            params["min_outstanding"] = min_outstanding
        if has_pricing is not None:
            params["has_pricing"] = has_pricing
        if has_guarantors is not None:
            params["has_guarantors"] = has_guarantors
        if has_cusip is not None:
            params["has_cusip"] = has_cusip
        if currency:
            params["currency"] = currency
        if fields:
            params["fields"] = fields

        client = await self._get_client()
        response = await client.get("/bonds", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 3: resolve.bond
    # =========================================================================

    async def resolve_bond(
        self,
        q: Optional[str] = None,
        cusip: Optional[str] = None,
        isin: Optional[str] = None,
        ticker: Optional[str] = None,
        coupon: Optional[float] = None,
        maturity_year: Optional[int] = None,
        match_mode: str = "fuzzy",
        limit: int = 5,
    ) -> Dict[str, Any]:
        """
        Resolve bond identifiers - map descriptions to CUSIPs, ISINs, etc.

        Args:
            q: Free-text search (e.g., "RIG 8% 2027")
            cusip: Exact CUSIP lookup
            isin: Exact ISIN lookup
            ticker: Company ticker
            coupon: Coupon rate (%)
            maturity_year: Maturity year
            match_mode: "exact" or "fuzzy"
            limit: Max matches to return

        Returns:
            Dictionary with matches and confidence scores

        Example:
            # Resolve a bond description
            result = await client.resolve_bond(q="RIG 8% 2027")
            print(result["data"]["matches"][0]["bond"]["cusip"])
        """
        params = {
            "match_mode": match_mode,
            "limit": limit,
        }

        if q:
            params["q"] = q
        if cusip:
            params["cusip"] = cusip
        if isin:
            params["isin"] = isin
        if ticker:
            params["ticker"] = ticker
        if coupon is not None:
            params["coupon"] = coupon
        if maturity_year is not None:
            params["maturity_year"] = maturity_year

        client = await self._get_client()
        response = await client.get("/bonds/resolve", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 4: traverse.entities
    # =========================================================================

    async def traverse_entities(
        self,
        start_type: str,
        start_id: str,
        relationships: List[str],
        direction: str = "outbound",
        depth: int = 3,
        filters: Optional[Dict[str, Any]] = None,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Traverse entity relationships - guarantor chains, corporate structure.

        Args:
            start_type: "company", "bond", or "entity"
            start_id: Ticker, CUSIP, or entity UUID
            relationships: List of relationship types:
                - "guarantees": Entities that guarantee a bond
                - "subsidiaries": Child entities owned by parent
                - "parents": Parent entities (ownership chain)
                - "debt": Debt instruments issued at entity
                - "borrowers": Entities that are borrowers on debt
            direction: "outbound", "inbound", or "both"
            depth: Max traversal depth (default 3)
            filters: Optional filters (entity_type, is_guarantor, jurisdiction)
            fields: Fields to return for each entity

        Returns:
            Dictionary with start node and traversed entities

        Example:
            # Get all guarantors of a bond
            result = await client.traverse_entities(
                start_type="bond",
                start_id="893830AK8",
                relationships=["guarantees"],
                direction="inbound",
                fields=["name", "entity_type", "jurisdiction"]
            )
        """
        body = {
            "start": {
                "type": start_type,
                "id": start_id
            },
            "relationships": relationships,
            "direction": direction,
            "depth": depth,
        }

        if filters:
            body["filters"] = filters
        if fields:
            body["fields"] = fields

        client = await self._get_client()
        response = await client.post("/entities/traverse", json=body)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 5: search.pricing
    # =========================================================================

    async def search_pricing(
        self,
        ticker: Optional[str] = None,
        cusip: Optional[str] = None,
        pricing_date: Optional[Union[str, date]] = None,
        date_from: Optional[Union[str, date]] = None,
        date_to: Optional[Union[str, date]] = None,
        aggregation: str = "latest",
        min_ytm: Optional[float] = None,
        max_ytm: Optional[float] = None,
        min_spread: Optional[int] = None,
        fields: Optional[str] = None,
        sort: str = "-ytm",
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Search bond pricing from FINRA TRACE.

        Args:
            ticker: Company ticker(s)
            cusip: CUSIP(s)
            pricing_date: Pricing as of date
            date_from: History start date
            date_to: History end date
            aggregation: "latest", "daily", "weekly"
            min_ytm: Minimum YTM (%)
            max_ytm: Maximum YTM (%)
            min_spread: Minimum spread (bps)
            fields: Comma-separated fields to return
            sort: Sort field
            limit: Results per page

        Returns:
            Dictionary with pricing data

        Example:
            # Get current pricing for RIG bonds
            result = await client.search_pricing(
                ticker="RIG",
                aggregation="latest",
                fields="cusip,bond_name,last_price,ytm,spread"
            )
        """
        params = {
            "aggregation": aggregation,
            "sort": sort,
            "limit": limit,
        }

        if ticker:
            params["ticker"] = ticker
        if cusip:
            params["cusip"] = cusip
        if pricing_date:
            params["date"] = str(pricing_date)
        if date_from:
            params["date_from"] = str(date_from)
        if date_to:
            params["date_to"] = str(date_to)
        if min_ytm is not None:
            params["min_ytm"] = min_ytm
        if max_ytm is not None:
            params["max_ytm"] = max_ytm
        if min_spread is not None:
            params["min_spread"] = min_spread
        if fields:
            params["fields"] = fields

        client = await self._get_client()
        response = await client.get("/pricing", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 6: search.documents
    # =========================================================================

    async def search_documents(
        self,
        q: str,
        ticker: Optional[str] = None,
        doc_type: Optional[str] = None,
        section_type: Optional[str] = None,
        filed_after: Optional[Union[str, date]] = None,
        filed_before: Optional[Union[str, date]] = None,
        fields: Optional[str] = None,
        sort: str = "-relevance",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Full-text search across SEC filing sections.

        Args:
            q: Search query (required)
            ticker: Comma-separated company tickers
            doc_type: Filing type: 10-K, 10-Q, 8-K
            section_type: Section type:
                - exhibit_21: Subsidiary list
                - debt_footnote: Long-term debt details
                - mda_liquidity: Liquidity and Capital Resources
                - credit_agreement: Full credit facility documents
                - indenture: Bond indentures
                - guarantor_list: Guarantor subsidiaries
                - covenants: Financial covenant details
            filed_after: Minimum filing date
            filed_before: Maximum filing date
            fields: Comma-separated fields to return
            sort: -relevance (default), -filing_date, filing_date
            limit: Results per page (max 100)
            offset: Pagination offset

        Returns:
            Dictionary with search results and snippets

        Example:
            # Search for covenant language
            result = await client.search_documents(
                q="maintenance covenant",
                section_type="credit_agreement",
                ticker="CHTR"
            )
        """
        params = {
            "q": q,
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }

        if ticker:
            params["ticker"] = ticker
        if doc_type:
            params["doc_type"] = doc_type
        if section_type:
            params["section_type"] = section_type
        if filed_after:
            params["filed_after"] = str(filed_after)
        if filed_before:
            params["filed_before"] = str(filed_before)
        if fields:
            params["fields"] = fields

        client = await self._get_client()
        response = await client.get("/documents/search", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 7: batch
    # =========================================================================

    async def batch(
        self,
        operations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Execute multiple primitives in a single request.

        Args:
            operations: List of operations, each with:
                - primitive: One of search.companies, search.bonds, resolve.bond,
                            traverse.entities, search.pricing, search.documents
                - params: Parameters for that primitive

        Returns:
            Dictionary with results for each operation

        Example:
            result = await client.batch([
                {
                    "primitive": "search.companies",
                    "params": {"ticker": "AAPL,MSFT", "fields": "ticker,net_leverage_ratio"}
                },
                {
                    "primitive": "search.bonds",
                    "params": {"ticker": "AAPL", "has_pricing": True}
                }
            ])
        """
        if len(operations) > 10:
            raise ValueError("Maximum 10 operations per batch request")

        body = {"operations": operations}

        client = await self._get_client()
        response = await client.post("/batch", json=body)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Primitive 8: changes
    # =========================================================================

    async def get_changes(
        self,
        ticker: str,
        since: Union[str, date],
    ) -> Dict[str, Any]:
        """
        Get changes to a company's debt structure since a date.

        Args:
            ticker: Company ticker
            since: Compare changes since this date (YYYY-MM-DD)

        Returns:
            Dictionary with:
                - new_debt: Newly issued debt since date
                - removed_debt: Matured/retired debt
                - entity_changes: Added/removed entities
                - metric_changes: Changes to leverage, coverage, etc.
                - pricing_changes: Bond price movements

        Example:
            result = await client.get_changes(
                ticker="RIG",
                since="2025-10-01"
            )
            for new_bond in result["data"]["changes"]["new_debt"]:
                print(f"New: {new_bond['name']}")
        """
        params = {"since": str(since)}

        client = await self._get_client()
        response = await client.get(f"/companies/{ticker}/changes", params=params)
        response.raise_for_status()
        return response.json()

    # =========================================================================
    # Convenience methods
    # =========================================================================

    async def get_company(self, ticker: str, include_metadata: bool = False) -> Dict[str, Any]:
        """
        Get a single company by ticker.

        Args:
            ticker: Company ticker symbol
            include_metadata: Include extraction quality metadata

        Returns:
            Company data dictionary
        """
        result = await self.search_companies(ticker=ticker, include_metadata=include_metadata)
        if not result.get("data"):
            raise ValueError(f"Company not found: {ticker}")
        return result["data"][0]

    async def get_company_bonds(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Get all bonds for a company.

        Args:
            ticker: Company ticker symbol

        Returns:
            List of bond dictionaries
        """
        result = await self.search_bonds(ticker=ticker, limit=100)
        return result.get("data", [])

    async def get_guarantors(self, cusip: str) -> List[Dict[str, Any]]:
        """
        Get all guarantors for a bond.

        Args:
            cusip: Bond CUSIP

        Returns:
            List of guarantor entity dictionaries
        """
        result = await self.traverse_entities(
            start_type="bond",
            start_id=cusip,
            relationships=["guarantees"],
            direction="inbound",
            fields=["name", "entity_type", "jurisdiction", "is_guarantor"]
        )
        return result.get("data", {}).get("traversal", {}).get("entities", [])

    async def get_corporate_structure(self, ticker: str) -> Dict[str, Any]:
        """
        Get full corporate structure for a company.

        Args:
            ticker: Company ticker symbol

        Returns:
            Dictionary with entity hierarchy and debt at each level
        """
        result = await self.traverse_entities(
            start_type="company",
            start_id=ticker,
            relationships=["subsidiaries"],
            direction="outbound",
            depth=10,
            fields=["name", "entity_type", "jurisdiction", "is_guarantor", "is_vie", "debt_at_entity"]
        )
        return result.get("data", {})


class DebtStackSyncClient:
    """
    Synchronous wrapper for DebtStackClient.

    Uses asyncio.run() internally for each call. For better performance
    in async contexts, use DebtStackClient directly.
    """

    def __init__(self, api_key: str, **kwargs):
        self.api_key = api_key
        self.kwargs = kwargs

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def search_companies(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.search_companies(**kwargs)
        return self._run(_call())

    def search_bonds(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.search_bonds(**kwargs)
        return self._run(_call())

    def resolve_bond(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.resolve_bond(**kwargs)
        return self._run(_call())

    def traverse_entities(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.traverse_entities(**kwargs)
        return self._run(_call())

    def search_pricing(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.search_pricing(**kwargs)
        return self._run(_call())

    def search_documents(self, **kwargs) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.search_documents(**kwargs)
        return self._run(_call())

    def batch(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.batch(operations)
        return self._run(_call())

    def get_changes(self, ticker: str, since: Union[str, date]) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_changes(ticker, since)
        return self._run(_call())

    def get_company(self, ticker: str, include_metadata: bool = False) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_company(ticker, include_metadata)
        return self._run(_call())

    def get_company_bonds(self, ticker: str) -> List[Dict[str, Any]]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_company_bonds(ticker)
        return self._run(_call())

    def get_guarantors(self, cusip: str) -> List[Dict[str, Any]]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_guarantors(cusip)
        return self._run(_call())

    def get_corporate_structure(self, ticker: str) -> Dict[str, Any]:
        async def _call():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_corporate_structure(ticker)
        return self._run(_call())

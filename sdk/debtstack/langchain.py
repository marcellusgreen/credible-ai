"""
LangChain integration for DebtStack.ai

Provides tools and toolkit for using DebtStack with LangChain agents.
All 8 primitives are exposed as individual tools.

Usage:
    from debtstack.langchain import DebtStackToolkit

    toolkit = DebtStackToolkit(api_key="your-api-key")
    tools = toolkit.get_tools()

    # Use with LangChain agent
    from langchain.agents import AgentExecutor, create_openai_functions_agent
    agent = create_openai_functions_agent(llm, tools, prompt)
"""

import json
from typing import Any, Dict, List, Optional, Type

try:
    from langchain_core.tools import BaseTool
    from langchain_core.callbacks import CallbackManagerForToolRun
    from pydantic import BaseModel, Field
except ImportError:
    raise ImportError(
        "LangChain dependencies not installed. "
        "Install with: pip install debtstack-ai[langchain]"
    )

import httpx


# =============================================================================
# API Wrapper
# =============================================================================

class DebtStackAPIWrapper:
    """Wrapper for DebtStack.ai API - used by all tools."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.debtstack.ai/v1",
    ):
        import os
        self.api_key = api_key or os.getenv("DEBTSTACK_API_KEY")
        if not self.api_key:
            raise ValueError(
                "DebtStack API key required. Pass api_key or set DEBTSTACK_API_KEY env var."
            )
        self.base_url = base_url
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """Make GET request to API."""
        response = httpx.get(
            f"{self.base_url}{endpoint}",
            params=params,
            headers=self._headers,
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint: str, json_data: Dict) -> Dict[str, Any]:
        """Make POST request to API."""
        response = httpx.post(
            f"{self.base_url}{endpoint}",
            json=json_data,
            headers=self._headers,
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()

    def search_companies(self, **kwargs) -> Dict[str, Any]:
        """Search companies with filters."""
        return self._get("/companies", params=kwargs)

    def search_bonds(self, **kwargs) -> Dict[str, Any]:
        """Search bonds with filters."""
        return self._get("/bonds", params=kwargs)

    def resolve_bond(self, **kwargs) -> Dict[str, Any]:
        """Resolve bond identifier."""
        return self._get("/bonds/resolve", params=kwargs)

    def traverse_entities(self, body: Dict) -> Dict[str, Any]:
        """Traverse entity relationships."""
        return self._post("/entities/traverse", body)

    def search_pricing(self, **kwargs) -> Dict[str, Any]:
        """Search bond pricing."""
        return self._get("/pricing", params=kwargs)

    def search_documents(self, **kwargs) -> Dict[str, Any]:
        """Search SEC filing sections."""
        return self._get("/documents/search", params=kwargs)

    def batch(self, operations: List[Dict]) -> Dict[str, Any]:
        """Execute batch operations."""
        return self._post("/batch", {"operations": operations})

    def get_changes(self, ticker: str, since: str) -> Dict[str, Any]:
        """Get company changes since date."""
        return self._get(f"/companies/{ticker}/changes", params={"since": since})


# =============================================================================
# Tool Input Schemas
# =============================================================================

class SearchCompaniesInput(BaseModel):
    """Input for company search tool."""
    ticker: Optional[str] = Field(
        None,
        description="Comma-separated tickers (e.g., 'AAPL,MSFT,GOOGL')"
    )
    sector: Optional[str] = Field(
        None,
        description="Filter by sector (e.g., 'Technology', 'Energy')"
    )
    min_leverage: Optional[float] = Field(
        None,
        description="Minimum leverage ratio (e.g., 3.0)"
    )
    max_leverage: Optional[float] = Field(
        None,
        description="Maximum leverage ratio (e.g., 6.0)"
    )
    has_structural_sub: Optional[bool] = Field(
        None,
        description="Filter for companies with structural subordination"
    )
    fields: Optional[str] = Field(
        None,
        description="Comma-separated fields to return (e.g., 'ticker,name,net_leverage_ratio')"
    )
    sort: Optional[str] = Field(
        None,
        description="Sort field, prefix with - for descending (e.g., '-net_leverage_ratio')"
    )
    limit: int = Field(
        10,
        description="Maximum results to return"
    )


class SearchBondsInput(BaseModel):
    """Input for bond search tool."""
    ticker: Optional[str] = Field(
        None,
        description="Company ticker(s) to filter by"
    )
    seniority: Optional[str] = Field(
        None,
        description="Seniority level: 'senior_secured', 'senior_unsecured', 'subordinated'"
    )
    min_ytm: Optional[float] = Field(
        None,
        description="Minimum yield to maturity (%)"
    )
    max_ytm: Optional[float] = Field(
        None,
        description="Maximum yield to maturity (%)"
    )
    min_spread: Optional[int] = Field(
        None,
        description="Minimum spread to treasury (basis points)"
    )
    has_pricing: Optional[bool] = Field(
        None,
        description="Filter for bonds with pricing data"
    )
    maturity_before: Optional[str] = Field(
        None,
        description="Maturity before date (YYYY-MM-DD)"
    )
    maturity_after: Optional[str] = Field(
        None,
        description="Maturity after date (YYYY-MM-DD)"
    )
    fields: Optional[str] = Field(
        None,
        description="Comma-separated fields to return"
    )
    sort: Optional[str] = Field(
        None,
        description="Sort field (e.g., '-pricing.ytm' for highest yield first)"
    )
    limit: int = Field(
        10,
        description="Maximum results to return"
    )


class ResolveBondInput(BaseModel):
    """Input for bond resolution tool."""
    query: str = Field(
        ...,
        description="Bond identifier to resolve - can be CUSIP, ISIN, or description (e.g., 'RIG 8% 2027')"
    )


class TraverseEntitiesInput(BaseModel):
    """Input for entity traversal tool."""
    start_type: str = Field(
        ...,
        description="Type of starting node: 'company' (use ticker), 'bond' (use CUSIP), or 'entity' (use UUID)"
    )
    start_id: str = Field(
        ...,
        description="ID of starting node - ticker for company, CUSIP for bond, UUID for entity"
    )
    relationship: str = Field(
        ...,
        description="Relationship to traverse: 'guarantees', 'subsidiaries', 'parents', 'debt', 'borrowers'"
    )
    direction: str = Field(
        "inbound",
        description="Traversal direction: 'inbound', 'outbound', or 'both'"
    )


class SearchPricingInput(BaseModel):
    """Input for pricing search tool."""
    ticker: Optional[str] = Field(
        None,
        description="Company ticker(s) to filter by"
    )
    cusip: Optional[str] = Field(
        None,
        description="CUSIP(s) to look up"
    )
    min_ytm: Optional[float] = Field(
        None,
        description="Minimum yield to maturity (%)"
    )
    fields: Optional[str] = Field(
        None,
        description="Comma-separated fields to return"
    )
    limit: int = Field(
        10,
        description="Maximum results to return"
    )


class SearchDocumentsInput(BaseModel):
    """Input for document search tool."""
    query: str = Field(
        ...,
        description="Search query for SEC filing content"
    )
    ticker: Optional[str] = Field(
        None,
        description="Company ticker(s) to filter by"
    )
    section_type: Optional[str] = Field(
        None,
        description="Section type: 'debt_footnote', 'credit_agreement', 'indenture', 'covenants', 'mda_liquidity', 'exhibit_21', 'guarantor_list'"
    )
    limit: int = Field(
        10,
        description="Maximum results to return"
    )


class GetChangesInput(BaseModel):
    """Input for changes tool."""
    ticker: str = Field(
        ...,
        description="Company ticker"
    )
    since: str = Field(
        ...,
        description="Date to compare from (YYYY-MM-DD)"
    )


# =============================================================================
# Tool Implementations
# =============================================================================

class DebtStackSearchCompaniesTool(BaseTool):
    """Search companies with filtering for leverage, sectors, and risk flags."""

    name: str = "debtstack_search_companies"
    description: str = (
        "Search companies by ticker, sector, leverage ratio, and risk flags. "
        "Use this to find companies matching specific criteria, compare leverage across peers, "
        "or screen for structural subordination. "
        "Returns company metrics including leverage ratios, debt amounts, and risk indicators."
    )
    args_schema: Type[BaseModel] = SearchCompaniesInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        ticker: Optional[str] = None,
        sector: Optional[str] = None,
        min_leverage: Optional[float] = None,
        max_leverage: Optional[float] = None,
        has_structural_sub: Optional[bool] = None,
        fields: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if sector:
            params["sector"] = sector
        if min_leverage is not None:
            params["min_leverage"] = min_leverage
        if max_leverage is not None:
            params["max_leverage"] = max_leverage
        if has_structural_sub is not None:
            params["has_structural_sub"] = has_structural_sub
        if fields:
            params["fields"] = fields
        if sort:
            params["sort"] = sort

        result = self.api_wrapper.search_companies(**params)
        return json.dumps(result, indent=2, default=str)


class DebtStackSearchBondsTool(BaseTool):
    """Search bonds with yield, spread, and seniority filters."""

    name: str = "debtstack_search_bonds"
    description: str = (
        "Search bonds by ticker, seniority, yield to maturity, spread, and maturity date. "
        "Use this for yield hunting, finding high-yield bonds, screening by seniority, "
        "or building maturity walls. "
        "Returns bond details including coupon, maturity, outstanding amount, and pricing."
    )
    args_schema: Type[BaseModel] = SearchBondsInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        ticker: Optional[str] = None,
        seniority: Optional[str] = None,
        min_ytm: Optional[float] = None,
        max_ytm: Optional[float] = None,
        min_spread: Optional[int] = None,
        has_pricing: Optional[bool] = None,
        maturity_before: Optional[str] = None,
        maturity_after: Optional[str] = None,
        fields: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if seniority:
            params["seniority"] = seniority
        if min_ytm is not None:
            params["min_ytm"] = min_ytm
        if max_ytm is not None:
            params["max_ytm"] = max_ytm
        if min_spread is not None:
            params["min_spread"] = min_spread
        if has_pricing is not None:
            params["has_pricing"] = has_pricing
        if maturity_before:
            params["maturity_before"] = maturity_before
        if maturity_after:
            params["maturity_after"] = maturity_after
        if fields:
            params["fields"] = fields
        if sort:
            params["sort"] = sort

        result = self.api_wrapper.search_bonds(**params)
        return json.dumps(result, indent=2, default=str)


class DebtStackResolveBondTool(BaseTool):
    """Resolve bond identifiers - CUSIP, ISIN, or descriptions."""

    name: str = "debtstack_resolve_bond"
    description: str = (
        "Resolve a bond identifier to get full details. "
        "Accepts CUSIP, ISIN, or free-text description (e.g., 'RIG 8% 2027'). "
        "Returns matching bonds with confidence scores, useful for identifier lookup "
        "or when you have a partial bond description."
    )
    args_schema: Type[BaseModel] = ResolveBondInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        # Detect if it's a CUSIP (9 chars alphanumeric) or ISIN (12 chars starting with letters)
        query = query.strip()
        params = {}

        if len(query) == 9 and query.isalnum():
            params["cusip"] = query
        elif len(query) == 12 and query[:2].isalpha():
            params["isin"] = query
        else:
            params["q"] = query
            params["match_mode"] = "fuzzy"

        result = self.api_wrapper.resolve_bond(**params)
        return json.dumps(result, indent=2, default=str)


class DebtStackTraverseEntitiesTool(BaseTool):
    """Traverse entity relationships - guarantors, subsidiaries, corporate structure."""

    name: str = "debtstack_traverse_entities"
    description: str = (
        "Follow relationships between companies, bonds, and entities. "
        "Use 'guarantees' with direction='inbound' to find who guarantees a bond. "
        "Use 'subsidiaries' with direction='outbound' to see corporate structure. "
        "Use 'parents' to trace ownership chain upward. "
        "Essential for understanding structural subordination and guarantee coverage."
    )
    args_schema: Type[BaseModel] = TraverseEntitiesInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        start_type: str,
        start_id: str,
        relationship: str,
        direction: str = "inbound",
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        body = {
            "start": {
                "type": start_type,
                "id": start_id.upper() if start_type == "company" else start_id
            },
            "relationships": [relationship],
            "direction": direction,
            "fields": ["name", "entity_type", "jurisdiction", "is_guarantor", "is_vie", "debt_at_entity"]
        }

        result = self.api_wrapper.traverse_entities(body)
        return json.dumps(result, indent=2, default=str)


class DebtStackSearchPricingTool(BaseTool):
    """Search bond pricing from FINRA TRACE."""

    name: str = "debtstack_search_pricing"
    description: str = (
        "Get bond pricing data including price, yield to maturity, and spread. "
        "Use to find current prices, identify distressed bonds (high yields), "
        "or compare relative value across bonds. "
        "Data sourced from FINRA TRACE."
    )
    args_schema: Type[BaseModel] = SearchPricingInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        ticker: Optional[str] = None,
        cusip: Optional[str] = None,
        min_ytm: Optional[float] = None,
        fields: Optional[str] = None,
        limit: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"limit": limit, "aggregation": "latest"}
        if ticker:
            params["ticker"] = ticker
        if cusip:
            params["cusip"] = cusip
        if min_ytm is not None:
            params["min_ytm"] = min_ytm
        if fields:
            params["fields"] = fields

        result = self.api_wrapper.search_pricing(**params)
        return json.dumps(result, indent=2, default=str)


class DebtStackSearchDocumentsTool(BaseTool):
    """Full-text search across SEC filing sections."""

    name: str = "debtstack_search_documents"
    description: str = (
        "Search across SEC filing sections for specific terms or concepts. "
        "Section types: 'debt_footnote' (long-term debt details), 'credit_agreement' (full facility docs), "
        "'indenture' (bond indentures), 'covenants' (financial covenants), 'mda_liquidity' (MD&A liquidity section). "
        "Returns matching sections with highlighted snippets. "
        "Use to find covenant language, credit agreement terms, or debt descriptions."
    )
    args_schema: Type[BaseModel] = SearchDocumentsInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        query: str,
        ticker: Optional[str] = None,
        section_type: Optional[str] = None,
        limit: int = 10,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        params = {"q": query, "limit": limit}
        if ticker:
            params["ticker"] = ticker
        if section_type:
            params["section_type"] = section_type

        result = self.api_wrapper.search_documents(**params)
        return json.dumps(result, indent=2, default=str)


class DebtStackGetChangesTool(BaseTool):
    """Get changes to a company's debt structure since a date."""

    name: str = "debtstack_get_changes"
    description: str = (
        "Compare a company's current debt structure against a historical snapshot. "
        "Returns new issuances, matured debt, entity changes, leverage changes, and pricing movements. "
        "Use to monitor portfolio companies for material changes or track refinancing activity."
    )
    args_schema: Type[BaseModel] = GetChangesInput
    api_wrapper: DebtStackAPIWrapper

    def _run(
        self,
        ticker: str,
        since: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        result = self.api_wrapper.get_changes(ticker.upper(), since)
        return json.dumps(result, indent=2, default=str)


# =============================================================================
# Toolkit
# =============================================================================

class DebtStackToolkit:
    """
    Toolkit for interacting with DebtStack.ai credit data API.

    Setup:
        Set environment variable ``DEBTSTACK_API_KEY`` or pass api_key.

        .. code-block:: bash

            export DEBTSTACK_API_KEY="your-api-key"

    Instantiate:
        .. code-block:: python

            from debtstack.langchain import DebtStackToolkit

            toolkit = DebtStackToolkit(api_key="your-api-key")
            tools = toolkit.get_tools()

    Use with agent:
        .. code-block:: python

            from langchain.agents import AgentExecutor, create_openai_functions_agent
            from langchain_openai import ChatOpenAI
            from langchain import hub

            toolkit = DebtStackToolkit()
            tools = toolkit.get_tools()

            llm = ChatOpenAI(temperature=0, model="gpt-4")
            prompt = hub.pull("hwchase17/openai-functions-agent")
            agent = create_openai_functions_agent(llm, tools, prompt)
            agent_executor = AgentExecutor(agent=agent, tools=tools)

            result = agent_executor.invoke({
                "input": "Which MAG7 company has the highest leverage?"
            })
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.debtstack.ai/v1"):
        """
        Initialize DebtStack toolkit.

        Args:
            api_key: DebtStack API key. If not provided, reads from DEBTSTACK_API_KEY env var.
            base_url: API base URL.
        """
        self.api_wrapper = DebtStackAPIWrapper(api_key=api_key, base_url=base_url)

    def get_tools(self) -> List[BaseTool]:
        """
        Get all DebtStack tools.

        Returns:
            List of 7 tools covering all DebtStack primitives:
            - debtstack_search_companies: Company search with filtering
            - debtstack_search_bonds: Bond search with yield/spread filters
            - debtstack_resolve_bond: Bond identifier resolution
            - debtstack_traverse_entities: Graph traversal for guarantors/structure
            - debtstack_search_pricing: Bond pricing from FINRA TRACE
            - debtstack_search_documents: Full-text search across SEC filings
            - debtstack_get_changes: Diff/changelog for debt structure
        """
        return [
            DebtStackSearchCompaniesTool(api_wrapper=self.api_wrapper),
            DebtStackSearchBondsTool(api_wrapper=self.api_wrapper),
            DebtStackResolveBondTool(api_wrapper=self.api_wrapper),
            DebtStackTraverseEntitiesTool(api_wrapper=self.api_wrapper),
            DebtStackSearchPricingTool(api_wrapper=self.api_wrapper),
            DebtStackSearchDocumentsTool(api_wrapper=self.api_wrapper),
            DebtStackGetChangesTool(api_wrapper=self.api_wrapper),
        ]

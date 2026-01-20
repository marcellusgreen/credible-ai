# DebtStack.ai Distribution Playbook (INTERNAL)
## *Replicating FinancialDatasets.ai's Go-to-Market Strategy*

**Status**: CONFIDENTIAL - Internal strategy document  
**Last Updated**: January 2026  
**Owner**: Sunil

---

## Executive Summary

**Goal**: Achieve 1,000+ API signups and integration into major AI agent frameworks within 6 months through zero-cost distribution channels.

**Strategy**: Build infrastructure, not just an API. Embed DebtStack.ai into every framework where developers build financial AI agents.

**Key Insight**: FinancialDatasets.ai achieved widespread adoption without VC funding by integrating into LangChain, MCP (Anthropic), and other frameworks. We replicate this playbook for the credit market.

**Success Metrics**:
- LangChain integration merged (Month 2)
- MCP server listed on Anthropic (Month 2)
- 1,000 Discord members (Month 6)
- 500 active API users (Month 6)
- 50+ community projects using DebtStack (Month 6)

---

## Phase 1: Foundation (Weeks 1-8)
### *Build the Core Infrastructure*

### Week 1-2: API Productization

#### 1.1 OpenAPI Specification

**Priority**: CRITICAL  
**Timeline**: 3 days

Create complete OpenAPI 3.0 spec:

```yaml
# /api/openapi.yaml
openapi: 3.0.0
info:
  title: DebtStack.ai Credit Data API
  description: Corporate debt structures API for AI agents
  version: 1.0.0
servers:
  - url: https://api.debtstack.ai/v1

paths:
  /companies:
    get:
      summary: List all companies
      responses:
        '200':
          description: List of companies
          
  /companies/{ticker}:
    get:
      summary: Get company overview
      parameters:
        - name: ticker
          in: path
          required: true
          schema:
            type: string
            
  /companies/{ticker}/structure:
    get:
      summary: Get corporate structure with debt
      
  /companies/{ticker}/debt:
    get:
      summary: Get all debt instruments

components:
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: X-API-KEY
```

**Deliverables**:
- [ ] Complete OpenAPI spec covering all endpoints
- [ ] Authentication documented (API keys)
- [ ] Rate limiting rules defined
- [ ] Error response schemas
- [ ] Example requests/responses

---

#### 1.2 Python SDK (PyPI Package)

**Priority**: CRITICAL  
**Timeline**: 1 week

Build `debtstack-ai` package for PyPI:

```python
# debtstack/client.py
"""DebtStack.ai Python SDK"""

import httpx
from typing import Optional, Dict, Any, List

class DebtStackClient:
    """Main client for DebtStack.ai API"""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.debtstack.ai/v1",
        timeout: int = 30
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-API-KEY": api_key}
        )
    
    async def get_company_structure(self, ticker: str) -> Dict[str, Any]:
        """Get complete corporate structure with debt at each entity."""
        response = await self.client.get(f"/companies/{ticker}/structure")
        response.raise_for_status()
        return response.json()
    
    async def get_company_debt(self, ticker: str) -> Dict[str, Any]:
        """Get all debt instruments for a company."""
        response = await self.client.get(f"/companies/{ticker}/debt")
        response.raise_for_status()
        return response.json()
    
    async def get_company_overview(self, ticker: str) -> Dict[str, Any]:
        """Get company overview and metrics."""
        response = await self.client.get(f"/companies/{ticker}")
        response.raise_for_status()
        return response.json()
    
    async def list_companies(self) -> List[Dict[str, Any]]:
        """List all available companies."""
        response = await self.client.get("/companies")
        response.raise_for_status()
        return response.json()["companies"]
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

# Sync client wrapper
class DebtStackSyncClient:
    """Synchronous wrapper for DebtStackClient"""
    
    def __init__(self, api_key: str, **kwargs):
        self.api_key = api_key
        self.kwargs = kwargs
    
    def get_company_structure(self, ticker: str) -> Dict[str, Any]:
        import asyncio
        async def _get():
            async with DebtStackClient(self.api_key, **self.kwargs) as client:
                return await client.get_company_structure(ticker)
        return asyncio.run(_get())
    
    # Similar wrappers for other methods...
```

**Package Structure**:
```
debtstack-ai/
├── pyproject.toml
├── README.md
├── LICENSE (MIT)
├── debtstack/
│   ├── __init__.py
│   ├── client.py
│   ├── sync_client.py
│   ├── exceptions.py
│   └── types.py
├── tests/
│   ├── test_client.py
│   └── test_integration.py
└── examples/
    ├── basic_usage.py
    ├── async_usage.py
    └── credit_analysis.py
```

**pyproject.toml**:
```toml
[build-system]
requires = ["setuptools>=45", "wheel"]

[project]
name = "debtstack-ai"
version = "0.1.0"
description = "Corporate debt structures API for AI agents"
authors = [{name = "DebtStack.ai", email = "hello@debtstack.ai"}]
license = {text = "MIT"}
requires-python = ">=3.8"
dependencies = [
    "httpx>=0.24.0",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "mypy>=1.0",
    "black>=23.0",
    "ruff>=0.1.0",
]

[project.urls]
Homepage = "https://debtstack.ai"
Documentation = "https://docs.debtstack.ai"
Repository = "https://github.com/debtstack-ai/debtstack-python"
Issues = "https://github.com/debtstack-ai/debtstack-python/issues"
```

**README.md for SDK**:
```markdown
# DebtStack.ai Python SDK

Official Python SDK for DebtStack.ai's credit data API.

## Installation

```bash
pip install debtstack-ai
```

## Quick Start

```python
from debtstack import DebtStackClient
import asyncio

async def main():
    async with DebtStackClient(api_key="your_api_key") as client:
        # Get company structure
        structure = await client.get_company_structure("AAPL")
        print(structure)
        
        # Get all debt
        debt = await client.get_company_debt("AAPL")
        print(debt)

asyncio.run(main())
```

## Synchronous Usage

```python
from debtstack import DebtStackSyncClient

client = DebtStackSyncClient(api_key="your_api_key")
structure = client.get_company_structure("AAPL")
```

## Documentation

Full documentation: [docs.debtstack.ai](https://docs.debtstack.ai)

## License

MIT
```

**Deliverables**:
- [ ] Python SDK implemented with async/sync
- [ ] Published to PyPI as `debtstack-ai`
- [ ] Type hints + mypy validation
- [ ] 90%+ test coverage
- [ ] GitHub repo: `debtstack-ai/debtstack-python`
- [ ] CI/CD setup (GitHub Actions)

---

#### 1.3 Documentation Site

**Priority**: HIGH  
**Timeline**: 1 week

**Tech Stack**:
- Framework: Mintlify or Docusaurus
- Hosting: Vercel
- Domain: `docs.debtstack.ai`

**Site Structure**:
```
docs.debtstack.ai/
├── Introduction
│   ├── What is DebtStack
│   ├── Why DebtStack
│   └── Quick Start
├── API Reference (auto-generated)
│   ├── Authentication
│   ├── Companies
│   ├── Structure
│   ├── Debt
│   └── Errors
├── Integrations
│   ├── LangChain
│   ├── LlamaIndex
│   ├── MCP Server (Claude)
│   ├── n8n
│   └── Dify
├── Use Cases
│   ├── Credit Analysis Agent
│   ├── Distressed Debt Screening
│   ├── Covenant Monitoring
│   └── CLO Portfolio Analysis
├── SDKs
│   ├── Python
│   └── JavaScript (coming soon)
└── Guides
    ├── Understanding Credit Data
    ├── Best Practices
    └── Rate Limits
```

**Key Pages**:

**introduction.md**:
```markdown
# DebtStack.ai - Credit Infrastructure for AI Agents

DebtStack.ai provides structured corporate debt data extracted from SEC filings, 
optimized for AI agents and LLMs.

## What We Provide

- **Debt Structures**: Complete capital structures with debt instruments, guarantees, priorities
- **Corporate Hierarchies**: Parent-subsidiary relationships and guarantor chains
- **Covenant Data**: Financial and non-financial covenants with thresholds
- **Intercreditor Agreements**: Payment waterfalls and priority structures

## Built for AI Agents

Unlike traditional financial data APIs, DebtStack is designed specifically for:
- LangChain agents analyzing credit risk
- Claude analyzing 10-Ks and extracting debt terms
- Custom financial AI applications
- Credit research automation

## Key Features

✅ **Pre-computed & QA-verified** - 85%+ quality score with 5-check verification  
✅ **Individual debt instruments** - Not just totals, but each bond and note  
✅ **Complex structures** - VIEs, joint ventures, multiple parents  
✅ **Fast API** - Sub-second response with ETag caching  
✅ **Cost-optimized** - $0.03/company extraction cost  

## Get Started in 5 Minutes

```bash
pip install debtstack-ai
```

```python
from debtstack import DebtStackClient

async with DebtStackClient(api_key="your_key") as client:
    structure = await client.get_company_structure("AAPL")
    print(structure)
```

[Continue to Quick Start →](/quickstart)
```

**Deliverables**:
- [ ] Documentation site deployed at docs.debtstack.ai
- [ ] All sections populated with content
- [ ] API reference auto-generated from OpenAPI
- [ ] SEO optimized (meta tags, sitemap, structured data)
- [ ] Analytics (PostHog or Plausible)
- [ ] Search functionality

---

#### 1.4 Free Tier Strategy

**Priority**: CRITICAL  
**Timeline**: 3 days

**Pricing Model** (modeled after FinancialDatasets.ai):

```yaml
Free Tier:
  companies: S&P 100 (100 tickers)
  api_calls: 1,000/month
  features: All endpoints
  support: Community (Discord)
  
Developer: $49/month
  companies: Russell 3000 (3,000 tickers)
  api_calls: 10,000/month
  features: All endpoints
  support: Email
  
Pro: $199/month
  companies: All public (15,000+ tickers)
  api_calls: 100,000/month
  features: All endpoints + early access
  support: Priority email
  redistribution: Yes
  
Enterprise: Custom
  companies: Unlimited
  api_calls: Unlimited
  features: Custom extractions
  support: SLA + phone
  account_manager: Dedicated
```

**Implementation**:

```python
# app/auth/plans.py

FREE_TIER_TICKERS = [
    # S&P 100 companies
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "BRK.B", "JPM", "V", "UNH", "JNJ", "WMT", "XOM", "PG",
    # ... rest of S&P 100
]

PLAN_LIMITS = {
    "free": {
        "tickers": FREE_TIER_TICKERS,
        "rate_limit": 1000,  # per month
        "features": ["structure", "debt", "overview"]
    },
    "developer": {
        "tickers": "russell_3000",  # Check against list
        "rate_limit": 10000,
        "features": ["structure", "debt", "overview"]
    },
    "pro": {
        "tickers": "all",
        "rate_limit": 100000,
        "features": ["structure", "debt", "overview", "early_access"]
    },
    "enterprise": {
        "tickers": "all",
        "rate_limit": float('inf'),
        "features": ["all"]
    }
}

def check_access(api_key: str, ticker: str) -> bool:
    """Check if user has access to ticker based on plan"""
    user = get_user_from_api_key(api_key)
    plan = PLAN_LIMITS[user.plan]
    
    if user.plan == "free":
        return ticker in plan["tickers"]
    elif user.plan == "developer":
        return ticker in RUSSELL_3000
    else:  # pro, enterprise
        return True
```

**Deliverables**:
- [ ] Free tier implemented with S&P 100
- [ ] Rate limiting by plan tier
- [ ] Self-service signup flow
- [ ] Stripe integration for paid tiers
- [ ] Usage dashboard for users

---

### Week 3-4: GitHub & Community

#### 2.1 GitHub Organization

**Priority**: HIGH  
**Timeline**: 1 day

**Repository Structure**:
```
github.com/debtstack-ai/
├── debtstack-python          # Python SDK
├── mcp-server              # MCP server for Claude
├── langchain-integration   # Code before PR merge
├── llamaindex-integration  # LlamaIndex tools
├── examples                # Example projects
│   ├── credit-analysis-agent
│   ├── distressed-debt-screener
│   ├── covenant-monitor
│   └── clo-analyzer
└── docs                    # Documentation source
```

**Template README for main SDK**:
```markdown
# DebtStack.ai Python SDK

[![PyPI](https://img.shields.io/pypi/v/debtstack-ai)](https://pypi.org/project/debtstack-ai/)
[![Tests](https://github.com/debtstack-ai/debtstack-python/workflows/tests/badge.svg)](https://github.com/debtstack-ai/debtstack-python/actions)
[![Coverage](https://codecov.io/gh/debtstack-ai/debtstack-python/branch/main/graph/badge.svg)](https://codecov.io/gh/debtstack-ai/debtstack-python)

Official Python SDK for DebtStack.ai's credit data API.

## Installation

```bash
pip install debtstack-ai
```

## Quick Start

```python
from debtstack import DebtStackClient

async with DebtStackClient(api_key="your_api_key") as client:
    debt = await client.get_company_debt("AAPL")
    print(debt)
```

## Documentation

Full documentation: [docs.debtstack.ai](https://docs.debtstack.ai)

## Examples

- [Credit Analysis Agent](./examples/credit_analysis.py)
- [Covenant Monitoring](./examples/covenant_monitor.py)
- [Distressed Debt Screener](./examples/distressed_screener.py)

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md)

## License

MIT
```

**Deliverables**:
- [ ] GitHub org created: `debtstack-ai`
- [ ] All repos initialized with READMEs
- [ ] MIT license on all repos
- [ ] CONTRIBUTING.md guidelines
- [ ] Issue templates (.github/ISSUE_TEMPLATE/)
- [ ] PR template (.github/pull_request_template.md)

---

#### 2.2 Discord Community

**Priority**: HIGH  
**Timeline**: 1 day

**Server Structure**:
```
DebtStack.ai Discord
├── 📢 announcements (read-only)
├── 💬 general
├── 🆘 support
├── 💡 feature-requests
├── 🏗️ show-and-tell (user projects)
├── 🤖 ai-agents (building credit agents)
├── 📊 credit-analysis (domain discussions)
├── 🔧 api-feedback
└── 🎯 jobs (if community grows)
```

**Setup Steps**:
1. Create Discord server
2. Configure channels with descriptions
3. Set up roles: Admin, Moderator, Developer, Member
4. Create permanent invite: `discord.gg/debtstack-ai`
5. Add welcome bot (optional)

**Welcome Message**:
```
👋 Welcome to DebtStack.ai!

Get started:
📚 Docs: https://docs.debtstack.ai
🔑 Get API key: https://debtstack.ai/signup
🐍 Python SDK: pip install debtstack-ai
💬 Ask questions in #support

Building something cool? Share in #show-and-tell!
```

**Deliverables**:
- [ ] Discord server created and configured
- [ ] Invite link: discord.gg/debtstack-ai
- [ ] Linked from website, docs, GitHub
- [ ] Basic moderation rules in #welcome
- [ ] Welcome bot configured (optional)

---

#### 2.3 Landing Page (debtstack.ai)

**Priority**: HIGH  
**Timeline**: 1 week

**Tech Stack**:
- Framework: Next.js 14 + Tailwind CSS
- Hosting: Vercel
- Analytics: PostHog or Plausible

**Page Sections**:

1. **Hero**
```
Credit Data Infrastructure for AI Agents

The Tavily of Credit

Structured corporate debt data from SEC filings, 
optimized for LangChain, Claude, and AI agents.

[Get API Key →] [View Docs →] [Join Discord →]

✓ 38 companies  ✓ 779 entities  ✓ 330 debt instruments
```

2. **Social Proof**
```
Integrated with leading AI frameworks:

[LangChain logo] [Anthropic MCP logo] [LlamaIndex logo]
```

3. **Problem/Solution**
```
Building credit analysis AI? Don't extract from scratch.

❌ Ad-hoc extraction: $0.50+ per company, 90-300 seconds, malformed JSON
✅ DebtStack API: <$0.03 per company, <100ms response, QA-verified

Pre-computed. Quality-assured. Ready for your AI agent.
```

4. **Use Cases** (4 cards)
- Credit Analysis Agents
- Distressed Debt Screening
- Covenant Monitoring
- CLO Portfolio Analysis

5. **Features** (grid)
- ✅ Pre-computed API responses
- ✅ Individual debt instruments
- ✅ Complex corporate structures
- ✅ 85%+ QA verification
- ✅ Sub-second serving
- ✅ LangChain/MCP/LlamaIndex ready

6. **Pricing Table**
Free | Developer | Pro | Enterprise

7. **CTA**
```
Start building your credit AI agent today

[Get Free API Key →]

No credit card required • 1,000 free calls/month • S&P 100 companies
```

**Deliverables**:
- [ ] Landing page at debtstack.ai
- [ ] Pricing page
- [ ] Docs page (links to docs.debtstack.ai)
- [ ] Signup flow functional
- [ ] Analytics tracking
- [ ] SEO optimized (meta, og:tags, sitemap)
- [ ] Mobile responsive

---

## Phase 2: Framework Integrations (Weeks 5-12)
### *Embed into AI Agent Ecosystem*

### Week 5-8: LangChain Integration (HIGHEST PRIORITY)

#### 3.1 Build the Toolkit

**Priority**: CRITICAL  
**Timeline**: 2 weeks

**Goal**: Get into `langchain-community` package

**Files to Create**:

```python
# langchain_community/utilities/debtstack.py
"""Wrapper around DebtStack.ai API."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import httpx

class DebtStackAPIWrapper(BaseModel):
    """Wrapper for DebtStack.ai API."""
    
    debtstack_api_key: Optional[str] = Field(default=None)
    base_url: str = "https://api.debtstack.ai/v1"
    
    class Config:
        extra = "forbid"
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {"X-API-KEY": self.debtstack_api_key}
    
    def get_company_structure(
        self,
        ticker: str
    ) -> Dict[str, Any]:
        """Get complete corporate structure with debt at each entity.
        
        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL')
            
        Returns:
            Dictionary containing corporate structure with debt details
        """
        response = httpx.get(
            f"{self.base_url}/companies/{ticker}/structure",
            headers=self._get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    def get_company_debt(
        self,
        ticker: str
    ) -> Dict[str, Any]:
        """Get all debt instruments for a company.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Dictionary containing all debt instruments with full details
        """
        response = httpx.get(
            f"{self.base_url}/companies/{ticker}/debt",
            headers=self._get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    def get_company_overview(
        self,
        ticker: str
    ) -> Dict[str, Any]:
        """Get company overview and credit metrics.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Dictionary with company info and metrics
        """
        response = httpx.get(
            f"{self.base_url}/companies/{ticker}",
            headers=self._get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
    
    def list_companies(self) -> List[Dict[str, Any]]:
        """List all available companies in database.
        
        Returns:
            List of companies with basic info
        """
        response = httpx.get(
            f"{self.base_url}/companies",
            headers=self._get_headers(),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()["companies"]
```

```python
# langchain_community/agent_toolkits/debtstack/toolkit.py
"""Toolkit for interacting with DebtStack.ai API."""

from typing import List
from langchain_core.tools import BaseToolkit
from langchain_core.pydantic_v1 import Field

from langchain_community.tools import BaseTool
from langchain_community.tools.debtstack.tool import (
    DebtStackCompanyStructureTool,
    DebtStackCompanyDebtTool,
    DebtStackCompanyOverviewTool,
    DebtStackListCompaniesTool,
)
from langchain_community.utilities.debtstack import DebtStackAPIWrapper


class DebtStackToolkit(BaseToolkit):
    """Toolkit for interacting with DebtStack.ai credit data API.
    
    Setup:
        Install ``debtstack-ai`` and set environment variable ``DEBTSTACK_API_KEY``.
        
        .. code-block:: bash
        
            pip install debtstack-ai
            export DEBTSTACK_API_KEY="your-api-key"
    
    Key init args:
        api_wrapper: DebtStackAPIWrapper
            The DebtStack API wrapper.
    
    Instantiate:
        .. code-block:: python
        
            from langchain_community.agent_toolkits import DebtStackToolkit
            from langchain_community.utilities import DebtStackAPIWrapper
            
            api_wrapper = DebtStackAPIWrapper(
                debtstack_api_key="your-api-key"
            )
            toolkit = DebtStackToolkit(api_wrapper=api_wrapper)
    
    Tools:
        .. code-block:: python
        
            tools = toolkit.get_tools()
            for tool in tools:
                print(tool.name)
        
        .. code-block:: none
        
            debtstack_company_structure
            debtstack_company_debt
            debtstack_company_overview
            debtstack_list_companies
    
    Use within an agent:
        .. code-block:: python
        
            from langchain import hub
            from langchain.agents import AgentExecutor, create_openai_functions_agent
            from langchain_openai import ChatOpenAI
            
            # Pull prompt
            prompt = hub.pull("hwchase17/openai-functions-agent")
            
            # Initialize LLM
            llm = ChatOpenAI(temperature=0, model="gpt-4")
            
            # Get tools
            tools = toolkit.get_tools()
            
            # Create agent
            agent = create_openai_functions_agent(llm, tools, prompt)
            agent_executor = AgentExecutor(agent=agent, tools=tools)
            
            # Example query
            agent_executor.invoke({
                "input": "What is Tesla's debt structure? Are there any high-leverage covenants?"
            })
    """
    
    api_wrapper: DebtStackAPIWrapper = Field(default_factory=DebtStackAPIWrapper)
    
    class Config:
        arbitrary_types_allowed = True
    
    def get_tools(self) -> List[BaseTool]:
        """Get the tools in the toolkit."""
        return [
            DebtStackCompanyStructureTool(api_wrapper=self.api_wrapper),
            DebtStackCompanyDebtTool(api_wrapper=self.api_wrapper),
            DebtStackCompanyOverviewTool(api_wrapper=self.api_wrapper),
            DebtStackListCompaniesTool(api_wrapper=self.api_wrapper),
        ]
```

```python
# langchain_community/tools/debtstack/tool.py
"""Tools for interacting with DebtStack.ai API."""

from typing import Optional, Type
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_core.tools import BaseTool

from langchain_community.utilities.debtstack import DebtStackAPIWrapper


class CompanyStructureInput(BaseModel):
    """Input for DebtStack Company Structure tool."""
    
    ticker: str = Field(
        description="Stock ticker symbol (e.g., 'AAPL', 'TSLA', 'RIG')"
    )


class DebtStackCompanyStructureTool(BaseTool):
    """Tool that queries DebtStack.ai for a company's corporate structure and debt."""
    
    name: str = "debtstack_company_structure"
    description: str = (
        "Get the complete corporate structure for a public company with debt at each entity. "
        "Shows parent-subsidiary hierarchy, entity types (holdco/finco/opco), "
        "and all debt instruments with their issuers and guarantors. "
        "Use this when you need to understand HOW a company's debt is structured across entities. "
        "Input should be a stock ticker symbol."
    )
    args_schema: Type[BaseModel] = CompanyStructureInput
    api_wrapper: DebtStackAPIWrapper
    
    def _run(
        self,
        ticker: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Use the tool."""
        result = self.api_wrapper.get_company_structure(ticker=ticker)
        return str(result)


class CompanyDebtInput(BaseModel):
    """Input for DebtStack Company Debt tool."""
    
    ticker: str = Field(
        description="Stock ticker symbol (e.g., 'AAPL', 'TSLA', 'RIG')"
    )


class DebtStackCompanyDebtTool(BaseTool):
    """Tool that queries DebtStack.ai for a company's debt instruments."""
    
    name: str = "debtstack_company_debt"
    description: str = (
        "Get all debt instruments for a public company with full details. "
        "Returns individual bonds, notes, credit facilities with amounts, rates, maturities, "
        "seniority, security, and guarantors. "
        "Use this when you need detailed information about WHAT debt a company has. "
        "Input should be a stock ticker symbol."
    )
    args_schema: Type[BaseModel] = CompanyDebtInput
    api_wrapper: DebtStackAPIWrapper
    
    def _run(
        self,
        ticker: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Use the tool."""
        result = self.api_wrapper.get_company_debt(ticker=ticker)
        return str(result)


class CompanyOverviewInput(BaseModel):
    """Input for DebtStack Company Overview tool."""
    
    ticker: str = Field(
        description="Stock ticker symbol (e.g., 'AAPL', 'TSLA', 'RIG')"
    )


class DebtStackCompanyOverviewTool(BaseTool):
    """Tool that queries DebtStack.ai for company overview and metrics."""
    
    name: str = "debtstack_company_overview"
    description: str = (
        "Get company overview with basic info and credit metrics. "
        "Returns company name, sector, entity count, total debt, "
        "structural subordination indicators, and data freshness. "
        "Use this for a high-level summary before diving into details. "
        "Input should be a stock ticker symbol."
    )
    args_schema: Type[BaseModel] = CompanyOverviewInput
    api_wrapper: DebtStackAPIWrapper
    
    def _run(
        self,
        ticker: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Use the tool."""
        result = self.api_wrapper.get_company_overview(ticker=ticker)
        return str(result)


class ListCompaniesInput(BaseModel):
    """Input for DebtStack List Companies tool."""
    pass  # No inputs needed


class DebtStackListCompaniesTool(BaseTool):
    """Tool that lists all companies available in DebtStack.ai."""
    
    name: str = "debtstack_list_companies"
    description: str = (
        "List all companies available in the DebtStack database. "
        "Returns ticker, name, and sector for each company. "
        "Use this to see what companies you can query or to search for specific companies. "
        "No input required."
    )
    args_schema: Type[BaseModel] = ListCompaniesInput
    api_wrapper: DebtStackAPIWrapper
    
    def _run(
        self,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """Use the tool."""
        result = self.api_wrapper.list_companies()
        return str(result)
```

**Unit Tests**:

```python
# tests/unit/test_debtstack_wrapper.py
import pytest
from unittest.mock import Mock, patch
from langchain_community.utilities.debtstack import DebtStackAPIWrapper

def test_debtstack_wrapper_init():
    """Test DebtStackAPIWrapper initialization."""
    wrapper = DebtStackAPIWrapper(debtstack_api_key="test-key")
    assert wrapper.debtstack_api_key == "test-key"
    assert wrapper.base_url == "https://api.debtstack.ai/v1"

@patch('httpx.get')
def test_get_company_structure(mock_get):
    """Test get_company_structure method."""
    mock_response = Mock()
    mock_response.json.return_value = {"company": {"ticker": "AAPL"}}
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response
    
    wrapper = DebtStackAPIWrapper(debtstack_api_key="test-key")
    result = wrapper.get_company_structure("AAPL")
    
    assert result == {"company": {"ticker": "AAPL"}}
    mock_get.assert_called_once()

# Similar tests for other methods...
```

**Deliverables**:
- [ ] All LangChain code written
- [ ] Unit tests with 90%+ coverage
- [ ] Integration tests
- [ ] Documentation strings complete
- [ ] Follows LangChain coding standards

---

#### 3.2 Submit PR to LangChain

**Priority**: CRITICAL  
**Timeline**: 2-4 weeks (includes review time)

**Steps**:

1. **Fork LangChain**:
```bash
git clone https://github.com/langchain-ai/langchain.git
cd langchain
git checkout -b add-debtstack-toolkit
```

2. **Add Code**:
- Place files in correct locations
- Follow their contribution guidelines
- Run all tests: `make test`
- Run linters: `make lint`, `make format`

3. **Write PR Description**:
```markdown
## Description

Adds DebtStack.ai toolkit for corporate debt and credit structure analysis.

DebtStack provides structured corporate debt data extracted from SEC filings:
- Corporate structures with parent-subsidiary hierarchies
- Individual debt instruments (bonds, notes, credit facilities)
- Guarantee relationships and intercreditor priorities
- Pre-computed, QA-verified data optimized for LLM consumption

## Motivation

Financial AI agents need accurate corporate debt data. Current options require:
- Ad-hoc extraction from SEC filings ($0.50+/company, slow, error-prone)
- Expensive enterprise data providers (Bloomberg, FactSet)

DebtStack provides a developer-friendly API with:
- Pre-computed, QA-verified data (<$0.03/company)
- Fast serving (<100ms response time)
- Individual debt instruments (not just totals)
- Support for complex structures (VIEs, JVs, partial ownership)

## New Features

- `DebtStackAPIWrapper`: API client wrapper
- `DebtStackToolkit`: Toolkit with 4 tools for corporate debt analysis
- `DebtStackCompanyStructureTool`: Get corporate structure with debt
- `DebtStackCompanyDebtTool`: Get all debt instruments
- `DebtStackCompanyOverviewTool`: Get company overview and metrics
- `DebtStackListCompaniesTool`: List available companies

## Testing

- ✅ Unit tests (90%+ coverage)
- ✅ Integration tests
- ✅ Type checking (mypy)
- ✅ Linting (ruff)
- ✅ Docs built successfully

## Example Usage

```python
from langchain_community.agent_toolkits import DebtStackToolkit
from langchain_community.utilities import DebtStackAPIWrapper
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain import hub

# Initialize
api_wrapper = DebtStackAPIWrapper(debtstack_api_key=os.getenv("DEBTSTACK_API_KEY"))
toolkit = DebtStackToolkit(api_wrapper=api_wrapper)
tools = toolkit.get_tools()

# Create agent
llm = ChatOpenAI(temperature=0, model="gpt-4")
prompt = hub.pull("hwchase17/openai-functions-agent")
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools)

# Use it
result = agent_executor.invoke({
    "input": "What is Tesla's debt structure? Are there any concerning covenants?"
})
```

## Documentation

Full docs at: https://docs.debtstack.ai/integrations/langchain

## Checklist

- [x] Unit tests added
- [x] Integration tests added
- [x] Documentation added
- [x] Type hints included
- [x] Linting passed
- [x] Examples provided
```

4. **Submit PR**:
- Create PR against `langchain-ai/langchain:master`
- Link to docs.debtstack.ai
- Be responsive to reviewers

5. **Engage with Reviewers**:
- Respond within 24 hours
- Make requested changes quickly
- Be professional and grateful
- Don't be defensive about feedback

**Expected Timeline**:
- Week 1: Submit PR
- Week 2-3: Review, requested changes
- Week 4: Final approval and merge

**Deliverables**:
- [ ] PR submitted to LangChain
- [ ] All reviewer feedback addressed
- [ ] PR approved and merged
- [ ] Integration live in `langchain-community`

---

#### 3.3 Documentation for LangChain Integration

**Priority**: HIGH  
**Timeline**: 2 days

Create comprehensive docs at `docs.debtstack.ai/integrations/langchain`:

```markdown
# LangChain Integration

DebtStack.ai is integrated into LangChain via the `DebtStackToolkit`.

## Installation

```bash
pip install langchain-community debtstack-ai
```

## Setup

Set your API key:

```bash
export DEBTSTACK_API_KEY="your-api-key"
```

Or in Python:

```python
import os
os.environ["DEBTSTACK_API_KEY"] = "your-api-key"
```

Get your free API key at [debtstack.ai/signup](https://debtstack.ai/signup).

## Quick Start

```python
from langchain_community.agent_toolkits import DebtStackToolkit
from langchain_community.utilities import DebtStackAPIWrapper
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain import hub

# Initialize toolkit
api_wrapper = DebtStackAPIWrapper()  # Reads from DEBTSTACK_API_KEY env var
toolkit = DebtStackToolkit(api_wrapper=api_wrapper)
tools = toolkit.get_tools()

# Create agent
llm = ChatOpenAI(temperature=0, model="gpt-4")
prompt = hub.pull("hwchase17/openai-functions-agent")
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Use it
result = agent_executor.invoke({
    "input": "Analyze Tesla's credit structure. What are the key risks?"
})
print(result["output"])
```

## Available Tools

The `DebtStackToolkit` provides 4 tools:

### 1. debtstack_company_structure

Get the complete corporate structure with debt at each entity.

**Use when**: You need to understand HOW debt is structured across entities.

**Example**:
```python
result = agent_executor.invoke({
    "input": "Show me Transocean's corporate structure with debt"
})
```

### 2. debtstack_company_debt

Get all debt instruments with full details.

**Use when**: You need detailed information about WHAT debt a company has.

**Example**:
```python
result = agent_executor.invoke({
    "input": "List all of Apple's debt instruments with rates and maturities"
})
```

### 3. debtstack_company_overview

Get company overview with metrics.

**Use when**: You want a high-level summary first.

**Example**:
```python
result = agent_executor.invoke({
    "input": "Give me an overview of Tesla's credit profile"
})
```

### 4. debtstack_list_companies

List all available companies.

**Use when**: You want to see what data is available or search for companies.

**Example**:
```python
result = agent_executor.invoke({
    "input": "What companies do you have data for?"
})
```

## Use Case Examples

### Credit Analysis Agent

Build an agent that analyzes credit structures:

```python
# [Full example code...]
```

### Distressed Debt Screener

Screen for companies with high leverage:

```python
# [Full example code...]
```

### Covenant Monitor

Monitor for covenant breaches:

```python
# [Full example code...]
```

## API Reference

- [DebtStackAPIWrapper](https://api.python.langchain.com/en/latest/utilities/langchain_community.utilities.debtstack.DebtStackAPIWrapper.html)
- [DebtStackToolkit](https://api.python.langchain.com/en/latest/agent_toolkits/langchain_community.agent_toolkits.debtstack.toolkit.DebtStackToolkit.html)

## Support

- Discord: [discord.gg/debtstack-ai](https://discord.gg/debtstack-ai)
- Issues: [GitHub](https://github.com/langchain-ai/langchain/issues)
- Email: hello@debtstack.ai
```

**Deliverables**:
- [ ] LangChain integration docs complete
- [ ] Code examples tested and working
- [ ] Published at docs.debtstack.ai/integrations/langchain

---

### Week 9-10: MCP Server (Claude Integration)

#### 4.1 Build MCP Server

**Priority**: CRITICAL  
**Timeline**: 2 weeks

**Tech Stack**:
- Language: Python (using official MCP SDK)
- Hosting: Railway or similar
- Domain: https://mcp.debtstack.ai
- Auth: OAuth 2.1 + API Key

**Implementation**:

```python
# src/server.py
"""DebtStack.ai MCP Server"""

import os
import logging
from typing import Any
from mcp.server import Server
from mcp.types import Tool, TextContent
from debtstack import DebtStackClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MCP server
app = Server("debtstack-ai")

# Initialize DebtStack client
async def get_client() -> DebtStackClient:
    """Get DebtStack client with API key from environment."""
    api_key = os.getenv("DEBTSTACK_API_KEY")
    if not api_key:
        raise ValueError("DEBTSTACK_API_KEY environment variable not set")
    return DebtStackClient(api_key=api_key)


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_company_structure",
            description=(
                "Get complete corporate structure for a company with debt at each entity. "
                "Shows parent-subsidiary hierarchy, entity types (holdco/finco/opco), "
                "and all debt instruments with their issuers and guarantors. "
                "Use when you need to understand HOW a company's debt is structured."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL, TSLA, RIG)"
                    }
                },
                "required": ["ticker"]
            }
        ),
        Tool(
            name="get_company_debt",
            description=(
                "Get all debt instruments for a company with full details. "
                "Returns individual bonds, notes, credit facilities with amounts, rates, "
                "maturities, seniority, security, and guarantors. "
                "Use when you need detailed information about WHAT debt a company has."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL, TSLA, RIG)"
                    }
                },
                "required": ["ticker"]
            }
        ),
        Tool(
            name="get_company_overview",
            description=(
                "Get company overview with basic info and credit metrics. "
                "Returns company name, sector, entity count, total debt, "
                "structural subordination indicators. "
                "Use for a high-level summary before diving into details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., AAPL, TSLA, RIG)"
                    }
                },
                "required": ["ticker"]
            }
        ),
        Tool(
            name="list_companies",
            description=(
                "List all companies available in the DebtStack database. "
                "Returns ticker, name, and sector for each company. "
                "Use to see what companies you can query."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="search_by_sector",
            description=(
                "Search for companies by sector. "
                "Available sectors: Technology, Telecommunications, Energy, Healthcare, "
                "Financial Services, Consumer, Industrials, Real Estate, and more. "
                "Use to find companies in a specific industry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sector": {
                        "type": "string",
                        "description": "Industry sector (e.g., Technology, Energy, Telecommunications)"
                    }
                },
                "required": ["sector"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Call a tool."""
    
    try:
        client = await get_client()
        
        if name == "get_company_structure":
            ticker = arguments["ticker"].upper()
            logger.info(f"Getting structure for {ticker}")
            result = await client.get_company_structure(ticker)
            return [TextContent(
                type="text",
                text=f"Corporate structure for {ticker}:\n\n{format_structure(result)}"
            )]
        
        elif name == "get_company_debt":
            ticker = arguments["ticker"].upper()
            logger.info(f"Getting debt for {ticker}")
            result = await client.get_company_debt(ticker)
            return [TextContent(
                type="text",
                text=f"Debt instruments for {ticker}:\n\n{format_debt(result)}"
            )]
        
        elif name == "get_company_overview":
            ticker = arguments["ticker"].upper()
            logger.info(f"Getting overview for {ticker}")
            result = await client.get_company_overview(ticker)
            return [TextContent(
                type="text",
                text=f"Overview for {ticker}:\n\n{format_overview(result)}"
            )]
        
        elif name == "list_companies":
            logger.info("Listing companies")
            result = await client.list_companies()
            return [TextContent(
                type="text",
                text=f"Available companies:\n\n{format_company_list(result)}"
            )]
        
        elif name == "search_by_sector":
            sector = arguments["sector"]
            logger.info(f"Searching by sector: {sector}")
            companies = await client.list_companies()
            filtered = [c for c in companies if c.get("sector", "").lower() == sector.lower()]
            return [TextContent(
                type="text",
                text=f"Companies in {sector}:\n\n{format_company_list(filtered)}"
            )]
        
        else:
            raise ValueError(f"Unknown tool: {name}")
            
    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


def format_structure(data: dict) -> str:
    """Format structure data for display."""
    # Implementation to format hierarchical structure nicely
    # ...
    return str(data)


def format_debt(data: dict) -> str:
    """Format debt data for display."""
    # Implementation to format debt instruments nicely
    # ...
    return str(data)


def format_overview(data: dict) -> str:
    """Format overview data for display."""
    # Implementation to format overview nicely
    # ...
    return str(data)


def format_company_list(companies: list) -> str:
    """Format company list for display."""
    # Implementation to format company list nicely
    # ...
    return "\n".join([f"- {c['ticker']}: {c['name']}" for c in companies])


if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server
    
    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
    
    asyncio.run(main())
```

**README.md for MCP Server**:

```markdown
# DebtStack.ai MCP Server

Model Context Protocol server for DebtStack.ai credit data API.

## What is MCP?

[Model Context Protocol (MCP)](https://modelcontextprotocol.io) is an open standard 
developed by Anthropic that enables AI assistants to securely connect to data sources.

## Installation

### For Claude Desktop (OAuth)

1. Open Claude Desktop
2. Go to Settings → Integrations
3. Click "Add Integration"
4. Enter: `https://mcp.debtstack.ai/mcp`
5. Authenticate with your DebtStack account

### For Cursor/Windsurf/VS Code (API Key)

Add to your MCP config file (`~/.cursor/mcp.json` or similar):

```json
{
  "mcpServers": {
    "debtstack-ai": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.debtstack.ai/api"],
      "headers": {
        "X-API-KEY": "your-api-key-here"
      }
    }
  }
}
```

Get your API key at [debtstack.ai/signup](https://debtstack.ai/signup).

### For Claude Code

```bash
claude mcp add --transport http debtstack-ai https://mcp.debtstack.ai/api
```

## Usage

Once connected, you can ask Claude:

**Corporate Structure**:
- "What is Tesla's corporate structure?"
- "Show me the entity hierarchy for Transocean"

**Debt Analysis**:
- "What debt does Apple have?"
- "List all of Microsoft's secured debt"

**Credit Risk**:
- "Analyze the credit risk for Altice USA"
- "Which companies have the most debt?"

**Sector Analysis**:
- "Show me all telecom companies in the database"
- "What's the debt profile of energy companies?"

## Available Tools

| Tool | Description |
|------|-------------|
| `get_company_structure` | Corporate hierarchy with debt |
| `get_company_debt` | All debt instruments |
| `get_company_overview` | Company info and metrics |
| `list_companies` | Available companies |
| `search_by_sector` | Find companies by sector |

## Development

### Local Setup

```bash
# Clone repo
git clone https://github.com/debtstack-ai/mcp-server
cd mcp-server

# Install dependencies
pip install -e .

# Set API key
export DEBTSTACK_API_KEY="your-key"

# Run locally
python src/server.py
```

### Testing with MCP Inspector

```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector python src/server.py
```

## Support

- Discord: [discord.gg/debtstack-ai](https://discord.gg/debtstack-ai)
- Issues: [GitHub Issues](https://github.com/debtstack-ai/mcp-server/issues)
- Email: hello@debtstack.ai

## License

MIT
```

**Deliverables**:
- [ ] MCP server code complete
- [ ] Hosted at https://mcp.debtstack.ai
- [ ] OAuth 2.1 authentication working
- [ ] API key authentication working
- [ ] README with setup instructions
- [ ] Published to GitHub: debtstack-ai/mcp-server
- [ ] Tested with Claude Desktop

---

#### 4.2 Get Listed on Anthropic's Directory

**Priority**: HIGH  
**Timeline**: 1-2 weeks (depends on Anthropic review)

**Steps**:

1. **Submit to Anthropic**:

Email: `mcp@anthropic.com`

Subject: `MCP Server Submission: DebtStack.ai`

Body:
```
Hi Anthropic team,

I've built an MCP server for DebtStack.ai, a credit data API for financial AI agents.

**Server Details**:
- Server URL: https://mcp.debtstack.ai/mcp
- GitHub: https://github.com/debtstack-ai/mcp-server
- Docs: https://docs.debtstack.ai/integrations/mcp
- Website: https://debtstack.ai

**Description**:
DebtStack provides structured corporate debt data extracted from SEC filings, 
optimized for AI agents. Our MCP server enables Claude to:

- Analyze corporate debt structures
- Compare credit profiles across companies
- Monitor covenants and guarantees
- Search companies by sector

**Tools Provided**:
- get_company_structure: Corporate hierarchy with debt
- get_company_debt: All debt instruments with details
- get_company_overview: Company metrics
- list_companies: Available companies
- search_by_sector: Find companies by industry

**Data Coverage**:
38 companies across Technology, Energy, Telecommunications, Healthcare, 
Financial Services, and other sectors. Data extracted from SEC 10-K/10-Q filings 
with 85%+ QA verification.

**Why this is valuable**:
Financial professionals and analysts need accurate credit data. Traditional options 
are expensive (Bloomberg, FactSet) or require manual extraction. DebtStack provides 
pre-computed, QA-verified data via API.

Would love to be listed in the MCP registry!

Best,
Sunil
Founder, DebtStack.ai
```

2. **Submit to PulseMCP**:
- Visit: https://www.pulsemcp.com/submit
- Fill out server submission form
- Provide server details, description, tags

3. **Add to GitHub MCP Registry**:
- Fork: https://github.com/modelcontextprotocol/servers
- Add entry to community servers list
- Submit PR

**Deliverables**:
- [ ] Submitted to Anthropic for review
- [ ] Listed on claude.ai/partners/mcp (pending approval)
- [ ] Listed on PulseMCP
- [ ] Added to GitHub MCP registry
- [ ] Listed on other MCP directories

---

### Week 11-12: LlamaIndex Integration

#### 5.1 Build LlamaIndex Tools

**Priority**: MEDIUM  
**Timeline**: 1 week

**Implementation**:

```python
# debtstack/llamaindex.py (part of debtstack-ai package)
"""LlamaIndex tools for DebtStack.ai"""

from typing import Optional
from llama_index.core.tools import FunctionTool
from debtstack import DebtStackClient

def create_debtstack_tools(api_key: str) -> list[FunctionTool]:
    """Create all DebtStack tools for LlamaIndex.
    
    Args:
        api_key: Your DebtStack API key
        
    Returns:
        List of FunctionTool objects
        
    Example:
        >>> from debtstack.llamaindex import create_debtstack_tools
        >>> tools = create_debtstack_tools(api_key="your-key")
        >>> from llama_index.core.agent import ReActAgent
        >>> agent = ReActAgent.from_tools(tools)
    """
    client = DebtStackClient(api_key=api_key)
    
    async def get_company_structure(ticker: str) -> str:
        """Get complete corporate structure for a company with debt at each entity.
        
        Args:
            ticker: Stock ticker symbol (e.g., 'AAPL', 'TSLA')
        """
        result = await client.get_company_structure(ticker)
        return str(result)
    
    async def get_company_debt(ticker: str) -> str:
        """Get all debt instruments for a company with full details.
        
        Args:
            ticker: Stock ticker symbol
        """
        result = await client.get_company_debt(ticker)
        return str(result)
    
    async def get_company_overview(ticker: str) -> str:
        """Get company overview with metrics.
        
        Args:
            ticker: Stock ticker symbol
        """
        result = await client.get_company_overview(ticker)
        return str(result)
    
    async def list_companies() -> str:
        """List all available companies in database."""
        result = await client.list_companies()
        return str(result)
    
    return [
        FunctionTool.from_defaults(
            fn=get_company_structure,
            name="get_company_structure",
            description="Get corporate structure with debt at each entity"
        ),
        FunctionTool.from_defaults(
            fn=get_company_debt,
            name="get_company_debt",
            description="Get all debt instruments with full details"
        ),
        FunctionTool.from_defaults(
            fn=get_company_overview,
            name="get_company_overview",
            description="Get company overview and metrics"
        ),
        FunctionTool.from_defaults(
            fn=list_companies,
            name="list_companies",
            description="List all available companies"
        ),
    ]
```

**Usage Example**:

```python
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI
from debtstack.llamaindex import create_debtstack_tools

# Create tools
tools = create_debtstack_tools(api_key="your-key")

# Create agent
agent = ReActAgent.from_tools(
    tools=tools,
    llm=OpenAI(model="gpt-4"),
    verbose=True
)

# Use agent
response = agent.chat("What is Apple's debt structure?")
print(response)
```

**Documentation**:

Create `docs.debtstack.ai/integrations/llamaindex`:

```markdown
# LlamaIndex Integration

DebtStack.ai provides tools for LlamaIndex agents via the `create_debtstack_tools()` function.

## Installation

```bash
pip install debtstack-ai llama-index
```

## Quick Start

```python
from llama_index.core.agent import ReActAgent
from llama_index.llms.openai import OpenAI
from debtstack.llamaindex import create_debtstack_tools

# Create tools
tools = create_debtstack_tools(api_key="your-api-key")

# Create agent
agent = ReActAgent.from_tools(
    tools=tools,
    llm=OpenAI(model="gpt-4"),
    verbose=True
)

# Use agent
response = agent.chat("Analyze Tesla's credit structure")
print(response)
```

## Available Tools

[Tool descriptions...]

## Examples

[Full examples...]
```

**Deliverables**:
- [ ] LlamaIndex tools implemented
- [ ] Published as part of debtstack-ai package
- [ ] Documentation written
- [ ] Example projects created

---

## Phase 3: No-Code Platforms (Weeks 13-16)

### Week 13-14: n8n Integration

#### 6.1 Create n8n Workflow Templates

**Priority**: MEDIUM  
**Timeline**: 1 week

**Template 1: "Corporate Credit Analysis Workflow"**

```yaml
name: Analyze Company Credit Structure
description: Automatically analyze corporate debt when added to watchlist
nodes:
  - type: trigger
    name: Manual Trigger
    
  - type: function
    name: Set Ticker
    code: return [{json: {ticker: 'TSLA'}}];
    
  - type: http-request
    name: Get Debt Structure
    endpoint: https://api.debtstack.ai/v1/companies/{{$json.ticker}}/debt
    method: GET
    headers:
      X-API-KEY: "={{$credentials.debtstackApi.apiKey}}"
    
  - type: code
    name: Analyze Risk
    code: |
      const debt = $input.item.json;
      
      // Calculate risk metrics
      const totalDebt = debt.summary.total_outstanding;
      const securedPct = debt.summary.secured_count / debt.summary.total_count;
      
      // Flag high-risk covenants
      const highRisk = debt.debt_instruments.filter(d => 
        d.covenants && d.covenants.some(c => 
          c.type === 'leverage' && c.threshold > 5.0
        )
      );
      
      return {
        company: debt.company.name,
        ticker: debt.company.ticker,
        total_debt: (totalDebt / 100).toFixed(2), // Convert cents to dollars
        secured_percentage: (securedPct * 100).toFixed(1),
        high_risk_covenants: highRisk.length,
        alert: highRisk.length > 0 || securedPct < 0.5
      };
  
  - type: slack
    name: Send Alert
    condition: "={{$json.alert}}"
    message: |
      ⚠️ Credit Risk Alert: {{$json.company}}
      
      Total Debt: ${{$json.total_debt}}M
      Secured: {{$json.secured_percentage}}%
      High-Risk Covenants: {{$json.high_risk_covenants}}
```

**Template 2: "Covenant Monitoring Dashboard"**

**Template 3: "Distressed Debt Screener"**

**Publication**:
1. Create workflows in n8n Cloud
2. Click "Share workflow"
3. Submit to n8n community templates
4. Add to docs.debtstack.ai/integrations/n8n

**Deliverables**:
- [ ] 3+ n8n templates created and tested
- [ ] Published to n8n community
- [ ] Documentation on docs.debtstack.ai
- [ ] Video tutorial (optional)

---

#### 6.2 Dify Plugin

**Priority**: LOW  
**Timeline**: 3 days

**Plugin Configuration**:

```yaml
# dify-plugin.yaml
name: debtstack-ai
version: 1.0.0
author: DebtStack.ai
description: Corporate credit data for AI agents
icon: https://debtstack.ai/icon.png
category: finance

tools:
  - name: get_company_structure
    description: Get corporate structure with debt
    method: GET
    endpoint: /companies/{ticker}/structure
    parameters:
      - name: ticker
        type: string
        required: true
        
  - name: get_company_debt
    description: Get all debt instruments
    method: GET
    endpoint: /companies/{ticker}/debt
    parameters:
      - name: ticker
        type: string
        required: true
```

**Deliverables**:
- [ ] Dify plugin created
- [ ] Published to Dify marketplace
- [ ] Listed at marketplace.dify.ai

---

### Week 15-16: Example Projects

#### 7.1 Build Showcase Projects

**Priority**: HIGH  
**Timeline**: 2 weeks

**Project 1: Credit Analysis Agent**

Full-featured agent that:
- Analyzes corporate structures
- Identifies credit risks
- Compares companies
- Generates reports

**Project 2: Distressed Debt Screener**

Screens for:
- High leverage ratios
- Upcoming maturities
- Weak security positions
- Covenant violations

**Project 3: Covenant Monitor**

Monitors:
- Financial covenants
- Breach thresholds
- Sends alerts

**Each Project Needs**:
- [ ] Complete working code
- [ ] README with setup
- [ ] requirements.txt
- [ ] .env.example
- [ ] Demo video or GIF
- [ ] Deployed demo (optional)

**Deliverables**:
- [ ] 3 complete example projects
- [ ] All on GitHub: debtstack-ai/examples
- [ ] Featured on website
- [ ] Video demos

---

## Phase 4: Content & Community (Ongoing)

### Content Strategy

**Blog Posts** (1 per month):

- Month 1: "Introducing DebtStack.ai: Credit Infrastructure for AI Agents"
- Month 2: "Building a Credit Analysis Agent with LangChain in 10 Minutes"
- Month 3: "How We Extract Corporate Debt Structures from SEC Filings"
- Month 4: "Monitoring Debt Covenants with AI: A Complete Guide"
- Month 5: "The Credit Agent Developer's Handbook"
- Month 6: "Case Study: How [Customer] Automated Credit Analysis"

**Video Tutorials**:

1. "Get Started with DebtStack in 5 Minutes"
2. "Build a Credit Agent with LangChain"
3. "Analyzing Corporate Debt with Claude"

**Community Engagement**:

- **Discord**: Daily activity, weekly show-and-tell
- **GitHub**: 24hr response to issues
- **Twitter/X**: Daily posts about credit data, AI agents

---

## Success Metrics

### Track Weekly:

```python
distribution_metrics = {
    "integrations": {
        "langchain_merged": bool,
        "mcp_listed": bool,
        "llamaindex_merged": bool,
        "n8n_templates": int,
        "dify_plugin": bool
    },
    "community": {
        "discord_members": int,
        "github_stars": int,
        "pypi_downloads": int
    },
    "usage": {
        "api_signups": int,
        "active_users_30d": int,
        "api_calls_30d": int,
        "paid_conversions": int
    }
}
```

### Target Milestones:

**Month 2**:
- ✓ LangChain PR submitted
- ✓ MCP server live
- ✓ 100 API signups
- ✓ 50 Discord members

**Month 4**:
- ✓ LangChain merged
- ✓ MCP listed on Anthropic
- ✓ 500 API signups
- ✓ 200 Discord members

**Month 6**:
- ✓ 1,000 API signups
- ✓ 500 active users
- ✓ 1,000 Discord members
- ✓ 50+ community projects
- ✓ $10K MRR

---

## Resource Requirements

### Time Investment:

- Weeks 1-4: 40 hrs/week (foundation)
- Weeks 5-12: 30 hrs/week (integrations)
- Weeks 13+: 20 hrs/week (maintenance + content)

### Optional Contractors:

- Website designer: $2-5K
- Video editor: $500/video
- Technical writer: $100/hr
- Community manager: $2K/month (Month 6+)

### Infrastructure Costs:

~$100/month total:
- Vercel hosting: $20
- Domains: $20
- MCP hosting: $50
- Analytics: $0 (free tier)

---

## Week 1 Action Items

**Day 1-2**:
- [ ] Finalize OpenAPI spec
- [ ] Set up GitHub org
- [ ] Register domains

**Day 3-5**:
- [ ] Build Python SDK foundation
- [ ] Set up Discord
- [ ] Create landing page wireframes

**Day 6-7**:
- [ ] Deploy basic docs site
- [ ] Implement free tier
- [ ] Draft first blog post

**Week 2 Kickoff**:
- [ ] Launch Discord
- [ ] Publish SDK to PyPI
- [ ] Start LangChain code

---

## Critical Success Factors

1. **Speed**: LangChain + MCP within 8 weeks
2. **Quality**: Clean code, good docs, fast API
3. **Community**: Active Discord, responsive support
4. **Content**: Regular blog posts, tutorials, examples
5. **Distribution**: Get into every AI agent framework

**The key insight**: FinancialDatasets.ai succeeded by embedding into frameworks. We do the same for credit.

---

*This playbook is confidential. Do not share publicly.*

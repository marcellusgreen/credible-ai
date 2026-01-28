"""
Base Extractor
==============

Abstract base for LLM-based extraction services.

Provides the common pattern:
    1. Source content (from DB or filings)
    2. Build prompt
    3. Call LLM
    4. Parse response
    5. Save to DB

Each concrete extractor implements:
    - get_prompt(): Build the extraction prompt
    - parse_result(): Parse LLM response into domain objects
    - save_result(): Persist to database

USAGE
-----
    class GuaranteeExtractor(BaseExtractor):
        async def get_prompt(self, context: ExtractionContext) -> str:
            return f"Extract guarantees from {context.content}..."

        async def parse_result(self, response: LLMResponse, context: ExtractionContext) -> list:
            return response.data.get('guarantees', [])

        async def save_result(self, items: list, context: ExtractionContext) -> int:
            # Save to DB, return count
            ...
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_utils import (
    get_gemini_model,
    call_gemini,
    LLMResponse,
)


@dataclass
class ExtractionContext:
    """
    Context passed through extraction pipeline.

    Contains all data needed for extraction:
    - Company info
    - Source content
    - Existing entities/instruments for reference
    - Database session for queries
    """
    session: AsyncSession
    company_id: UUID
    ticker: str

    # Source content
    content: str = ""
    filings: dict = field(default_factory=dict)

    # Reference data (populated by extractor as needed)
    entities: list = field(default_factory=list)
    instruments: list = field(default_factory=list)
    documents: list = field(default_factory=list)

    # Metadata
    metadata: dict = field(default_factory=dict)


class BaseExtractor(ABC):
    """
    Abstract base for LLM extraction services.

    Subclasses implement:
    - get_prompt(): Build extraction prompt
    - parse_result(): Parse LLM response
    - save_result(): Save to database

    Optionally override:
    - load_context(): Load reference data
    - get_model(): Change LLM model
    """

    def __init__(self, model_name: str = "gemini-2.0-flash"):
        self.model_name = model_name
        self._model = None

    def get_model(self):
        """Get or create LLM model."""
        if self._model is None:
            self._model = get_gemini_model(self.model_name)
        return self._model

    async def load_context(self, context: ExtractionContext) -> ExtractionContext:
        """
        Load reference data into context.

        Override to load entities, instruments, documents, etc.
        Default implementation does nothing.
        """
        return context

    @abstractmethod
    async def get_prompt(self, context: ExtractionContext) -> str:
        """
        Build the extraction prompt.

        PARAMETERS
        ----------
        context : ExtractionContext
            Context with company info and content

        RETURNS
        -------
        str
            Prompt to send to LLM
        """
        pass

    @abstractmethod
    async def parse_result(
        self,
        response: LLMResponse,
        context: ExtractionContext
    ) -> list[Any]:
        """
        Parse LLM response into domain objects.

        PARAMETERS
        ----------
        response : LLMResponse
            LLM response with text and parsed JSON
        context : ExtractionContext
            Context for reference data lookups

        RETURNS
        -------
        list
            Parsed items ready for saving
        """
        pass

    @abstractmethod
    async def save_result(
        self,
        items: list[Any],
        context: ExtractionContext
    ) -> int:
        """
        Save parsed items to database.

        PARAMETERS
        ----------
        items : list
            Parsed items from parse_result()
        context : ExtractionContext
            Context with DB session

        RETURNS
        -------
        int
            Number of items saved
        """
        pass

    async def extract(
        self,
        session: AsyncSession,
        company_id: UUID,
        ticker: str,
        content: str = "",
        filings: dict = None,
    ) -> int:
        """
        Run the full extraction pipeline.

        STEPS
        -----
        1. Create context
        2. Load reference data
        3. Build prompt
        4. Call LLM
        5. Parse response
        6. Save to DB

        PARAMETERS
        ----------
        session : AsyncSession
            Database session
        company_id : UUID
            Company ID
        ticker : str
            Stock ticker
        content : str
            Pre-loaded content (optional)
        filings : dict
            Raw filings dict (optional)

        RETURNS
        -------
        int
            Number of items extracted and saved
        """
        model = self.get_model()
        if not model:
            return 0

        # Create context
        context = ExtractionContext(
            session=session,
            company_id=company_id,
            ticker=ticker,
            content=content,
            filings=filings or {},
        )

        # Load reference data
        context = await self.load_context(context)

        # Build prompt
        prompt = await self.get_prompt(context)
        if not prompt:
            return 0

        # Call LLM
        try:
            response = call_gemini(model, prompt, parse_json=True)
        except Exception as e:
            print(f"      LLM call error: {e}")
            return 0

        if not response.data:
            return 0

        # Parse and save
        try:
            items = await self.parse_result(response, context)
            count = await self.save_result(items, context)
            return count
        except Exception as e:
            print(f"      Extraction error: {e}")
            return 0

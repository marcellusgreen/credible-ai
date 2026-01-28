"""
LLM Utilities
=============

Stateless utilities for working with LLMs in extraction pipelines.

CONTENTS
--------
- LLM client initialization (Gemini, Claude)
- Response parsing with retry logic
- Cost tracking

USAGE
-----
    from app.services.llm_utils import (
        get_gemini_model,
        get_claude_client,
        call_llm,
        LLMProvider,
    )

    # Get a configured Gemini model
    model = get_gemini_model("gemini-2.0-flash")

    # Call LLM with automatic JSON parsing
    result = call_llm(model, prompt, parse_json=True)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Union

from app.core.config import get_settings
from app.services.utils import parse_json_robust


class LLMProvider(Enum):
    """Supported LLM providers."""
    GEMINI_FLASH = "gemini-2.0-flash"
    GEMINI_PRO = "gemini-2.5-pro"
    CLAUDE_SONNET = "claude-sonnet-4-20250514"
    CLAUDE_OPUS = "claude-opus-4-1-20250620"
    DEEPSEEK = "deepseek-chat"


@dataclass
class LLMResponse:
    """Standardized LLM response."""
    text: str
    data: Optional[dict] = None  # Parsed JSON if requested
    input_tokens: int = 0
    output_tokens: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None


def get_gemini_model(model_name: str = "gemini-2.0-flash"):
    """
    Get a configured Gemini model.

    STEPS
    -----
    1. Load API key from settings
    2. Configure genai client
    3. Return GenerativeModel instance

    PARAMETERS
    ----------
    model_name : str
        Model to use (default: gemini-2.0-flash)

    RETURNS
    -------
    GenerativeModel or None
        Configured model, or None if API key not available
    """
    import google.generativeai as genai

    settings = get_settings()
    if not settings.gemini_api_key:
        return None

    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(model_name)


def get_claude_client():
    """
    Get a configured Anthropic client.

    RETURNS
    -------
    Anthropic or None
        Configured client, or None if API key not available
    """
    import anthropic

    settings = get_settings()
    if not settings.anthropic_api_key:
        return None

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def call_gemini(
    model,
    prompt: str,
    parse_json: bool = True,
) -> LLMResponse:
    """
    Call Gemini model and optionally parse JSON response.

    STEPS
    -----
    1. Call model.generate_content()
    2. Extract text from response
    3. Parse JSON if requested
    4. Return standardized LLMResponse

    PARAMETERS
    ----------
    model : GenerativeModel
        Configured Gemini model
    prompt : str
        Prompt to send
    parse_json : bool
        Whether to parse response as JSON (default: True)

    RETURNS
    -------
    LLMResponse
        Standardized response with text and optional parsed data
    """
    response = model.generate_content(prompt)
    text = response.text

    data = None
    if parse_json:
        try:
            data = parse_json_robust(text)
        except ValueError:
            pass  # Leave data as None if parsing fails

    # Extract token counts if available
    input_tokens = 0
    output_tokens = 0
    if hasattr(response, 'usage_metadata'):
        input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
        output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)

    return LLMResponse(
        text=text,
        data=data,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider="gemini",
        model=model.model_name if hasattr(model, 'model_name') else "gemini",
    )


def call_claude(
    client,
    prompt: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    parse_json: bool = True,
) -> LLMResponse:
    """
    Call Claude model and optionally parse JSON response.

    STEPS
    -----
    1. Call client.messages.create()
    2. Extract text from response
    3. Parse JSON if requested
    4. Return standardized LLMResponse

    PARAMETERS
    ----------
    client : Anthropic
        Configured Anthropic client
    prompt : str
        Prompt to send
    model : str
        Model to use (default: claude-sonnet-4-20250514)
    max_tokens : int
        Maximum response tokens (default: 4096)
    parse_json : bool
        Whether to parse response as JSON (default: True)

    RETURNS
    -------
    LLMResponse
        Standardized response with text and optional parsed data
    """
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text

    data = None
    if parse_json:
        try:
            data = parse_json_robust(text)
        except ValueError:
            pass

    return LLMResponse(
        text=text,
        data=data,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        provider="anthropic",
        model=model,
    )


# Cost per 1M tokens (in USD)
COST_PER_MILLION = {
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-1-20250620": {"input": 15.00, "output": 75.00},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
}


def calculate_cost(response: LLMResponse) -> float:
    """
    Calculate cost of an LLM call.

    PARAMETERS
    ----------
    response : LLMResponse
        Response with token counts and model info

    RETURNS
    -------
    float
        Cost in USD
    """
    model = response.model or ""
    costs = COST_PER_MILLION.get(model, {"input": 0, "output": 0})

    input_cost = (response.input_tokens / 1_000_000) * costs["input"]
    output_cost = (response.output_tokens / 1_000_000) * costs["output"]

    return input_cost + output_cost

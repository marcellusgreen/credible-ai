"""
Unit tests for BatchOperation and BatchRequest Pydantic model validation.

Tests the request validation rules enforced by the batch endpoint models.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pydantic import ValidationError
from app.api.primitives import BatchOperation, BatchRequest


class TestBatchOperationModel:
    """Tests for BatchOperation Pydantic model."""

    @pytest.mark.unit
    def test_valid_operation(self):
        """BatchOperation with valid primitive and params succeeds."""
        op = BatchOperation(primitive="search.companies", params={"ticker": "AAPL"})
        assert op.primitive == "search.companies"
        assert op.params == {"ticker": "AAPL"}

    @pytest.mark.unit
    def test_missing_primitive_raises(self):
        """BatchOperation without primitive raises ValidationError."""
        with pytest.raises(ValidationError):
            BatchOperation(params={})

    @pytest.mark.unit
    def test_empty_params_defaults_to_dict(self):
        """BatchOperation without params defaults to empty dict."""
        op = BatchOperation(primitive="search.companies")
        assert op.params == {}


class TestBatchRequestModel:
    """Tests for BatchRequest Pydantic model."""

    @pytest.mark.unit
    def test_valid_request_single(self):
        """BatchRequest with 1 operation succeeds."""
        req = BatchRequest(operations=[
            BatchOperation(primitive="search.companies", params={"ticker": "AAPL"})
        ])
        assert len(req.operations) == 1

    @pytest.mark.unit
    def test_empty_operations_raises(self):
        """BatchRequest with empty operations list raises ValidationError."""
        with pytest.raises(ValidationError):
            BatchRequest(operations=[])

    @pytest.mark.unit
    def test_eleven_operations_raises(self):
        """BatchRequest with 11 operations raises ValidationError (max is 10)."""
        ops = [BatchOperation(primitive="search.companies") for _ in range(11)]
        with pytest.raises(ValidationError):
            BatchRequest(operations=ops)

    @pytest.mark.unit
    def test_ten_operations_succeeds(self):
        """BatchRequest with exactly 10 operations succeeds."""
        ops = [BatchOperation(primitive="search.companies") for _ in range(10)]
        req = BatchRequest(operations=ops)
        assert len(req.operations) == 10

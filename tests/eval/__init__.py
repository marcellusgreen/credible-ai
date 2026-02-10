"""
DebtStack.ai Evaluation Framework

Comprehensive eval suite that validates API correctness against source documents,
tracks accuracy scores, and detects regressions.

Structure:
- conftest.py: Fixtures for API client, DB session, ground truth data
- ground_truth.py: Load/manage ground truth datasets
- scoring.py: Accuracy calculation, regression detection
- test_*.py: Individual primitive test modules (~57 total use cases)
- baseline/: Stored baseline results for regression detection
"""

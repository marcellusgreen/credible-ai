"""
Scoring and Regression Detection

Provides accuracy calculation, test result tracking, and regression detection
by comparing current results against stored baselines.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class EvalResult:
    """Result of a single eval test case."""
    test_id: str  # e.g., "companies.leverage_accuracy.CHTR"
    passed: bool
    expected: Any
    actual: Any
    tolerance: float = 0.0  # e.g., 0.05 for 5%
    error_pct: Optional[float] = None  # Actual deviation
    ground_truth_source: str = ""  # e.g., "company_financials.ttm_ebitda"
    message: str = ""

    def to_dict(self) -> dict:
        """Convert to serializable dict."""
        return {
            "test_id": self.test_id,
            "passed": self.passed,
            "expected": str(self.expected) if self.expected is not None else None,
            "actual": str(self.actual) if self.actual is not None else None,
            "tolerance": self.tolerance,
            "error_pct": self.error_pct,
            "ground_truth_source": self.ground_truth_source,
            "message": self.message,
        }


@dataclass
class PrimitiveScore:
    """Aggregate score for a primitive endpoint."""
    primitive: str  # e.g., "/v1/companies"
    tests_passed: int = 0
    tests_total: int = 0
    results: list[EvalResult] = field(default_factory=list)

    @property
    def accuracy_pct(self) -> float:
        if self.tests_total == 0:
            return 0.0
        return (self.tests_passed / self.tests_total) * 100

    @property
    def avg_error_pct(self) -> Optional[float]:
        """Average deviation for tests with numeric comparisons."""
        errors = [r.error_pct for r in self.results if r.error_pct is not None]
        if not errors:
            return None
        return sum(errors) / len(errors)

    def add_result(self, result: EvalResult):
        self.results.append(result)
        self.tests_total += 1
        if result.passed:
            self.tests_passed += 1

    def to_dict(self) -> dict:
        return {
            "primitive": self.primitive,
            "tests_passed": self.tests_passed,
            "tests_total": self.tests_total,
            "accuracy_pct": round(self.accuracy_pct, 1),
            "avg_error_pct": round(self.avg_error_pct, 2) if self.avg_error_pct else None,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class EvalReport:
    """Complete evaluation report with regression detection."""
    timestamp: datetime
    primitive_scores: list[PrimitiveScore] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)  # Tests that went pass->fail
    improvements: list[str] = field(default_factory=list)  # Tests that went fail->pass

    @property
    def overall_accuracy(self) -> float:
        """Weighted average accuracy across all primitives."""
        total_passed = sum(p.tests_passed for p in self.primitive_scores)
        total_tests = sum(p.tests_total for p in self.primitive_scores)
        if total_tests == 0:
            return 0.0
        return (total_passed / total_tests) * 100

    @property
    def total_passed(self) -> int:
        return sum(p.tests_passed for p in self.primitive_scores)

    @property
    def total_tests(self) -> int:
        return sum(p.tests_total for p in self.primitive_scores)

    def get_failures(self) -> list[EvalResult]:
        """Get all failed test results."""
        failures = []
        for score in self.primitive_scores:
            failures.extend([r for r in score.results if not r.passed])
        return failures

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "overall_accuracy": round(self.overall_accuracy, 1),
            "total_passed": self.total_passed,
            "total_tests": self.total_tests,
            "regressions": self.regressions,
            "improvements": self.improvements,
            "primitive_scores": [p.to_dict() for p in self.primitive_scores],
        }


# =============================================================================
# COMPARISON FUNCTIONS
# =============================================================================


def compare_numeric(
    expected: float | int,
    actual: float | int,
    tolerance: float = 0.05,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Compare numeric values within tolerance.

    Args:
        expected: Ground truth value
        actual: API response value
        tolerance: Acceptable deviation (0.05 = 5%)
        test_id: Test identifier
        source: Ground truth source

    Returns:
        EvalResult with pass/fail status
    """
    if expected is None or actual is None:
        return EvalResult(
            test_id=test_id,
            passed=False,
            expected=expected,
            actual=actual,
            ground_truth_source=source,
            message="Missing value (expected or actual is None)",
        )

    if expected == 0:
        error_pct = 0 if actual == 0 else 1.0
    else:
        error_pct = abs(expected - actual) / abs(expected)

    passed = error_pct <= tolerance

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=expected,
        actual=actual,
        tolerance=tolerance,
        error_pct=error_pct,
        ground_truth_source=source,
        message="" if passed else f"Deviation {error_pct*100:.1f}% exceeds tolerance {tolerance*100:.0f}%",
    )


def compare_exact(
    expected: Any,
    actual: Any,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Compare values for exact match.
    """
    passed = expected == actual

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=expected,
        actual=actual,
        ground_truth_source=source,
        message="" if passed else f"Expected {expected}, got {actual}",
    )


def compare_contains(
    expected_subset: list | set,
    actual: list | set,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Check if actual contains all expected elements.
    """
    expected_set = set(expected_subset)
    actual_set = set(actual)
    missing = expected_set - actual_set

    passed = len(missing) == 0

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=list(expected_subset),
        actual=list(actual),
        ground_truth_source=source,
        message="" if passed else f"Missing elements: {missing}",
    )


def compare_all_match(
    actual: list,
    field: str,
    expected_value: Any,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Check if all items in list have field matching expected value.
    """
    if not actual:
        return EvalResult(
            test_id=test_id,
            passed=False,
            expected=expected_value,
            actual=None,
            ground_truth_source=source,
            message="Empty result list",
        )

    non_matching = [
        item for item in actual
        if isinstance(item, dict) and item.get(field) != expected_value
    ]
    passed = len(non_matching) == 0

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=f"all {field}={expected_value}",
        actual=f"{len(actual) - len(non_matching)}/{len(actual)} match",
        ground_truth_source=source,
        message="" if passed else f"{len(non_matching)} items have {field} != {expected_value}",
    )


def compare_all_gte(
    actual: list,
    field: str,
    min_value: float | int,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Check if all items have field >= min_value.
    """
    if not actual:
        return EvalResult(
            test_id=test_id,
            passed=False,
            expected=f">= {min_value}",
            actual=None,
            ground_truth_source=source,
            message="Empty result list",
        )

    values = []
    for item in actual:
        if isinstance(item, dict):
            val = item.get(field)
            # Handle nested fields like "pricing.ytm"
            if val is None and "." in field:
                parts = field.split(".")
                val = item
                for part in parts:
                    if isinstance(val, dict):
                        val = val.get(part)
                    else:
                        val = None
                        break
            if val is not None:
                values.append(val)

    below_min = [v for v in values if v < min_value]
    passed = len(below_min) == 0

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=f"all {field} >= {min_value}",
        actual=f"{len(values) - len(below_min)}/{len(values)} pass",
        ground_truth_source=source,
        message="" if passed else f"{len(below_min)} values below {min_value}: min={min(values) if values else 'N/A'}",
    )


def compare_sorted(
    actual: list,
    field: str,
    descending: bool = True,
    test_id: str = "",
    source: str = "",
) -> EvalResult:
    """
    Check if list is sorted by field.
    """
    if not actual:
        return EvalResult(
            test_id=test_id,
            passed=True,  # Empty list is trivially sorted
            expected="sorted",
            actual="empty",
            ground_truth_source=source,
        )

    values = []
    for item in actual:
        if isinstance(item, dict):
            val = item.get(field)
            if val is not None:
                values.append(val)

    if not values:
        return EvalResult(
            test_id=test_id,
            passed=False,
            expected="sorted",
            actual="no values",
            ground_truth_source=source,
            message=f"No values found for field {field}",
        )

    expected_sorted = sorted(values, reverse=descending)
    passed = values == expected_sorted

    return EvalResult(
        test_id=test_id,
        passed=passed,
        expected=f"sorted {'desc' if descending else 'asc'}",
        actual=f"{'sorted' if passed else 'not sorted'}",
        ground_truth_source=source,
        message="" if passed else f"First mismatch at index {next((i for i, (a, b) in enumerate(zip(values, expected_sorted)) if a != b), -1)}",
    )


# =============================================================================
# BASELINE MANAGEMENT
# =============================================================================


BASELINE_DIR = Path(__file__).parent / "baseline"


def load_baseline(primitive: str) -> Optional[dict]:
    """Load baseline results for a primitive."""
    baseline_file = BASELINE_DIR / f"{primitive}_baseline.json"
    if not baseline_file.exists():
        return None

    with open(baseline_file) as f:
        return json.load(f)


def save_baseline(primitive: str, results: dict):
    """Save baseline results for a primitive."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_file = BASELINE_DIR / f"{primitive}_baseline.json"

    data = {
        "generated_at": datetime.utcnow().isoformat(),
        "results": results,
    }

    with open(baseline_file, "w") as f:
        json.dump(data, f, indent=2)


def detect_regressions(
    current_results: dict[str, EvalResult],
    primitive: str,
) -> tuple[list[str], list[str]]:
    """
    Compare current results against baseline to detect regressions.

    Returns:
        Tuple of (regressions, improvements)
    """
    baseline = load_baseline(primitive)
    if not baseline:
        return [], []

    baseline_results = baseline.get("results", {})
    regressions = []
    improvements = []

    for test_id, result in current_results.items():
        baseline_entry = baseline_results.get(test_id)
        if not baseline_entry:
            continue

        was_passing = baseline_entry.get("passed", False)
        is_passing = result.passed

        if was_passing and not is_passing:
            regressions.append(test_id)
        elif not was_passing and is_passing:
            improvements.append(test_id)

    return regressions, improvements


# =============================================================================
# REPORT GENERATION
# =============================================================================


def generate_markdown_report(report: EvalReport) -> str:
    """Generate markdown report from eval results."""
    lines = [
        f"# DebtStack Evaluation Report - {report.timestamp.strftime('%Y-%m-%d')}",
        "",
        f"**Overall Accuracy: {report.overall_accuracy:.1f}%** ({report.total_passed}/{report.total_tests} tests)",
        "",
        "## Primitive Scores",
        "",
        "| Primitive | Passed | Total | Score | Status |",
        "|-----------|--------|-------|-------|--------|",
    ]

    for score in report.primitive_scores:
        status = "✓" if score.accuracy_pct >= 95 else "⚠" if score.accuracy_pct >= 80 else "✗"
        lines.append(
            f"| {score.primitive} | {score.tests_passed} | {score.tests_total} | "
            f"{score.accuracy_pct:.1f}% | {status} |"
        )

    # Regressions
    if report.regressions:
        lines.extend([
            "",
            "## Regressions",
            "",
        ])
        for r in report.regressions:
            lines.append(f"- **{r}**: Previously passing, now failing")

    # Improvements
    if report.improvements:
        lines.extend([
            "",
            "## Improvements",
            "",
        ])
        for i in report.improvements:
            lines.append(f"- **{i}**: Previously failing, now passing")

    # Failures
    failures = report.get_failures()
    if failures:
        lines.extend([
            "",
            "## Failures",
            "",
        ])
        for f in failures:
            lines.append(f"- **{f.test_id}**: {f.message}")

    return "\n".join(lines)


def generate_terminal_report(report: EvalReport) -> str:
    """Generate terminal-friendly report."""
    lines = [
        "",
        f"DebtStack Evaluation Report - {report.timestamp.strftime('%Y-%m-%d')}",
        "=" * 55,
        "",
        f"OVERALL ACCURACY: {report.overall_accuracy:.1f}%",
        "",
        "Primitive Scores:",
        "┌" + "─" * 24 + "┬" + "─" * 8 + "┬" + "─" * 10 + "┬" + "─" * 9 + "┐",
        "│ Primitive              │ Passed │ Total    │ Score   │",
        "├" + "─" * 24 + "┼" + "─" * 8 + "┼" + "─" * 10 + "┼" + "─" * 9 + "┤",
    ]

    for score in report.primitive_scores:
        status = "✓" if score.accuracy_pct >= 95 else "⚠" if score.accuracy_pct >= 80 else "✗"
        lines.append(
            f"│ {score.primitive:<22} │ {score.tests_passed:>6} │ {score.tests_total:>8} │ "
            f"{score.accuracy_pct:>5.1f}% {status} │"
        )

    lines.append("└" + "─" * 24 + "┴" + "─" * 8 + "┴" + "─" * 10 + "┴" + "─" * 9 + "┘")

    # Regressions
    if report.regressions:
        lines.extend([
            "",
            f"Regressions ({len(report.regressions)}):",
        ])
        for r in report.regressions:
            lines.append(f"  • {r}")
    else:
        lines.extend([
            "",
            "Regressions (0): None",
        ])

    # Failures
    failures = report.get_failures()
    if failures:
        lines.extend([
            "",
            f"Failures ({len(failures)}):",
        ])
        for f in failures[:10]:  # Show first 10
            lines.append(f"  • {f.test_id}: {f.message}")
        if len(failures) > 10:
            lines.append(f"  ... and {len(failures) - 10} more")

    lines.append("")
    return "\n".join(lines)

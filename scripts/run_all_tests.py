#!/usr/bin/env python3
"""
Unified Test Runner for DebtStack

Runs all test suites and produces a consolidated report:
1. pytest unit tests (fast, no external deps)
2. pytest API contract tests (requires API key)
3. Existing E2E test scripts (test_demo_scenarios.py, test_api_edge_cases.py)
4. Optionally: QC Master data quality checks

Usage:
    python scripts/run_all_tests.py                    # Run all tests
    python scripts/run_all_tests.py --unit             # Unit tests only
    python scripts/run_all_tests.py --api              # API tests only
    python scripts/run_all_tests.py --e2e              # E2E scripts only
    python scripts/run_all_tests.py --qc               # Include QC checks
    python scripts/run_all_tests.py --quick            # Unit tests only (fastest)
    python scripts/run_all_tests.py --json             # Output JSON report
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class TestSuiteResult:
    """Result from a test suite."""
    name: str
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    error: Optional[str] = None
    details: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.error is None


@dataclass
class TestReport:
    """Consolidated test report."""
    timestamp: str
    suites: list
    total_passed: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    total_duration: float = 0.0

    def calculate_totals(self):
        self.total_passed = sum(s.passed for s in self.suites)
        self.total_failed = sum(s.failed for s in self.suites)
        self.total_skipped = sum(s.skipped for s in self.suites)
        self.total_duration = sum(s.duration_seconds for s in self.suites)

    @property
    def success(self) -> bool:
        return all(s.success for s in self.suites)


def run_pytest(test_path: str = "", markers: str = "", verbose: bool = False) -> TestSuiteResult:
    """Run pytest with specified path or markers."""
    start = time.time()
    if test_path:
        name = f"pytest ({test_path})"
    elif markers:
        name = f"pytest ({markers})"
    else:
        name = "pytest (all)"

    cmd = ["python", "-m", "pytest", "-s"]  # -s to avoid Windows capture issue
    if test_path:
        cmd.append(test_path)
    if markers:
        cmd.extend(["-m", markers])
    if verbose:
        cmd.append("-v")
    cmd.append("--tb=short")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        duration = time.time() - start

        # Parse pytest output for counts
        output = result.stdout + result.stderr
        passed = failed = skipped = 0

        # Look for summary line like "73 passed in 0.62s" or "5 passed, 2 failed"
        import re

        # First try: summary line at end
        for line in output.split('\n'):
            # Match patterns like "73 passed" or "5 failed" or "2 skipped"
            pass_match = re.search(r'(\d+)\s+passed', line)
            fail_match = re.search(r'(\d+)\s+failed', line)
            skip_match = re.search(r'(\d+)\s+skipped', line)

            if pass_match:
                passed = int(pass_match.group(1))
            if fail_match:
                failed = int(fail_match.group(1))
            if skip_match:
                skipped = int(skip_match.group(1))

            # Also check for "no tests ran" which means 0
            if 'no tests ran' in line.lower():
                passed = failed = skipped = 0

        # Second try: count individual PASSED/FAILED lines if summary not found
        if passed == 0 and failed == 0:
            passed = output.count(' PASSED')
            failed = output.count(' FAILED')
            skipped = output.count(' SKIPPED')

        # Legacy parsing for backward compatibility
        for line in output.split('\n'):
            if 'passed' in line or 'failed' in line or 'skipped' in line:
                if ' passed' in line:
                    try:
                        passed = int(line.split(' passed')[0].split()[-1])
                    except (ValueError, IndexError):
                        pass
                if ' failed' in line:
                    try:
                        failed = int(line.split(' failed')[0].split()[-1])
                    except (ValueError, IndexError):
                        pass
                if ' skipped' in line:
                    try:
                        skipped = int(line.split(' skipped')[0].split()[-1])
                    except (ValueError, IndexError):
                        pass

        return TestSuiteResult(
            name=name,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=duration,
            error=None if result.returncode == 0 else output[-500:] if output else "Unknown error"
        )

    except subprocess.TimeoutExpired:
        return TestSuiteResult(
            name=name,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=300,
            error="Test suite timed out after 5 minutes"
        )
    except Exception as e:
        return TestSuiteResult(
            name=name,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=time.time() - start,
            error=str(e)
        )


def run_e2e_script(script_name: str, args: list = None) -> TestSuiteResult:
    """Run an existing E2E test script."""
    start = time.time()
    script_path = f"scripts/{script_name}"

    cmd = ["python", script_path]
    if args:
        cmd.extend(args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        duration = time.time() - start
        output = result.stdout + result.stderr

        # Parse output for pass/fail counts
        passed = failed = 0

        # Look for common patterns
        for line in output.split('\n'):
            line_lower = line.lower()
            if 'passed' in line_lower and ('/' in line or ':' in line):
                # Pattern: "Passed: 45/50" or "45 passed"
                try:
                    if '/' in line:
                        parts = line.split('/')
                        passed = int(parts[0].split()[-1])
                        total = int(parts[1].split()[0])
                        failed = total - passed
                    elif ':' in line and 'passed' in line_lower:
                        passed = int(line.split(':')[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            elif 'fail' in line_lower and ':' in line:
                try:
                    failed = int(line.split(':')[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
            elif '[pass]' in line_lower:
                passed += 1
            elif '[fail]' in line_lower:
                failed += 1

        return TestSuiteResult(
            name=script_name,
            passed=passed,
            failed=failed,
            skipped=0,
            duration_seconds=duration,
            error=None if result.returncode == 0 else output[-500:] if output else "Script failed"
        )

    except subprocess.TimeoutExpired:
        return TestSuiteResult(
            name=script_name,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=300,
            error="Script timed out after 5 minutes"
        )
    except Exception as e:
        return TestSuiteResult(
            name=script_name,
            passed=0,
            failed=0,
            skipped=0,
            duration_seconds=time.time() - start,
            error=str(e)
        )


def print_report(report: TestReport, verbose: bool = False):
    """Print formatted test report."""
    print()
    print("=" * 70)
    print("DEBTSTACK TEST REPORT")
    print("=" * 70)
    print(f"Timestamp: {report.timestamp}")
    print()

    for suite in report.suites:
        status = "[PASS]" if suite.success else "[FAIL]"
        print(f"{status} {suite.name}")
        print(f"       Passed: {suite.passed}, Failed: {suite.failed}, Skipped: {suite.skipped}")
        print(f"       Duration: {suite.duration_seconds:.1f}s")
        if suite.error and verbose:
            print(f"       Error: {suite.error[:200]}...")
        print()

    print("-" * 70)
    print("TOTALS")
    print("-" * 70)
    print(f"  Passed:  {report.total_passed}")
    print(f"  Failed:  {report.total_failed}")
    print(f"  Skipped: {report.total_skipped}")
    print(f"  Duration: {report.total_duration:.1f}s")
    print()

    if report.success:
        print("[SUCCESS] All test suites passed!")
    else:
        print("[FAILURE] Some tests failed.")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Run DebtStack test suites")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--api", action="store_true", help="Run API contract tests only")
    parser.add_argument("--e2e", action="store_true", help="Run E2E scripts only")
    parser.add_argument("--qc", action="store_true", help="Include QC Master checks")
    parser.add_argument("--quick", action="store_true", help="Quick mode: unit tests only")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Determine what to run
    run_unit = args.unit or args.quick or not (args.api or args.e2e)
    run_api = args.api or not (args.unit or args.e2e or args.quick)
    run_e2e = args.e2e or not (args.unit or args.api or args.quick)
    run_qc = args.qc

    suites = []

    print("DebtStack Test Runner")
    print("=" * 70)

    # 1. Unit tests (pytest -m unit)
    if run_unit:
        print("\n[1/4] Running unit tests...")
        result = run_pytest(test_path="tests/unit", verbose=args.verbose)
        suites.append(result)
        print(f"      {result.passed} passed, {result.failed} failed ({result.duration_seconds:.1f}s)")

    # 2. API contract tests (pytest -m api)
    if run_api:
        print("\n[2/4] Running API contract tests...")
        result = run_pytest(test_path="tests/api", verbose=args.verbose)
        suites.append(result)
        print(f"      {result.passed} passed, {result.failed} failed ({result.duration_seconds:.1f}s)")

    # 3. E2E test scripts
    if run_e2e:
        print("\n[3/4] Running E2E test scripts...")

        # test_demo_scenarios.py
        print("      Running test_demo_scenarios.py...")
        result = run_e2e_script("test_demo_scenarios.py")
        suites.append(result)
        print(f"      {result.passed} passed, {result.failed} failed ({result.duration_seconds:.1f}s)")

        # test_api_edge_cases.py
        print("      Running test_api_edge_cases.py...")
        result = run_e2e_script("test_api_edge_cases.py")
        suites.append(result)
        print(f"      {result.passed} passed, {result.failed} failed ({result.duration_seconds:.1f}s)")

    # 4. QC Master (optional)
    if run_qc:
        print("\n[4/4] Running QC Master checks...")
        result = run_e2e_script("qc_master.py")
        suites.append(result)
        print(f"      Completed ({result.duration_seconds:.1f}s)")

    # Build report
    report = TestReport(
        timestamp=datetime.now().isoformat(),
        suites=suites
    )
    report.calculate_totals()

    # Output
    if args.json:
        # Convert to JSON-serializable format
        output = {
            "timestamp": report.timestamp,
            "total_passed": report.total_passed,
            "total_failed": report.total_failed,
            "total_skipped": report.total_skipped,
            "total_duration": report.total_duration,
            "success": report.success,
            "suites": [asdict(s) for s in report.suites]
        }
        print(json.dumps(output, indent=2))
    else:
        print_report(report, verbose=args.verbose)

    # Exit code
    sys.exit(0 if report.success else 1)


if __name__ == "__main__":
    main()

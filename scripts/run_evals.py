#!/usr/bin/env python3
"""
DebtStack Eval Runner

Runs the evaluation test suite, calculates accuracy scores,
detects regressions, and generates reports.

Usage:
    python scripts/run_evals.py                    # Run all evals
    python scripts/run_evals.py --primitive companies  # Single primitive
    python scripts/run_evals.py --update-baseline  # Update baseline after fix
    python scripts/run_evals.py --report-only      # Generate report from last run
    python scripts/run_evals.py --json             # JSON output for CI
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


# =============================================================================
# CONFIGURATION
# =============================================================================

EVAL_DIR = Path(__file__).parent.parent / "tests" / "eval"
RESULTS_DIR = EVAL_DIR / "results"
BASELINE_DIR = EVAL_DIR / "baseline"

# Map CLI names to test files
PRIMITIVE_MAP = {
    "companies": "test_companies.py",
    "bonds": "test_bonds.py",
    "bonds_resolve": "test_bonds_resolve.py",
    "financials": "test_financials.py",
    "collateral": "test_collateral.py",
    "covenants": "test_covenants.py",
    "covenants_compare": "test_covenants_compare.py",
    "entities": "test_entities_traverse.py",
    "documents": "test_documents_search.py",
    "workflows": "test_workflows.py",
}


# =============================================================================
# TEST EXECUTION
# =============================================================================

def run_pytest(test_path: Path, json_output: bool = False, verbose: bool = True) -> dict:
    """
    Run pytest on given path and capture results.

    Returns dict with:
        - passed: int
        - failed: int
        - skipped: int
        - total: int
        - duration: float
        - failures: list of failure details
    """
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "--tb=short",
        "-q",
    ]

    if verbose:
        cmd.append("-v")

    # Use pytest-json-report if available for detailed results
    json_report_path = RESULTS_DIR / "pytest_report.json"
    cmd.extend(["--json-report", f"--json-report-file={json_report_path}"])

    # Run pytest
    env = os.environ.copy()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    # Parse results
    parsed = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "total": 0,
        "duration": 0.0,
        "failures": [],
        "output": result.stdout + result.stderr,
    }

    # Try to load JSON report
    if json_report_path.exists():
        try:
            with open(json_report_path) as f:
                report = json.load(f)
                parsed["passed"] = report.get("summary", {}).get("passed", 0)
                parsed["failed"] = report.get("summary", {}).get("failed", 0)
                parsed["skipped"] = report.get("summary", {}).get("skipped", 0)
                parsed["total"] = report.get("summary", {}).get("total", 0)
                parsed["duration"] = report.get("duration", 0)

                # Extract failures
                for test in report.get("tests", []):
                    if test.get("outcome") == "failed":
                        parsed["failures"].append({
                            "nodeid": test.get("nodeid"),
                            "message": test.get("call", {}).get("longrepr", ""),
                        })
        except Exception:
            pass

    # Fallback: parse stdout
    if parsed["total"] == 0:
        # Parse summary line like "5 passed, 2 failed, 1 skipped"
        for line in result.stdout.split("\n"):
            if "passed" in line or "failed" in line:
                import re
                passed_match = re.search(r"(\d+) passed", line)
                failed_match = re.search(r"(\d+) failed", line)
                skipped_match = re.search(r"(\d+) skipped", line)

                if passed_match:
                    parsed["passed"] = int(passed_match.group(1))
                if failed_match:
                    parsed["failed"] = int(failed_match.group(1))
                if skipped_match:
                    parsed["skipped"] = int(skipped_match.group(1))

                parsed["total"] = parsed["passed"] + parsed["failed"] + parsed["skipped"]
                break

    return parsed


def run_single_primitive(primitive: str, verbose: bool = True) -> dict:
    """Run evals for a single primitive."""
    test_file = PRIMITIVE_MAP.get(primitive)
    if not test_file:
        print(f"Unknown primitive: {primitive}")
        print(f"Available: {', '.join(PRIMITIVE_MAP.keys())}")
        sys.exit(1)

    test_path = EVAL_DIR / test_file
    if not test_path.exists():
        print(f"Test file not found: {test_path}")
        sys.exit(1)

    return run_pytest(test_path, verbose=verbose)


def run_all_evals(verbose: bool = True) -> dict:
    """Run all eval tests."""
    return run_pytest(EVAL_DIR, verbose=verbose)


# =============================================================================
# REPORTING
# =============================================================================

def generate_report(results: dict, primitive: str = None) -> str:
    """Generate terminal report from results."""
    lines = [
        "",
        "=" * 55,
        "DebtStack Evaluation Report",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 55,
        "",
    ]

    if primitive:
        lines.append(f"Primitive: /v1/{primitive}")
        lines.append("")

    # Summary
    passed = results["passed"]
    failed = results["failed"]
    skipped = results["skipped"]
    total = results["total"]
    accuracy = (passed / total * 100) if total > 0 else 0

    lines.append(f"OVERALL ACCURACY: {accuracy:.1f}%")
    lines.append("")
    lines.append(f"  Passed:  {passed}")
    lines.append(f"  Failed:  {failed}")
    lines.append(f"  Skipped: {skipped}")
    lines.append(f"  Total:   {total}")
    lines.append("")

    # Duration
    if results.get("duration"):
        lines.append(f"Duration: {results['duration']:.2f}s")
        lines.append("")

    # Failures
    if results["failures"]:
        lines.append("FAILURES:")
        lines.append("-" * 40)
        for f in results["failures"][:10]:  # Show first 10
            lines.append(f"  • {f['nodeid']}")
            if f.get("message"):
                # Truncate long messages
                msg = f["message"][:200] + "..." if len(f["message"]) > 200 else f["message"]
                for line in msg.split("\n")[:3]:
                    lines.append(f"    {line}")
        if len(results["failures"]) > 10:
            lines.append(f"  ... and {len(results['failures']) - 10} more")
        lines.append("")

    # Status
    if accuracy >= 95:
        lines.append("STATUS: ✓ PASSING (>= 95%)")
    elif accuracy >= 80:
        lines.append("STATUS: ⚠ WARNING (80-95%)")
    else:
        lines.append("STATUS: ✗ FAILING (< 80%)")

    lines.append("")
    return "\n".join(lines)


def save_results(results: dict, primitive: str = None):
    """Save results to JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"{primitive}_results.json" if primitive else "all_results.json"
    filepath = RESULTS_DIR / filename

    data = {
        "timestamp": datetime.now().isoformat(),
        "primitive": primitive,
        **results,
    }

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Results saved to: {filepath}")


# =============================================================================
# BASELINE MANAGEMENT
# =============================================================================

def update_baseline(primitive: str = None):
    """Update baseline from latest results."""
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    if primitive:
        # Load primitive results
        results_file = RESULTS_DIR / f"{primitive}_results.json"
        if not results_file.exists():
            print(f"No results file found. Run evals first: python scripts/run_evals.py --primitive {primitive}")
            sys.exit(1)

        with open(results_file) as f:
            results = json.load(f)

        # Save as baseline
        baseline_file = BASELINE_DIR / f"{primitive}_baseline.json"
        baseline = {
            "generated_at": datetime.now().isoformat(),
            "results": {
                "passed": results["passed"],
                "failed": results["failed"],
                "total": results["total"],
            }
        }

        with open(baseline_file, "w") as f:
            json.dump(baseline, f, indent=2)

        print(f"Baseline updated: {baseline_file}")
    else:
        # Update all baselines
        all_results = RESULTS_DIR / "all_results.json"
        if not all_results.exists():
            print("No results file found. Run evals first: python scripts/run_evals.py")
            sys.exit(1)

        with open(all_results) as f:
            results = json.load(f)

        baseline_file = BASELINE_DIR / "all_baseline.json"
        baseline = {
            "generated_at": datetime.now().isoformat(),
            "results": {
                "passed": results["passed"],
                "failed": results["failed"],
                "total": results["total"],
            }
        }

        with open(baseline_file, "w") as f:
            json.dump(baseline, f, indent=2)

        print(f"Baseline updated: {baseline_file}")


def check_regressions(results: dict, primitive: str = None) -> list:
    """Check for regressions against baseline."""
    baseline_file = BASELINE_DIR / (
        f"{primitive}_baseline.json" if primitive else "all_baseline.json"
    )

    if not baseline_file.exists():
        return []

    with open(baseline_file) as f:
        baseline = json.load(f)

    baseline_results = baseline.get("results", {})
    regressions = []

    # Simple check: did we have fewer failures before?
    if results["failed"] > baseline_results.get("failed", 0):
        regressions.append(
            f"Failures increased: {baseline_results.get('failed', 0)} -> {results['failed']}"
        )

    # Check: did we have more passes before?
    if results["passed"] < baseline_results.get("passed", 0):
        regressions.append(
            f"Passes decreased: {baseline_results.get('passed', 0)} -> {results['passed']}"
        )

    return regressions


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Run DebtStack evaluation tests")
    parser.add_argument("--primitive", "-p", help="Run single primitive (e.g., companies, bonds)")
    parser.add_argument("--update-baseline", action="store_true", help="Update baseline after verified fix")
    parser.add_argument("--report-only", action="store_true", help="Generate report from last run")
    parser.add_argument("--json", action="store_true", help="JSON output for CI")
    parser.add_argument("--quiet", "-q", action="store_true", help="Less verbose output")
    parser.add_argument("--list", action="store_true", help="List available primitives")
    args = parser.parse_args()

    # List primitives
    if args.list:
        print("Available primitives:")
        for name, file in PRIMITIVE_MAP.items():
            print(f"  {name}: {file}")
        sys.exit(0)

    # Update baseline
    if args.update_baseline:
        update_baseline(args.primitive)
        sys.exit(0)

    # Create results directory
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Report only (from last run)
    if args.report_only:
        results_file = RESULTS_DIR / (
            f"{args.primitive}_results.json" if args.primitive else "all_results.json"
        )
        if not results_file.exists():
            print(f"No results file found: {results_file}")
            sys.exit(1)

        with open(results_file) as f:
            results = json.load(f)

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(generate_report(results, args.primitive))
        sys.exit(0)

    # Run evals
    print("Running DebtStack Evaluations...")
    print("")

    if args.primitive:
        results = run_single_primitive(args.primitive, verbose=not args.quiet)
    else:
        results = run_all_evals(verbose=not args.quiet)

    # Save results
    save_results(results, args.primitive)

    # Check regressions
    regressions = check_regressions(results, args.primitive)
    if regressions:
        results["regressions"] = regressions

    # Output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(generate_report(results, args.primitive))

        if regressions:
            print("⚠️  REGRESSIONS DETECTED:")
            for r in regressions:
                print(f"  • {r}")
            print("")

    # Exit code
    if results["failed"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

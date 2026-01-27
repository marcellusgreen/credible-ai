"""
Shared utilities for CLI scripts.

Provides common patterns for:
- Database session management
- CLI argument parsing
- Output formatting
- Batch processing with progress
"""

import argparse
import asyncio
import io
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional

# Handle Windows UTF-8 output
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import async_session_maker


# =============================================================================
# DATABASE UTILITIES
# =============================================================================

@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Get an async database session with proper cleanup."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_all_companies(session: AsyncSession) -> list[tuple]:
    """Get all companies with their CIKs."""
    result = await session.execute(text('''
        SELECT id, ticker, cik, name
        FROM companies
        ORDER BY ticker
    '''))
    return result.fetchall()


async def get_company_by_ticker(session: AsyncSession, ticker: str) -> Optional[tuple]:
    """Get a single company by ticker."""
    result = await session.execute(text('''
        SELECT id, ticker, cik, name
        FROM companies
        WHERE ticker = :ticker
    '''), {'ticker': ticker.upper()})
    return result.fetchone()


# =============================================================================
# CLI UTILITIES
# =============================================================================

def create_base_parser(description: str) -> argparse.ArgumentParser:
    """Create a base argument parser with common options."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        '--ticker',
        type=str,
        help='Process single company by ticker'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Process all companies'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Limit number of companies to process (0 = unlimited)'
    )
    return parser


def create_fix_parser(description: str) -> argparse.ArgumentParser:
    """Create argument parser for fix scripts with dry-run/save options."""
    parser = create_base_parser(description)
    parser.add_argument(
        '--save',
        action='store_true',
        help='Apply changes to database (default is dry run)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )
    return parser


def create_extract_parser(description: str) -> argparse.ArgumentParser:
    """Create argument parser for extraction scripts."""
    parser = create_base_parser(description)
    parser.add_argument(
        '--cik',
        type=str,
        help='SEC CIK number (required for single company)'
    )
    parser.add_argument(
        '--save-db',
        action='store_true',
        help='Save results to database'
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip companies that already have data'
    )
    return parser


# =============================================================================
# OUTPUT FORMATTING
# =============================================================================

def print_header(title: str, width: int = 70) -> None:
    """Print a formatted header."""
    print('=' * width)
    print(title)
    print('=' * width)


def print_subheader(title: str, width: int = 70) -> None:
    """Print a formatted subheader."""
    print('-' * width)
    print(title)
    print('-' * width)


def print_summary(stats: dict, width: int = 70) -> None:
    """Print a summary of statistics."""
    print()
    print('=' * width)
    print('SUMMARY')
    print('=' * width)
    for key, value in stats.items():
        print(f"  {key}: {value}")


def print_progress(current: int, total: int, ticker: str = '') -> None:
    """Print progress indicator."""
    pct = (current / total * 100) if total > 0 else 0
    msg = f"[{current}/{total}] ({pct:.0f}%)"
    if ticker:
        msg += f" {ticker}"
    print(msg, end='\r', flush=True)


# =============================================================================
# BATCH PROCESSING
# =============================================================================

async def process_companies(
    session: AsyncSession,
    process_func: Callable,
    ticker: Optional[str] = None,
    process_all: bool = False,
    limit: int = 0,
    **kwargs
) -> dict:
    """
    Process companies with common patterns.

    Args:
        session: Database session
        process_func: Async function(session, company_id, ticker, **kwargs) -> dict
        ticker: Single ticker to process (optional)
        process_all: Process all companies
        limit: Maximum companies to process (0 = unlimited)
        **kwargs: Additional args passed to process_func

    Returns:
        Dict with 'processed', 'success', 'errors' counts
    """
    stats = {'processed': 0, 'success': 0, 'errors': 0}

    if ticker:
        company = await get_company_by_ticker(session, ticker)
        if not company:
            print(f"Company not found: {ticker}")
            return stats

        companies = [company]
    elif process_all:
        companies = await get_all_companies(session)
        if limit > 0:
            companies = companies[:limit]
    else:
        print("Specify --ticker or --all")
        return stats

    total = len(companies)
    print(f"Processing {total} company(ies)...")
    print()

    for i, company in enumerate(companies):
        company_id, ticker, cik, name = company
        print_progress(i + 1, total, ticker)

        try:
            result = await process_func(
                session,
                company_id=company_id,
                ticker=ticker,
                cik=cik,
                **kwargs
            )
            stats['processed'] += 1
            if result.get('success', True):
                stats['success'] += 1
            else:
                stats['errors'] += 1
        except Exception as e:
            print(f"\n  Error processing {ticker}: {e}")
            stats['errors'] += 1

    print()  # Clear progress line
    return stats


# =============================================================================
# COMMON PATTERNS
# =============================================================================

def run_async(coro):
    """Run async function with proper event loop handling."""
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)

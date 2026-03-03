"""
Automated Scheduler

Runs on APScheduler AsyncIOScheduler (US/Eastern timezone):

  Bond Pricing:
  - 11:00 AM ET: Refresh current prices (bond_pricing table)
  - 3:00 PM ET:  Refresh current prices (bond_pricing table)
  - 6:00 PM ET:  Refresh treasury yields (treasury_yield_history table)
  - 9:00 PM ET:  Refresh current prices + save daily snapshot (bond_pricing_history)

  SEC Filing Refresh:
  - 7:30 AM ET:  Check for new filings, refresh data (catches overnight/early filings)
  - 1:00 PM ET:  Check for new filings, refresh data (catches late morning filings)

Reuses the same service functions as scripts/collect_daily_pricing.py
and scripts/refresh_filings.py.
"""

import asyncio
from datetime import datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.database import async_session_maker
from app.services.bond_pricing import (
    get_bonds_needing_pricing,
    get_bond_price,
    save_bond_pricing,
    REQUEST_DELAY,
)
from app.services.yield_calculation import calculate_ytm_and_spread
from app.services.pricing_history import copy_current_to_history
from app.services.treasury_yields import backfill_treasury_yields
from app.core.alerting import check_and_alert

logger = structlog.get_logger()

scheduler = AsyncIOScheduler(timezone="US/Eastern")


async def refresh_current_prices() -> dict:
    """Fetch latest TRACE prices and update the bond_pricing table.

    Only fetches bonds with ISINs (required for Finnhub) that haven't been
    updated in the last day. Processes up to 2,500 bonds per run.
    """
    logger.info("scheduler.refresh_prices.start")
    stats = {
        "bonds_checked": 0,
        "prices_updated": 0,
        "prices_failed": 0,
        "prices_no_data": 0,
        "yields_calculated": 0,
        "consecutive_errors": 0,
    }

    try:
        async with async_session_maker() as session:
            bonds = await get_bonds_needing_pricing(
                session,
                stale_only=True,
                stale_days=1,
                limit=2500,
                require_isin=True,
            )
            stats["bonds_checked"] = len(bonds)
            logger.info("scheduler.refresh_prices.found", count=len(bonds))

            for bond in bonds:
                try:
                    price = await get_bond_price(
                        cusip=bond.cusip,
                        isin=bond.isin,
                        coupon_rate_pct=(
                            bond.interest_rate / 100 if bond.interest_rate else None
                        ),
                        maturity_date=bond.maturity_date,
                        session=session,
                        debt_instrument_id=bond.id,
                    )

                    if price.last_price:
                        stats["prices_updated"] += 1
                        stats["consecutive_errors"] = 0

                        ytm_bps = None
                        spread_bps = None
                        benchmark = None

                        if bond.interest_rate and bond.maturity_date:
                            try:
                                ytm_bps, spread_bps, benchmark = (
                                    await calculate_ytm_and_spread(
                                        price=float(price.last_price),
                                        coupon_rate=bond.interest_rate / 100,
                                        maturity_date=bond.maturity_date,
                                    )
                                )
                                stats["yields_calculated"] += 1
                            except Exception:
                                pass

                        await save_bond_pricing(
                            session=session,
                            debt_instrument_id=bond.id,
                            cusip=bond.cusip,
                            price=price,
                            ytm_bps=ytm_bps,
                            spread_bps=spread_bps,
                            treasury_benchmark=benchmark,
                        )
                    elif price.error and "rate limit" in price.error.lower():
                        stats["prices_failed"] += 1
                        stats["consecutive_errors"] += 1
                        logger.warning(
                            "scheduler.refresh_prices.rate_limited",
                            bond_id=str(bond.id),
                        )
                        # Back off on rate limit
                        await asyncio.sleep(10)
                    else:
                        stats["prices_no_data"] += 1
                        stats["consecutive_errors"] = 0

                    # Stop if too many consecutive errors (API key issue, etc.)
                    if stats["consecutive_errors"] >= 10:
                        logger.error(
                            "scheduler.refresh_prices.abort",
                            reason="10 consecutive errors",
                            last_error=price.error if price else "unknown",
                        )
                        break

                    await asyncio.sleep(REQUEST_DELAY)

                except Exception as exc:
                    logger.error(
                        "scheduler.refresh_prices.bond_error",
                        bond_id=str(bond.id),
                        error=str(exc),
                    )
                    stats["prices_failed"] += 1
                    stats["consecutive_errors"] += 1

                    if stats["consecutive_errors"] >= 10:
                        logger.error(
                            "scheduler.refresh_prices.abort",
                            reason="10 consecutive exceptions",
                        )
                        break

    except Exception as exc:
        logger.error("scheduler.refresh_prices.error", error=str(exc))

    logger.info("scheduler.refresh_prices.done", **stats)
    return stats


async def refresh_treasury_yields() -> dict:
    """Fetch latest treasury yields from Treasury.gov for current year."""
    from datetime import date

    logger.info("scheduler.refresh_treasury.start")
    stats = {"saved": 0, "error": None}

    try:
        async with async_session_maker() as session:
            current_year = date.today().year
            result = await backfill_treasury_yields(
                session,
                from_year=current_year,
                to_year=current_year,
                dry_run=False,
            )
            stats["saved"] = result["saved"]
            if result["errors"]:
                stats["error"] = result["errors"][0]
    except Exception as exc:
        logger.error("scheduler.refresh_treasury.error", error=str(exc))
        stats["error"] = str(exc)

    logger.info("scheduler.refresh_treasury.done", **stats)
    return stats


async def check_and_refresh_filings() -> dict:
    """Check all companies for new SEC filings and refresh stale data.

    Polls EDGAR for new 10-K, 10-Q, 8-K filings and runs the appropriate
    extraction steps for each. Sends Slack summary when new filings are found.
    """
    from app.core.alerting import send_slack_alert
    from app.services.filing_monitor import FilingMonitor
    from app.services.filing_refresh import FilingRefreshService

    logger.info("scheduler.filing_refresh.start")
    stats = {
        "companies_checked": 0,
        "new_filings": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
    }

    try:
        # Step 1: Scan for new filings
        monitor = FilingMonitor()
        try:
            async with async_session_maker() as session:
                new_filings = await monitor.check_all_companies(session)
                stats["companies_checked"] = 314  # approximate
        finally:
            await monitor.close()

        if not new_filings:
            logger.info("scheduler.filing_refresh.no_new_filings")
            return stats

        # Step 2: Deduplicate — keep newest filing per (company_id, form_type)
        seen = {}
        for filing in new_filings:
            key = (filing.company_id, filing.form_type)
            if key not in seen or filing.filing_date > seen[key].filing_date:
                seen[key] = filing
        deduped = list(seen.values())
        stats["new_filings"] = len(deduped)

        logger.info(
            "scheduler.filing_refresh.found",
            count=len(deduped),
            tickers=[f.ticker for f in deduped],
        )

        # Step 3: Process each filing
        service = FilingRefreshService()
        results = []

        for filing in deduped:
            try:
                result = await service.refresh_for_filing(filing)
                stats["processed"] += 1
                if result.success:
                    stats["succeeded"] += 1
                else:
                    stats["failed"] += 1
                results.append(result)
            except Exception as exc:
                stats["failed"] += 1
                logger.error(
                    "scheduler.filing_refresh.filing_error",
                    ticker=filing.ticker,
                    form_type=filing.form_type,
                    error=str(exc),
                )

        # Step 4: Send Slack summary
        summary_lines = [f"SEC Filing Refresh: {len(deduped)} new filing(s)"]
        for result in results:
            f = result.filing
            status = "OK" if result.success else "PARTIAL"
            summary_lines.append(
                f"  {f.ticker} {f.form_type} {f.filing_date}: "
                f"{status} ({len(result.steps_run)} steps, "
                f"{result.duration_seconds:.0f}s)"
            )
        if stats["failed"] > 0:
            summary_lines.append(f"\n{stats['failed']} filing(s) had step failures")

        await send_slack_alert("\n".join(summary_lines), level="info")

    except Exception as exc:
        logger.error("scheduler.filing_refresh.error", error=str(exc))
        try:
            from app.core.alerting import send_slack_alert
            await send_slack_alert(
                f"SEC Filing Refresh failed: {exc}", level="error"
            )
        except Exception:
            pass

    logger.info("scheduler.filing_refresh.done", **stats)
    return stats


async def refresh_and_snapshot() -> None:
    """Refresh current prices, then copy today's prices into bond_pricing_history."""
    await refresh_current_prices()

    logger.info("scheduler.snapshot.start")
    try:
        async with async_session_maker() as session:
            snapshot_stats = await copy_current_to_history(session)
            logger.info(
                "scheduler.snapshot.done",
                total_current=snapshot_stats.total_current,
                copied=snapshot_stats.copied,
                skipped_existing=snapshot_stats.skipped_existing,
                errors=snapshot_stats.errors,
            )
    except Exception as exc:
        logger.error("scheduler.snapshot.error", error=str(exc))


def start_scheduler() -> None:
    """Register jobs and start the scheduler."""
    scheduler.add_job(
        refresh_current_prices,
        CronTrigger(hour=11, minute=0, timezone="US/Eastern"),
        id="refresh_prices_11am",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_current_prices,
        CronTrigger(hour=15, minute=0, timezone="US/Eastern"),
        id="refresh_prices_3pm",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_treasury_yields,
        CronTrigger(hour=18, minute=0, timezone="US/Eastern"),
        id="refresh_treasury_6pm",
        replace_existing=True,
    )
    scheduler.add_job(
        refresh_and_snapshot,
        CronTrigger(hour=21, minute=0, timezone="US/Eastern"),
        id="refresh_and_snapshot_9pm",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_refresh_filings,
        CronTrigger(hour=7, minute=30, timezone="US/Eastern"),
        id="refresh_filings_730am",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_refresh_filings,
        CronTrigger(hour=13, minute=0, timezone="US/Eastern"),
        id="refresh_filings_1pm",
        replace_existing=True,
    )
    scheduler.add_job(
        check_and_alert,
        "interval",
        minutes=15,
        id="check_alerts_15min",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "scheduler.started",
        jobs=[j.id for j in scheduler.get_jobs()],
    )


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")

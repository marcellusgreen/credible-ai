"""
Automated Bond Pricing Scheduler

Runs on APScheduler AsyncIOScheduler (US/Eastern timezone):
  - 11:00 AM ET: Refresh current prices (bond_pricing table)
  - 3:00 PM ET:  Refresh current prices (bond_pricing table)
  - 6:00 PM ET:  Refresh treasury yields (treasury_yield_history table)
  - 9:00 PM ET:  Refresh current prices + save daily snapshot (bond_pricing_history)

Reuses the same service functions as scripts/collect_daily_pricing.py.
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
    """Fetch latest TRACE prices and update the bond_pricing table."""
    logger.info("scheduler.refresh_prices.start")
    stats = {
        "bonds_checked": 0,
        "prices_updated": 0,
        "prices_failed": 0,
        "yields_calculated": 0,
    }

    try:
        async with async_session_maker() as session:
            bonds = await get_bonds_needing_pricing(
                session,
                stale_only=True,
                stale_days=0,
                limit=5000,
            )
            stats["bonds_checked"] = len(bonds)

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
                    else:
                        stats["prices_failed"] += 1

                    await asyncio.sleep(REQUEST_DELAY)

                except Exception as exc:
                    logger.error(
                        "scheduler.refresh_prices.bond_error",
                        bond_id=str(bond.id),
                        error=str(exc),
                    )
                    stats["prices_failed"] += 1

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

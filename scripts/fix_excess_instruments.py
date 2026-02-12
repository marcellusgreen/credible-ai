#!/usr/bin/env python3
"""
Fix EXCESS debt coverage — dedup, deactivate matured, fix misattributed amounts.

After Phase 1+2 fixes, 78 companies had EXCESS outstanding amounts (instrument sum
exceeds reported total debt). After Phase 6 backfill, 27 companies have
EXCESS_SIGNIFICANT (>200%). Root causes:
  1. Duplicate instruments (SEC + Finnhub, same rate+year, different names)
  2. Matured bonds still active (maturity_date < today)
  3. Total-debt-as-per-instrument (LLM assigned aggregate total to each instrument)
  4. Phase 6 backfill assigned total debt to every instrument (identical amounts)
  5. Single instruments with outstanding exceeding total company debt
  6. Stale amounts from very old filings (>5 years)
  7. Complex issues requiring LLM judgment (aggregates, duplicates, wrong amounts)

Usage:
    # Analyze current state
    python scripts/fix_excess_instruments.py --analyze

    # Dry run each step
    python scripts/fix_excess_instruments.py --deactivate-matured --dry-run
    python scripts/fix_excess_instruments.py --deduplicate --dry-run
    python scripts/fix_excess_instruments.py --fix-totals --dry-run
    python scripts/fix_excess_instruments.py --fix-phase6-totals --dry-run
    python scripts/fix_excess_instruments.py --fix-outliers --dry-run
    python scripts/fix_excess_instruments.py --fix-stale --dry-run
    python scripts/fix_excess_instruments.py --fix-llm-review --dry-run
    python scripts/fix_excess_instruments.py --fix-revolver-capacity --dry-run

    # Execute
    python scripts/fix_excess_instruments.py --deactivate-matured
    python scripts/fix_excess_instruments.py --deduplicate
    python scripts/fix_excess_instruments.py --fix-totals
    python scripts/fix_excess_instruments.py --fix-phase6-totals
    python scripts/fix_excess_instruments.py --fix-outliers
    python scripts/fix_excess_instruments.py --fix-stale
    python scripts/fix_excess_instruments.py --fix-llm-review
    python scripts/fix_excess_instruments.py --fix-revolver-capacity

    # LLM review with custom excess threshold (default 2.0 = 200%)
    python scripts/fix_excess_instruments.py --fix-llm-review --excess-threshold 1.5

    # Run ALL steps in order
    python scripts/fix_excess_instruments.py --fix-all-excess --dry-run
    python scripts/fix_excess_instruments.py --fix-all-excess

    # Single company
    python scripts/fix_excess_instruments.py --deduplicate --ticker MA
"""

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import text

from script_utils import (
    get_db_session,
    print_header,
    print_subheader,
    print_summary,
    run_async,
)

from app.services.llm_utils import get_claude_client, call_claude, calculate_cost


# =============================================================================
# STEP 1: Deactivate Matured Instruments
# =============================================================================

async def analyze_matured(session):
    """Find active instruments with maturity_date in the past."""
    result = await session.execute(text('''
        SELECT di.id, c.ticker, di.name, di.maturity_date,
               di.outstanding, di.interest_rate
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        WHERE di.is_active = true
          AND di.maturity_date < CURRENT_DATE
        ORDER BY c.ticker, di.maturity_date
    '''))
    return result.fetchall()


async def deactivate_matured(session, dry_run=True, ticker=None):
    """Deactivate instruments that have matured."""
    print_subheader("STEP 1: DEACTIVATE MATURED INSTRUMENTS")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    result = await session.execute(text(f'''
        SELECT di.id, c.ticker, di.name, di.maturity_date,
               di.outstanding, di.interest_rate
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        WHERE di.is_active = true
          AND di.maturity_date < CURRENT_DATE
          {where_ticker}
        ORDER BY c.ticker, di.maturity_date
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No matured active instruments found.")
        return {'matured_count': 0, 'matured_amount_cents': 0}

    total_amount = 0
    by_ticker = defaultdict(list)
    for row in rows:
        di_id, tick, name, mat_date, outstanding, rate = row
        by_ticker[tick].append({
            'id': di_id, 'name': name, 'maturity_date': mat_date,
            'outstanding': outstanding, 'rate': rate,
        })
        total_amount += outstanding or 0

    print(f"  Found {len(rows)} matured instruments across {len(by_ticker)} companies")
    print(f"  Total outstanding: ${total_amount / 1e11:.2f}B")
    print()

    # Show top companies by count
    sorted_tickers = sorted(by_ticker.items(), key=lambda x: len(x[1]), reverse=True)
    for tick, instruments in sorted_tickers[:15]:
        amt = sum(i['outstanding'] or 0 for i in instruments) / 1e11
        print(f"    {tick:6s}: {len(instruments):3d} matured, ${amt:.2f}B")
    if len(sorted_tickers) > 15:
        print(f"    ... and {len(sorted_tickers) - 15} more companies")

    if dry_run:
        print(f"\n  [DRY RUN] Would deactivate {len(rows)} instruments (${total_amount / 1e11:.2f}B)")
    else:
        print(f"\n  Deactivating {len(rows)} instruments...")
        count = 0
        for tick, instruments in by_ticker.items():
            ids = [i['id'] for i in instruments]
            await session.execute(text('''
                UPDATE debt_instruments
                SET is_active = false,
                    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "matured"}'::jsonb,
                    updated_at = NOW()
                WHERE id = ANY(:ids)
            '''), {'ids': ids})
            await session.commit()
            count += len(ids)

        print(f"  Deactivated {count} instruments.")

    return {'matured_count': len(rows), 'matured_amount_cents': total_amount}


# =============================================================================
# STEP 2: Deduplicate by Rate + Maturity Year
# =============================================================================

def score_instrument(inst):
    """Score an instrument for keeper selection. Higher = better to keep."""
    score = 0
    if inst['cusip']:
        score += 4
    if inst['outstanding'] and inst['outstanding'] > 0:
        score += 3
    if inst['has_pricing']:
        score += 2
    if inst['isin']:
        score += 1
    if inst['has_doc_links']:
        score += 1
    return score


async def deduplicate(session, dry_run=True, ticker=None, verbose=False):
    """Deduplicate instruments by company_id + rate + maturity year."""
    print_subheader("STEP 2: DEDUPLICATE BY RATE + MATURITY YEAR")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    # Find duplicate groups: same company, same rate (rounded to 2 decimal %),
    # same maturity year. interest_rate is stored in bps (e.g., 850 = 8.50%).
    # ROUND(interest_rate / 100.0, 2) converts bps to percent with 2 decimals.
    result = await session.execute(text(f'''
        SELECT
            di.id, c.ticker, c.id as company_id, di.name,
            di.interest_rate, di.maturity_date,
            di.outstanding, di.principal, di.commitment,
            di.cusip, di.isin,
            di.rate_type, di.seniority, di.security_type,
            di.benchmark, di.spread_bps, di.floor_bps,
            di.instrument_type, di.issuer_id,
            di.created_at, di.attributes,
            COALESCE(di.interest_rate, 0) as rate_key,
            EXTRACT(YEAR FROM di.maturity_date)::int as mat_year
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        WHERE di.is_active = true
          AND di.maturity_date IS NOT NULL
          AND di.interest_rate IS NOT NULL
          {where_ticker}
        ORDER BY c.ticker, di.interest_rate, di.maturity_date, di.created_at
    '''), params)
    all_instruments = result.fetchall()

    if not all_instruments:
        print("  No instruments with rate + maturity to deduplicate.")
        return {'dedup_groups': 0, 'deactivated': 0, 'dedup_amount_cents': 0}

    # Get bond_pricing data to check which instruments have pricing
    pricing_result = await session.execute(text('''
        SELECT debt_instrument_id FROM bond_pricing
    '''))
    instruments_with_pricing = {row[0] for row in pricing_result.fetchall()}

    # Get document link counts
    doc_result = await session.execute(text('''
        SELECT debt_instrument_id, COUNT(*) as cnt
        FROM debt_instrument_documents
        GROUP BY debt_instrument_id
    '''))
    doc_counts = {row[0]: row[1] for row in doc_result.fetchall()}

    # Group by (company_id, rounded_rate_pct, maturity_year)
    groups = defaultdict(list)
    for row in all_instruments:
        (di_id, tick, company_id, name,
         interest_rate, maturity_date,
         outstanding, principal, commitment,
         cusip, isin,
         rate_type, seniority, security_type,
         benchmark, spread_bps, floor_bps,
         instrument_type, issuer_id,
         created_at, attributes,
         rate_key, mat_year) = row

        # Round rate to 2 decimal places of percentage
        # e.g., 295 bps and 295 bps both -> 2.95; 850 -> 8.50
        rate_pct_rounded = round(interest_rate / 100.0, 2) if interest_rate else 0

        group_key = (company_id, tick, rate_pct_rounded, mat_year)
        groups[group_key].append({
            'id': di_id,
            'ticker': tick,
            'company_id': company_id,
            'name': name,
            'interest_rate': interest_rate,
            'maturity_date': maturity_date,
            'outstanding': outstanding,
            'principal': principal,
            'commitment': commitment,
            'cusip': cusip,
            'isin': isin,
            'rate_type': rate_type,
            'seniority': seniority,
            'security_type': security_type,
            'benchmark': benchmark,
            'spread_bps': spread_bps,
            'floor_bps': floor_bps,
            'instrument_type': instrument_type,
            'issuer_id': issuer_id,
            'created_at': created_at,
            'attributes': attributes,
            'has_pricing': di_id in instruments_with_pricing,
            'has_doc_links': doc_counts.get(di_id, 0) > 0,
        })

    # Filter to groups with 2+ instruments (actual duplicates)
    dup_groups = {k: v for k, v in groups.items() if len(v) >= 2}

    if not dup_groups:
        print("  No duplicate groups found.")
        return {'dedup_groups': 0, 'deactivated': 0, 'dedup_amount_cents': 0}

    # Plan deduplication
    total_deactivated = 0
    total_amount_removed = 0
    merge_plans = []  # (keeper, losers, merged_fields)
    by_ticker_stats = defaultdict(lambda: {'groups': 0, 'deactivated': 0, 'amount': 0})

    for group_key, instruments in dup_groups.items():
        company_id, tick, rate_pct, mat_year = group_key

        # Score each instrument
        scored = [(score_instrument(inst), inst['created_at'], inst) for inst in instruments]
        scored.sort(key=lambda x: (-x[0], x[1]))  # highest score first, earliest created as tiebreaker

        keeper = scored[0][2]
        losers = [s[2] for s in scored[1:]]

        # Determine what to merge from losers to keeper
        merged_fields = {}
        for loser in losers:
            if not keeper['cusip'] and loser['cusip']:
                merged_fields['cusip'] = loser['cusip']
                keeper['cusip'] = loser['cusip']
            if not keeper['isin'] and loser['isin']:
                merged_fields['isin'] = loser['isin']
                keeper['isin'] = loser['isin']
            if (not keeper['outstanding'] or keeper['outstanding'] == 0) and loser['outstanding'] and loser['outstanding'] > 0:
                merged_fields['outstanding'] = loser['outstanding']
                keeper['outstanding'] = loser['outstanding']
            if (not keeper['principal'] or keeper['principal'] == 0) and loser['principal'] and loser['principal'] > 0:
                merged_fields['principal'] = loser['principal']
                keeper['principal'] = loser['principal']
            for field in ['rate_type', 'seniority', 'security_type', 'benchmark', 'spread_bps', 'floor_bps']:
                if not keeper[field] and loser[field]:
                    merged_fields[field] = loser[field]
                    keeper[field] = loser[field]

        merge_plans.append({
            'group_key': group_key,
            'keeper': keeper,
            'losers': losers,
            'merged_fields': merged_fields,
        })

        total_deactivated += len(losers)
        loser_amount = sum(l['outstanding'] or 0 for l in losers)
        total_amount_removed += loser_amount
        by_ticker_stats[tick]['groups'] += 1
        by_ticker_stats[tick]['deactivated'] += len(losers)
        by_ticker_stats[tick]['amount'] += loser_amount

    print(f"  Found {len(dup_groups)} duplicate groups")
    print(f"  Instruments to deactivate: {total_deactivated}")
    print(f"  Outstanding to remove: ${total_amount_removed / 1e11:.2f}B")
    print()

    # Show top companies
    sorted_stats = sorted(by_ticker_stats.items(), key=lambda x: x[1]['deactivated'], reverse=True)
    for tick, stats in sorted_stats[:20]:
        print(f"    {tick:6s}: {stats['groups']:3d} groups, {stats['deactivated']:3d} to deactivate, ${stats['amount'] / 1e11:.2f}B")
    if len(sorted_stats) > 20:
        print(f"    ... and {len(sorted_stats) - 20} more companies")

    if verbose:
        print()
        print_subheader("DEDUP DETAIL (sample groups)")
        shown = 0
        for plan in merge_plans:
            if shown >= 10:
                break
            gk = plan['group_key']
            keeper = plan['keeper']
            losers = plan['losers']
            merged = plan['merged_fields']
            print(f"\n  {gk[1]} | {gk[2]}% due {gk[3]}")
            print(f"    KEEP: {keeper['name'][:60]} (cusip={keeper['cusip']}, score={score_instrument(keeper)})")
            for loser in losers:
                print(f"    DROP: {loser['name'][:60]} (cusip={loser['cusip']}, score={score_instrument(loser)})")
            if merged:
                print(f"    MERGE: {list(merged.keys())}")
            shown += 1

    if dry_run:
        print(f"\n  [DRY RUN] Would deactivate {total_deactivated} instruments (${total_amount_removed / 1e11:.2f}B)")
    else:
        print(f"\n  Applying deduplication...")
        applied = 0

        # Group plans by company for per-company commits
        by_company = defaultdict(list)
        for plan in merge_plans:
            by_company[plan['group_key'][0]].append(plan)

        for company_id, plans in by_company.items():
            for plan in plans:
                keeper = plan['keeper']
                losers = plan['losers']
                merged_fields = plan['merged_fields']

                # 1. Merge fields to keeper
                if merged_fields:
                    set_clauses = []
                    merge_params = {'keeper_id': keeper['id']}
                    for field, value in merged_fields.items():
                        set_clauses.append(f"{field} = :{field}")
                        merge_params[field] = value
                    set_clauses.append("updated_at = NOW()")
                    await session.execute(text(f'''
                        UPDATE debt_instruments
                        SET {', '.join(set_clauses)}
                        WHERE id = :keeper_id
                    '''), merge_params)

                # 2. Transfer bond_pricing records from losers to keeper
                loser_ids = [l['id'] for l in losers]
                for loser in losers:
                    if loser['has_pricing']:
                        # Check if keeper already has pricing
                        existing = await session.execute(text('''
                            SELECT id FROM bond_pricing WHERE debt_instrument_id = :keeper_id
                        '''), {'keeper_id': keeper['id']})
                        if existing.fetchone():
                            # Keeper already has pricing, delete loser's
                            await session.execute(text('''
                                DELETE FROM bond_pricing WHERE debt_instrument_id = :loser_id
                            '''), {'loser_id': loser['id']})
                        else:
                            # Transfer pricing to keeper
                            await session.execute(text('''
                                UPDATE bond_pricing
                                SET debt_instrument_id = :keeper_id
                                WHERE debt_instrument_id = :loser_id
                            '''), {'keeper_id': keeper['id'], 'loser_id': loser['id']})

                # 3. Handle guarantees - transfer unique ones, delete duplicates
                for loser_id in loser_ids:
                    await session.execute(text('''
                        DELETE FROM guarantees
                        WHERE debt_instrument_id = :loser_id
                          AND guarantor_id IN (
                              SELECT guarantor_id FROM guarantees
                              WHERE debt_instrument_id = :keeper_id
                          )
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                    await session.execute(text('''
                        UPDATE guarantees SET debt_instrument_id = :keeper_id
                        WHERE debt_instrument_id = :loser_id
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                # 4. Handle collateral
                for loser_id in loser_ids:
                    await session.execute(text('''
                        DELETE FROM collateral
                        WHERE debt_instrument_id = :loser_id
                          AND collateral_type IN (
                              SELECT collateral_type FROM collateral
                              WHERE debt_instrument_id = :keeper_id
                          )
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                    await session.execute(text('''
                        UPDATE collateral SET debt_instrument_id = :keeper_id
                        WHERE debt_instrument_id = :loser_id
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                # 5. Handle document links
                for loser_id in loser_ids:
                    await session.execute(text('''
                        DELETE FROM debt_instrument_documents
                        WHERE debt_instrument_id = :loser_id
                          AND document_section_id IN (
                              SELECT document_section_id FROM debt_instrument_documents
                              WHERE debt_instrument_id = :keeper_id
                          )
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                    await session.execute(text('''
                        UPDATE debt_instrument_documents SET debt_instrument_id = :keeper_id
                        WHERE debt_instrument_id = :loser_id
                    '''), {'keeper_id': keeper['id'], 'loser_id': loser_id})

                # 6. Deactivate losers (soft delete)
                await session.execute(text('''
                    UPDATE debt_instruments
                    SET is_active = false,
                        attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "deduplicated"}'::jsonb,
                        updated_at = NOW()
                    WHERE id = ANY(:ids)
                '''), {'ids': loser_ids})

                applied += len(loser_ids)

            # Commit per company
            await session.commit()

        print(f"  Deactivated {applied} duplicate instruments.")

    return {
        'dedup_groups': len(dup_groups),
        'deactivated': total_deactivated,
        'dedup_amount_cents': total_amount_removed,
    }


# =============================================================================
# STEP 3: Fix Total-Debt-as-Per-Instrument
# =============================================================================

async def fix_totals(session, dry_run=True, ticker=None, verbose=False):
    """Fix instruments where LLM assigned total debt amount to each instrument."""
    print_subheader("STEP 3: FIX TOTAL-DEBT-AS-PER-INSTRUMENT")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    # Find companies where 3+ active instruments have identical outstanding amounts
    # and those identical amounts sum to >> total_debt
    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_amounts AS (
            SELECT
                di.company_id,
                c.ticker,
                di.outstanding,
                COUNT(*) as same_amount_count,
                ARRAY_AGG(di.id) as instrument_ids,
                ARRAY_AGG(di.name) as instrument_names
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE di.is_active = true
              AND di.outstanding IS NOT NULL
              AND di.outstanding > 0
              {where_ticker}
            GROUP BY di.company_id, c.ticker, di.outstanding
            HAVING COUNT(*) >= 3
        )
        SELECT
            ia.ticker,
            ia.outstanding,
            ia.same_amount_count,
            ia.instrument_ids,
            ia.instrument_names,
            lf.total_debt,
            ia.outstanding * ia.same_amount_count as combined_amount
        FROM instrument_amounts ia
        LEFT JOIN latest_financials lf ON lf.company_id = ia.company_id
        WHERE lf.total_debt IS NOT NULL
          AND ia.outstanding * ia.same_amount_count > lf.total_debt * 2
        ORDER BY ia.ticker
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No total-debt-as-per-instrument issues found.")
        return {'totals_fixed': 0}

    total_instruments = 0
    for row in rows:
        tick, outstanding, count, ids, names, total_debt, combined = row
        print(f"\n  {tick}: {count} instruments each with ${outstanding / 1e11:.2f}B")
        print(f"    Combined: ${combined / 1e11:.2f}B vs total debt ${total_debt / 1e11:.2f}B ({combined / total_debt:.1f}x)")
        if verbose:
            for name in names[:5]:
                print(f"      - {name[:70]}")
            if len(names) > 5:
                print(f"      ... and {len(names) - 5} more")
        total_instruments += count

    if dry_run:
        print(f"\n  [DRY RUN] Would clear outstanding on {total_instruments} instruments")
    else:
        print(f"\n  Clearing outstanding amounts...")
        cleared = 0
        for row in rows:
            tick, outstanding, count, ids, names, total_debt, combined = row
            await session.execute(text('''
                UPDATE debt_instruments
                SET outstanding = NULL,
                    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_cleared": "was_total_debt"}'::jsonb,
                    updated_at = NOW()
                WHERE id = ANY(:ids)
            '''), {'ids': list(ids)})
            await session.commit()
            cleared += count

        print(f"  Cleared outstanding on {cleared} instruments.")

    return {'totals_fixed': total_instruments}


# =============================================================================
# STEP 4: Fix Phase 6 Total-as-Per-Instrument
# =============================================================================

async def fix_phase6_totals(session, dry_run=True, ticker=None, verbose=False):
    """Clear Phase 6 backfill amounts where Gemini assigned total debt to every instrument.

    Detects the pattern: multiple Phase 6-backfilled instruments with identical
    outstanding amounts. If 3+ have the same amount, or 2 with same amount whose
    combined total exceeds 1.5x total_debt, clear the outstanding on all of them.
    """
    print_subheader("STEP 4: FIX PHASE 6 TOTAL-AS-PER-INSTRUMENT")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    # Get all Phase 6 backfilled instruments with amounts, grouped by company
    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        )
        SELECT
            di.id, c.ticker, di.company_id, di.name,
            di.outstanding, lf.total_debt
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        LEFT JOIN latest_financials lf ON lf.company_id = di.company_id
        WHERE di.is_active = true
          AND di.outstanding IS NOT NULL
          AND di.outstanding > 0
          AND di.attributes->>'amount_source' = 'doc_backfill'
          {where_ticker}
        ORDER BY c.ticker, di.outstanding DESC
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No Phase 6 backfilled instruments found.")
        return {'phase6_totals_cleared': 0}

    # Group by company
    by_company = defaultdict(list)
    company_total_debt = {}
    company_ticker = {}
    for row in rows:
        di_id, tick, company_id, name, outstanding, total_debt = row
        by_company[company_id].append({
            'id': di_id, 'name': name, 'outstanding': outstanding,
        })
        if total_debt:
            company_total_debt[company_id] = total_debt
        company_ticker[company_id] = tick

    total_cleared = 0
    clear_plans = []

    for company_id, instruments in by_company.items():
        tick = company_ticker[company_id]
        total_debt = company_total_debt.get(company_id)
        if not total_debt:
            continue

        # Group instruments by outstanding amount
        amount_groups = defaultdict(list)
        for inst in instruments:
            amount_groups[inst['outstanding']].append(inst)

        # Find the largest group of identical amounts
        largest_group = max(amount_groups.values(), key=len)
        largest_amount = largest_group[0]['outstanding']
        group_size = len(largest_group)

        should_clear = False
        combined = largest_amount * group_size
        # The pattern: Gemini assigned the total debt figure to each instrument,
        # so each instrument's amount ≈ total_debt (within 50%)
        amount_is_total = largest_amount > total_debt * 0.5
        if group_size >= 3 and amount_is_total:
            # 3+ instruments each with ~total_debt amount
            should_clear = True
        elif group_size == 2 and combined > total_debt * 1.5:
            # 2 with same amount — combined well exceeds total debt
            should_clear = True

        if should_clear:
            # Clear outstanding on ALL Phase 6 instruments with that amount
            to_clear = amount_groups[largest_amount]
            combined = largest_amount * len(to_clear)
            clear_plans.append({
                'company_id': company_id,
                'ticker': tick,
                'amount': largest_amount,
                'count': len(to_clear),
                'ids': [i['id'] for i in to_clear],
                'names': [i['name'] for i in to_clear],
                'total_debt': total_debt,
                'combined': combined,
            })
            total_cleared += len(to_clear)

    if not clear_plans:
        print("  No Phase 6 total-as-per-instrument patterns found.")
        return {'phase6_totals_cleared': 0}

    print(f"  Found {len(clear_plans)} companies with Phase 6 total-as-per-instrument pattern")
    print(f"  Total instruments to clear: {total_cleared}")
    print()

    for plan in clear_plans:
        print(f"  {plan['ticker']:6s}: {plan['count']:3d} instruments x ${plan['amount'] / 1e11:.2f}B = ${plan['combined'] / 1e11:.2f}B"
              f" (total debt: ${plan['total_debt'] / 1e11:.2f}B, {plan['combined'] / plan['total_debt']:.1f}x)")
        if verbose:
            for name in plan['names'][:5]:
                print(f"      - {name[:70]}")
            if len(plan['names']) > 5:
                print(f"      ... and {len(plan['names']) - 5} more")

    if dry_run:
        print(f"\n  [DRY RUN] Would clear outstanding on {total_cleared} instruments")
    else:
        print(f"\n  Clearing outstanding amounts...")
        cleared = 0
        for plan in clear_plans:
            await session.execute(text('''
                UPDATE debt_instruments
                SET outstanding = NULL,
                    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_cleared": "total_as_per_instrument_phase6"}'::jsonb,
                    updated_at = NOW()
                WHERE id = ANY(:ids)
            '''), {'ids': plan['ids']})
            await session.commit()
            cleared += plan['count']

        print(f"  Cleared outstanding on {cleared} instruments.")

    return {'phase6_totals_cleared': total_cleared}


# =============================================================================
# STEP 5: Fix Single Instruments Exceeding Total Debt
# =============================================================================

async def fix_outliers(session, dry_run=True, ticker=None, verbose=False):
    """Clear single instruments whose outstanding exceeds total company debt.

    For EXCESS_SIGNIFICANT companies, find instruments where one instrument's
    outstanding > company total_debt and amount_source is 'doc_backfill'.
    """
    print_subheader("STEP 5: FIX OUTLIER INSTRUMENTS (SINGLE > TOTAL DEBT)")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_stats AS (
            SELECT
                company_id,
                SUM(COALESCE(outstanding, 0)) as instruments_sum
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        )
        SELECT
            di.id, c.ticker, di.name, di.outstanding,
            lf.total_debt, ist.instruments_sum,
            di.attributes->>'amount_source' as amount_source
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        JOIN latest_financials lf ON lf.company_id = di.company_id
        JOIN instrument_stats ist ON ist.company_id = di.company_id
        WHERE di.is_active = true
          AND di.outstanding IS NOT NULL
          AND di.outstanding > lf.total_debt
          AND di.attributes->>'amount_source' = 'doc_backfill'
          AND ist.instruments_sum > lf.total_debt * 2
          {where_ticker}
        ORDER BY c.ticker, di.outstanding DESC
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No outlier instruments found.")
        return {'outliers_cleared': 0}

    print(f"  Found {len(rows)} instruments exceeding total company debt:")
    print()

    ids_to_clear = []
    for row in rows:
        di_id, tick, name, outstanding, total_debt, inst_sum, amt_src = row
        print(f"  {tick:6s}: {name[:60]}")
        print(f"    Outstanding: ${outstanding / 1e11:.2f}B > Total debt: ${total_debt / 1e11:.2f}B ({outstanding / total_debt:.1f}x)")
        ids_to_clear.append(di_id)

    if dry_run:
        print(f"\n  [DRY RUN] Would clear outstanding on {len(ids_to_clear)} instruments")
    else:
        print(f"\n  Clearing outstanding amounts...")
        await session.execute(text('''
            UPDATE debt_instruments
            SET outstanding = NULL,
                attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_cleared": "single_exceeds_total_debt"}'::jsonb,
                updated_at = NOW()
            WHERE id = ANY(:ids)
        '''), {'ids': ids_to_clear})
        await session.commit()
        print(f"  Cleared outstanding on {len(ids_to_clear)} instruments.")

    return {'outliers_cleared': len(ids_to_clear)}


# =============================================================================
# STEP 6: Fix Stale Amounts from Old Filings
# =============================================================================

async def fix_stale_amounts(session, dry_run=True, ticker=None, verbose=False):
    """Clear Phase 6 amounts from filings older than 5 years.

    Phase 6 sometimes pulled amounts from 10+ year old filings. If amount_doc_date
    is >5 years old, the amounts are likely wrong for current instruments.
    Only applies to EXCESS companies (instrument sum > 120% of total debt).
    """
    print_subheader("STEP 6: FIX STALE DOC AMOUNTS (>5 YEARS OLD)")

    where_ticker = ""
    params = {'cutoff_date': '2021-01-01'}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_stats AS (
            SELECT
                company_id,
                SUM(COALESCE(outstanding, 0)) as instruments_sum
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        ),
        excess_companies AS (
            SELECT ist.company_id
            FROM instrument_stats ist
            JOIN latest_financials lf ON lf.company_id = ist.company_id
            WHERE ist.instruments_sum > lf.total_debt * 1.2
        )
        SELECT
            di.id, c.ticker, di.name, di.outstanding,
            di.attributes->>'amount_doc_date' as doc_date,
            lf.total_debt
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        JOIN excess_companies ec ON ec.company_id = di.company_id
        JOIN latest_financials lf ON lf.company_id = di.company_id
        WHERE di.is_active = true
          AND di.outstanding IS NOT NULL
          AND di.outstanding > 0
          AND di.attributes->>'amount_source' = 'doc_backfill'
          AND di.attributes->>'amount_doc_date' IS NOT NULL
          AND di.attributes->>'amount_doc_date' < :cutoff_date
          {where_ticker}
        ORDER BY c.ticker, di.attributes->>'amount_doc_date'
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No stale doc amounts found.")
        return {'stale_cleared': 0}

    print(f"  Found {len(rows)} instruments with stale doc amounts (<2021):")
    print()

    by_ticker = defaultdict(list)
    for row in rows:
        di_id, tick, name, outstanding, doc_date, total_debt = row
        by_ticker[tick].append({
            'id': di_id, 'name': name, 'outstanding': outstanding,
            'doc_date': doc_date,
        })

    ids_to_clear = []
    tag_by_id = {}
    for tick, instruments in sorted(by_ticker.items()):
        total_stale = sum(i['outstanding'] for i in instruments)
        print(f"  {tick:6s}: {len(instruments)} stale instruments, ${total_stale / 1e11:.2f}B")
        for inst in instruments:
            doc_year = inst['doc_date'][:4] if inst['doc_date'] else '????'
            tag = f"stale_doc_amount_{doc_year}"
            if verbose:
                print(f"    - {inst['name'][:55]} (${inst['outstanding'] / 1e11:.3f}B, doc: {inst['doc_date']})")
            ids_to_clear.append(inst['id'])
            tag_by_id[inst['id']] = tag

    if dry_run:
        print(f"\n  [DRY RUN] Would clear outstanding on {len(ids_to_clear)} instruments")
    else:
        print(f"\n  Clearing outstanding amounts...")
        for di_id, tag in tag_by_id.items():
            tag_json = f'{{"amount_cleared": "{tag}"}}'
            await session.execute(text('''
                UPDATE debt_instruments
                SET outstanding = NULL,
                    attributes = COALESCE(attributes, '{}'::jsonb) || CAST(:tag_json AS jsonb),
                    updated_at = NOW()
                WHERE id = :id
            '''), {'id': di_id, 'tag_json': tag_json})
        await session.commit()
        print(f"  Cleared outstanding on {len(ids_to_clear)} instruments.")

    return {'stale_cleared': len(ids_to_clear)}


# =============================================================================
# STEP 7: Claude-Assisted Review of EXCESS_SIGNIFICANT Companies
# =============================================================================

LLM_REVIEW_PROMPT = """You are a credit analyst reviewing a company's debt instrument list.
The company's instrument outstanding amounts sum to MORE THAN DOUBLE their reported total debt,
indicating duplicates, aggregates, or wrong amounts that need to be cleaned up.

COMPANY: {ticker}
TOTAL DEBT (from financials): ${total_debt_b:.3f}B
INSTRUMENT SUM (active instruments): ${instrument_sum_b:.3f}B
COVERAGE RATIO: {coverage_pct:.0f}% (target: 80-120%)
GAP: ${gap_b:.3f}B excess

ACTIVE INSTRUMENTS:
{instrument_list}

TASK: Identify instruments that should be deactivated or have their amounts cleared.

Common patterns to look for:
1. AGGREGATE ENTRIES: A single line like "Senior Unsecured Notes $19B" that duplicates
   the sum of individual notes listed separately. The aggregate should be deactivated.
2. DUPLICATE INSTRUMENTS: Same bond appearing multiple times from different data sources
   (one may have CUSIP, another may not). Keep the one with more data.
3. WRONG AMOUNTS: Face value (original issuance) instead of current outstanding,
   or revolver capacity instead of amount drawn (revolvers are usually $0 drawn).
4. PRE-REORG DEBT: Bonds from before a bankruptcy/restructuring that should no longer be active.
5. TOTAL DEBT ASSIGNED TO INSTRUMENT: An instrument showing the company's total debt as its
   outstanding amount (common LLM extraction error).

For REVOLVERS: If a revolver shows capacity (e.g. "$3B Revolving Credit Facility" with $3B outstanding),
the drawn amount is usually $0 or a small fraction. Clear the amount unless there's evidence it's drawn.

Return ONLY valid JSON (no markdown, no code blocks):
{{
  "actions": [
    {{
      "instrument_index": <1-based index from the list above>,
      "action": "deactivate" | "clear_amount" | "keep",
      "reason": "aggregate_entry" | "duplicate" | "face_value_not_outstanding" | "capacity_not_drawn" | "pre_reorg" | "total_debt_as_amount" | "legitimate",
      "explanation": "<brief explanation of why>"
    }}
  ],
  "expected_coverage_after": "<estimated coverage % after changes>",
  "notes": "<brief summary of what was found and fixed>"
}}

IMPORTANT:
- Include an action for EVERY instrument (even "keep" actions).
- Only deactivate/clear what's clearly wrong. When in doubt, use "keep".
- After your changes, the remaining instrument sum should be close to ${total_debt_b:.3f}B (80-120%).
- Do NOT deactivate legitimate individual bonds just to reduce the total.
"""


def _format_instrument_for_prompt(idx, inst):
    """Format a single instrument for the LLM prompt."""
    parts = [f"  [{idx}] {inst['name']}"]
    parts.append(f"      Type: {inst['instrument_type']}")
    if inst['outstanding']:
        parts.append(f"      Outstanding: ${inst['outstanding'] / 1e11:.3f}B (${inst['outstanding'] / 1e9:.1f}M)")
    else:
        parts.append(f"      Outstanding: NULL")
    if inst['interest_rate']:
        parts.append(f"      Rate: {inst['interest_rate'] / 100:.2f}%")
    if inst['maturity_date']:
        parts.append(f"      Maturity: {inst['maturity_date']}")
    if inst['cusip']:
        parts.append(f"      CUSIP: {inst['cusip']}")
    if inst['amount_source']:
        parts.append(f"      Amount Source: {inst['amount_source']}")
    return '\n'.join(parts)


async def fix_llm_review(session, dry_run=True, ticker=None, verbose=False, excess_threshold=2.0):
    """Step 7: Use Claude to identify instruments to deactivate or clear in EXCESS companies."""
    threshold_pct = int(excess_threshold * 100)
    print_subheader(f"STEP 7: CLAUDE-ASSISTED REVIEW OF EXCESS COMPANIES (>{threshold_pct}%)")

    # Initialize Claude client
    claude_client = get_claude_client()
    if not claude_client:
        print("  ERROR: ANTHROPIC_API_KEY not set. Cannot run LLM review.")
        return {'llm_review_companies': 0, 'llm_deactivated': 0, 'llm_cleared': 0}

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    # Find EXCESS companies above the threshold (default: instrument_sum > total_debt * 2)
    params['threshold'] = excess_threshold
    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_stats AS (
            SELECT
                company_id,
                COUNT(*) as instrument_count,
                SUM(COALESCE(outstanding, 0)) as instruments_sum
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        )
        SELECT
            c.id as company_id, c.ticker,
            ist.instrument_count,
            ist.instruments_sum,
            lf.total_debt,
            ROUND(ist.instruments_sum::numeric / NULLIF(lf.total_debt, 0) * 100, 1) as coverage_pct
        FROM companies c
        JOIN latest_financials lf ON lf.company_id = c.id
        JOIN instrument_stats ist ON ist.company_id = c.id
        WHERE ist.instruments_sum > lf.total_debt * :threshold
          AND c.is_financial_institution IS NOT TRUE
          {where_ticker}
        ORDER BY ist.instruments_sum::numeric / NULLIF(lf.total_debt, 0) DESC
    '''), params)
    excess_companies = result.fetchall()

    if not excess_companies:
        print(f"  No EXCESS companies found above {threshold_pct}% threshold (excluding banks).")
        return {'llm_review_companies': 0, 'llm_deactivated': 0, 'llm_cleared': 0}

    print(f"  Found {len(excess_companies)} EXCESS companies (>{threshold_pct}% coverage, excluding banks)")
    print()
    for row in excess_companies:
        company_id, tick, count, inst_sum, total_debt, coverage_pct = row
        print(f"    {tick:6s}: {count:3d} instruments, ${float(inst_sum) / 1e11:.2f}B vs ${float(total_debt) / 1e11:.2f}B ({float(coverage_pct):.0f}%)")
    print()

    total_deactivated = 0
    total_cleared = 0
    total_cost = 0.0
    companies_processed = 0

    for row in excess_companies:
        company_id, tick, inst_count, inst_sum, total_debt, coverage_pct = row

        print(f"\n  --- {tick} ({float(coverage_pct):.0f}% coverage) ---")

        # Fetch all active instruments for this company
        inst_result = await session.execute(text('''
            SELECT
                di.id, di.name, di.instrument_type, di.outstanding,
                di.interest_rate, di.maturity_date, di.cusip,
                di.attributes->>'amount_source' as amount_source
            FROM debt_instruments di
            WHERE di.company_id = :company_id
              AND di.is_active = true
            ORDER BY COALESCE(di.outstanding, 0) DESC, di.name
        '''), {'company_id': company_id})
        instruments = []
        for irow in inst_result.fetchall():
            instruments.append({
                'id': irow[0],
                'name': irow[1],
                'instrument_type': irow[2],
                'outstanding': irow[3],
                'interest_rate': irow[4],
                'maturity_date': irow[5],
                'cusip': irow[6],
                'amount_source': irow[7],
            })

        if not instruments:
            print(f"    No active instruments found.")
            continue

        # Format instrument list for prompt
        instrument_lines = []
        for idx, inst in enumerate(instruments, 1):
            instrument_lines.append(_format_instrument_for_prompt(idx, inst))

        instrument_list = '\n'.join(instrument_lines)

        total_debt_b = float(total_debt) / 1e11
        instrument_sum_b = float(inst_sum) / 1e11
        gap_b = instrument_sum_b - total_debt_b

        prompt = LLM_REVIEW_PROMPT.format(
            ticker=tick,
            total_debt_b=total_debt_b,
            instrument_sum_b=instrument_sum_b,
            coverage_pct=float(coverage_pct),
            gap_b=gap_b,
            instrument_list=instrument_list,
        )

        # Call Claude
        try:
            response = call_claude(
                claude_client,
                prompt,
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                parse_json=True,
            )
            cost = calculate_cost(response)
            total_cost += cost
            print(f"    Claude response: {response.input_tokens} in + {response.output_tokens} out tokens (${cost:.4f})")
        except Exception as e:
            print(f"    ERROR calling Claude for {tick}: {e}")
            continue

        if not response.data:
            print(f"    ERROR: Could not parse Claude response as JSON")
            if verbose:
                print(f"    Raw response: {response.text[:500]}")
            continue

        actions = response.data.get('actions', [])
        notes = response.data.get('notes', '')
        expected_coverage = response.data.get('expected_coverage_after', 'unknown')

        if verbose and notes:
            print(f"    Notes: {notes}")
            print(f"    Expected coverage after: {expected_coverage}")

        # Process actions
        deactivate_ids = []
        clear_ids = []
        deactivate_details = []  # (id, reason, explanation)
        clear_details = []  # (id, reason, explanation)
        keep_count = 0

        for action in actions:
            idx = action.get('instrument_index')
            act = action.get('action', 'keep')
            reason = action.get('reason', 'unknown')
            explanation = action.get('explanation', '')

            if not idx or idx < 1 or idx > len(instruments):
                continue

            inst = instruments[idx - 1]

            if act == 'deactivate':
                deactivate_ids.append(inst['id'])
                deactivate_details.append((inst['id'], inst['name'], reason, explanation))
                if verbose:
                    print(f"    DEACTIVATE [{idx}] {inst['name'][:55]}")
                    print(f"      Reason: {reason} — {explanation}")
            elif act == 'clear_amount':
                clear_ids.append(inst['id'])
                clear_details.append((inst['id'], inst['name'], reason, explanation))
                if verbose:
                    print(f"    CLEAR [{idx}] {inst['name'][:55]}")
                    print(f"      Reason: {reason} — {explanation}")
            else:
                keep_count += 1

        print(f"    Actions: {len(deactivate_ids)} deactivate, {len(clear_ids)} clear, {keep_count} keep")

        if dry_run:
            if deactivate_details:
                print(f"    [DRY RUN] Would deactivate {len(deactivate_ids)} instruments:")
                for di_id, name, reason, explanation in deactivate_details:
                    print(f"      - {name[:60]} ({reason})")
            if clear_details:
                print(f"    [DRY RUN] Would clear amounts on {len(clear_ids)} instruments:")
                for di_id, name, reason, explanation in clear_details:
                    print(f"      - {name[:60]} ({reason})")
        else:
            now = datetime.now(timezone.utc).isoformat()

            # Apply deactivations
            for di_id, name, reason, explanation in deactivate_details:
                tag = json.dumps({
                    'deactivation_reason': f'llm_review_{reason}',
                    'llm_review_explanation': explanation,
                    'llm_review_at': now,
                })
                await session.execute(text('''
                    UPDATE debt_instruments
                    SET is_active = false,
                        attributes = COALESCE(attributes, '{}'::jsonb) || CAST(:tag AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                '''), {'id': di_id, 'tag': tag})

            # Apply amount clears
            for di_id, name, reason, explanation in clear_details:
                tag = json.dumps({
                    'amount_cleared': f'llm_review_{reason}',
                    'llm_review_explanation': explanation,
                    'llm_review_at': now,
                })
                await session.execute(text('''
                    UPDATE debt_instruments
                    SET outstanding = NULL,
                        attributes = COALESCE(attributes, '{}'::jsonb) || CAST(:tag AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                '''), {'id': di_id, 'tag': tag})

            await session.commit()
            print(f"    Applied: {len(deactivate_ids)} deactivated, {len(clear_ids)} cleared")

        total_deactivated += len(deactivate_ids)
        total_cleared += len(clear_ids)
        companies_processed += 1

    print(f"\n  Total cost: ${total_cost:.4f}")
    if dry_run:
        print(f"  [DRY RUN] Would process {companies_processed} companies: "
              f"{total_deactivated} deactivate, {total_cleared} clear amounts")
    else:
        print(f"  Processed {companies_processed} companies: "
              f"{total_deactivated} deactivated, {total_cleared} amounts cleared")

    return {
        'llm_review_companies': companies_processed,
        'llm_deactivated': total_deactivated,
        'llm_cleared': total_cleared,
        'llm_cost_usd': round(total_cost, 4),
    }


# =============================================================================
# STEP 8: Clear Revolver/ABL Capacity Amounts
# =============================================================================

REVOLVER_KEYWORDS = [
    'revolv', 'credit facility', 'credit agreement', 'abl', 'asset-based',
    'asset based', 'line of credit', 'borrowing base',
]


async def fix_revolver_capacity(session, dry_run=True, ticker=None, verbose=False):
    """Step 8: Clear revolver/ABL amounts that show facility capacity, not drawn amount.

    Revolvers and ABLs typically show their total capacity as 'outstanding' when
    the actual drawn amount is usually $0 or a small fraction. This clears those
    amounts for EXCESS companies where instrument sum > 120% of total debt.
    """
    print_subheader("STEP 8: CLEAR REVOLVER/ABL CAPACITY AMOUNTS")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_stats AS (
            SELECT
                company_id,
                SUM(COALESCE(outstanding, 0)) as instruments_sum
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        )
        SELECT di.id, c.ticker, di.name, di.outstanding, di.commitment,
               di.instrument_type, lf.total_debt, ist.instruments_sum
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        JOIN instrument_stats ist ON ist.company_id = di.company_id
        JOIN latest_financials lf ON lf.company_id = di.company_id
        WHERE di.is_active = true
          AND di.instrument_type IN ('revolver', 'abl')
          AND di.outstanding IS NOT NULL
          AND di.outstanding > 0
          AND ist.instruments_sum > lf.total_debt * 1.2
          {where_ticker}
        ORDER BY c.ticker, di.outstanding DESC
    '''), params)
    rows = result.fetchall()

    if not rows:
        print("  No revolver/ABL capacity amounts found in EXCESS companies.")
        return {'revolver_cleared': 0}

    # Filter to instruments whose names match revolver/ABL keywords (safety check)
    to_clear = []
    skipped = []
    for row in rows:
        di_id, tick, name, outstanding, commitment, inst_type, total_debt, inst_sum = row
        name_lower = (name or '').lower()
        if any(kw in name_lower for kw in REVOLVER_KEYWORDS) or inst_type in ('revolver', 'abl'):
            to_clear.append(row)
        else:
            skipped.append(row)

    if not to_clear:
        print("  No matching revolver/ABL instruments found.")
        if skipped and verbose:
            print(f"  Skipped {len(skipped)} instruments (no keyword match):")
            for row in skipped:
                print(f"    {row[1]:6s}: {row[2][:60]}")
        return {'revolver_cleared': 0}

    # Group by company for display
    by_ticker = defaultdict(list)
    for row in to_clear:
        di_id, tick, name, outstanding, commitment, inst_type, total_debt, inst_sum = row
        by_ticker[tick].append({
            'id': di_id, 'name': name, 'outstanding': outstanding,
            'commitment': commitment, 'type': inst_type,
            'total_debt': total_debt, 'inst_sum': inst_sum,
        })

    total_amount = sum(row[3] for row in to_clear)
    print(f"  Found {len(to_clear)} revolver/ABL capacity amounts across {len(by_ticker)} companies")
    print(f"  Total capacity to clear: ${total_amount / 1e11:.2f}B")
    print()

    for tick, instruments in sorted(by_ticker.items()):
        tick_amount = sum(i['outstanding'] for i in instruments)
        td = instruments[0]['total_debt']
        ist = instruments[0]['inst_sum']
        coverage = ist / td * 100 if td else 0
        print(f"  {tick:6s}: {len(instruments)} revolvers/ABLs, ${tick_amount / 1e11:.2f}B capacity "
              f"(coverage: {coverage:.0f}%, total debt: ${td / 1e11:.2f}B)")
        if verbose:
            for inst in instruments:
                print(f"    - {inst['name'][:60]} (${inst['outstanding'] / 1e11:.3f}B, type={inst['type']})")

    if skipped and verbose:
        print(f"\n  Skipped {len(skipped)} instruments (instrument_type matched but no keyword):")
        for row in skipped:
            print(f"    {row[1]:6s}: {row[2][:60]}")

    if dry_run:
        print(f"\n  [DRY RUN] Would clear outstanding on {len(to_clear)} revolver/ABL instruments (${total_amount / 1e11:.2f}B)")
    else:
        print(f"\n  Clearing revolver/ABL capacity amounts...")
        ids_to_clear = [row[0] for row in to_clear]
        await session.execute(text('''
            UPDATE debt_instruments
            SET outstanding = NULL,
                attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_cleared": "revolver_capacity"}'::jsonb,
                updated_at = NOW()
            WHERE id = ANY(:ids)
        '''), {'ids': ids_to_clear})
        await session.commit()
        print(f"  Cleared outstanding on {len(ids_to_clear)} instruments.")

    return {'revolver_cleared': len(to_clear)}


# =============================================================================
# ANALYZE MODE
# =============================================================================

async def analyze(session, ticker=None):
    """Show current state of excess coverage issues."""
    print_subheader("ANALYSIS: EXCESS COVERAGE ISSUES")

    where_ticker = ""
    params = {}
    if ticker:
        where_ticker = "AND c.ticker = :ticker"
        params['ticker'] = ticker.upper()

    # Overall excess companies
    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt, fiscal_year, fiscal_quarter
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        ),
        instrument_stats AS (
            SELECT
                company_id,
                COUNT(*) as instrument_count,
                SUM(COALESCE(outstanding, 0)) as instruments_sum
            FROM debt_instruments
            WHERE is_active = true
            GROUP BY company_id
        )
        SELECT
            c.ticker, c.name,
            ist.instrument_count,
            ist.instruments_sum,
            lf.total_debt,
            ROUND(ist.instruments_sum::numeric / NULLIF(lf.total_debt, 0) * 100, 1) as coverage_pct
        FROM companies c
        JOIN latest_financials lf ON lf.company_id = c.id
        JOIN instrument_stats ist ON ist.company_id = c.id
        WHERE ist.instruments_sum > lf.total_debt * 1.2
          {where_ticker}
        ORDER BY ist.instruments_sum::numeric / NULLIF(lf.total_debt, 0) DESC
    '''), params)
    excess_companies = result.fetchall()

    print(f"\n  Companies with EXCESS coverage (>120%): {len(excess_companies)}")
    print(f"  {'Ticker':6s}  {'Instruments':>5s}  {'Inst Sum':>12s}  {'Total Debt':>12s}  {'Coverage':>8s}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*12}  {'-'*12}  {'-'*8}")

    for row in excess_companies[:30]:
        tick, name, count, inst_sum, total_debt, coverage_pct = row
        print(f"  {tick:6s}  {count:5d}  ${float(inst_sum) / 1e11:10.2f}B  ${float(total_debt) / 1e11:10.2f}B  {float(coverage_pct):>7.1f}%")
    if len(excess_companies) > 30:
        print(f"  ... and {len(excess_companies) - 30} more")

    # Matured instruments summary
    matured = await analyze_matured(session)
    print(f"\n  Matured active instruments: {len(matured)}")
    if matured:
        matured_amount = sum(float(row[4] or 0) for row in matured) / 1e11
        print(f"  Matured outstanding: ${matured_amount:.2f}B")

    # Potential dedup groups
    result = await session.execute(text(f'''
        SELECT COUNT(*) as group_count, SUM(cnt - 1) as excess_count
        FROM (
            SELECT
                di.company_id,
                ROUND(di.interest_rate / 100.0, 2),
                EXTRACT(YEAR FROM di.maturity_date)::int,
                COUNT(*) as cnt
            FROM debt_instruments di
            JOIN companies c ON c.id = di.company_id
            WHERE di.is_active = true
              AND di.maturity_date IS NOT NULL
              AND di.interest_rate IS NOT NULL
              {where_ticker}
            GROUP BY di.company_id, ROUND(di.interest_rate / 100.0, 2), EXTRACT(YEAR FROM di.maturity_date)::int
            HAVING COUNT(*) >= 2
        ) sub
    '''), params)
    dedup_row = result.fetchone()
    if dedup_row and dedup_row[0]:
        print(f"\n  Potential dedup groups (rate+year): {dedup_row[0]}")
        print(f"  Instruments to deactivate: {dedup_row[1]}")

    # Total-debt-as-per-instrument candidates
    result = await session.execute(text(f'''
        WITH latest_financials AS (
            SELECT DISTINCT ON (company_id)
                company_id, total_debt
            FROM company_financials
            WHERE total_debt IS NOT NULL AND total_debt > 0
            ORDER BY company_id, fiscal_year DESC, fiscal_quarter DESC
        )
        SELECT c.ticker, di.outstanding, COUNT(*) as cnt,
               lf.total_debt
        FROM debt_instruments di
        JOIN companies c ON c.id = di.company_id
        LEFT JOIN latest_financials lf ON lf.company_id = di.company_id
        WHERE di.is_active = true
          AND di.outstanding IS NOT NULL
          AND di.outstanding > 0
          {where_ticker}
        GROUP BY c.ticker, di.outstanding, lf.total_debt
        HAVING COUNT(*) >= 3
           AND lf.total_debt IS NOT NULL
           AND di.outstanding * COUNT(*) > lf.total_debt * 2
        ORDER BY c.ticker
    '''), params)
    total_debt_rows = result.fetchall()
    if total_debt_rows:
        print(f"\n  Total-debt-as-per-instrument cases: {len(total_debt_rows)}")
        for row in total_debt_rows:
            tick, outstanding, cnt, total_debt = row
            print(f"    {tick}: {cnt} instruments x ${float(outstanding) / 1e11:.2f}B = ${float(outstanding) * cnt / 1e11:.2f}B (total debt: ${float(total_debt) / 1e11:.2f}B)")
    else:
        print(f"\n  Total-debt-as-per-instrument cases: 0")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Fix EXCESS debt coverage: dedup, deactivate matured, fix misattributed amounts"
    )
    parser.add_argument('--analyze', action='store_true', help='Analyze current state (default if no action specified)')
    parser.add_argument('--deactivate-matured', action='store_true', help='Step 1: Deactivate matured instruments')
    parser.add_argument('--deduplicate', action='store_true', help='Step 2: Deduplicate by rate + maturity year')
    parser.add_argument('--fix-totals', action='store_true', help='Step 3: Fix total-debt-as-per-instrument (3+ identical)')
    parser.add_argument('--fix-phase6-totals', action='store_true', help='Step 4: Fix Phase 6 total-as-per-instrument')
    parser.add_argument('--fix-outliers', action='store_true', help='Step 5: Fix single instruments exceeding total debt')
    parser.add_argument('--fix-stale', action='store_true', help='Step 6: Fix stale amounts from old filings (>5yr)')
    parser.add_argument('--fix-llm-review', action='store_true', help='Step 7: Claude-assisted review of EXCESS companies')
    parser.add_argument('--fix-revolver-capacity', action='store_true', help='Step 8: Clear revolver/ABL capacity amounts')
    parser.add_argument('--fix-all-excess', action='store_true', help='Run ALL steps 1-8 in order')
    parser.add_argument('--excess-threshold', type=float, default=2.0,
                        help='Coverage threshold for --fix-llm-review (default: 2.0 = 200%%)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without applying')
    parser.add_argument('--ticker', type=str, help='Process single company by ticker')
    parser.add_argument('--verbose', action='store_true', help='Show detailed output')
    args = parser.parse_args()

    # --fix-all-excess enables all steps
    if args.fix_all_excess:
        args.deactivate_matured = True
        args.deduplicate = True
        args.fix_totals = True
        args.fix_phase6_totals = True
        args.fix_outliers = True
        args.fix_stale = True
        args.fix_revolver_capacity = True  # Step 8 (before Step 7)
        args.fix_llm_review = True         # Step 7 (runs after Step 8)

    # Default to analyze if no action specified
    has_action = (args.deactivate_matured or args.deduplicate or args.fix_totals
                  or args.fix_phase6_totals or args.fix_outliers or args.fix_stale
                  or args.fix_revolver_capacity or args.fix_llm_review)
    if not has_action:
        args.analyze = True

    print_header("FIX EXCESS DEBT INSTRUMENTS")

    stats = {}

    async with get_db_session() as session:
        if args.analyze:
            await analyze(session, ticker=args.ticker)
            return

        if args.deactivate_matured:
            result = await deactivate_matured(session, dry_run=args.dry_run, ticker=args.ticker)
            stats.update(result)

        if args.deduplicate:
            result = await deduplicate(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_totals:
            result = await fix_totals(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_phase6_totals:
            result = await fix_phase6_totals(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_outliers:
            result = await fix_outliers(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_stale:
            result = await fix_stale_amounts(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_revolver_capacity:
            result = await fix_revolver_capacity(session, dry_run=args.dry_run, ticker=args.ticker, verbose=args.verbose)
            stats.update(result)

        if args.fix_llm_review:
            result = await fix_llm_review(session, dry_run=args.dry_run, ticker=args.ticker,
                                          verbose=args.verbose, excess_threshold=args.excess_threshold)
            stats.update(result)

    if stats:
        print_summary(stats)


if __name__ == "__main__":
    run_async(main())

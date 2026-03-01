# Bond Pricing (Finnhub Integration)

Bond pricing data comes from Finnhub, which sources from FINRA TRACE (same data as Bloomberg/Reuters).

## Automated Pricing Schedule

The APScheduler in `app/core/scheduler.py` runs these jobs (US/Eastern timezone):

| Time (ET) | Job | What It Does |
|-----------|-----|-------------|
| 11:00 AM | `refresh_current_prices()` | Fetch latest TRACE prices for stale bonds → `bond_pricing` |
| 3:00 PM | `refresh_current_prices()` | Second refresh of current prices |
| 6:00 PM | `refresh_treasury_yields()` | Update current-year treasury yield curves |
| 9:00 PM | `refresh_and_snapshot()` | Refresh prices + copy to `bond_pricing_history` for daily snapshot |
| Every 15 min | `check_and_alert()` | Check error rates, send Slack alerts |

The scheduler starts/stops with the FastAPI app lifecycle (`app/main.py`). No external cron needed.

**Key implementation details:**
- `copy_current_to_history()` uses `US/Eastern` date via `zoneinfo` (Railway runs UTC; without this, 9 PM ET = 2 AM UTC next day would save tomorrow's date)
- Daily snapshots insert in batches of 100 with per-batch commits to avoid Neon serverless timeout
- Only active, non-matured instruments are snapshotted (filtered via JOIN to `debt_instruments`)
- Errors are logged with structlog; failed batches roll back without blocking subsequent batches

**Past issue (fixed 2026-02-12):** Daily snapshots silently failed from Feb 9-12 because `copy_current_to_history()` tried to insert ~3,900 records in a single statement (timed out on Neon), and the exception was swallowed with no logging. Fix: batched inserts + error logging + timezone correction.

## Data Flow

```
Finnhub API (FINRA TRACE)           Treasury.gov
    ↓                                    ↓
scripts/update_pricing.py           scripts/backfill_treasury_yields.py
    ↓                                    ↓
bond_pricing table (current)        treasury_yield_history table
    ↓                                    ↓
scripts/collect_daily_pricing.py ←──────┘ (for spread calc)
    ↓
bond_pricing_history table (daily snapshots)
```

## Finnhub API Endpoints

| Endpoint | Purpose | Key Fields |
|----------|---------|------------|
| `GET /bond/price?isin={ISIN}` | Current pricing | close, yield, volume, timestamp |
| `GET /bond/profile?isin={ISIN}` | Bond characteristics | FIGI, callable, coupon_type |

**Note:** Finnhub requires ISIN, not CUSIP. Convert US CUSIPs by adding "US" prefix + Luhn check digit.

## Data Mapping

| Finnhub Field | DebtStack Column | Notes |
|---------------|------------------|-------|
| `close` | `bond_pricing.last_price` | Clean price as % of par (e.g., 94.25) |
| `yield` | `bond_pricing.ytm_bps` | Convert to basis points (6.82% → 682) |
| `volume` | `bond_pricing.last_trade_volume` | Face value in cents |
| `t` (timestamp) | `bond_pricing.last_trade_date` | Unix → datetime |
| `"Finnhub"` | `bond_pricing.price_source` | Track data source |

## Tables

**`bond_pricing`** — Current prices (one row per active, non-matured instrument):
- 3,064 records (all TRACE — estimated pricing removed)
- `last_price`: Clean price as % of par
- `ytm_bps`: Yield to maturity in basis points
- `spread_to_treasury_bps`: Spread over benchmark treasury
- `staleness_days`: Days since last trade
- `price_source`: "TRACE", "Finnhub", "estimated"

**`bond_pricing_history`** — Historical daily snapshots (active, non-matured only):
- 785,258 records
- `price_date`, `price`, `ytm_bps`, `spread_bps`, `volume`
- Unique constraint on (debt_instrument_id, price_date)

**`treasury_yield_history`** — Historical treasury yield curves:
- `yield_date`, `benchmark` (1M-30Y), `yield_pct`, `source`
- Coverage: 13,970 records from 2021-01-04 to present

## API Exposure

```bash
# Current prices (always included in bonds response)
GET /v1/bonds?has_pricing=true&fields=name,cusip,pricing
GET /v1/bonds?ticker=RIG&fields=name,cusip,pricing

# Historical prices (Business tier only)
GET /v1/pricing/history?cusip=76825DAJ7&from=2025-01-01&to=2026-01-27
```

**Note:** The `/v1/pricing` endpoint is deprecated (removal: 2026-06-01). Use `/v1/bonds?has_pricing=true` instead.

## Pricing Response Format

```json
{
  "name": "8.000% Senior Notes due 2027",
  "cusip": "76825DAJ7",
  "pricing": {
    "price": 94.25,
    "ytm_pct": 9.82,
    "spread_bps": 450,
    "as_of": "2026-01-24",
    "source": "Finnhub"
  }
}
```

## Scripts

```bash
# Update current prices
python scripts/update_pricing.py --all
python scripts/update_pricing.py --ticker CHTR

# Backfill treasury yields (free from Treasury.gov)
python scripts/backfill_treasury_yields.py --from-year 2021 --to-year 2026
python scripts/backfill_treasury_yields.py --stats

# Backfill historical bond pricing (requires Finnhub premium)
python scripts/backfill_pricing_history.py --all --days 1095
python scripts/backfill_pricing_history.py --all --with-spreads

# Daily pricing collection (for cron job)
python scripts/collect_daily_pricing.py --all
```

## Services

**`app/services/pricing_history.py`**: `fetch_historical_candles()`, `calculate_ytm_for_price()`, `calculate_spread_for_price()`, `backfill_bond_history()`, `copy_current_to_history()`

**`app/services/treasury_yields.py`**: `fetch_treasury_gov_yields()`, `backfill_treasury_yields()`, `get_treasury_yield_for_date()`, `get_treasury_curve_for_date()`

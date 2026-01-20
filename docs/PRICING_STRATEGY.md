# DebtStack.ai Pricing Strategy

Last Updated: 2026-01-19

## Overview

DebtStack uses a **credit-based pricing model** optimized for AI agents. Credits are consumed per API call, with costs varying by endpoint complexity.

### Design Principles

1. **Predictable** — Agents know the cost before making a call
2. **Simple** — Fixed credits per endpoint, no dynamic calculations
3. **Fair** — Complex operations (traversal, search) cost more than simple lookups
4. **Competitive** — Priced in line with other agent-focused APIs ($0.008-0.02/query)

---

## Credit Costs by Endpoint

| Endpoint | Credits | Rationale |
|----------|---------|-----------|
| `GET /v1/companies` | 1 | Simple database lookup |
| `GET /v1/bonds` | 1 | Simple database lookup |
| `GET /v1/bonds/resolve` | 1 | Simple database lookup |
| `GET /v1/pricing` | 1 | Simple database lookup |
| `GET /v1/companies/{ticker}/changes` | 2 | Snapshot comparison |
| `POST /v1/entities/traverse` | 3 | Graph traversal (recursive, expensive) |
| `GET /v1/documents/search` | 3 | Full-text search (FTS is expensive) |
| `POST /v1/batch` | Sum of operations | No batching discount |

### Examples

| Use Case | API Calls | Credits |
|----------|-----------|---------|
| Get one company's metrics | 1× companies | 1 |
| Get company + bonds + guarantors | 1× companies + 1× bonds + 1× traverse | 5 |
| Screen 50 companies by leverage | 1× companies | 1 |
| Search for covenant language | 1× documents/search | 3 |
| Full company analysis (batch) | 1× companies + 1× bonds + 1× traverse + 1× search | 8 |

---

## Pricing Tiers

| Plan | Credits/Month | Price | $/Credit | $/Simple Query | Best For |
|------|---------------|-------|----------|----------------|----------|
| **Free** | 1,000 | $0 | — | — | Testing, evaluation |
| **Starter** | 3,000 | $49 | $0.016 | $0.016 | Individual analysts, prototypes |
| **Growth** | 15,000 | $149 | $0.010 | $0.010 | Production agents, small teams |
| **Scale** | 50,000 | $399 | $0.008 | $0.008 | High-volume applications |
| **Enterprise** | Custom | Custom | Custom | Custom | Unlimited, SLA, support |

### What Each Tier Gets You

| Queries/Month (approx) | Free | Starter | Growth | Scale |
|------------------------|------|---------|--------|-------|
| Simple queries (1 credit) | 1,000 | 3,000 | 15,000 | 50,000 |
| Complex queries (3 credits) | 333 | 1,000 | 5,000 | 16,667 |
| Full analyses (8 credits) | 125 | 375 | 1,875 | 6,250 |

---

## Competitive Analysis

| Provider | Model | Effective $/Query | Notes |
|----------|-------|-------------------|-------|
| **Tavily** | Credits | $0.008 | Web search, 1-250 credits/query |
| **Polygon.io** | Subscription | ~$0.001 | Equity data, high volume |
| **Alpha Vantage** | Calls/min | $0.002 | Equity data, rate limited |
| **Intrinio** | Subscription | ~$0.01 | Financial data |
| **Bloomberg API** | Enterprise | N/A | $24K+/year |
| **DebtStack** | Credits | $0.008-0.016 | Credit data, unique |

### Positioning

- **Cheaper than Bloomberg/CapIQ** for credit data
- **Comparable to Tavily** for per-query costs
- **Premium to equity APIs** (justified by unique data)

---

## Free Tier Strategy

### Purpose
1. Enable testing and evaluation
2. Support hobbyists and learners
3. Drive adoption in AI agent ecosystem
4. Generate word-of-mouth

### Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| Credits/month | 1,000 | ~30 queries/day, enough to evaluate |
| Rate limit | 10 req/min | Prevent abuse |
| Data access | Full | No feature restrictions |
| Overage | Hard cap | No surprise bills |

### Conversion Triggers
- User hits 80% of monthly credits → prompt to upgrade
- User hits rate limit repeatedly → prompt to upgrade
- User on free tier for 30+ days with consistent usage → outreach

---

## Overage Policy

| Plan | Overage Handling |
|------|------------------|
| Free | Hard cap (blocked until next month) |
| Starter | Pay-as-you-go at $0.02/credit |
| Growth | Pay-as-you-go at $0.015/credit |
| Scale | Pay-as-you-go at $0.01/credit |
| Enterprise | Custom (usually unlimited) |

### Why Pay-as-you-go (not hard caps) for Paid Tiers
- Agents shouldn't break mid-workflow
- Users pay for what they use
- Encourages upgrade when overage becomes regular

---

## Implementation Requirements

### API Response Headers

Every response includes:
```
X-Credits-Used: 3
X-Credits-Remaining: 2,847
X-Credits-Limit: 3,000
X-Credits-Reset: 2026-02-01T00:00:00Z
```

### Database Schema

```sql
-- User accounts
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    api_key_hash VARCHAR(64) NOT NULL,
    tier VARCHAR(20) DEFAULT 'free',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Credit tracking
CREATE TABLE user_credits (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    credits_remaining DECIMAL(12,2) NOT NULL DEFAULT 1000,
    credits_monthly_limit INT NOT NULL DEFAULT 1000,
    overage_credits_used DECIMAL(12,2) DEFAULT 0,
    billing_cycle_start DATE NOT NULL,
    tier VARCHAR(20) NOT NULL DEFAULT 'free'
);

-- Usage log (for billing, analytics)
CREATE TABLE usage_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    endpoint VARCHAR(100) NOT NULL,
    credits_used DECIMAL(10,2) NOT NULL,
    response_status INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_usage_log_user_date ON usage_log(user_id, created_at);
CREATE INDEX idx_usage_log_date ON usage_log(created_at);
```

### Credit Deduction Logic

```python
ENDPOINT_CREDITS = {
    "/v1/companies": 1,
    "/v1/bonds": 1,
    "/v1/bonds/resolve": 1,
    "/v1/pricing": 1,
    "/v1/companies/{ticker}/changes": 2,
    "/v1/entities/traverse": 3,
    "/v1/documents/search": 3,
}

async def deduct_credits(user_id: UUID, endpoint: str, db: AsyncSession) -> bool:
    """Deduct credits for an API call. Returns False if insufficient credits."""
    credits_needed = ENDPOINT_CREDITS.get(endpoint, 1)

    # Get user's credit balance
    user_credits = await db.get(UserCredits, user_id)

    if user_credits.credits_remaining >= credits_needed:
        user_credits.credits_remaining -= credits_needed
        await db.commit()
        return True

    # Check if overage allowed (paid tiers)
    if user_credits.tier in ("starter", "growth", "scale"):
        user_credits.overage_credits_used += credits_needed
        await db.commit()
        return True

    # Free tier - hard cap
    return False
```

### Batch Endpoint Handling

```python
@router.post("/v1/batch")
async def batch_operations(request: BatchRequest, user: User = Depends(get_user)):
    # Calculate total credits BEFORE execution
    total_credits = sum(
        ENDPOINT_CREDITS.get(op.primitive, 1)
        for op in request.operations
    )

    # Check/deduct credits
    if not await deduct_credits(user.id, total_credits):
        raise HTTPException(402, "Insufficient credits")

    # Execute operations...
```

---

## Billing Integration

### Recommended: Stripe

- Metered billing for overages
- Subscription management for tiers
- Usage records API for credit tracking

### Stripe Products

| Product | Stripe Type | Price |
|---------|-------------|-------|
| Free | Free tier (no product) | $0 |
| Starter | Subscription + metered | $49/mo + overage |
| Growth | Subscription + metered | $149/mo + overage |
| Scale | Subscription + metered | $399/mo + overage |

### Overage Reporting

```python
# Report overage to Stripe at end of billing cycle
stripe.SubscriptionItem.create_usage_record(
    subscription_item_id,
    quantity=overage_credits_used,
    timestamp=billing_cycle_end,
    action='set'
)
```

---

## Pricing Communication

### On Website (debtstack.ai/pricing)

```
Simple, predictable pricing for AI agents.

Free        $0/mo     1,000 credits    Get started
Starter     $49/mo    3,000 credits    For individuals
Growth      $149/mo   15,000 credits   For production
Scale       $399/mo   50,000 credits   For high volume
Enterprise  Custom    Unlimited        Contact us

All plans include:
✓ Full API access (all 8 endpoints)
✓ 177 companies, 3,000+ entities, 1,800+ bonds
✓ Document search across SEC filings
✓ No feature restrictions
```

### In SDK/README

Keep it brief—link to pricing page:
```
## Pricing

DebtStack uses credit-based pricing.

| Plan | Credits/Month | Price |
|------|---------------|-------|
| Free | 1,000 | $0 |
| Starter | 3,000 | $49 |
| Growth | 15,000 | $149 |

Simple queries cost 1 credit. Complex queries (traversal, search) cost 3 credits.

Full pricing: [debtstack.ai/pricing](https://debtstack.ai/pricing)
```

### In LangChain/MCP Tool Descriptions

Include credit cost in tool descriptions so agents can budget:
```python
description = (
    "Search companies by leverage, sector, and risk flags. "
    "Costs 1 credit per call."
)
```

---

## Launch Pricing vs. Future Pricing

### Launch (2026 Q1)
- Generous free tier (1,000 credits) to drive adoption
- Competitive paid tiers to convert serious users
- No enterprise tier yet (handle manually)

### Future Considerations
- Reduce free tier if abuse occurs
- Add annual plans (2 months free)
- Volume discounts for 100K+ credits
- Endpoint-specific pricing adjustments based on actual costs

---

## Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Pricing model | Credits per endpoint | Predictable for agents |
| Credit costs | 1-3 per endpoint | Reflects actual complexity |
| Free tier | 1,000 credits | Enables evaluation, drives adoption |
| Paid tiers | $49-$399 | Competitive with Tavily, covers costs |
| Overage | Pay-as-you-go (paid), hard cap (free) | Don't break agents mid-workflow |

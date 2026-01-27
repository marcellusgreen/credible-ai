# DebtStack.ai Pricing Strategy

Last Updated: 2026-01-27

## Overview

DebtStack uses a **simple, tiered pricing model** optimized for AI agents and developers. The focus is on query limits and data access, with straightforward pricing that scales with usage.

### Design Principles

1. **Simple** — Three clear tiers: Free, Pro, Business
2. **Predictable** — Fixed query limits and pricing
3. **Value-based** — Higher tiers unlock more data and features
4. **Developer-friendly** — Generous free tier for testing and evaluation

---

## Pricing Tiers

| Plan | Price | Queries | Companies | Best For |
|------|-------|---------|-----------|----------|
| **Free** | $0/month | 25/day | 25 (curated sample) | Testing & evaluation |
| **Pro** | $49/month | Unlimited | 200+ (full coverage) | Production agents & developers |
| **Business** | $499/month | Unlimited | 200+ + custom requests | Hedge funds, PE shops, credit teams |

---

## Tier Details

### Free ($0/month)

**For testing & evaluation**

| Feature | Value |
|---------|-------|
| Queries/day | 25 |
| Companies | 25 (curated sample) |
| Endpoints | All |
| Bond pricing | Yes (updated throughout trading day) |
| Rate limit | 10 req/min |

**Ideal for:**
- Evaluating the API
- Building prototypes
- Learning and exploration
- Hobbyist projects

### Pro ($49/month)

**For production agents & developers**

| Feature | Value |
|---------|-------|
| Queries | Unlimited |
| Companies | 200+ (full coverage) |
| Endpoints | All |
| Bond pricing | Yes |
| Historical pricing | Yes |
| Rate limit | 120 req/min |

**Ideal for:**
- Production AI agents
- Developer applications
- Research tools
- Small teams

### Business ($499/month)

**For hedge funds, PE shops, credit teams**

Everything in Pro, plus:

| Feature | Value |
|---------|-------|
| Support | Priority (24hr response) |
| Company coverage | Custom requests |
| SLA | 99.9% uptime |
| Onboarding | Dedicated |
| Rate limit | 1000 req/min |

**Ideal for:**
- Hedge funds
- Private equity shops
- Credit research teams
- Financial institutions

---

## Credit Costs by Endpoint

All tiers have access to all endpoints. Each API call counts as 1 query.

| Endpoint | Description |
|----------|-------------|
| `GET /v1/companies` | Search and filter companies |
| `GET /v1/bonds` | Search and filter bonds |
| `GET /v1/bonds/resolve` | Resolve CUSIP/ISIN |
| `GET /v1/pricing` | Get bond pricing |
| `GET /v1/companies/{ticker}/changes` | Track structure changes |
| `POST /v1/entities/traverse` | Traverse entity graph |
| `GET /v1/documents/search` | Full-text search SEC filings |
| `POST /v1/batch` | Batch operations (counts as N queries) |

---

## Company Coverage

### Free Tier (25 companies)

Curated sample of well-known companies across sectors for testing:
- Technology: AAPL, MSFT, GOOGL, META, AMZN
- Retail: WMT, TGT, HD, COST
- Healthcare: JNJ, PFE, UNH
- Energy: XOM, CVX
- Financials: JPM, BAC, GS
- And more...

### Pro & Business Tiers (200+ companies)

Full coverage including:
- All major public companies with significant debt
- Investment-grade and high-yield issuers
- Cross-sector coverage
- Regular additions based on market activity

---

## Implementation Details

### Rate Limits

| Tier | Requests/Minute |
|------|-----------------|
| Free | 10 |
| Pro | 120 |
| Business | 1000 |

### API Response Headers

```
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 115
X-RateLimit-Reset: 1706140800
```

For free tier daily limits:
```
X-Queries-Used: 12
X-Queries-Remaining: 13
X-Queries-Limit: 25
X-Queries-Reset: 2026-01-28T00:00:00Z
```

---

## Billing Integration (Stripe)

### Stripe Products

| Plan | Stripe Product | Price ID |
|------|----------------|----------|
| Free | No product (tracked in DB) | — |
| Pro | Subscription | price_1StwgYAmvjlETourYUAbKPlB |
| Business | Subscription | price_1SuFq6AmvjlETourFzfIesa5 |

### Webhook Events

- `customer.subscription.created` → Upgrade user to Pro/Business
- `customer.subscription.updated` → Handle plan changes
- `customer.subscription.deleted` → Downgrade to Free
- `invoice.paid` → Reset billing cycle

---

## Conversion Strategy

### Free → Pro Triggers

- User hits 80% of daily queries → prompt to upgrade
- User hits rate limit repeatedly → prompt to upgrade
- User on free tier for 14+ days with consistent usage → email outreach
- User attempts to access non-sample company → show Pro upsell

### Pro → Business Triggers

- User requests custom company coverage
- User needs SLA guarantee
- User mentions institutional use case
- User inquires about support options

---

## Competitive Positioning

| Provider | Model | Price | Notes |
|----------|-------|-------|-------|
| Bloomberg API | Enterprise | $24K+/year | Full terminal data |
| CapIQ API | Enterprise | $15K+/year | S&P data |
| Refinitiv | Enterprise | $12K+/year | Reuters data |
| **DebtStack Pro** | Self-serve | $588/year | Credit-focused, AI-optimized |
| **DebtStack Business** | Self-serve | $5,988/year | With SLA and support |

### Value Proposition

- **10-40x cheaper** than enterprise terminals
- **Self-serve** — no sales calls for Pro tier
- **AI-optimized** — designed for agent consumption
- **Credit-focused** — unique corporate structure data

---

## Summary

| Aspect | Free | Pro | Business |
|--------|------|-----|----------|
| Price | $0/month | $49/month | $499/month |
| Queries | 25/day | Unlimited | Unlimited |
| Companies | 25 | 200+ | 200+ + custom |
| Rate limit | 10/min | 120/min | 1000/min |
| Support | Community | Standard | Priority (24hr) |
| SLA | None | None | 99.9% |
| Target | Testing | Production | Institutional |

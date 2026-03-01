# Agent User Journey & API Examples

The API supports a two-phase workflow for credit analysis.

## Phase 1: Discovery (Primitives API)

Screen and filter the bond universe using structured data:

```python
import requests
BASE = "https://api.debtstack.ai/v1"

# Find high-yield bonds with equipment collateral
r = requests.get(f"{BASE}/bonds", params={
    "min_ytm": 800,  # 8.0% in basis points
    "seniority": "senior_secured",
    "has_pricing": True,
    "fields": "name,cusip,ticker,ytm_pct,collateral"
})

# Compare leverage across companies
r = requests.get(f"{BASE}/companies", params={
    "ticker": "RIG,VAL,DO,NE",
    "fields": "ticker,name,net_leverage_ratio,total_debt",
    "sort": "-net_leverage_ratio"
})

# What are CHTR's financial covenants?
r = requests.get(f"{BASE}/covenants", params={
    "ticker": "CHTR",
    "covenant_type": "financial",
    "fields": "covenant_name,test_metric,threshold_value,threshold_type"
})

# Compare leverage covenants across cable companies
r = requests.get(f"{BASE}/covenants/compare", params={
    "ticker": "CHTR,ATUS,LUMN",
    "test_metric": "leverage_ratio"
})
```

## Phase 2: Deep Dive (Document Search)

Once user selects a specific bond, answer questions using document search:

```python
ticker = "RIG"

# Q: "What are the negative covenants?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "shall not covenant",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "Any make-whole premium for early redemption?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "make-whole redemption price treasury",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "What triggers an event of default?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "event of default failure to pay",
    "ticker": ticker,
    "section_type": "indenture"
})

# Q: "Can they pay dividends?"
r = requests.get(f"{BASE}/documents/search", params={
    "q": "restricted payment dividend distribution",
    "ticker": ticker,
    "section_type": "indenture"
})
```

## How It Works

```
Discovery                              Deep Dive
─────────────────────────             ─────────────────────────────
1. GET /v1/bonds?min_ytm=800
2. Filter results by collateral
3. User picks "RIG 8.75% 2030" ──────► 4. GET /v1/documents/search
                                          ?q=covenant&ticker=RIG
                                       5. Agent summarizes snippets
                                       6. User sees plain English answer
```

**DebtStack provides**: Structured data + document snippets + source links
**Agent provides**: Query conversion + summarization + presentation

## Additional Examples

```python
# Q: Which MAG7 company has highest leverage?
r = requests.get(f"{BASE}/companies", params={
    "ticker": "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA",
    "fields": "ticker,name,net_leverage_ratio",
    "sort": "-net_leverage_ratio",
    "limit": 1
})

# Q: Who guarantees this bond?
r = requests.post(f"{BASE}/entities/traverse", json={
    "start": {"type": "bond", "id": "893830AK8"},
    "relationships": ["guarantees"],
    "direction": "inbound"
})

# Q: Resolve bond identifier
r = requests.get(f"{BASE}/bonds/resolve", params={"q": "RIG 8% 2027"})
```

## Document Search Coverage

| Term | Docs Found | Use Case |
|------|------------|----------|
| "event of default" | 3,608 | Default triggers, grace periods |
| "change of control" | 2,050 | Put provisions, 101% repurchase |
| "collateral" | 1,752 | Security package analysis |
| "asset sale" | 976 | Mandatory prepayment triggers |
| "make-whole" | 679 | Early redemption premiums |
| "restricted payment" | 464 | Dividend/buyback restrictions |

See `docs/api/PRIMITIVES_API_SPEC.md` for full specification.

# DebtStack API Load Tests

Load tests for the DebtStack Primitives API using [Locust](https://locust.io/).

## Installation

```bash
pip install locust
```

## Configuration

Set these environment variables before running:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DEBTSTACK_API_KEY` | Yes | — | API key (`ds_...`) for authenticated requests |
| `DEBTSTACK_API_URL` | No | `https://api.debtstack.ai` | Base URL for the API |
| `EXPECTED_RATE_LIMIT` | No | `60` | Expected rate limit (rpm) for your tier |
| `MAX_USERS` | No | `100` | Max concurrent users for stress test |
| `STEP_USERS` | No | `10` | Users added per step in stress test |
| `STEP_DURATION` | No | `30` | Seconds per step in stress test |
| `SPAWN_RATE` | No | `2` | Users spawned per second during ramp |

```bash
export DEBTSTACK_API_KEY="ds_your_key_here"
export DEBTSTACK_API_URL="https://api.debtstack.ai"
```

## Test Scenarios

### 1. Baseline Load Test (`locustfile.py`)

Simulates realistic API usage across 4 user personas:

| Persona | Weight | Workflow |
|---------|--------|----------|
| CreditAnalystUser | 3 | Search companies → bonds → resolve → traverse |
| ScreeningUser | 2 | Filter bonds by yield/seniority, screen companies by leverage |
| ResearchUser | 1 | Document search, covenants, collateral, changes |
| BatchUser | 1 | Multi-primitive batch operations |

**Run headless:**
```bash
locust -f tests/load/locustfile.py --headless -u 5 -r 1 -t 60s
```

**Run with web UI:**
```bash
locust -f tests/load/locustfile.py
# Open http://localhost:8089
```

**Parameters:**
- `-u 5` — 5 concurrent users
- `-r 1` — spawn 1 user per second
- `-t 60s` — run for 60 seconds

### 2. Rate Limit Validation (`rate_limit_test.py`)

Validates rate limiting behavior. Contains 4 user classes (run one at a time via tags):

| Tag | User Class | Purpose |
|-----|-----------|---------|
| `under-limit` | UnderLimitUser | 75% of limit — should see zero 429s |
| `over-limit` | OverLimitUser | 120% of limit — should see 429s with headers |
| `burst` | BurstUser | Rapid burst → cooldown → verify window reset |
| `headers` | HeaderValidationUser | Check rate limit headers on every response |

```bash
# Test under-limit (should see 0 errors)
locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 120s \
    --tags under-limit

# Test over-limit (should see 429s with correct headers)
locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 120s \
    --tags over-limit

# Test burst + reset
locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 180s \
    --tags burst

# Validate headers present on all responses
locust -f tests/load/rate_limit_test.py --headless -u 1 -r 1 -t 60s \
    --tags headers

# For Pro tier (100 rpm)
EXPECTED_RATE_LIMIT=100 locust -f tests/load/rate_limit_test.py \
    --headless -u 1 -r 1 -t 120s --tags over-limit
```

### 3. Stress / Breaking Point Test (`stress_test.py`)

Ramps users in stages to find the degradation point. Uses a custom `StepLoadShape`.

**Default shape** (10 steps × 30s = 300s total):
```
Step 0:  10 users  (0-30s)
Step 1:  20 users  (30-60s)
Step 2:  30 users  (60-90s)
...
Step 9: 100 users  (270-300s)
```

**Run:**
```bash
locust -f tests/load/stress_test.py --headless -t 300s
```

**Custom ramp:**
```bash
MAX_USERS=200 STEP_USERS=20 STEP_DURATION=45 \
    locust -f tests/load/stress_test.py --headless -t 600s
```

**Latency thresholds:**
- **WARN**: p95 > 2,000ms
- **DANGER**: p95 > 5,000ms
- **CRITICAL (breaking point)**: p95 > 10,000ms

The test prints a summary table at the end showing per-step percentiles and where thresholds were breached.

## Interpreting Results

### Locust Output

Locust prints a summary table after each run:

```
Type     Name                    # reqs    # fails  Avg   Min   Max  Median  req/s
--------|----------------------|---------|---------|-----|-----|------|-------|------
GET      /v1/companies?ticker=X    150    0(0.00%)   89    45   312     78   2.50
GET      /v1/bonds?ticker=X        120    0(0.00%)  145    62   567    120   2.00
...
```

### Key Metrics

| Metric | Good | Acceptable | Investigate |
|--------|------|-----------|-------------|
| p50 latency | < 200ms | < 500ms | > 1,000ms |
| p95 latency | < 500ms | < 2,000ms | > 5,000ms |
| p99 latency | < 1,000ms | < 5,000ms | > 10,000ms |
| Error rate | 0% | < 1% | > 5% |
| 429 rate | Per-tier RPM | Slight over | Constant |

### Rate Limit Tiers

| Tier | Rate Limit | Expected Behavior |
|------|-----------|-------------------|
| Pay-as-you-go | 60 rpm | 429 after ~60 requests/minute |
| Pro | 100 rpm | 429 after ~100 requests/minute |
| Business | 500 rpm | 429 after ~500 requests/minute |

### HTML Reports

Save a detailed HTML report:
```bash
locust -f tests/load/locustfile.py --headless -u 5 -r 1 -t 60s \
    --html results/load_test_report.html
```

## Endpoint Coverage

All 10 public Primitives endpoints are tested:

| Endpoint | Locustfile | Rate Limit | Stress |
|----------|-----------|------------|--------|
| `GET /v1/companies` | CreditAnalyst, Screening | All | Yes |
| `GET /v1/bonds` | CreditAnalyst, Screening | — | Yes |
| `GET /v1/bonds/resolve` | CreditAnalyst | — | Yes |
| `GET /v1/financials` | Screening | — | Yes |
| `GET /v1/collateral` | Research | — | Yes |
| `GET /v1/covenants` | Research | — | Yes |
| `GET /v1/documents/search` | Research | — | Yes |
| `GET /v1/companies/{ticker}/changes` | Research | — | — |
| `POST /v1/entities/traverse` | CreditAnalyst, Research | — | Yes |
| `POST /v1/batch` | Batch | — | Yes |

## Safety Notes

- Default spawn rate is 1 user/sec — increase with caution against production
- Rate limit tests intentionally trigger 429s — be mindful of API costs
- The stress test uses shorter wait times (0.5-1.5s) to build pressure
- All tests treat 429 responses as expected (not failures) to avoid skewing error metrics
- Consider running against a staging environment for stress tests

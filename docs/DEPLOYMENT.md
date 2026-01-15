# DebtStack Deployment Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Cloudflare (CDN + DNS)                   │
│                    api.debtstack.ai                         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Railway (FastAPI)                         │
│                    debtstack-api.up.railway.app             │
└─────────────────────────────────────────────────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
┌─────────────────┐ ┌──────────────┐ ┌─────────────────┐
│ Neon PostgreSQL │ │ Upstash Redis│ │ Cloudflare R2   │
│ (database)      │ │ (cache)      │ │ (documents)     │
└─────────────────┘ └──────────────┘ └─────────────────┘
```

## Vendor Accounts

| Service | URL | Purpose | Free Tier |
|---------|-----|---------|-----------|
| Railway | https://railway.app | API hosting | $5/month credit |
| Neon | https://neon.tech | PostgreSQL | 512MB storage |
| Upstash | https://upstash.com | Redis cache | 10K commands/day |
| Cloudflare | https://cloudflare.com | CDN + R2 storage | 10GB R2 + unlimited CDN |

---

## Phase 1: Create Accounts (Do Now - 30 min)

### 1.1 Railway Account

```bash
# 1. Go to https://railway.app and sign up with GitHub

# 2. Install CLI
npm install -g @railway/cli

# 3. Login
railway login
```

### 1.2 Cloudflare Account

```bash
# 1. Go to https://cloudflare.com and sign up

# 2. Install Wrangler CLI
npm install -g wrangler

# 3. Login
wrangler login
```

### 1.3 Upstash Account

```
1. Go to https://upstash.com and sign up
2. No CLI needed - use web console
```

### 1.4 Neon Account (Already Done)

Your database is already on Neon. Just note your connection string:
```
DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/debtstack
```

---

## Phase 2: Prepare Codebase for Deployment

### 2.1 Create Production Config

Create `app/core/config.py`:

```python
"""Application configuration with environment-based settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Environment
    environment: str = "development"
    debug: bool = False

    # Database
    database_url: str

    # Redis (optional - for caching)
    redis_url: str | None = None

    # Cloudflare R2 (optional - for document storage)
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "debtstack-documents"

    # External APIs
    anthropic_api_key: str
    gemini_api_key: str | None = None
    sec_api_key: str | None = None
    finnhub_api_key: str | None = None

    # API Settings
    api_title: str = "DebtStack API"
    api_version: str = "1.0.0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
```

### 2.2 Create Dockerfile

Create `Dockerfile` in project root:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2.3 Create railway.json

Create `railway.json` in project root:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/v1/health",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

### 2.4 Update requirements.txt

Ensure `requirements.txt` has all production dependencies:

```
# Core
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0
pydantic-settings>=2.1.0

# Database
sqlalchemy[asyncio]>=2.0.25
asyncpg>=0.29.0
alembic>=1.13.0

# External APIs
anthropic>=0.18.0
google-generativeai>=0.3.0
httpx>=0.26.0
sec-api>=1.0.0

# Caching (optional)
redis>=5.0.0

# Cloud storage (optional)
boto3>=1.34.0

# Utilities
orjson>=3.9.0
python-dotenv>=1.0.0
```

### 2.5 Create .env.example

Create `.env.example` for reference:

```bash
# Environment
ENVIRONMENT=production
DEBUG=false

# Database (Neon)
DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/debtstack?sslmode=require

# Redis (Upstash) - Optional
REDIS_URL=redis://default:xxx@us1-xxx.upstash.io:6379

# Cloudflare R2 - Optional
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET_NAME=debtstack-documents

# External APIs
ANTHROPIC_API_KEY=sk-ant-xxx
GEMINI_API_KEY=xxx
SEC_API_KEY=xxx
FINNHUB_API_KEY=xxx
```

---

## Phase 3: Deploy to Railway

### 3.1 Initialize Railway Project

```bash
cd /path/to/credible

# Initialize new project
railway init

# Select "Empty Project" when prompted
```

### 3.2 Link to GitHub (Recommended)

```bash
# In Railway dashboard:
# 1. Go to your project
# 2. Click "New Service" → "GitHub Repo"
# 3. Select your repository
# 4. Railway will auto-deploy on push
```

Or deploy manually:

```bash
railway up
```

### 3.3 Set Environment Variables

```bash
# Set each variable
railway variables set DATABASE_URL="postgresql+asyncpg://..."
railway variables set ANTHROPIC_API_KEY="sk-ant-..."
railway variables set GEMINI_API_KEY="..."
railway variables set SEC_API_KEY="..."
railway variables set ENVIRONMENT="production"

# Or use Railway dashboard: Project → Variables
```

### 3.4 Run Database Migrations

```bash
# Connect to Railway shell
railway run alembic upgrade head
```

### 3.5 Verify Deployment

```bash
# Get your deployment URL
railway status

# Test health endpoint
curl https://your-app.up.railway.app/v1/health
```

---

## Phase 4: Set Up Upstash Redis (When Needed)

### 4.1 Create Redis Database

```
1. Go to https://console.upstash.com
2. Click "Create Database"
3. Select region: us-east-1 (closest to Neon)
4. Name: debtstack-cache
5. Copy the Redis URL
```

### 4.2 Add to Railway

```bash
railway variables set REDIS_URL="redis://default:xxx@us1-xxx.upstash.io:6379"
```

### 4.3 Add Redis Client to App

Create `app/core/cache.py`:

```python
"""Redis cache client."""

from typing import Optional
import redis.asyncio as redis
from app.core.config import get_settings


_redis_client: Optional[redis.Redis] = None


async def get_redis() -> Optional[redis.Redis]:
    """Get Redis client, creating if needed."""
    global _redis_client

    settings = get_settings()
    if not settings.redis_url:
        return None

    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    return _redis_client


async def close_redis():
    """Close Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# Cache helper functions
async def cache_get(key: str) -> Optional[str]:
    """Get value from cache."""
    client = await get_redis()
    if client:
        return await client.get(key)
    return None


async def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> bool:
    """Set value in cache with TTL."""
    client = await get_redis()
    if client:
        await client.setex(key, ttl_seconds, value)
        return True
    return False


async def cache_delete(key: str) -> bool:
    """Delete value from cache."""
    client = await get_redis()
    if client:
        await client.delete(key)
        return True
    return False
```

---

## Phase 5: Set Up Cloudflare R2 (When Needed)

### 5.1 Create R2 Bucket

```bash
# Using Wrangler CLI
wrangler r2 bucket create debtstack-documents

# Or via Cloudflare dashboard:
# 1. Go to R2 → Create bucket
# 2. Name: debtstack-documents
# 3. Location: Automatic
```

### 5.2 Create API Token

```
1. Cloudflare Dashboard → R2 → Manage R2 API Tokens
2. Create API Token
3. Permissions: Object Read & Write
4. Specify bucket: debtstack-documents
5. Copy the credentials
```

### 5.3 Add to Railway

```bash
railway variables set R2_ACCOUNT_ID="your_account_id"
railway variables set R2_ACCESS_KEY_ID="your_access_key"
railway variables set R2_SECRET_ACCESS_KEY="your_secret_key"
railway variables set R2_BUCKET_NAME="debtstack-documents"
```

### 5.4 Add R2 Client to App

Create `app/core/storage.py`:

```python
"""Cloudflare R2 storage client."""

import gzip
from typing import Optional
import boto3
from botocore.config import Config
from app.core.config import get_settings


_s3_client = None


def get_r2_client():
    """Get R2 client (S3-compatible)."""
    global _s3_client

    settings = get_settings()
    if not settings.r2_account_id:
        return None

    if _s3_client is None:
        _s3_client = boto3.client(
            's3',
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version='s3v4'),
        )

    return _s3_client


async def upload_document(key: str, content: str, compress: bool = True) -> bool:
    """Upload document to R2."""
    client = get_r2_client()
    if not client:
        return False

    settings = get_settings()

    if compress:
        body = gzip.compress(content.encode('utf-8'))
        content_encoding = 'gzip'
    else:
        body = content.encode('utf-8')
        content_encoding = None

    try:
        params = {
            'Bucket': settings.r2_bucket_name,
            'Key': key,
            'Body': body,
            'ContentType': 'text/plain',
        }
        if content_encoding:
            params['ContentEncoding'] = content_encoding

        client.put_object(**params)
        return True
    except Exception as e:
        print(f"R2 upload error: {e}")
        return False


async def download_document(key: str, decompress: bool = True) -> Optional[str]:
    """Download document from R2."""
    client = get_r2_client()
    if not client:
        return None

    settings = get_settings()

    try:
        response = client.get_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
        )
        body = response['Body'].read()

        if decompress and response.get('ContentEncoding') == 'gzip':
            body = gzip.decompress(body)

        return body.decode('utf-8')
    except client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"R2 download error: {e}")
        return None


async def delete_document(key: str) -> bool:
    """Delete document from R2."""
    client = get_r2_client()
    if not client:
        return False

    settings = get_settings()

    try:
        client.delete_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
        )
        return True
    except Exception as e:
        print(f"R2 delete error: {e}")
        return False


def get_document_url(key: str, expires_in: int = 3600) -> Optional[str]:
    """Get presigned URL for document (for large files)."""
    client = get_r2_client()
    if not client:
        return None

    settings = get_settings()

    try:
        url = client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.r2_bucket_name,
                'Key': key,
            },
            ExpiresIn=expires_in,
        )
        return url
    except Exception as e:
        print(f"R2 presign error: {e}")
        return None
```

---

## Phase 6: Custom Domain (Optional)

### 6.1 Add Domain to Cloudflare

```
1. Cloudflare Dashboard → Add Site
2. Enter: debtstack.ai (or your domain)
3. Update nameservers at your registrar
4. Wait for propagation (up to 24 hours)
```

### 6.2 Create DNS Record

```
1. Cloudflare DNS → Add Record
2. Type: CNAME
3. Name: api
4. Target: your-app.up.railway.app
5. Proxy status: Proxied (orange cloud)
```

### 6.3 Update Railway

```bash
# Add custom domain in Railway dashboard
# Project → Settings → Domains → Add Custom Domain
# Enter: api.debtstack.ai
```

---

## Deployment Checklist

### Before First Deploy

- [ ] Create Railway account and install CLI
- [ ] Create Cloudflare account
- [ ] Create Upstash account
- [ ] Verify Neon database connection
- [ ] Create `Dockerfile`
- [ ] Create `railway.json`
- [ ] Create `app/core/config.py`
- [ ] Update `requirements.txt`
- [ ] Test locally with production config

### First Deploy

- [ ] `railway init` in project directory
- [ ] Connect GitHub repo (or `railway up`)
- [ ] Set environment variables in Railway
- [ ] Run `railway run alembic upgrade head`
- [ ] Test `/v1/health` endpoint
- [ ] Test `/v1/companies` endpoint

### After Deploy (When Needed)

- [ ] Set up Upstash Redis
- [ ] Set up Cloudflare R2
- [ ] Configure custom domain
- [ ] Set up monitoring/alerts

---

## Monitoring & Maintenance

### Railway Logs

```bash
# View live logs
railway logs

# Or in dashboard: Project → Deployments → View Logs
```

### Health Checks

Railway automatically pings `/v1/health` and restarts if unhealthy.

### Database Backups

Neon provides automatic daily backups on Pro plan ($19/mo).

For free tier, run manual backups:

```bash
# Export database
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

### Cost Monitoring

| Service | Where to Check | Alert Threshold |
|---------|---------------|-----------------|
| Railway | Dashboard → Usage | $10/month |
| Neon | Dashboard → Usage | 400MB storage |
| Upstash | Console → Usage | 8K commands/day |
| Cloudflare R2 | Dashboard → R2 | 8GB storage |

---

## Troubleshooting

### Railway Deploy Fails

```bash
# Check build logs
railway logs --build

# Common issues:
# - Missing dependencies in requirements.txt
# - Python version mismatch
# - Environment variables not set
```

### Database Connection Issues

```bash
# Test connection
railway run python -c "from app.core.database import engine; print(engine.url)"

# Common issues:
# - Missing ?sslmode=require for Neon
# - Using psycopg2 instead of asyncpg
# - Wrong DATABASE_URL format
```

### Redis Connection Issues

```bash
# Test connection
railway run python -c "import redis; r = redis.from_url('$REDIS_URL'); print(r.ping())"

# Common issues:
# - Upstash requires TLS (use rediss:// not redis://)
# - Rate limit exceeded on free tier
```

---

## Cost Summary

| Phase | Monthly Cost |
|-------|--------------|
| **Phase 1-3** (Core) | ~$5-10 |
| **+ Redis** | +$0.20 |
| **+ R2 Storage** | +$0-1 |
| **+ Custom Domain** | +$0 (Cloudflare free) |
| **+ Neon Pro** (when needed) | +$19 |
| **Total (typical)** | **~$10-30/month** |

---

## Quick Reference

```bash
# Deploy
railway up

# View logs
railway logs

# Set variable
railway variables set KEY=value

# Run command
railway run <command>

# Open dashboard
railway open

# Connect to shell
railway shell
```

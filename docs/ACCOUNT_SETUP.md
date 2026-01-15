# Account Setup Guide

Step-by-step instructions to set up all vendor accounts for DebtStack deployment.

---

## 1. Railway (Hosting) - ~5 minutes

### Create Account

1. Go to **https://railway.app**
2. Click **"Login"** → **"Login with GitHub"**
3. Authorize Railway to access your GitHub

### Install CLI

```bash
# Using npm
npm install -g @railway/cli

# Or using brew (Mac)
brew install railway
```

### Login to CLI

```bash
railway login
```

This opens a browser window. Click **"Authorize"**.

### Verify Setup

```bash
railway whoami
# Should show your email
```

### What You Get

- **$5/month free credit** (enough for light usage)
- **Automatic deploys** from GitHub
- **Environment variables** management
- **Logs and monitoring**

---

## 2. Neon (PostgreSQL) - ~5 minutes

You likely already have this set up. If not:

### Create Account

1. Go to **https://console.neon.tech**
2. Click **"Sign Up"** → Use GitHub or email
3. Create a new project named `debtstack`

### Get Connection String

1. Go to your project **Dashboard**
2. Click **"Connection Details"**
3. Select **"Pooled connection"** (recommended)
4. Copy the connection string

### Format for DebtStack

```bash
# Neon gives you:
postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/debtstack?sslmode=require

# Change to asyncpg format:
postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/debtstack?sslmode=require
```

**Important:** Add `+asyncpg` after `postgresql` and keep `?sslmode=require`

### What You Get

- **512MB storage** on free tier
- **Automatic backups** (Pro tier)
- **Branching** for dev/staging

---

## 3. Upstash (Redis Cache) - ~3 minutes

### Create Account

1. Go to **https://console.upstash.com**
2. Click **"Sign Up"** → Use GitHub, Google, or email

### Create Redis Database

1. Click **"Create Database"**
2. **Name:** `debtstack-cache`
3. **Region:** `us-east-1` (closest to Neon)
4. **Type:** Regional (not Global)
5. Click **"Create"**

### Get Connection URL

1. Go to your database
2. Find **"Redis URL"** section
3. Click **"Copy"** on the `rediss://` URL (with TLS)

```bash
# You'll get something like:
rediss://default:AxxxYYY@us1-helping-termite-12345.upstash.io:6379
```

**Important:** Use `rediss://` (double s) for TLS connection

### What You Get

- **10,000 commands/day** free
- **Pay-as-you-go** after that ($0.2 per 100K commands)
- **Global replication** available

---

## 4. Cloudflare (R2 Storage + CDN) - ~10 minutes

### Create Account

1. Go to **https://dash.cloudflare.com/sign-up**
2. Enter email and password
3. Verify email

### Enable R2

1. In dashboard, go to **"R2"** in left sidebar
2. Click **"Get Started"**
3. Enter payment info (won't be charged for free tier)

### Create Bucket

1. Click **"Create bucket"**
2. **Name:** `debtstack-documents`
3. **Location:** Automatic
4. Click **"Create bucket"**

### Create API Token

1. Go to **R2 → "Manage R2 API Tokens"**
2. Click **"Create API token"**
3. **Token name:** `debtstack-api`
4. **Permissions:** `Object Read & Write`
5. **Bucket:** Select `debtstack-documents`
6. Click **"Create API Token"**

### Save Credentials

You'll see a screen with:
- **Access Key ID:** `xxxxxxxxxxxxx`
- **Secret Access Key:** `yyyyyyyyyyyyy`

**Copy these immediately - you can't see them again!**

Also note your **Account ID** (found in URL or R2 overview page):
- URL: `dash.cloudflare.com/[ACCOUNT_ID]/r2`

### What You Get

- **10GB storage** free
- **1M Class A ops** (writes) free
- **10M Class B ops** (reads) free
- **Unlimited egress** (huge savings vs S3!)

---

## 5. Anthropic (Claude API) - ~2 minutes

### Create Account

1. Go to **https://console.anthropic.com**
2. Sign up with email
3. Verify email and add payment method

### Get API Key

1. Go to **"API Keys"**
2. Click **"Create Key"**
3. **Name:** `debtstack-production`
4. Copy the key (starts with `sk-ant-`)

### Set Usage Limits (Recommended)

1. Go to **"Plans & Billing"** → **"Usage Limits"**
2. Set a monthly spend limit (e.g., $50)

### What You Get

- **Pay-per-use** pricing
- Claude Sonnet: ~$3 per 1M input tokens
- Claude Opus: ~$15 per 1M input tokens

---

## 6. Google AI Studio (Gemini) - ~2 minutes

### Create Account

1. Go to **https://aistudio.google.com**
2. Sign in with Google account
3. Accept terms of service

### Get API Key

1. Click **"Get API Key"** in left sidebar
2. Click **"Create API key"**
3. Select **"Create API key in new project"**
4. Copy the key

### What You Get

- **Free tier:** 60 requests/minute
- Gemini 2.0 Flash: Very cheap (~$0.008 per extraction)
- Used for Tier 1 extraction

---

## 7. SEC-API.io - ~3 minutes

### Create Account

1. Go to **https://sec-api.io**
2. Click **"Get Started"** or **"Sign Up"**
3. Create account with email

### Get API Key

1. Go to **Console** → **API Key**
2. Copy your API key

### Choose Plan

- **Free:** 100 requests/day
- **Basic ($49/mo):** 5,000 requests/day ← Recommended
- **Pro ($99/mo):** 20,000 requests/day

For batch extraction, you'll likely need Basic tier.

### What You Get

- Fast SEC filing retrieval
- Full-text search
- Filing download

---

## Summary: Environment Variables

After setting up all accounts, your `.env` should have:

```bash
# Required
DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/debtstack?sslmode=require
ANTHROPIC_API_KEY=sk-ant-api03-xxx

# Recommended
GEMINI_API_KEY=AIzaSy-xxx
SEC_API_KEY=xxx

# Optional (add when ready)
REDIS_URL=rediss://default:xxx@us1-xxx.upstash.io:6379
R2_ACCOUNT_ID=xxx
R2_ACCESS_KEY_ID=xxx
R2_SECRET_ACCESS_KEY=xxx
```

---

## Quick Reference Card

| Service | Dashboard URL | What to Copy |
|---------|--------------|--------------|
| Railway | railway.app/dashboard | (deploy via CLI) |
| Neon | console.neon.tech | Connection string |
| Upstash | console.upstash.com | Redis URL |
| Cloudflare R2 | dash.cloudflare.com/xxx/r2 | Account ID, Access Key, Secret |
| Anthropic | console.anthropic.com | API Key |
| Google AI | aistudio.google.com | API Key |
| SEC-API | sec-api.io/console | API Key |

---

## Estimated Setup Time

| Service | Time | Priority |
|---------|------|----------|
| Railway | 5 min | Do now |
| Neon | Already done | - |
| Anthropic | 2 min | Do now |
| Gemini | 2 min | Do now |
| SEC-API | 3 min | Do now |
| Upstash | 3 min | When deploying |
| Cloudflare R2 | 10 min | When adding documents |

**Total: ~25 minutes** for essential accounts

---

## Next Steps

After setting up accounts:

1. Copy `.env.example` to `.env`
2. Fill in your API keys
3. Test locally: `uvicorn app.main:app --reload`
4. When ready to deploy: `railway up`

See `docs/DEPLOYMENT.md` for full deployment instructions.

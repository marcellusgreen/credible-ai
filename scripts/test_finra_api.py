"""
Test FINRA API connection and explore available endpoints.

Usage:
    python scripts/test_finra_api.py
"""

import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import httpx

load_dotenv()

FINRA_CLIENT_ID = os.getenv("FINRA_CLIENT_ID")
FINRA_CLIENT_SECRET = os.getenv("FINRA_CLIENT_SECRET")
FINRA_TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
FINRA_API_BASE = "https://api.finra.org/data/group/fixedIncomeMarket/name"


async def get_token():
    """Get OAuth access token."""
    print("=" * 60)
    print("Testing FINRA API Authentication")
    print("=" * 60)

    if not FINRA_CLIENT_ID or not FINRA_CLIENT_SECRET:
        print("[X] FINRA credentials not found in environment")
        return None

    print(f"Client ID: {FINRA_CLIENT_ID[:8]}...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            FINRA_TOKEN_URL,
            auth=(FINRA_CLIENT_ID, FINRA_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        print(f"Token Response: {resp.status_code}")

        if resp.status_code != 200:
            print(f"[X] Token request failed: {resp.text}")
            return None

        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in")

        print(f"[OK] Got access token (expires in {expires_in}s)")
        return token


async def list_endpoints(token: str):
    """List available FINRA API endpoints."""
    print("\n" + "=" * 60)
    print("Exploring FINRA API Endpoints")
    print("=" * 60)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    # Try to get metadata about available groups
    metadata_url = "https://api.finra.org/data"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(metadata_url, headers=headers)
        print(f"\nData catalog: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Available groups: {data}")
        else:
            print(f"Response: {resp.text[:500]}")


async def test_bond_lookup(token: str, cusip: str = "037833EP2"):
    """Test looking up a specific bond."""
    print("\n" + "=" * 60)
    print(f"Testing Various FINRA Endpoints")
    print("=" * 60)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    # Try various endpoint patterns based on FINRA API docs
    test_urls = [
        # Group listing
        "https://api.finra.org/data/group",
        # Fixed income specific
        "https://api.finra.org/data/group/fixedIncomeMarket",
        # Treasury aggregates (known to exist)
        "https://api.finra.org/data/group/fixedIncomeMarket/name/treasuryDailyAggregates",
        "https://api.finra.org/data/group/fixedIncomeMarket/name/treasuryMonthlyAggregates",
        # Corporate bond data
        "https://api.finra.org/data/group/fixedIncomeMarket/name/corporateBondDailyAggregates",
        # Market breadth
        "https://api.finra.org/data/group/fixedIncomeMarket/name/marketBreadth",
        # OTC Markets
        "https://api.finra.org/data/group/otcMarket",
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in test_urls:
            print(f"\n[Testing] {url.split('/')[-1] or url.split('/')[-2]}")

            try:
                resp = await client.get(url, headers=headers, params={"$top": "3"})
                print(f"Status: {resp.status_code}")

                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        if isinstance(data, list):
                            print(f"[OK] Got {len(data)} records")
                            if len(data) > 0:
                                print(f"Keys: {list(data[0].keys())[:10]}...")
                        else:
                            print(f"[OK] Response: {str(data)[:200]}")
                    else:
                        print("[--] Empty response")
                elif resp.status_code == 204:
                    print("[--] No content")
                else:
                    print(f"Error: {resp.text[:150]}")

            except Exception as e:
                print(f"[X] Error: {e}")


async def main():
    token = await get_token()

    if not token:
        print("\nFailed to get token. Check your credentials.")
        return

    await list_endpoints(token)
    await test_bond_lookup(token)

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

"""
Explore FINRA bond search by CUSIP.
"""

import asyncio
from playwright.async_api import async_playwright


async def search_by_cusip(cusip: str = "037833EP2"):
    """Try to search for a bond by CUSIP."""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # Try direct URL with CUSIP as symbol
        print(f"Trying CUSIP as symbol: {cusip}")
        url = f"https://www.finra.org/finra-data/fixed-income/bond?symbol={cusip}&bondType=CORP"
        print(f"URL: {url}")

        await page.goto(url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)

        # Take screenshot
        await page.screenshot(path="finra_cusip_search.png")
        print("Screenshot saved: finra_cusip_search.png")

        # Check page content
        content = await page.content()
        if "not found" in content.lower() or "no results" in content.lower():
            print("Bond not found with CUSIP as symbol")
        else:
            print("Page loaded - checking for price data...")

        # Look for price elements
        price_selectors = [
            "[data-testid*='price']",
            ".price",
            "td:has-text('Price')",
            "span:has-text('$')",
            "[class*='price']",
            "[class*='Price']",
        ]

        for selector in price_selectors:
            try:
                elements = await page.query_selector_all(selector)
                if elements:
                    print(f"Found {len(elements)} elements for: {selector}")
                    for el in elements[:3]:
                        text = await el.inner_text()
                        print(f"  Text: {text[:100]}")
            except Exception as e:
                pass

        # Try the main search page
        print("\n" + "="*60)
        print("Now trying the search page...")

        await page.goto("https://www.finra.org/finra-data/fixed-income", timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)

        # Look for search input
        search_input = await page.query_selector("input[name='search_api_fulltext']")
        if search_input:
            print(f"Found search input, entering CUSIP: {cusip}")
            await search_input.fill(cusip)
            await search_input.press("Enter")
            await asyncio.sleep(5)

            await page.screenshot(path="finra_search_results.png")
            print("Screenshot saved: finra_search_results.png")

            # Check for results
            links = await page.query_selector_all("a[href*='bond?symbol']")
            print(f"Found {len(links)} bond links in results")
            for link in links[:5]:
                href = await link.get_attribute("href")
                text = await link.inner_text()
                print(f"  {text} -> {href}")

        print("\n" + "="*60)
        print("Browser open for inspection. Press Enter to close...")
        input()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(search_by_cusip())

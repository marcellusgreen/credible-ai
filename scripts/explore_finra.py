"""
Explore FINRA bond search page structure.

This script helps understand the page layout to build the scraper.
"""

import asyncio
from playwright.async_api import async_playwright


async def explore_finra():
    """Open FINRA and explore the bond search interface."""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Visible for debugging
        page = await browser.new_page()

        # Go to FINRA fixed income page
        print("Navigating to FINRA...")
        await page.goto("https://www.finra.org/finra-data/fixed-income/corp-and-agency", timeout=60000)

        # Wait for page to load (use domcontentloaded instead of networkidle)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)  # Give JS time to render
        print("Page loaded")

        # Take a screenshot
        await page.screenshot(path="finra_landing.png")
        print("Screenshot saved: finra_landing.png")

        # Look for search input
        print("\nLooking for search elements...")

        # Try various selectors
        selectors = [
            "input[type='search']",
            "input[placeholder*='CUSIP']",
            "input[placeholder*='search']",
            "input[name*='search']",
            "#search",
            ".search-input",
            "input[type='text']",
        ]

        for selector in selectors:
            elements = await page.query_selector_all(selector)
            if elements:
                print(f"  Found {len(elements)} elements for: {selector}")
                for i, el in enumerate(elements[:3]):
                    placeholder = await el.get_attribute("placeholder")
                    name = await el.get_attribute("name")
                    print(f"    [{i}] placeholder='{placeholder}', name='{name}'")

        # Look for iframes (FINRA might embed the data in an iframe)
        iframes = await page.query_selector_all("iframe")
        print(f"\nFound {len(iframes)} iframes")
        for i, iframe in enumerate(iframes):
            src = await iframe.get_attribute("src")
            print(f"  [{i}] src: {src}")

        # Check for any bond-related links
        print("\nLooking for bond search links...")
        links = await page.query_selector_all("a")
        for link in links:
            href = await link.get_attribute("href")
            text = await link.inner_text()
            if href and ("bond" in href.lower() or "search" in href.lower() or "cusip" in text.lower()):
                print(f"  Link: '{text[:50]}' -> {href}")

        # Keep browser open for manual inspection
        print("\n" + "="*60)
        print("Browser is open for manual inspection.")
        print("Press Enter to close...")
        input()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(explore_finra())

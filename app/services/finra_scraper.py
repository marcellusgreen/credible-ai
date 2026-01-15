"""
FINRA Bond Price Scraper using Playwright.

Scrapes bond pricing data from FINRA's Fixed Income Data portal.
https://www.finra.org/finra-data/fixed-income

Rate Limit: ~2-3 seconds between requests to be respectful.
"""

import asyncio
import re
from datetime import datetime
from decimal import Decimal
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page
from pydantic import BaseModel


class ScrapedBondPrice(BaseModel):
    """Bond pricing data scraped from FINRA."""

    cusip: str
    symbol: Optional[str] = None
    issuer_name: Optional[str] = None

    # Pricing
    last_price: Optional[Decimal] = None
    last_trade_date: Optional[datetime] = None
    last_trade_time: Optional[str] = None

    # Additional data
    coupon: Optional[Decimal] = None
    maturity_date: Optional[str] = None
    yield_pct: Optional[Decimal] = None
    rating_moody: Optional[str] = None
    rating_sp: Optional[str] = None

    # Metadata
    error: Optional[str] = None
    scraped_at: datetime = None

    def __init__(self, **data):
        if "scraped_at" not in data:
            data["scraped_at"] = datetime.now()
        super().__init__(**data)


class FINRAScraper:
    """
    Playwright-based scraper for FINRA bond pricing.

    Usage:
        async with FINRAScraper() as scraper:
            price = await scraper.get_bond_price("037833EP2")
            print(price)
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._initialized = False

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def initialize(self):
        """Initialize browser and accept terms."""
        if self._initialized:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page()

        # Navigate to FINRA fixed income page
        await self._page.goto(
            "https://www.finra.org/finra-data/fixed-income/bond",
            timeout=60000
        )
        await self._page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        # Handle dismissals
        await self._dismiss_modals()

        self._initialized = True

    async def _dismiss_modals(self):
        """Dismiss cookie consent, user notices, and other modals."""

        # 1. Cookie consent - click "Continue" button
        for _ in range(3):
            try:
                continue_btn = self._page.locator("button:has-text('Continue')")
                if await continue_btn.count() > 0 and await continue_btn.first.is_visible():
                    await continue_btn.first.click()
                    await asyncio.sleep(1)
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)

        # 2. Close User Notice modal - the X button is in the top right corner
        # It's rendered as an SVG inside a button or clickable element
        try:
            # The modal has "User Notice" heading and an X button
            # Try clicking outside the modal first (on the overlay)
            modal = self._page.locator("text=User Notice")
            if await modal.count() > 0 and await modal.first.is_visible():
                # Look for the close X button near the modal
                close_selectors = [
                    # SVG close icon
                    "svg[class*='close']",
                    "button svg",
                    "[role='dialog'] button",
                    # Generic close patterns
                    "button[aria-label='Close']",
                    "button[aria-label='close']",
                    ".close",
                    "[class*='CloseIcon']",
                    "[class*='close-icon']",
                    # The actual button might just be styled as X
                    "button:has-text('✕')",
                    "button:has-text('✖')",
                ]

                for selector in close_selectors:
                    try:
                        btn = self._page.locator(selector)
                        if await btn.count() > 0:
                            for i in range(await btn.count()):
                                try:
                                    if await btn.nth(i).is_visible():
                                        await btn.nth(i).click(timeout=2000)
                                        await asyncio.sleep(0.5)
                                        # Check if modal is gone
                                        if await modal.count() == 0 or not await modal.first.is_visible():
                                            break
                                except Exception:
                                    continue
                    except Exception:
                        continue

                # If modal still visible, try pressing Escape
                if await modal.count() > 0 and await modal.first.is_visible():
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)

                # If still visible, try clicking outside the modal
                if await modal.count() > 0 and await modal.first.is_visible():
                    await self._page.mouse.click(10, 10)
                    await asyncio.sleep(0.5)

        except Exception:
            pass

        # 3. Accept the user agreement checkbox if visible
        try:
            checkbox = self._page.locator("input[type='checkbox']")
            if await checkbox.count() > 0 and await checkbox.first.is_visible():
                is_checked = await checkbox.first.is_checked()
                if not is_checked:
                    await checkbox.first.click()
                    await asyncio.sleep(0.5)
        except Exception:
            pass

        await asyncio.sleep(0.5)

    async def close(self):
        """Close browser."""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._initialized = False

    async def get_bond_price(self, cusip: str) -> ScrapedBondPrice:
        """
        Get bond price by CUSIP.

        Args:
            cusip: 9-character CUSIP identifier

        Returns:
            ScrapedBondPrice with pricing data or error
        """
        if not self._initialized:
            await self.initialize()

        cusip = cusip.strip().upper()

        try:
            # Navigate to the fixed income search page
            await self._page.goto(
                "https://www.finra.org/finra-data/fixed-income/bond",
                timeout=30000
            )
            await self._page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            # Handle modals
            await self._dismiss_modals()

            # Check the agreement checkbox
            try:
                checkbox = self._page.locator("input[type='checkbox']")
                if await checkbox.count() > 0:
                    if not await checkbox.first.is_checked():
                        await checkbox.first.click()
                        await asyncio.sleep(0.5)
            except Exception:
                pass

            # Find and use the search box
            search_input = self._page.locator("input[placeholder*='symbol or CUSIP']")
            if await search_input.count() == 0:
                search_input = self._page.locator("input[placeholder*='Look up']")

            if await search_input.count() == 0:
                return ScrapedBondPrice(cusip=cusip, error="Search input not found")

            # Enter CUSIP and search
            await search_input.first.fill(cusip)
            await asyncio.sleep(0.5)

            # Press Enter or click search button
            await search_input.first.press("Enter")
            await asyncio.sleep(3)

            # Handle modals that may appear after search
            await self._dismiss_modals()

            # Check if bond was found
            page_content = await self._page.content()
            page_text = await self._page.inner_text("body")

            if "no data to display" in page_text.lower():
                return ScrapedBondPrice(cusip=cusip, error="No data to display")

            if "no results" in page_text.lower():
                return ScrapedBondPrice(cusip=cusip, error="No results found")

            # Check if we got redirected to a bond detail page
            current_url = self._page.url
            if "symbol=" in current_url:
                # We found a bond, extract data
                return await self._extract_bond_data(cusip)

            # Look for bond links in search results
            bond_links = self._page.locator("a[href*='bond?symbol=']")
            if await bond_links.count() > 0:
                # Click the first bond result
                await bond_links.first.click()
                await asyncio.sleep(2)
                await self._dismiss_modals()
                return await self._extract_bond_data(cusip)

            return ScrapedBondPrice(cusip=cusip, error="Bond not found in search results")

        except Exception as e:
            return ScrapedBondPrice(cusip=cusip, error=f"Scraping error: {str(e)}")

    async def _extract_bond_data(self, cusip: str) -> ScrapedBondPrice:
        """Extract bond data from the current page."""

        result = ScrapedBondPrice(cusip=cusip)

        # Try to find price in various formats
        # FINRA typically shows "Last Sale Price" or similar

        # Method 1: Look for specific data elements
        try:
            # Get all text content
            body = await self._page.query_selector("body")
            all_text = await body.inner_text() if body else ""

            # Parse price - look for patterns like "Last Sale: 98.500" or "Price: 98.500"
            price_patterns = [
                r"Last\s*Sale[:\s]*\$?([\d.]+)",
                r"Price[:\s]*\$?([\d.]+)",
                r"Last[:\s]*\$?([\d.]+)",
            ]

            for pattern in price_patterns:
                match = re.search(pattern, all_text, re.IGNORECASE)
                if match:
                    try:
                        result.last_price = Decimal(match.group(1))
                        break
                    except Exception:
                        continue

            # Parse yield
            yield_match = re.search(r"Yield[:\s]*([\d.]+)\s*%?", all_text, re.IGNORECASE)
            if yield_match:
                try:
                    result.yield_pct = Decimal(yield_match.group(1))
                except Exception:
                    pass

            # Parse issuer name
            issuer_match = re.search(r"Issuer[:\s]*([^\n]+)", all_text, re.IGNORECASE)
            if issuer_match:
                result.issuer_name = issuer_match.group(1).strip()[:100]

            # Parse trade date
            date_match = re.search(r"Trade\s*Date[:\s]*(\d{1,2}/\d{1,2}/\d{2,4})", all_text, re.IGNORECASE)
            if date_match:
                try:
                    date_str = date_match.group(1)
                    for fmt in ["%m/%d/%Y", "%m/%d/%y"]:
                        try:
                            result.last_trade_date = datetime.strptime(date_str, fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # Parse ratings
            moody_match = re.search(r"Moody['\"]?s?[:\s]*([A-Za-z0-9+-]+)", all_text, re.IGNORECASE)
            if moody_match:
                result.rating_moody = moody_match.group(1)

            sp_match = re.search(r"S&P[:\s]*([A-Za-z0-9+-]+)", all_text, re.IGNORECASE)
            if sp_match:
                result.rating_sp = sp_match.group(1)

        except Exception as e:
            if not result.error:
                result.error = f"Parse error: {str(e)}"

        # Method 2: Try to find data in table cells
        try:
            rows = await self._page.query_selector_all("tr")
            for row in rows:
                cells = await row.query_selector_all("td, th")
                if len(cells) >= 2:
                    label = await cells[0].inner_text()
                    value = await cells[1].inner_text()

                    label_lower = label.lower().strip()
                    value = value.strip()

                    if "last" in label_lower and "price" in label_lower and not result.last_price:
                        try:
                            clean_val = re.sub(r"[^\d.]", "", value)
                            if clean_val:
                                result.last_price = Decimal(clean_val)
                        except Exception:
                            pass

                    elif "yield" in label_lower and not result.yield_pct:
                        try:
                            clean_val = re.sub(r"[^\d.]", "", value)
                            if clean_val:
                                result.yield_pct = Decimal(clean_val)
                        except Exception:
                            pass

        except Exception:
            pass

        return result

    async def get_bond_prices_batch(
        self,
        cusips: list[str],
        delay: float = 3.0,
        progress_callback=None,
    ) -> list[ScrapedBondPrice]:
        """
        Get prices for multiple CUSIPs.

        Args:
            cusips: List of CUSIP identifiers
            delay: Seconds to wait between requests
            progress_callback: Optional callback(current, total, cusip, result)

        Returns:
            List of ScrapedBondPrice results
        """
        results = []

        for i, cusip in enumerate(cusips):
            result = await self.get_bond_price(cusip)
            results.append(result)

            if progress_callback:
                progress_callback(i + 1, len(cusips), cusip, result)

            # Rate limit
            if i < len(cusips) - 1:
                await asyncio.sleep(delay)

        return results


async def test_scraper():
    """Test the scraper with a sample CUSIP."""
    # Apple bond CUSIP
    test_cusips = [
        "037833EP2",  # Apple
    ]

    print("Testing FINRA scraper...")
    print("=" * 60)

    async with FINRAScraper(headless=False) as scraper:
        for cusip in test_cusips:
            print(f"\nLooking up: {cusip}")

            # Take screenshot for debugging
            await scraper._page.screenshot(path=f"debug_{cusip}.png")
            print(f"  Screenshot saved: debug_{cusip}.png")

            # Print page text for debugging
            body = await scraper._page.query_selector("body")
            if body:
                text = await body.inner_text()
                print(f"\n--- Page Text (first 2000 chars) ---")
                print(text[:2000])
                print("--- End ---\n")

            result = await scraper.get_bond_price(cusip)

            # Take another screenshot after lookup
            await scraper._page.screenshot(path=f"debug_{cusip}_after.png")

            if result.error:
                print(f"  Error: {result.error}")
            else:
                print(f"  Price: {result.last_price}")
                print(f"  Yield: {result.yield_pct}")
                print(f"  Issuer: {result.issuer_name}")
                print(f"  Trade Date: {result.last_trade_date}")

            await asyncio.sleep(2)

    print("\n" + "=" * 60)
    print("Test complete")


if __name__ == "__main__":
    asyncio.run(test_scraper())

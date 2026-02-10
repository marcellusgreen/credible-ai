"""
Debug script to understand and close the FINRA User Notice modal.
"""

import asyncio
from playwright.async_api import async_playwright


async def debug_modal():
    """Debug the FINRA modal."""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("Navigating to FINRA...")
        await page.goto(
            "https://www.finra.org/finra-data/fixed-income/bond",
            timeout=60000
        )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)

        # Take initial screenshot
        await page.screenshot(path="debug_modal_1.png")
        print("Screenshot 1 saved")

        # First, handle cookie consent
        print("\n1. Looking for cookie Continue button...")
        continue_btn = page.locator("button:has-text('Continue')")
        if await continue_btn.count() > 0:
            print(f"   Found {await continue_btn.count()} Continue buttons")
            if await continue_btn.first.is_visible():
                print("   Clicking Continue...")
                await continue_btn.first.click()
                await asyncio.sleep(1)

        await page.screenshot(path="debug_modal_2.png")
        print("Screenshot 2 saved")

        # Now look for the User Notice modal
        print("\n2. Looking for User Notice modal...")
        modal_text = page.locator("text=User Notice")
        if await modal_text.count() > 0:
            print(f"   Modal found (count: {await modal_text.count()})")
            print(f"   Visible: {await modal_text.first.is_visible()}")

        # Get all clickable elements in the modal area
        print("\n3. Looking for close buttons...")
        all_buttons = await page.query_selector_all("button")
        print(f"   Found {len(all_buttons)} total buttons")

        for i, btn in enumerate(all_buttons[:10]):
            try:
                text = await btn.inner_text()
                is_visible = await btn.is_visible()
                bbox = await btn.bounding_box()
                print(f"   [{i}] text='{text[:30]}' visible={is_visible} bbox={bbox}")
            except Exception as e:
                print(f"   [{i}] Error: {e}")

        # Look specifically for SVG close icons
        print("\n4. Looking for SVG elements...")
        svgs = await page.query_selector_all("svg")
        print(f"   Found {len(svgs)} SVG elements")

        for i, svg in enumerate(svgs[:10]):
            try:
                parent = await svg.query_selector("xpath=..")
                parent_tag = await parent.evaluate("el => el.tagName")
                is_visible = await svg.is_visible()
                bbox = await svg.bounding_box()
                print(f"   [{i}] parent={parent_tag} visible={is_visible} bbox={bbox}")
            except Exception as e:
                print(f"   [{i}] Error: {e}")

        # Try clicking the close button by coordinates (top right of modal)
        print("\n5. Trying to close modal by clicking top-right area...")
        # The X button appears to be around x=763, y=305 based on screenshot
        await page.mouse.click(763, 307)
        await asyncio.sleep(1)

        await page.screenshot(path="debug_modal_3.png")
        print("Screenshot 3 saved")

        # Check if modal is still there
        modal_text = page.locator("text=User Notice")
        if await modal_text.count() > 0 and await modal_text.first.is_visible():
            print("   Modal still visible, trying Escape key...")
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)

        await page.screenshot(path="debug_modal_4.png")
        print("Screenshot 4 saved")

        # Check final state
        modal_text = page.locator("text=User Notice")
        if await modal_text.count() == 0 or not await modal_text.first.is_visible():
            print("\n[SUCCESS] Modal closed!")
        else:
            print("\n[FAIL] Modal still visible")

        print("\nPress Enter to close browser...")
        input()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(debug_modal())

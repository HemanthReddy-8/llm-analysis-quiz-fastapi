# pw_test.py
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://tds-llm-analysis.s-anand.net/demo", wait_until="networkidle", timeout=120000)
        print("TITLE:", await page.title())
        body = await page.inner_text("body")
        print("BODY SNIPPET:", body[:600])
        await browser.close()

asyncio.run(main())


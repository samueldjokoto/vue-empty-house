"""
DEBUG version — prints exactly what GitHub Actions sees when it loads Vue.
Once we confirm the diagnosis, we replace this with the real scraper.
"""

import asyncio
import json
from datetime import datetime, timezone
from playwright.async_api import async_playwright

CINEMAS = {
    "Croydon Purley Way": "https://www.myvue.com/cinema/croydon-purley-way/whats-on",
    "Croydon Grants": "https://www.myvue.com/cinema/croydon-grants/whats-on",
}

OUTPUT_FILE = "vue_results.json"


async def diagnose(url: str, label: str):
    print(f"\n{'='*70}")
    print(f"DIAGNOSING: {label}")
    print(f"URL: {url}")
    print(f"{'='*70}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
            locale="en-GB",
        )
        page = await context.new_page()

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = response.status if response else "no-response"
            print(f"\nHTTP STATUS: {status}")
            if response:
                headers = response.headers
                interesting = ['server', 'cf-ray', 'cf-cache-status', 'x-amz-cf-id',
                              'x-vercel-id', 'set-cookie', 'content-type']
                print(f"\nResponse headers (interesting only):")
                for k in interesting:
                    if k in headers:
                        v = headers[k][:200]
                        print(f"  {k}: {v}")
        except Exception as e:
            print(f"\nFAILED TO LOAD: {type(e).__name__}: {e}")
            await browser.close()
            return

        await page.wait_for_timeout(5000)

        title = await page.title()
        print(f"\nPAGE <title>: {title!r}")
        print(f"FINAL URL:    {page.url}")

        session_count = await page.evaluate(
            "() => document.querySelectorAll('a.session, a[href*=\"/book-tickets\"]').length"
        )
        print(f"SESSION LINKS FOUND: {session_count}")

        markers = await page.evaluate("""() => {
            const text = document.body ? document.body.innerText.slice(0, 5000) : '';
            return {
                hasJustAMoment: /just a moment/i.test(text),
                hasCloudflare:  /cloudflare/i.test(text),
                hasChallenge:   /challenge|verify|captcha|attention required/i.test(text),
                hasBlocked:     /blocked|denied|forbidden/i.test(text),
                bodyTextStart:  text.slice(0, 800),
            };
        }""")
        print(f"\nBOT-CHALLENGE MARKERS:")
        print(f"  'Just a moment'      : {markers['hasJustAMoment']}")
        print(f"  'Cloudflare'         : {markers['hasCloudflare']}")
        print(f"  challenge/verify/etc : {markers['hasChallenge']}")
        print(f"  blocked/denied       : {markers['hasBlocked']}")

        print(f"\nFIRST 800 CHARS OF VISIBLE TEXT:")
        print("-" * 70)
        print(markers['bodyTextStart'])
        print("-" * 70)

        html = await page.content()
        print(f"\nFIRST 2500 CHARS OF RAW HTML:")
        print("-" * 70)
        print(html[:2500])
        print("-" * 70)

        await browser.close()


async def main():
    started = datetime.now()
    for label, url in CINEMAS.items():
        await diagnose(url, label)

    output = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": (datetime.now() - started).total_seconds(),
        "sessions": [],
        "_debug_run": True,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n\nWrote {OUTPUT_FILE} (debug placeholder)")


if __name__ == "__main__":
    asyncio.run(main())
